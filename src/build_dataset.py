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
    "соседних месяцев, %. Публикация индекса начата 31.01.2025; значения за более ранние даты "
    "ISS отдаёт как ретроспективный расчёт Мосбиржи, они используются без изменений.",
    "Месячные доходности по ценам и индексам (`rusfarind_ret`, `imoex_ret`, `mm_etf_ret`) "
    "считаются только если последнее наблюдение месяца отстоит от конца месяца не более чем "
    "на 7 календарных дней (с учётом праздников); иначе значение месяца — пропуск. "
    "Реализованная волатильность считается при не менее чем 10 дневных изменениях в месяце.",
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
    "Если сводного ряда Мосбиржи нет, поток считается по СЧА отдельных фондов "
    "(`data/manual/fund_nav_monthly.csv`) для фиксированного списка фондов из "
    "`data/manual/mm_funds.csv`: поток фонда = СЧА(t) − СЧА(t−1)·(1 + r/100), где r — "
    "изменение расчётной стоимости пая по тем же справкам о СЧА; если её нет — биржевой цены "
    "фонда (ISS), если нет и её — RUSFARIND. В месяц "
    "запуска фонда поток равен его СЧА. Сумма по фондам считается только если СЧА есть у "
    "всех фондов списка, запущенных к этому месяцу, иначе месяц пропускается: так смена "
    "состава выборки не превращается в «приток». Интерполяция СЧА не применяется. "
    "Источник ряда указан в `flow_mm_source`, число фондов — в `mm_funds_n`.",
]


# Фиксированная схема dataset.csv: колонки есть всегда, даже если ряд не загружен.
COLUMNS = ["flow_mm", "nav_mm", "flow_mm_pct", "flow_is_estimated", "r_source",
           "flow_mm_source", "mm_funds_n", "flow_alt", "nav_alt", "flow_alt_pct",
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


EOM_TOLERANCE_DAYS = 7
MIN_DAYS_FOR_VOL = 10


def last_in_month(df: pd.DataFrame, value: str) -> pd.Series:
    """Значение на последний торговый день месяца; пропуск, если ряд в этом
    месяце обрывается раньше чем за EOM_TOLERANCE_DAYS дней до конца месяца."""
    df = _dates(df)
    g = df.groupby(df["date"].dt.to_period("M"))
    last_val, last_date = g[value].last(), g["date"].last()
    month_end = last_date.index.to_timestamp(how="end").normalize()
    complete = (month_end - last_date.values).days <= EOM_TOLERANCE_DAYS
    return last_val.where(complete)


def pct_change_eom(df: pd.DataFrame, value: str) -> pd.Series:
    eom = last_in_month(df, value)
    full = eom.reindex(pd.period_range(eom.index.min(), eom.index.max(), freq="M"))
    return full.pct_change(fill_method=None) * 100


def realized_vol(df: pd.DataFrame, value: str) -> pd.Series:
    df = _dates(df)
    lr = np.log(df[value].astype(float)).diff()
    g = lr.groupby(df["date"].dt.to_period("M"))
    vol = g.std(ddof=1) * np.sqrt(config.ANNUALIZATION_DAYS) * 100
    vol = vol.where(g.count() >= MIN_DAYS_FOR_VOL)
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
    fund_rets = pd.DataFrame(index=months)
    df, prov["moex_mm_etf_prices"] = load("moex_mm_etf_prices")
    if df is not None:
        rets = {t: pct_change_eom(g, "value") for t, g in df.groupby("ticker")}
        fund_rets = pd.DataFrame(rets)
        ds["mm_etf_ret"] = fund_rets.mean(axis=1, skipna=True)
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
    navf, prov["fund_nav_monthly"] = load("fund_nav_monthly")
    reg_path = config.MANUAL / "mm_funds.csv"
    registry = pd.read_csv(reg_path) if reg_path.exists() else None
    panel = fund_panel(ds.index, navf, registry, fund_rets,
                       ds["rusfarind_ret"] if "rusfarind_ret" in ds else pd.Series(np.nan, index=ds.index))
    _add_flows(ds, moex, cbr, panel)
    if registry is not None and navf is not None:
        alt_reg = registry[registry["ticker"].isin(config.ALT_PANEL_TICKERS)].assign(include=1)
        alt = fund_panel(ds.index, navf, alt_reg, fund_rets, ds["rusfarind_ret"])
        ds["flow_alt"] = pd.to_numeric(alt["flow"], errors="coerce")
        ds["nav_alt"] = pd.to_numeric(alt["nav"], errors="coerce")
        ds["flow_alt_pct"] = ds["flow_alt"] / ds["nav_alt"].shift(1) * 100

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


def fund_panel(index: pd.PeriodIndex, navf: pd.DataFrame | None, registry: pd.DataFrame | None,
               fund_rets: pd.DataFrame, fallback_r: pd.Series) -> pd.DataFrame:
    """Поток и СЧА по фиксированному списку фондов (см. DERIVATIONS)."""
    out = pd.DataFrame(index=index, columns=["flow", "nav", "n_funds", "r_source"], dtype=object)
    if navf is None or registry is None or registry.empty:
        return out
    reg = registry[registry["include"] == 1]
    # пустой launch_month: фонд работал до начала данных (месяцы без СЧА выпадают)
    funds = [(r.ticker, pd.Period(str(r.launch_month), freq="M") if pd.notna(r.launch_month) and
              str(r.launch_month).strip() else None) for r in reg.itertuples()]
    d = navf.assign(month=pd.PeriodIndex(navf["month"].astype(str), freq="M"))
    if d.duplicated(["month", "ticker"]).any():
        raise ValueError("дубли (month, ticker) в fund_nav_monthly.csv")
    nav = d.pivot(index="month", columns="ticker", values="nav_bln_eom").astype(float).reindex(index)
    # доходность пая по самим справкам о СЧА (точнее биржевой цены), если цена пая указана
    if "unit_price" in d and d["unit_price"].notna().any():
        px = d.pivot(index="month", columns="ticker", values="unit_price").astype(float).reindex(index)
        own = (px / px.shift(1) - 1) * 100
    else:
        own = pd.DataFrame(index=index)

    def get(frame, t, f):
        return frame.at[t, f] if (f in frame.columns and t in frame.index) else np.nan

    for t in index:
        active = [(f, launch) for f, launch in funds if launch is None or launch <= t]
        if not active:
            continue
        navs = [get(nav, t, f) for f, _ in active]
        if any(pd.isna(v) for v in navs):
            continue  # нет СЧА хотя бы одного фонда: месяц пропускается
        out.at[t, "nav"], out.at[t, "n_funds"] = float(sum(navs)), len(active)
        flows, srcs = [], set()
        for (f, launch), n_t in zip(active, navs):
            if launch == t:
                flows.append(n_t)
                srcs.add("запуск фонда")
                continue
            n_p = get(nav, t - 1, f)
            r, src = get(own, t, f), "цена пая по справкам о СЧА"
            if pd.isna(r):
                r, src = get(fund_rets, t, f), "цена фонда (ISS)"
            if pd.isna(r):
                r, src = fallback_r.get(t, np.nan), "RUSFARIND (нет цены фонда)"
            if pd.isna(n_p) or pd.isna(r):
                flows = None
                break
            flows.append(n_t - n_p * (1 + r / 100))
            srcs.add(src)
        if flows is not None:
            out.at[t, "flow"], out.at[t, "r_source"] = float(sum(flows)), "; ".join(sorted(srcs))
    return out


def _add_flows(ds: pd.DataFrame, moex, cbr, panel: pd.DataFrame | None = None) -> None:
    mm = _monthly(moex, "money_market", "month", "flow_bln", "nav_bln_eom")
    use_panel = (mm.dropna(how="all").empty and panel is not None
                 and pd.to_numeric(panel["nav"], errors="coerce").notna().any())
    if use_panel:
        # источник выбирается для ряда целиком, а не по месяцам
        ds["flow_mm"] = pd.to_numeric(panel["flow"], errors="coerce")
        ds["nav_mm"] = pd.to_numeric(panel["nav"], errors="coerce")
        ds["mm_funds_n"] = pd.to_numeric(panel["n_funds"], errors="coerce")
        ds["flow_is_estimated"] = ds["flow_mm"].notna().astype(int)
        ds["r_source"] = panel["r_source"].where(ds["flow_mm"].notna())
        ds["flow_mm_source"] = np.where(ds["flow_mm"].notna(), "СЧА фондов (fund_nav_monthly)", None)
        ds["flow_mm_pct"] = ds["flow_mm"] / ds["nav_mm"].shift(1) * 100
        _add_bond(ds, moex, cbr)
        return
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
    ds["flow_mm_source"] = np.where(ds["flow_mm"].isna(), None,
                                    np.where(need, "Мосбиржа: оценка по ΔСЧА", "Мосбиржа"))
    ds["flow_mm_pct"] = ds["flow_mm"] / ds["nav_mm"].shift(1) * 100
    _add_bond(ds, moex, cbr)


def _add_bond(ds: pd.DataFrame, moex, cbr) -> None:
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


# Категория ряда Мосбиржи, с которой сравнивается категория ЦБ. ЦБ в обзорах
# даёт потоки по всем БПИФ (bpif_all), не выделяя фонды денежного рынка.
RECON_PAIRS = {"money_market": "money_market", "bpif_all": "money_market", "bond": "bond"}


def reconciliation(moex, cbr) -> pd.DataFrame:
    """Сверка потоков Мосбиржи и ЦБ: строка на каждое значение ЦБ (месяц или
    квартал). Значения Мосбиржи суммируются до квартала только при наличии всех
    трёх месяцев. same_scope = 0, если охват рядов различается (ЦБ — все БПИФ)."""
    cols = ["period", "period_type", "cbr_category", "moex_category", "same_scope",
            "flow_moex_bln", "flow_cbr_bln", "diff_bln", "diff_pct_of_cbr",
            "nav_moex_bln", "nav_cbr_bln", "moex_months_in_quarter", "cbr_scope", "cbr_source"]
    if cbr is None:
        return pd.DataFrame(columns=cols)
    rows = []
    for _, cr in cbr.iterrows():
        cat_c = cr["category"]
        cat_m = RECON_PAIRS.get(cat_c)
        if cat_m is None:
            continue
        m = moex[moex["category"] == cat_m].copy() if moex is not None else pd.DataFrame()
        if not m.empty:
            m["month"] = pd.PeriodIndex(m["month"].astype(str), freq="M")
            m = m.set_index("month").sort_index()
        if cr["period_type"] == "M":
            p = pd.Period(str(cr["period"]), freq="M")
            f_m = m["flow_bln"].get(p, np.nan) if not m.empty else np.nan
            n_m = m["nav_bln_eom"].get(p, np.nan) if not m.empty else np.nan
            k = int(pd.notna(f_m))
        else:
            p = pd.Period(str(cr["period"]), freq="Q")
            sub = m[m.index.asfreq("Q") == p] if not m.empty else pd.DataFrame(columns=["flow_bln", "nav_bln_eom"])
            k = int(sub["flow_bln"].notna().sum())
            f_m = sub["flow_bln"].sum() if k == 3 else np.nan
            n_m = sub["nav_bln_eom"].get(p.asfreq("M", "end"), np.nan) if len(sub) else np.nan
        f_c = float(cr["flow_bln"]) if pd.notna(cr["flow_bln"]) else np.nan
        rows.append({
            "period": str(p), "period_type": cr["period_type"], "cbr_category": cat_c,
            "moex_category": cat_m, "same_scope": int(cat_c == cat_m),
            "flow_moex_bln": f_m, "flow_cbr_bln": f_c, "diff_bln": f_m - f_c,
            "diff_pct_of_cbr": (f_m - f_c) / abs(f_c) * 100 if f_c else np.nan,
            "nav_moex_bln": n_m, "nav_cbr_bln": cr.get("nav_bln_eop", np.nan),
            "moex_months_in_quarter": k, "cbr_scope": cr.get("fund_scope", ""),
            "cbr_source": cr.get("source_title", ""),
        })
    return pd.DataFrame(rows, columns=cols)


MAIN_VARS = ["flow_mm", "nav_mm", "flow_mm_pct", "flow_bond", "flow_bond_pct",
             "rusfar_avg", "rusfarind_ret", "key_rate_avg", "key_rate_eom", "d_key_rate",
             "dep_rate", "spread_dep", "spread_key", "ofz_slope", "imoex_ret",
             "imoex_vol", "fx_vol"]


AUX_VARS = ["rusfar_ret_calc", "mm_etf_ret", "dep_rate_long", "fx_vol_cny"]


def missing_report(ds: pd.DataFrame) -> pd.DataFrame:
    sample = ds[ds.index >= config.MODEL_START]
    rows = []
    for v in MAIN_VARS + AUX_VARS:
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
