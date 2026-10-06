"""Эконометрический анализ: описательные статистики, стационарность, VIF,
H1 и H2 (OLS с HAC), ARDL, разности и коинтеграция, диагностика остатков,
проверки устойчивости, тесты Чоу и Quandt–Andrews.

Каждый шаг выполняется только при достаточном числе наблюдений. Если шаг
не выполнен, причина записывается в outputs/tables/model_status.csv и
попадает в results_summary.md.
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.diagnostic import (acorr_breusch_godfrey, het_breuschpagan,
                                          het_white, linear_reset)
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.stattools import jarque_bera
from statsmodels.tools.sm_exceptions import InterpolationWarning
from statsmodels.tsa.stattools import adfuller, coint, kpss

from . import config
from .build_dataset import MAIN_VARS
from .tables import save_table

ALPHA = 0.05
MIN_OBS_EXTRA = 10  # минимум наблюдений сверх числа параметров
ADF_MAXLAG = 4  # при ~45 месяцах правило Шверта дало бы 9 лагов

LABELS = {
    "const": "Константа",
    "flow_mm_pct": "Приток в ФДР, % СЧА",
    "flow_mm": "Приток в ФДР, млрд руб.",
    "nav_mm": "СЧА ФДР, млрд руб.",
    "flow_bond": "Поток в облигационные фонды, млрд руб.",
    "flow_bond_pct": "Поток в облигационные фонды, % СЧА",
    "rusfar_avg": "RUSFAR, среднее за месяц, %",
    "rusfarind_ret": "Доходность RUSFARIND за месяц, %",
    "key_rate_avg": "Ключевая ставка, среднее за месяц, %",
    "key_rate_eom": "Ключевая ставка на конец месяца, %",
    "d_key_rate": "Изменение ключевой ставки, п.п.",
    "dep_rate": "Ставка по вкладам до 1 года, %",
    "spread_dep": "Спред RUSFAR − ставка по вкладам, п.п.",
    "spread_key": "Спред RUSFAR − ключевая ставка, п.п.",
    "ofz_slope": "Наклон кривой ОФЗ (1 год − 3 мес.), п.п.",
    "imoex_ret": "Доходность IMOEX за месяц, %",
    "imoex_vol": "Волатильность IMOEX, % годовых",
    "fx_vol": "Волатильность USD/RUB, % годовых",
}


def is_dummy(term: str) -> bool:
    """Дамми месяца (d_YYYY_MM...), но не изменение ключевой ставки d_key_rate."""
    return term.startswith("d_") and not term.startswith("d_key_rate")


def label(term: str) -> str:
    base, _, lag = term.partition("_L")
    if lag.isdigit() and base in LABELS:
        return f"{LABELS[base]}, лаг {lag}" if lag != "0" else LABELS[base]
    if is_dummy(term):
        parts = term[2:].split("_")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"Дамми: {parts[0]}-{parts[1]}" + (f" ({' '.join(parts[2:])})" if parts[2:] else "")
        return f"Дамми: {term[2:]}"
    return LABELS.get(term, term)


class Status:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, step: str, status: str, message: str = "") -> None:
        self.rows.append({"step": step, "status": status, "message": message})
        print(f"  [{status:>8}] {step}: {message}")

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=["step", "status", "message"])


# --- данные ------------------------------------------------------------------------

def load_dataset() -> pd.DataFrame:
    ds = pd.read_csv(config.PROCESSED / "dataset.csv")
    ds["month"] = pd.PeriodIndex(ds["month"], freq="M")
    return ds.set_index("month")


def dummy_cols(ds: pd.DataFrame) -> list[str]:
    return [c for c in ds.columns if is_dummy(c)]


def design(ds: pd.DataFrame, y: str, terms: list[tuple[str, int]],
           dummies: list[str], sample: pd.Index) -> tuple[pd.Series, pd.DataFrame]:
    """y и X с лагами, рассчитанными по полному ряду (включая 2022 г.)."""
    X = pd.DataFrame(index=ds.index)
    for col, lag in terms:
        X[f"{col}_L{lag}"] = ds[col].shift(lag) if col in ds else np.nan
    for d in dummies:
        X[d] = ds[d]
    Y = ds[y] if y in ds else pd.Series(np.nan, index=ds.index)
    data = pd.concat([Y.rename("y"), X], axis=1).loc[sample].dropna()
    # дамми без единиц в выборке неидентифицируемы — исключаются
    keep = [c for c in X.columns if not (c in dummies and data[c].sum() == 0)]
    return data["y"], sm.add_constant(data[keep], has_constant="add")


def fit(Y: pd.Series, X: pd.DataFrame):
    ols = sm.OLS(Y, X).fit()
    hac = sm.OLS(Y, X).fit(cov_type="HAC", cov_kwds={"maxlags": config.HAC_MAXLAGS})
    return ols, hac


def enough(n: int, k: int) -> bool:
    return n >= k + MIN_OBS_EXTRA


def coef_table(res) -> pd.DataFrame:
    ci = res.conf_int(alpha=ALPHA)
    t = pd.DataFrame({"term": res.params.index, "coef": res.params.values,
                      "se_hac": res.bse.values, "t": res.tvalues.values,
                      "p_value": res.pvalues.values, "ci95_low": ci[0].values,
                      "ci95_high": ci[1].values})
    t.insert(1, "label", t["term"].map(label))
    return t


def fit_stats(res, ols) -> pd.DataFrame:
    idx = res.model.data.row_labels
    return pd.DataFrame([
        ("N", res.nobs), ("Период", f"{idx.min()}…{idx.max()}"),
        ("R²", ols.rsquared), ("Скорр. R²", ols.rsquared_adj),
        ("AIC", ols.aic), ("BIC", ols.bic),
        ("Ст. ошибки", f"Ньюи–Уэст (HAC), maxlags={config.HAC_MAXLAGS}"),
    ], columns=["stat", "value"])


# --- 4.1 предварительный анализ ------------------------------------------------------

def longest_run(s: pd.Series) -> pd.Series:
    """Самый длинный непрерывный участок без пропусков."""
    s = s.dropna()
    if s.empty:
        return s
    idx = s.index
    breaks = np.where(np.diff(idx.asi8 if hasattr(idx, "asi8") else np.arange(len(idx))) != 1)[0]
    bounds = np.split(np.arange(len(s)), breaks + 1)
    best = max(bounds, key=len)
    return s.iloc[best]


def descriptive(sample: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for v in MAIN_VARS:
        x = sample[v].dropna() if v in sample else pd.Series(dtype=float)
        rows.append({"variable": v, "label": label(v), "n": len(x),
                     "mean": x.mean(), "std": x.std(), "min": x.min(), "max": x.max()})
    return pd.DataFrame(rows)


def stationarity(sample: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for v in MAIN_VARS:
        if v not in sample:
            continue
        for form in ("уровень", "первая разность"):
            x = longest_run(sample[v])
            if form == "первая разность":
                x = x.diff().dropna()
            row = {"variable": v, "form": form, "n": len(x)}
            if len(x) < 15 or x.std() == 0:
                row["conclusion"] = "не рассчитан (мало наблюдений)"
                rows.append(row)
                continue
            adf = adfuller(x.values, maxlag=ADF_MAXLAG, autolag="AIC", regression="c")
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", InterpolationWarning)
                kp = kpss(x.values, regression="c", nlags="auto")
                kp_note = "p за границей таблицы" if any(
                    issubclass(i.category, InterpolationWarning) for i in w) else ""
            adf_st, kpss_st = adf[1] < ALPHA, kp[1] > ALPHA
            concl = ("стационарен" if adf_st and kpss_st else
                     "нестационарен" if not adf_st and not kpss_st else "неоднозначно")
            row.update({"adf_stat": adf[0], "adf_p": adf[1], "adf_lags": adf[2],
                        "kpss_stat": kp[0], "kpss_p": kp[1], "kpss_note": kp_note,
                        "conclusion": concl})
            rows.append(row)
    return pd.DataFrame(rows)


def vif_table(X: pd.DataFrame) -> pd.DataFrame:
    Xc = X.loc[:, X.std() > 0]
    Xc = sm.add_constant(Xc, has_constant="add")
    rows = [{"term": c, "label": label(c), "vif": variance_inflation_factor(Xc.values, i)}
            for i, c in enumerate(Xc.columns) if c != "const"]
    return pd.DataFrame(rows)


# --- спецификации -------------------------------------------------------------------

def h1_terms(k: int, spread: str = "spread_dep", y: str = "flow_mm_pct"):
    return [(spread, k), ("d_key_rate", 0), ("imoex_ret", 0), ("fx_vol", 0), (y, 1)]


def h2_terms(k: int, with_bond: bool = True):
    t = [("ofz_slope", k)]
    if with_bond:
        t.append(("flow_bond_pct", 0))
    return t + [("spread_dep", k), ("d_key_rate", 0), ("imoex_ret", 0), ("fx_vol", 0),
                ("flow_mm_pct", 1)]


def select_lag(ds, sample_idx, make_terms, dummies, y="flow_mm_pct") -> tuple[int | None, pd.DataFrame]:
    """Выбор k ∈ LAG_CHOICES на общей выборке по BIC (при равном числе
    параметров и общей выборке AIC и BIC ранжируют модели одинаково)."""
    data = {k: design(ds, y, make_terms(k), dummies, sample_idx) for k in config.LAG_CHOICES}
    common = sample_idx
    for Y, _ in data.values():
        common = common.intersection(Y.index)
    rows = []
    for k in config.LAG_CHOICES:
        Y, X = design(ds, y, make_terms(k), dummies, common)
        if not enough(len(Y), X.shape[1]):
            rows.append({"k": k, "n": len(Y), "aic": np.nan, "bic": np.nan, "llf": np.nan})
            continue
        r = sm.OLS(Y, X).fit()
        rows.append({"k": k, "n": int(r.nobs), "aic": r.aic, "bic": r.bic, "llf": r.llf})
    tab = pd.DataFrame(rows)
    if tab["bic"].notna().any():
        best = int(tab.loc[tab["bic"].idxmin(), "k"])
        tab["selected"] = (tab["k"] == best).astype(int)
        return best, tab
    return None, tab


def diagnostics(ols) -> pd.DataFrame:
    rows = []
    exog = ols.model.exog

    def add(name, stat, p, note=""):
        rows.append({"test": name, "statistic": stat, "p_value": p,
                     "reject_h0_5pct": (p < ALPHA) if pd.notna(p) else np.nan, "note": note})
    try:
        lm, lmp, _, _ = acorr_breusch_godfrey(ols, nlags=3)
        add("Бройш–Годфри (3 лага)", lm, lmp, "H0: нет автокорреляции остатков")
    except Exception as e:  # noqa: BLE001
        add("Бройш–Годфри (3 лага)", np.nan, np.nan, f"не рассчитан: {e}")
    try:
        lm, lmp, _, _ = het_breuschpagan(ols.resid, exog)
        add("Бройш–Паган", lm, lmp, "H0: гомоскедастичность")
    except Exception as e:  # noqa: BLE001
        add("Бройш–Паган", np.nan, np.nan, f"не рассчитан: {e}")
    try:
        names = ols.model.exog_names
        keep = [i for i, n in enumerate(names) if not is_dummy(n)]
        lm, lmp, _, _ = het_white(ols.resid, exog[:, keep])
        add("Уайт", lm, lmp, "H0: гомоскедастичность; дамми месяцев исключены из вспомогательной регрессии")
    except Exception as e:  # noqa: BLE001
        add("Уайт", np.nan, np.nan, f"не рассчитан (мало наблюдений для вспомогательной регрессии): {type(e).__name__}")
    jb, jbp, skew, kurt = jarque_bera(ols.resid)
    add("Харке–Бера", jb, jbp, f"H0: нормальность; асимметрия {skew:.2f}, эксцесс {kurt:.2f}")
    try:
        rr = linear_reset(ols, power=3, test_type="fitted", use_f=True)
        add("Рамсей RESET (ŷ², ŷ³)", float(rr.fvalue), float(rr.pvalue), "H0: функциональная форма верна")
    except Exception as e:  # noqa: BLE001
        add("Рамсей RESET (ŷ², ŷ³)", np.nan, np.nan, f"не рассчитан: {e}")
    return pd.DataFrame(rows)


# --- ARDL -------------------------------------------------------------------------

def ardl(ds, sample_idx, dummies, status: Status) -> dict:
    """ARDL(p, q) для H1: p ∈ {1, 2} лагов Y, q ∈ {0, 1, 2} лагов спреда,
    контроли с порядком 0, дамми фиксированы. (p, q) выбираются по BIC на общей
    выборке (hold_back = 2). Лаги до января 2023 г. берутся из данных 2022 г."""
    from statsmodels.tsa.ardl import ARDL
    cols = ["flow_mm_pct", "spread_dep", "d_key_rate", "imoex_ret", "fx_vol"]
    if any(c not in ds for c in cols):
        status.add("ARDL", "skipped", "нет нужных рядов")
        return {}
    hb = 2
    lo = sample_idx.min() - hb
    block = ds.loc[(ds.index >= lo) & (ds.index <= sample_idx.max()), cols + dummies].dropna()
    block = block.loc[longest_run(block["flow_mm_pct"]).index.intersection(block.index)]
    dm = [d for d in dummies if block[d].iloc[hb:].sum() > 0]
    if len(block) - hb < 30:
        status.add("ARDL", "skipped", f"непрерывный участок без пропусков: {len(block)} наблюдений")
        return {}
    y = block["flow_mm_pct"].reset_index(drop=True)
    X = block[cols[1:]].reset_index(drop=True)
    fixed = block[dm].reset_index(drop=True) if dm else None
    grid = []
    for p_ in (1, 2):
        for q in (0, 1, 2):
            order = {"spread_dep": list(range(q + 1)), "d_key_rate": [0], "imoex_ret": [0], "fx_vol": [0]}
            m = ARDL(y, p_, X, order, trend="c", fixed=fixed, hold_back=hb)
            r = m.fit()
            grid.append({"p": p_, "q": q, "n": int(r.nobs), "aic": r.aic, "bic": r.bic, "model": m})
    sel = pd.DataFrame(grid)
    best = sel.loc[sel["bic"].idxmin()]
    sel["selected"] = (sel.index == best.name).astype(int)
    save_table(sel.drop(columns="model"), "ardl_selection", "Выбор порядков ARDL(p, q) по BIC")
    res = best["model"].fit(cov_type="HAC", cov_kwds={"maxlags": config.HAC_MAXLAGS})
    tab = coef_table(res)
    tab["label"] = tab["term"].map(_ardl_label)
    # долгосрочный эффект спреда: Σβ / (1 − Σφ), ст. ошибка дельта-методом (HAC)
    names = list(res.params.index)
    ph = [n for n in names if n.startswith("flow_mm_pct.L")]
    be = [n for n in names if n.startswith("spread_dep.L")]
    Phi, B = res.params[ph].sum(), res.params[be].sum()
    lr = B / (1 - Phi)
    g = pd.Series(0.0, index=names)
    g[be] = 1 / (1 - Phi)
    g[ph] = B / (1 - Phi) ** 2
    se = float(np.sqrt(g.values @ res.cov_params().loc[names, names].values @ g.values))
    from scipy import stats
    z = lr / se
    lr_row = {"term": "LR_spread_dep", "label": "Долгосрочный эффект спреда (дельта-метод)",
              "coef": lr, "se_hac": se, "t": z, "p_value": 2 * stats.norm.sf(abs(z)),
              "ci95_low": lr - 1.96 * se, "ci95_high": lr + 1.96 * se}
    tab = pd.concat([tab, pd.DataFrame([lr_row])], ignore_index=True)
    fit_tab = pd.DataFrame([("N", int(res.nobs)),
                            ("Период", f"{block.index[hb]}…{block.index.max()}"),
                            ("Порядок", f"ARDL({int(best['p'])}, {int(best['q'])})"),
                            ("AIC", res.aic), ("BIC", res.bic),
                            ("Ст. ошибки", f"Ньюи–Уэст (HAC), maxlags={config.HAC_MAXLAGS}")],
                           columns=["stat", "value"])
    save_table(tab, "ardl_h1", "H1, ARDL: порядки по BIC, HAC ст. ошибки", stats=fit_tab)
    status.add("ARDL", "ok", f"N={int(res.nobs)}, ARDL({int(best['p'])}, {int(best['q'])})")
    return {"n": int(res.nobs), "period": f"{block.index[hb]}…{block.index.max()}",
            "p": int(best["p"]), "q": int(best["q"]), "lr": lr_row}


def _ardl_label(term: str) -> str:
    base, _, lag = term.partition(".L")
    if lag.isdigit():
        return f"{LABELS.get(base, base)}, лаг {lag}"
    return label(term)


# --- структурный сдвиг ----------------------------------------------------------------

def _partial_break_F(Y, X, mask, shift_cols) -> tuple[float, int]:
    """F-тест на сдвиг коэффициентов shift_cols после точки (mask=True)."""
    Z = X[shift_cols].mul(mask.astype(float), axis=0).add_suffix("_post")
    Xu = pd.concat([X, Z], axis=1)
    r0 = sm.OLS(Y, X).fit()
    r1 = sm.OLS(Y, Xu).fit()
    q = Z.shape[1]
    F = ((r0.ssr - r1.ssr) / q) / (r1.ssr / (len(Y) - Xu.shape[1]))
    return float(F), q


def chow_tests(Y, X, brk: pd.Period, spread_col: str) -> pd.DataFrame:
    rows = []
    mask = pd.Series(Y.index >= brk, index=Y.index)
    n2, n1, k = int(mask.sum()), int((~mask).sum()), X.shape[1]
    if n2 == 0 or n1 <= k:
        return pd.DataFrame([{"test": f"Чоу, {brk}", "note": "точка вне выборки"}])
    from scipy import stats
    # (а) частичный сдвиг: константа и спред
    F, q = _partial_break_F(Y, X, mask, ["const", spread_col])
    df2 = len(Y) - k - q
    rows.append({"test": f"Чоу (сдвиг константы и спреда), с {brk}", "F": F, "df1": q,
                 "df2": df2, "p_value": stats.f.sf(F, q, df2), "n_before": n1, "n_after": n2,
                 "note": "остальные коэффициенты общие"})
    # (б) полный сдвиг или прогнозный вариант при малом n2
    # регрессоры, постоянные до точки (дамми месяцев после неё), исключаются
    cols = [c for c in X.columns if c == "const" or X.loc[~mask, c].std() > 0]
    X1, Xp = X.loc[~mask, cols], X[cols]
    rp = sm.OLS(Y, Xp).fit()
    r1 = sm.OLS(Y[~mask], X1).fit()
    kk = len(cols)
    if n2 > kk:
        r2 = sm.OLS(Y[mask], Xp[mask]).fit()
        ssr_u = r1.ssr + r2.ssr
        F = ((rp.ssr - ssr_u) / kk) / (ssr_u / (len(Y) - 2 * kk))
        rows.append({"test": f"Чоу (все коэффициенты), с {brk}", "F": F, "df1": kk,
                     "df2": len(Y) - 2 * kk, "p_value": stats.f.sf(F, kk, len(Y) - 2 * kk),
                     "n_before": n1, "n_after": n2, "note": ""})
    else:
        F = ((rp.ssr - r1.ssr) / n2) / (r1.ssr / (n1 - kk))
        rows.append({"test": f"Чоу прогнозный (все коэффициенты), с {brk}", "F": F, "df1": n2,
                     "df2": n1 - kk, "p_value": stats.f.sf(F, n2, n1 - kk),
                     "n_before": n1, "n_after": n2,
                     "note": "после точки наблюдений меньше числа параметров, поэтому прогнозный вариант"})
    return pd.DataFrame(rows)


def quandt_andrews(Y, X, spread_col: str) -> dict:
    """sup-F по датам внутри [15 %, 85 %] выборки для сдвига константы и спреда.
    p-значение — бутстрап с фиксированными регрессорами (Hansen, 2000)."""
    n = len(Y)
    lo, hi = int(np.floor(config.QA_TRIM * n)), int(np.ceil((1 - config.QA_TRIM) * n))
    cands = Y.index[lo:hi]
    if len(cands) < 3:
        return {"note": "мало наблюдений"}
    shift = ["const", spread_col]

    def supF(yv):
        vals = [(_partial_break_F(yv, X, pd.Series(Y.index >= c, index=Y.index), shift)[0], c)
                for c in cands]
        return max(vals, key=lambda t: t[0])

    F, at = supF(Y)
    resid = sm.OLS(Y, X).fit().resid
    rng = np.random.default_rng(config.RANDOM_STATE)
    boot = np.array([supF(pd.Series(resid.values * rng.standard_normal(n), index=Y.index))[0]
                     for _ in range(config.QA_BOOTSTRAP_REPS)])
    p = (1 + (boot >= F).sum()) / (config.QA_BOOTSTRAP_REPS + 1)
    return {"test": "Quandt–Andrews sup-F (сдвиг константы и спреда)", "F": F,
            "break_at": str(at), "p_value": p, "bootstrap_reps": config.QA_BOOTSTRAP_REPS,
            "trim": config.QA_TRIM, "candidates": f"{cands.min()}…{cands.max()}"}


# --- основной сценарий -----------------------------------------------------------------

def run() -> dict:
    config.TABLES.mkdir(parents=True, exist_ok=True)
    # таблицы прошлого прогона удаляются, чтобы пропущенный шаг не оставил устаревший результат
    for f in config.TABLES.glob("*"):
        if f.suffix in (".csv", ".md", ".json"):
            f.unlink()
    st = Status()
    out: dict = {"model_start": str(config.MODEL_START)}
    ds = load_dataset()
    sample = ds[ds["in_model_sample"] == 1]
    sidx = sample.index
    dums = dummy_cols(ds)
    out["dummies"] = dums

    save_table(descriptive(sample), "descriptive", "Описательные статистики (модельный период)")
    num = sample[[v for v in MAIN_VARS if v in sample and sample[v].notna().sum() > 2]]
    if num.shape[1] >= 2:
        corr = num.corr()
        corr.index.name = "variable"
        save_table(corr.reset_index(), "correlation", "Корреляции Пирсона (попарно доступные наблюдения)")
    st_tab = stationarity(sample)
    save_table(st_tab, "stationarity", "ADF (H0: единичный корень; лаги по AIC, не более 4) и KPSS (H0: стационарность), константа")
    out["stationarity"] = st_tab.to_dict("records")

    # ---- H1
    y_n = sample["flow_mm_pct"].notna().sum() if "flow_mm_pct" in sample else 0
    out["y_n"] = int(y_n)
    if y_n == 0:
        st.add("H1", "skipped", "нет зависимой переменной flow_mm_pct (нет данных о потоках/СЧА)")
        return _finish(out, st)
    k, lagtab = select_lag(ds, sidx, h1_terms, dums)
    save_table(lagtab, "lag_selection_h1", "Выбор лага k для spread_dep (общая выборка)")
    if k is None:
        st.add("H1", "skipped", "недостаточно наблюдений для выбора лага")
        return _finish(out, st)
    out["h1_k"] = k
    st.add("Выбор лага H1", "ok", f"k={k}")
    Y, X = design(ds, "flow_mm_pct", h1_terms(k), dums, sidx)
    if not enough(len(Y), X.shape[1]):
        st.add("H1", "skipped", f"N={len(Y)} при {X.shape[1]} параметрах")
        return _finish(out, st)
    ols, hac = fit(Y, X)
    main = coef_table(hac)
    save_table(main, "h1_main", "H1: OLS, ст. ошибки Ньюи–Уэста", stats=fit_stats(hac, ols))
    out["h1"] = {"coefs": main.to_dict("records"), "n": int(hac.nobs),
                 "r2": ols.rsquared, "r2_adj": ols.rsquared_adj,
                 "period": f"{Y.index.min()}…{Y.index.max()}",
                 "flow_estimated_share": float(ds.loc[Y.index, "flow_is_estimated"].mean())}
    st.add("H1 OLS-HAC", "ok", f"N={int(hac.nobs)}")
    diag = diagnostics(ols)
    save_table(diag, "h1_diagnostics", "Диагностика остатков основной регрессии H1")
    out["h1_diag"] = diag.to_dict("records")
    vif = vif_table(X.drop(columns="const"))
    save_table(vif, "vif_h1", "VIF регрессоров H1")
    out["vif_max"] = float(vif["vif"].max()) if len(vif) else np.nan

    # ---- ARDL
    try:
        out["ardl"] = ardl(ds, sidx, dums, st)
    except Exception as e:  # noqa: BLE001
        st.add("ARDL", "failed", repr(e))

    # ---- разности и коинтеграция
    out["diff"] = differences(ds, sidx, k, dums, st, st_tab)

    # ---- H2
    out["h2"] = h2(ds, sidx, dums, st)

    # ---- устойчивость
    out["robust"] = robustness(ds, sidx, k, dums, st)

    # ---- структурный сдвиг (без дамми пиков после точки: см. chow_tests)
    try:
        spread_col = f"spread_dep_L{k}"
        brk = chow_tests(Y, X, config.CHOW_BREAK, spread_col)
        qa = quandt_andrews(Y, X, spread_col)
        tab = pd.concat([brk, pd.DataFrame([qa])], ignore_index=True)
        save_table(tab, "structural_break", "Тесты на структурный сдвиг (H1)")
        out["breaks"] = tab.to_dict("records")
        st.add("Структурный сдвиг", "ok", f"Чоу в {config.CHOW_BREAK}, QA: {qa.get('break_at')}")
    except Exception as e:  # noqa: BLE001
        st.add("Структурный сдвиг", "failed", repr(e))
    return _finish(out, st)


def differences(ds, sidx, k, dums, st: Status, st_tab: pd.DataFrame) -> dict:
    lev = st_tab[st_tab["form"] == "уровень"].set_index("variable")["conclusion"]
    nonstat = [v for v in ("flow_mm_pct", "spread_dep") if lev.get(v) in ("нестационарен", "неоднозначно")]
    res = {"nonstationary_in_levels": nonstat}
    d = ds.copy()
    d["dy"] = d["flow_mm_pct"].diff()
    d["dspread"] = d["spread_dep"].diff()
    Y, X = design(d, "dy", [("dspread", k), ("d_key_rate", 0), ("imoex_ret", 0), ("fx_vol", 0)],
                  dums, sidx)
    if enough(len(Y), X.shape[1]):
        ols, hac = fit(Y, X)
        tab = coef_table(hac)
        tab.loc[tab["term"] == f"dspread_L{k}", "label"] = f"Δ спреда (вклады), лаг {k}"
        save_table(tab, "h1_differences", "H1 в разностях: Δflow_mm_pct, HAC",
                   stats=fit_stats(hac, ols))
        row = tab[tab["term"] == f"dspread_L{k}"].iloc[0]
        res["dspread"] = {"coef": row["coef"], "p": row["p_value"], "lo": row["ci95_low"],
                          "hi": row["ci95_high"], "n": int(hac.nobs)}
    block = ds.loc[sidx, ["flow_mm_pct", "spread_dep"]].dropna()
    block = block.loc[longest_run(block["flow_mm_pct"]).index.intersection(block.index)]
    if len(block) >= 20:
        t, p, _ = coint(block["flow_mm_pct"], block["spread_dep"], trend="c")
        res["eg"] = {"stat": t, "p": p, "n": len(block)}
        save_table(pd.DataFrame([{"test": "Энгл–Грейнджер (flow_mm_pct, spread_dep)",
                                  "stat": t, "p_value": p, "n": len(block)}]),
                   "cointegration", "Тест Энгла–Грейнджера на коинтеграцию (H0: нет коинтеграции)")
    st.add("Разности/коинтеграция", "ok",
           f"нестационарны в уровнях: {nonstat or 'нет'}")
    return res


def h2(ds, sidx, dums, st: Status) -> dict:
    has_bond = "flow_bond_pct" in ds and ds.loc[sidx, "flow_bond_pct"].notna().sum() > 0
    res = {"with_bond": has_bond}
    if not has_bond:
        st.add("H2", "partial", "нет помесячного flow_bond_pct; оценён вариант без потока облигационных фондов")
    make = (lambda k: h2_terms(k, has_bond))
    k, lagtab = select_lag(ds, sidx, make, dums)
    save_table(lagtab, "lag_selection_h2", "Выбор лага k для ofz_slope и spread_dep (H2)")
    if k is None:
        st.add("H2", "skipped", "недостаточно наблюдений (вероятно, нет ofz_slope)")
        return res
    Y, X = design(ds, "flow_mm_pct", make(k), dums, sidx)
    ols, hac = fit(Y, X)
    tab = coef_table(hac)
    title = "H2: OLS, ст. ошибки Ньюи–Уэста" + ("" if has_bond else " (без flow_bond_pct: нет данных)")
    save_table(tab, "h2_main", title, stats=fit_stats(hac, ols))
    save_table(diagnostics(ols), "h2_diagnostics", "Диагностика остатков H2")
    res.update({"k": k, "n": int(hac.nobs), "coefs": tab.to_dict("records"), "r2": ols.rsquared})
    st.add("H2 OLS-HAC", "ok", f"k={k}, N={int(hac.nobs)}")
    return res


def robustness(ds, sidx, k, dums, st: Status) -> list[dict]:
    peaks = [d for d in dums if d in ("d_2026_04", "d_2026_07")]
    peak_months = [pd.Period(d[2:].replace("_", "-"), freq="M") for d in peaks]
    variants = [
        ("Основная спецификация", "flow_mm_pct", h1_terms(k), dums, sidx, f"spread_dep_L{k}"),
        ("spread_key вместо spread_dep", "flow_mm_pct", h1_terms(k, "spread_key"), dums, sidx,
         f"spread_key_L{k}"),
        ("Без апреля и июля 2026", "flow_mm_pct", h1_terms(k), [d for d in dums if d not in peaks],
         sidx.difference(pd.PeriodIndex(peak_months, freq="M")), f"spread_dep_L{k}"),
        ("Только 2024–2026", "flow_mm_pct", h1_terms(k), dums,
         sidx[sidx >= pd.Period("2024-01", freq="M")], f"spread_dep_L{k}"),
        ("Без дамми пиков", "flow_mm_pct", h1_terms(k), [d for d in dums if d not in peaks], sidx,
         f"spread_dep_L{k}"),
        ("Y = поток, млрд руб.", "flow_mm", h1_terms(k, y="flow_mm"), dums, sidx, f"spread_dep_L{k}"),
    ]
    if "flow_mm" in ds and (ds.loc[sidx, "flow_mm"].dropna() > 0).all() and ds.loc[sidx, "flow_mm"].notna().any():
        d2 = ds.assign(log_flow_mm=np.log(ds["flow_mm"]))
        variants.append(("Y = ln(поток)", "log_flow_mm", h1_terms(k, y="log_flow_mm"), dums, sidx,
                         f"spread_dep_L{k}"))
    else:
        d2 = ds
        st.add("Устойчивость: логарифм Y", "skipped",
               "в выборке есть неположительные потоки или нет данных, логарифм неприменим")
    rows, full = [], []
    for name, y, terms, dm, idx, key in variants:
        Y, X = design(d2, y, terms, dm, idx)
        if not enough(len(Y), X.shape[1]):
            rows.append({"variant": name, "term": key, "n": len(Y), "note": "мало наблюдений"})
            continue
        ols, hac = fit(Y, X)
        tab = coef_table(hac)
        tab.insert(0, "variant", name)
        full.append(tab)
        r = tab[tab["term"] == key].iloc[0]
        rows.append({"variant": name, "term": key, "label": label(key), "coef": r["coef"],
                     "se_hac": r["se_hac"], "p_value": r["p_value"], "ci95_low": r["ci95_low"],
                     "ci95_high": r["ci95_high"], "n": int(hac.nobs), "r2": ols.rsquared,
                     "period": f"{Y.index.min()}…{Y.index.max()}", "note": ""})
    rob = pd.DataFrame(rows)
    save_table(rob, "robustness", "Проверки устойчивости: коэффициент при спреде (HAC)")
    if full:
        save_table(pd.concat(full, ignore_index=True), "robustness_full",
                   "Проверки устойчивости: все коэффициенты")
    st.add("Устойчивость", "ok", f"вариантов: {len(rob)}")
    return rob.to_dict("records")


def _finish(out: dict, st: Status) -> dict:
    save_table(st.frame(), "model_status", "Статус шагов анализа")
    out["status"] = st.frame().to_dict("records")
    (config.TABLES / "model_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return out


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Period):
        return str(o)
    return str(o)


if __name__ == "__main__":
    run()
