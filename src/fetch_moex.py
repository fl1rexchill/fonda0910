"""Загрузка рядов Московской биржи через ISS API.

Ряды: RUSFAR (overnight), RUSFARIND, IMOEX, кривая бескупонной доходности
ОФЗ (G-кривая) на конец месяца, цены биржевых фондов денежного рынка.

Нетто-притоки и СЧА фондов в ISS отсутствуют: они вводятся вручную из
публикаций Мосбиржи (см. data/manual/README.md).

Если ISS недоступен, ошибка пишется в data/raw/fetch_log.csv, а
build_dataset.py продолжает работу с тем, что уже есть в data/raw.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config
from .common import log_fetch, reachable, save_raw, session


def _iss_get(s, path: str, params: dict) -> dict:
    url = f"{config.ISS_BASE}{path}"
    r = s.get(url, params={"iss.meta": "off", "iss.json": "extended", **params},
              timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    # формат extended: [ {"charsetinfo":...}, {"history":[{...}], "history.cursor":[...]} ]
    out = {}
    for part in js:
        if isinstance(part, dict):
            out.update(part)
    return out


def iss_history(s, path: str, start: str, end: str, block: str = "history") -> pd.DataFrame:
    """Постраничная загрузка исторического блока ISS."""
    rows, pos = [], 0
    while True:
        data = _iss_get(s, path, {"from": start, "till": end, "start": pos})
        chunk = data.get(block, [])
        rows.extend(chunk)
        cur = data.get(f"{block}.cursor") or []
        if cur:
            c = cur[0]
            pos = int(c["INDEX"]) + int(c["PAGESIZE"])
            if pos >= int(c["TOTAL"]):
                break
        else:
            if not chunk:
                break
            pos += len(chunk)
        if not chunk:
            break
    return pd.DataFrame(rows)


def _close_series(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "value"])
    col = "CLOSE"
    if "LEGALCLOSEPRICE" in df.columns and df["LEGALCLOSEPRICE"].notna().any():
        col = "LEGALCLOSEPRICE"
    out = pd.DataFrame({"date": pd.to_datetime(df["TRADEDATE"]),
                        "value": pd.to_numeric(df[col], errors="coerce")})
    if "BOARDID" in df.columns:
        out["board"] = df["BOARDID"].values
    out = out.dropna(subset=["value"]).sort_values("date")
    # Индекс может дублироваться на разных режимах: оставляем одну запись на дату.
    return out.drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_index(s, key: str, secid: str, start: str, end: str) -> None:
    path = f"/history/engines/stock/markets/index/securities/{secid}.json"
    url = f"{config.ISS_BASE}{path}?from={start}&till={end}"
    try:
        df = _close_series(iss_history(s, path, start, end))
        if df.empty:
            raise ValueError("ISS вернул пустой ряд")
        save_raw(df, f"moex_{key}", url)
    except Exception as e:  # noqa: BLE001 — любая ошибка источника фиксируется в журнале
        log_fetch(f"moex_{key}", "failed", url=url, error=repr(e))


def fetch_mm_etf_prices(s, start: str, end: str) -> None:
    frames = []
    for t in config.MM_ETF_TICKERS:
        path = (f"/history/engines/stock/markets/shares/boards/{config.MM_ETF_BOARD}"
                f"/securities/{t}.json")
        url = f"{config.ISS_BASE}{path}?from={start}&till={end}"
        try:
            df = _close_series(iss_history(s, path, start, end))
            if df.empty:
                raise ValueError("пустой ряд")
            df["ticker"] = t
            frames.append(df[["date", "ticker", "value"]])
            log_fetch(f"moex_etf_{t}", "ok", url=url, rows=len(df))
        except Exception as e:  # noqa: BLE001
            log_fetch(f"moex_etf_{t}", "failed", url=url, error=repr(e))
    if frames:
        save_raw(pd.concat(frames, ignore_index=True), "moex_mm_etf_prices",
                 f"{config.ISS_BASE}/history/engines/stock/markets/shares/boards/"
                 f"{config.MM_ETF_BOARD}/securities/<ticker>.json")


# --- Кривая бескупонной доходности (G-кривая) -------------------------------------

def gcurve_yield(t: float, p: dict) -> float:
    """Доходность G-кривой для срока t лет по параметрам ISS (методика Мосбиржи).

    G(t) = B1 + (B2+B3)·(T1/t)·(1−e^{−t/T1}) − B3·e^{−t/T1} + Σ g_i·e^{−(t−a_i)²/b_i²},
    a1=0, a2=0.6, a_{i+1}=a_i+0.6·1.6^{i−1}, b1=0.6, b_{i+1}=1.6·b_i.
    Y(t) = 10000·(exp(G/10000) − 1) в б.п.; возвращается в % годовых.
    """
    b1, b2, b3, tau = (float(p[k]) for k in ("B1", "B2", "B3", "T1"))
    k = 1.6
    a = [0.0, 0.6]
    for i in range(1, 8):
        a.append(a[-1] + 0.6 * k ** i)
    b = [0.6 * k ** i for i in range(9)]
    g = b1 + (b2 + b3) * (tau / t) * (1 - math.exp(-t / tau)) - b3 * math.exp(-t / tau)
    for i in range(9):
        gi = float(p.get(f"G{i + 1}", 0) or 0)
        g += gi * math.exp(-((t - a[i]) ** 2) / b[i] ** 2)
    return 10000 * (math.exp(g / 10000) - 1) / 100


def _zcyc_on(s, date: pd.Timestamp) -> dict | None:
    data = _iss_get(s, "/engines/stock/zcyc.json", {"date": date.strftime("%Y-%m-%d")})
    params = data.get("params") or []
    if not params:
        return None
    p = params[-1]  # последний расчёт дня
    tradedate = pd.to_datetime(p.get("tradedate") or p.get("TRADEDATE"))
    if pd.isna(tradedate) or tradedate > date or (date - tradedate).days > 7:
        return None
    p = {k.upper(): v for k, v in p.items()}
    rec = {"date": tradedate,
           "y_3m_param": gcurve_yield(0.25, p), "y_1y_param": gcurve_yield(1.0, p)}
    yy = pd.DataFrame(data.get("yearyields") or [])
    if not yy.empty:
        yy.columns = [c.lower() for c in yy.columns]
        yy = yy[pd.to_datetime(yy["tradedate"]) == tradedate]
        if not yy.empty:
            last = yy[yy["tradetime"] == yy["tradetime"].max()] if "tradetime" in yy else yy
            per = last.set_index(last["period"].astype(float))["value"].astype(float)
            rec["y_3m_iss"] = per.get(0.25, np.nan)
            rec["y_1y_iss"] = per.get(1.0, np.nan)
    for key in ("B1", "B2", "B3", "T1", *[f"G{i}" for i in range(1, 10)]):
        rec[key] = p.get(key)
    return rec


def fetch_zcyc_month_ends(s, start: pd.Timestamp, end: pd.Timestamp) -> None:
    """G-кривая на последний торговый день каждого месяца."""
    url = f"{config.ISS_BASE}/engines/stock/zcyc.json?date=YYYY-MM-DD"
    recs, errors = [], []
    for m in pd.period_range(start, end, freq="M"):
        got = None
        for back in range(0, 8):
            d = m.end_time.normalize() - pd.Timedelta(days=back)
            try:
                got = _zcyc_on(s, d)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{d.date()}: {e!r}")
                if len(errors) > 5 and not recs:
                    log_fetch("moex_zcyc", "failed", url=url, error="; ".join(errors[:3]))
                    return
                continue
            if got:
                break
        if got:
            got["month"] = str(m)
            recs.append(got)
    if recs:
        save_raw(pd.DataFrame(recs), "moex_zcyc", url)
    else:
        log_fetch("moex_zcyc", "failed", url=url, error="; ".join(errors[:3]) or "нет данных")


def main() -> None:
    from .config import FETCH_START, last_full_month
    end_p = last_full_month()
    start, end = FETCH_START.strftime("%Y-%m-%d"), end_p.end_time.strftime("%Y-%m-%d")
    print(f"Мосбиржа ISS: {start} … {end}")
    s = session()
    ok, err = reachable(f"{config.ISS_BASE}/index.json")
    if not ok:
        for key in [*config.MOEX_INDEX_SECIDS, "mm_etf_prices", "zcyc"]:
            log_fetch(f"moex_{key}", "failed", url=config.ISS_BASE,
                      error=f"ISS недоступен: {err}")
        return
    for key, secid in config.MOEX_INDEX_SECIDS.items():
        fetch_index(s, key, secid, start, end)
    fetch_mm_etf_prices(s, start, end)
    fetch_zcyc_month_ends(s, FETCH_START, end_p.end_time)


if __name__ == "__main__":
    main()
