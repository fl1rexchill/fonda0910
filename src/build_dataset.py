"""Сборка помесячной таблицы data/processed/dataset.csv и data/reconciliation.csv.

Правила:
- пропуски не заполняются; все пропуски в модельном периоде выгружаются в
  data/processed/missing_values.csv и попадают в data_sources.md;
- единственные расчётные преобразования (не заполнение пропусков) описаны в
  DERIVATIONS и выводятся в data_sources.md.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config
from .common import latest_raw, raw_download_date, read_fetch_log

PROVENANCE_FILE = config.PROCESSED / "provenance.json"

# Описание расчётных шагов (выводится в data_sources.md).
DERIVATIONS = [
    "Ключевая ставка: дневной ряд ЦБ продлевается на нерабочие дни значением, "
    "действующим на эту дату (ставка действует до следующего решения). Это "
    "определение ставки, а не интерполяция. `key_rate_avg` — среднее по "
    "календарным дням месяца, `key_rate_eom` — значение на последний день месяца.",
    "`rusfar_avg` — среднее арифметическое дневных значений RUSFAR overnight за торговые дни месяца.",
    "`rusfar_ret_calc` — месячная доходность, рассчитанная капитализацией RUSFAR overnight "
    "по календарным дням (ставка последнего торгового дня действует до следующего): "
    "∏(1 + R/36500) − 1. Используется только как справочный ряд и как r в оценке потока, "
    "если нет цен фондов и RUSFARIND. В `rusfarind_ret` не подставляется.",
    "`rusfarind_ret` — изменение индекса RUSFARIND между последними торговыми днями "
    "соседних месяцев, %. Индекс рассчитывается с 31.01.2025, поэтому до февраля 2025 г. ряд пуст.",
    "`imoex_ret` — изменение IMOEX между последними торговыми днями месяцев, %; "
    "`imoex_vol` — стандартное отклонение дневных лог-доходностей за месяц × √252, % годовых.",
    "`fx_vol` (USD/RUB) и `fx_vol_cny` (CNY/RUB) — стандартное отклонение дневных "
    "лог-изменений официального курса ЦБ за месяц × √252, % годовых. Изменение курса, "
    "приходящееся на первый день месяца, относится к этому месяцу.",
    "`ofz_slope` — доходность бескупонной кривой ОФЗ на 1 год минус на 3 месяца, п.п., "
    "на последний торговый день месяца. Берутся значения блока yearyields ISS; если "
    "их нет, доходность считается по параметрам G-кривой (методика Мосбиржи); "
    "источник по месяцам — в колонке `ofz_source`.",
    "`flow_mm_pct` = `flow_mm` / `nav_mm`(t−1) × 100; `flow_bond_pct` аналогично.",
    "Если `flow_mm` за месяц нет, а СЧА на начало и конец месяца есть, поток "
    "оценивается как СЧА(t) − СЧА(t−1)·(1 + r/100), то есть ΔСЧА − r·СЧА(t−1), где r — "
    "средняя месячная доходность цен биржевых фондов денежного рынка (если нет — "
    "RUSFARIND, если нет — `rusfar_ret_calc`). Такие месяцы помечены `flow_is_estimated = 1`, "
    "источник r — в `r_source`.",
]


# Фиксированная схема dataset.csv: колонки есть всегда, даже если ряд не загружен.
COLUMNS = ["flow_mm", "nav_mm", "flow_mm_pct", "flow_is_estimated", "r_source",
           "flow_bond", "nav_bond", "flow_bond_pct", "flow_bond_source",
           "rusfar_avg", "rusfar_ret_calc", "rusfarind_ret", "mm_etf_ret", "mm_etf_n",
           "key_rate_avg", "key_rate_eom", "d_key_rate", "dep_rate", "dep_rate_long",
           "spread_dep", "spread_key", "ofz_3m", "ofz_1y", "ofz_slope", "ofz_source",
           "imoex_ret", "imoex_vol", "fx_vol", "fx_vol_cny"]


# --- загрузка ------------------------------------------------------------------

def _fetch_url(series: str) -> str:
    log = read_fetch_log()
    ok = log[(log["series"] == series) & (log["status"] == "ok")]
    return ok["url"].iloc[-1] if len(ok) else ""


def _read_nonempty(path) -> pd.DataFrame | None:
    if path is None or not path.exists():
        return None
    df = pd.read_csv(path)
    return df if len(df.dropna(how="all")) else None


def load(series: str, manual_name: str | None = None) -> tuple[pd.DataFrame | None, dict]:
    """Автоматическая выгрузка из data/raw, иначе ручной файл, иначе None."""
    path = latest_raw(series)
    df = _read_nonempty(path)
    if df is not None:
        return df, {"origin": "auto", "file": path.relative_to(config.WORK).as_posix(),
                    "download_date": raw_download_date(path), "url": _fetch_url(series)}
    mpath = config.MANUAL / f"{manual_name or series}.csv"
    df = _read_nonempty(mpath)
    if df is not None:
        mtime = pd.Timestamp.fromtimestamp(mpath.stat().st_mtime).date().isoformat()
        return df, {"origin": "manual", "file": mpath.relative_to(config.WORK).as_posix(),
                    "download_date": f"{mtime} (дата изменения файла)", "url": "см. source_url в файле"}
    return None, {"origin": "missing", "file": "", "download_date": "", "url": ""}


# --- помесячные преобразования -------------------------------------------------------

def _dates(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_datetime(df[col])
    return df.sort_values(col)


def last_in_month(df: pd.DataFrame, value: str) -> pd.Series:
    df = _dates(df)
    return df.groupby(df["date"].dt.to_period("M"))[value].last()


def pct_change_eom(df: pd.DataFrame, value: str) -> pd.Series:
    eom = last_in_month(df, value)
    full = eom.reindex(pd.period_range(eom.index.min(), eom.index.max(), freq="M"))
    return full.pct_change(fill_method=None) * 100


def realized_vol(df: pd.DataFrame, value: str) -> pd.Series:
    df = _dates(df)
    lr = np.log(df[value].astype(float)).diff()
    g = lr.groupby(df["date"].dt.to_period("M"))
    vol = g.std(ddof=1) * np.sqrt(config.ANNUALIZATION_DAYS) * 100
    # месяц без предыдущего наблюдения (самый первый) — лог-доходность неполная
    first = df["date"].dt.to_period("M").iloc[0]
    vol.loc[first] = np.nan
    return vol


def calendar_daily(df: pd.DataFrame, value: str, end: pd.Timestamp) -> pd.Series:
    """Ступенчатый ряд по календарным дням: значение действует до следующего."""
    df = _dates(df)
    s = df.set_index("date")[value].astype(float)
    s = s[~s.index.duplicated(keep="last")]
    idx = pd.date_range(s.index.min(), max(end, s.index.max()), freq="D")
    return s.reindex(idx).ffill()


def compounded_month_return(daily_rate: pd.Series) -> pd.Series:
    acc = np.log1p(daily_rate / 36500.0)
    out = np.expm1(acc.groupby(acc.index.to_period("M")).sum()) * 100
    # неполные месяцы на краях ряда не считаем
    first, last = daily_rate.index.min(), daily_rate.index.max()
    if first.day != 1:
        out = out.drop(first.to_period("M"), errors="ignore")
    if last != last.to_period("M").end_time.normalize():
        out = out.drop(last.to_period("M"), errors="ignore")
    return out


# --- основная функция ---------------------------------------------------------

def build() -> pd.DataFrame:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    end_p = config.last_full_month()
    months = pd.period_range(config.FETCH_START.to_period("M"), end_p, freq="M")
    ds = pd.DataFrame(index=months)
    ds.index.name = "month"
    prov: dict[str, dict] = {}

    # RUSFAR
    df, prov["moex_rusfar"] = load("moex_rusfar")
    if df is not None:
        df = _dates(df)
        ds["rusfar_avg"] = df.groupby(df["date"].dt.to_period("M"))["value"].mean()
        # ставка пятницы действует и в выходные в конце последнего месяца
        end = min(end_p.end_time.normalize(), df["date"].max() + pd.Timedelta(days=4))
        daily = calendar_daily(df, "value", end)
        ds["rusfar_ret_calc"] = compounded_month_return(daily)
    # RUSFARIND
    df, prov["moex_rusfarind"] = load("moex_rusfarind")
    if df is not None:
        ds["rusfarind_ret"] = pct_change_eom(df, "value")
    # IMOEX
    df, prov["moex_imoex"] = load("moex_imoex")
    if df is not None:
        ds["imoex_ret"] = pct_change_eom(df, "value")
        ds["imoex_vol"] = realized_vol(df, "value")
    # ОФЗ
    df, prov["moex_zcyc"] = load("moex_zcyc")
    if df is not None:
        df["month"] = pd.PeriodIndex(df["month"], freq="M")
        df = df.set_index("month")
        if {"y_3m_iss", "y_1y_iss"} <= set(df.columns):  # авто: ISS yearyields → параметры
            use_iss = df["y_3m_iss"].notna() & df["y_1y_iss"].notna()
            y3 = df["y_3m_iss"].where(use_iss, df["y_3m_param"])
            y1 = df["y_1y_iss"].where(use_iss, df["y_1y_param"])
            src = np.where(use_iss, "ISS yearyields", "параметры G-кривой")
        else:  # ручной файл
            y3, y1, src = df["y_3m"], df["y_1y"], "ручной ввод"
        ds["ofz_3m"], ds["ofz_1y"] = y3, y1
        ds["ofz_slope"] = y1 - y3
        ds["ofz_source"] = pd.Series(src, index=df.index).where(ds["ofz_slope"].notna())
    # Цены биржевых ФДР → доходность r
    df, prov["moex_mm_etf_prices"] = load("moex_mm_etf_prices")
    if df is not None:
        rets = {t: pct_change_eom(g, "value") for t, g in df.groupby("ticker")}
        ds["mm_etf_ret"] = pd.DataFrame(rets).mean(axis=1, skipna=True)
        ds["mm_etf_n"] = pd.DataFrame(rets).notna().sum(axis=1)
    # Ключевая ставка
    df, prov["cbr_key_rate"] = load("cbr_key_rate")
    if df is not None:
        daily = calendar_daily(df, "key_rate", end_p.end_time.normalize())
        ds["key_rate_avg"] = daily.groupby(daily.index.to_period("M")).mean()
        ds["key_rate_eom"] = daily.groupby(daily.index.to_period("M")).last()
        ds["d_key_rate"] = ds["key_rate_eom"].diff()
    # Курсы
    for cur, col in (("usd", "fx_vol"), ("cny", "fx_vol_cny")):
        df, prov[f"cbr_fx_{cur}"] = load(f"cbr_fx_{cur}")
        if df is not None:
            ds[col] = realized_vol(df, "rate")
    # Ставки по вкладам
    df, prov["cbr_dep_rate"] = load("cbr_dep_rate")
    if df is not None:
        df["month"] = pd.PeriodIndex(df["month"], freq="M")
        df = df.drop_duplicates("month", keep="last").set_index("month")
        ds["dep_rate"] = pd.to_numeric(df["dep_rate"], errors="coerce")
        if "dep_rate_long" in df:
            ds["dep_rate_long"] = pd.to_numeric(df["dep_rate_long"], errors="coerce")
        names = df.get("series_name", pd.Series(dtype=str)).dropna().unique()
        prov["cbr_dep_rate"]["series_name"] = "; ".join(map(str, names))

    for c in ("rusfar_avg", "dep_rate", "key_rate_avg"):
        if c not in ds:
            ds[c] = np.nan
    ds["spread_dep"] = ds["rusfar_avg"] - ds["dep_rate"]
    ds["spread_key"] = ds["rusfar_avg"] - ds["key_rate_avg"]

    # Потоки и СЧА: Мосбиржа (основной ряд), ЦБ (сверка, облигационные фонды)
    moex, prov["moex_fund_flows"] = load("moex_fund_flows")
    cbr, prov["cbr_fund_flows"] = load("cbr_fund_flows")
    _add_flows(ds, moex, cbr)

    # Дамми
    dmy = pd.read_csv(config.MANUAL / "dummies.csv")
    for _, row in dmy[dmy["include"] == 1].iterrows():
        ds[row["name"]] = (ds.index == pd.Period(row["month"], freq="M")).astype(int)

    dummies = [c for c in ds.columns if c not in COLUMNS]
    ds = ds.reindex(columns=COLUMNS + dummies)
    ds["in_model_sample"] = (ds.index >= config.MODEL_START).astype(int)
    out = ds.reset_index()
    out["month"] = out["month"].astype(str)
    out.to_csv(config.PROCESSED / "dataset.csv", index=False)

    rec = reconciliation(moex, cbr)
    rec.to_csv(config.DATA / "reconciliation.csv", index=False)
    missing_report(ds).to_csv(config.PROCESSED / "missing_values.csv", index=False)
    PROVENANCE_FILE.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"dataset.csv: {len(out)} строк, {out.shape[1]} колонок; reconciliation.csv: {len(rec)} строк")
    return ds


def _monthly(df: pd.DataFrame | None, category: str, period_col: str,
             flow_col: str, nav_col: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["flow", "nav"], dtype=float)
    if df is None:
        return empty
    d = df[df["category"] == category]
    if "period_type" in d:
        d = d[d["period_type"] == "M"]
    if d.empty:
        return empty
    d = d.assign(month=pd.PeriodIndex(d[period_col].astype(str), freq="M"))
    if d["month"].duplicated().any():
        raise ValueError(f"дубли месяцев в ручном файле ({category})")
    return d.set_index("month")[[flow_col, nav_col]].rename(
        columns={flow_col: "flow", nav_col: "nav"}).astype(float)


def _add_flows(ds: pd.DataFrame, moex, cbr) -> None:
    mm = _monthly(moex, "money_market", "month", "flow_bln", "nav_bln_eom")
    ds["flow_mm"] = mm["flow"]
    ds["nav_mm"] = mm["nav"]
    # оценка потока там, где прямого значения нет
    r = pd.Series(np.nan, index=ds.index)
    r_src = pd.Series(None, index=ds.index, dtype=object)
    for col, name in (("mm_etf_ret", "цены биржевых ФДР"), ("rusfarind_ret", "RUSFARIND"),
                      ("rusfar_ret_calc", "RUSFAR (расчёт)")):
        if col in ds:
            fill = r.isna() & ds[col].notna()
            r[fill], r_src[fill] = ds.loc[fill, col], name
    est = ds["nav_mm"] - ds["nav_mm"].shift(1) * (1 + r / 100)
    need = ds["flow_mm"].isna() & est.notna()
    ds["flow_is_estimated"] = need.astype(int)
    ds.loc[need, "flow_mm"] = est[need]
    ds["r_source"] = r_src.where(need)
    ds["flow_mm_pct"] = ds["flow_mm"] / ds["nav_mm"].shift(1) * 100

    # облигационные фонды: берётся источник с бо́льшим покрытием модельного периода
    best, best_n, best_name = None, -1, ""
    for name, df in (("cbr", _monthly(cbr, "bond", "period", "flow_bln", "nav_bln_eop")),
                     ("moex", _monthly(moex, "bond", "month", "flow_bln", "nav_bln_eom"))):
        n = df["flow"].reindex(ds.index[ds.index >= config.MODEL_START]).notna().sum()
        if n > best_n:
            best, best_n, best_name = df, n, name
    ds["flow_bond"] = best["flow"]
    ds["nav_bond"] = best["nav"]
    ds["flow_bond_pct"] = ds["flow_bond"] / ds["nav_bond"].shift(1) * 100
    ds["flow_bond_source"] = best_name if best_n > 0 else None


def reconciliation(moex, cbr) -> pd.DataFrame:
    """Сверка потоков Мосбиржи и ЦБ: помесячно (если у ЦБ есть месяцы) и поквартально."""
    cols = ["period", "period_type", "category", "flow_moex_bln", "flow_cbr_bln",
            "diff_bln", "diff_pct_of_cbr", "nav_moex_bln", "nav_cbr_bln",
            "moex_months_in_quarter", "cbr_scope"]
    if moex is None or cbr is None:
        return pd.DataFrame(columns=cols)
    rows = []
    for cat in ("money_market", "bond"):
        m = moex[moex["category"] == cat].copy()
        c = cbr[cbr["category"] == cat].copy()
        if m.empty or c.empty:
            continue
        m["month"] = pd.PeriodIndex(m["month"].astype(str), freq="M")
        m = m.set_index("month").sort_index()
        for _, cr in c.iterrows():
            if cr["period_type"] == "M":
                p = pd.Period(str(cr["period"]), freq="M")
                f_m = m["flow_bln"].get(p, np.nan)
                n_m = m["nav_bln_eom"].get(p, np.nan)
                k = int(pd.notna(f_m))
            else:
                p = pd.Period(str(cr["period"]), freq="Q")
                sub = m[(m.index.asfreq("Q") == p)]
                k = int(sub["flow_bln"].notna().sum())
                # квартальная сумма только при полном покрытии трёх месяцев
                f_m = sub["flow_bln"].sum() if k == 3 else np.nan
                n_m = sub["nav_bln_eom"].get(p.asfreq("M", "end"), np.nan)
            f_c = float(cr["flow_bln"]) if pd.notna(cr["flow_bln"]) else np.nan
            rows.append({
                "period": str(p), "period_type": cr["period_type"], "category": cat,
                "flow_moex_bln": f_m, "flow_cbr_bln": f_c, "diff_bln": f_m - f_c,
                "diff_pct_of_cbr": (f_m - f_c) / abs(f_c) * 100 if f_c else np.nan,
                "nav_moex_bln": n_m, "nav_cbr_bln": cr.get("nav_bln_eop", np.nan),
                "moex_months_in_quarter": k, "cbr_scope": cr.get("fund_scope", ""),
            })
    return pd.DataFrame(rows, columns=cols)


MAIN_VARS = ["flow_mm", "nav_mm", "flow_mm_pct", "flow_bond", "flow_bond_pct",
             "rusfar_avg", "rusfarind_ret", "key_rate_avg", "key_rate_eom", "d_key_rate",
             "dep_rate", "spread_dep", "spread_key", "ofz_slope", "imoex_ret",
             "imoex_vol", "fx_vol"]


def missing_report(ds: pd.DataFrame) -> pd.DataFrame:
    sample = ds[ds.index >= config.MODEL_START]
    rows = []
    for v in MAIN_VARS:
        col = sample[v] if v in sample else pd.Series(np.nan, index=sample.index)
        miss = col.index[col.isna()]
        rows.append({"variable": v, "n_months": len(col), "n_missing": len(miss),
                     "missing_months": _ranges(miss)})
    return pd.DataFrame(rows)


def _ranges(periods) -> str:
    """Сжатая запись списка месяцев: 2023-01…2023-05, 2024-02."""
    if len(periods) == 0:
        return ""
    ps = sorted(periods)
    out, start, prev = [], ps[0], ps[0]
    for p in ps[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(str(start) if start == prev else f"{start}…{prev}")
        start = prev = p
    out.append(str(start) if start == prev else f"{start}…{prev}")
    return ", ".join(out)


if __name__ == "__main__":
    build()
