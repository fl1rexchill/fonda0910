"""Графики для статьи: PNG, 300 dpi, подписи на русском, ч/б-совместимый стиль.

Ряды разного масштаба показываются на отдельных панелях с общей осью
времени: двойные оси Y не используются.
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import config  # noqa: E402
from .models import is_dummy  # noqa: E402

INK, MID, LIGHT = "#000000", "#595959", "#BFBFBF"
WIDTH_IN = 6.3  # ширина полосы набора A4 при половине страницы по высоте
PEAKS = ("2026-04", "2026-07")


def style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Liberation Sans", "Arial"],
        "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
        "axes.edgecolor": MID, "axes.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": "#E0E0E0", "grid.linewidth": 0.5,
        "xtick.color": MID, "ytick.color": MID,
        "lines.linewidth": 1.6, "legend.frameon": False,
        "savefig.dpi": 300, "savefig.bbox": "tight",
    })


def _ts(idx) -> pd.DatetimeIndex:
    return pd.PeriodIndex(idx, freq="M").to_timestamp(how="start") + pd.Timedelta(days=14)


def _date_axis(ax) -> None:
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%Y"))


def _mark_peaks(ax, ds) -> None:
    for p in PEAKS:
        if pd.Period(p, freq="M") in ds.index:
            x = _ts([p])[0]
            ax.axvline(x, color=MID, lw=0.8, ls=":")


def _save(fig, name: str, done: list) -> None:
    config.FIGURES.mkdir(parents=True, exist_ok=True)
    path = config.FIGURES / name
    fig.savefig(path)
    plt.close(fig)
    done.append(name)


def fig1_flows_nav(ds, done, skipped) -> None:
    d = ds[ds["in_model_sample"] == 1]
    if d["flow_mm"].notna().sum() == 0 and d["nav_mm"].notna().sum() == 0:
        skipped.append(("fig1_flows_nav.png", "нет данных о потоках и СЧА"))
        return
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(WIDTH_IN, 4.6), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 2]})
    x = _ts(d.index)
    est = d["flow_is_estimated"].fillna(0).astype(bool)
    a1.bar(x[~est], d["flow_mm"][~est], width=20, color=MID, edgecolor="white", lw=0.5,
           label="Нетто-приток (данные Мосбиржи)")
    if est.any():
        a1.bar(x[est], d["flow_mm"][est], width=20, color="white", edgecolor=INK, lw=0.6,
               hatch="////", label="Нетто-приток (оценка по ΔСЧА)")
    a1.axhline(0, color=INK, lw=0.6)
    a1.set_ylabel("млрд руб.")
    a1.set_title("Нетто-приток в фонды денежного рынка", loc="left")
    _mark_peaks(a1, d)
    a1.legend(loc="upper left")
    a2.plot(x, d["nav_mm"], color=INK, lw=1.6)
    a2.set_ylabel("млрд руб.")
    a2.set_title("СЧА фондов денежного рынка на конец месяца", loc="left")
    _mark_peaks(a2, d)
    _date_axis(a2)
    fig.text(0.01, -0.02, "Пунктир — месяцы пиков притока (апрель и июль 2026 г.).",
             fontsize=7, color=MID)
    fig.tight_layout()
    _save(fig, "fig1_flows_nav.png", done)


def fig2_spread_key(ds, done, skipped) -> None:
    d = ds[ds["in_model_sample"] == 1]
    if d["spread_dep"].notna().sum() == 0 and d["key_rate_eom"].notna().sum() == 0:
        skipped.append(("fig2_spread_keyrate.png", "нет данных о спреде и ключевой ставке"))
        return
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(WIDTH_IN, 4.6), sharex=True)
    x = _ts(d.index)
    a1.plot(x, d["spread_dep"], color=INK, lw=1.6, label="RUSFAR − ставка по вкладам")
    if d["spread_key"].notna().any():
        a1.plot(x, d["spread_key"], color=MID, lw=1.2, ls="--", label="RUSFAR − ключевая ставка")
    a1.axhline(0, color=INK, lw=0.6)
    a1.set_ylabel("п.п.")
    a1.set_title("Спред доходности ФДР", loc="left")
    a1.legend(loc="best")
    a2.step(x, d["key_rate_eom"], where="mid", color=INK, lw=1.6, label="Ключевая ставка (конец месяца)")
    if d["dep_rate"].notna().any():
        a2.plot(x, d["dep_rate"], color=MID, lw=1.2, ls="-.", label="Ставка по вкладам до 1 года")
    if d["rusfar_avg"].notna().any():
        a2.plot(x, d["rusfar_avg"], color=INK, lw=1.0, ls=":", label="RUSFAR, среднее за месяц")
    a2.set_ylabel("% годовых")
    a2.set_title("Ставки", loc="left")
    a2.legend(loc="best")
    _date_axis(a2)
    fig.tight_layout()
    _save(fig, "fig2_spread_keyrate.png", done)


def fig3_mm_vs_bond(ds, done, skipped) -> None:
    d = ds[ds["in_model_sample"] == 1]
    if d["flow_bond"].notna().sum() == 0 or d["flow_mm"].notna().sum() == 0:
        skipped.append(("fig3_mm_vs_bond.png", "нет помесячного ряда потоков облигационных фондов"))
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(WIDTH_IN, 3.0),
                                 gridspec_kw={"width_ratios": [3, 2]})
    x = _ts(d.index)
    a1.plot(x, d["flow_mm"], color=INK, lw=1.6, label="Фонды денежного рынка")
    a1.plot(x, d["flow_bond"], color=MID, lw=1.4, ls="--", label="Облигационные фонды")
    a1.axhline(0, color=INK, lw=0.6)
    a1.set_ylabel("млрд руб.")
    a1.set_title("Нетто-потоки", loc="left")
    a1.legend(loc="best")
    _date_axis(a1)
    for lab in a1.get_xticklabels():
        lab.set_rotation(45)
        lab.set_ha("right")
    both = d[["flow_mm", "flow_bond"]].dropna()
    a2.scatter(both["flow_bond"], both["flow_mm"], s=18, facecolor="white", edgecolor=INK, lw=0.8)
    a2.axhline(0, color=LIGHT, lw=0.6)
    a2.axvline(0, color=LIGHT, lw=0.6)
    a2.set_xlabel("Облигационные фонды, млрд руб.")
    a2.set_ylabel("ФДР, млрд руб.")
    a2.set_title(f"Помесячно, N = {len(both)}", loc="left")
    fig.tight_layout()
    _save(fig, "fig3_mm_vs_bond.png", done)


def fig4_reconciliation(done, skipped) -> None:
    path = config.DATA / "reconciliation.csv"
    rec = pd.read_csv(path) if path.exists() else pd.DataFrame()
    rec = rec[(rec.get("category") == "money_market")] if len(rec) else rec
    if rec.empty or rec[["flow_moex_bln", "flow_cbr_bln"]].dropna().empty:
        skipped.append(("fig4_reconciliation.png", "нет пар значений Мосбиржи и Банка России"))
        return
    rec = rec[rec["period_type"] == rec["period_type"].mode()[0]]
    rec = rec.dropna(subset=["flow_moex_bln", "flow_cbr_bln"], how="all").reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(WIDTH_IN, 3.0))
    pos = np.arange(len(rec))
    w = 0.38
    ax.bar(pos - w / 2 - 0.01, rec["flow_moex_bln"], w, color=INK, label="Мосбиржа")
    ax.bar(pos + w / 2 + 0.01, rec["flow_cbr_bln"], w, color="white", edgecolor=INK,
           hatch="////", lw=0.6, label="Банк России")
    ax.axhline(0, color=INK, lw=0.6)
    ax.set_xticks(pos, rec["period"].astype(str), rotation=45, ha="right")
    ax.set_ylabel("млрд руб.")
    kind = "по кварталам" if (rec["period_type"] == "Q").all() else "по периодам"
    ax.set_title(f"Нетто-приток в фонды денежного рынка {kind}: два источника", loc="left")
    ax.legend(loc="best")
    if rec[["flow_moex_bln", "flow_cbr_bln"]].isna().any(axis=None):
        fig.text(0.01, -0.02, "Отсутствующий столбец означает, что значения нет в источнике "
                 "(для Мосбиржи — неполный квартал).", fontsize=7, color=MID)
    fig.tight_layout()
    _save(fig, "fig4_reconciliation.png", done)


def fig5_coefs(done, skipped) -> None:
    path = config.TABLES / "h1_main.csv"
    if not path.exists():
        skipped.append(("fig5_h1_coefficients.png", "основная регрессия не оценена"))
        return
    t = pd.read_csv(path)
    t = t[(t["term"] != "const") & ~t["term"].map(is_dummy)]
    t = t.iloc[::-1]
    fig, ax = plt.subplots(figsize=(WIDTH_IN, 0.45 * len(t) + 1.0))
    y = np.arange(len(t))
    ax.hlines(y, t["ci95_low"], t["ci95_high"], color=INK, lw=1.4)
    ax.scatter(t["coef"], y, s=36, color=INK, zorder=3)
    ax.axvline(0, color=MID, lw=0.8, ls="--")
    ax.set_yticks(y, t["label"])
    ax.set_xlabel("Оценка коэффициента (Y: приток, % СЧА)")
    ax.set_title("Основная регрессия H1: оценки и 95% ДИ (HAC)", loc="left")
    ax.grid(axis="y", visible=False)
    fig.text(0.01, -0.02, "Константа и дамми-переменные не показаны.", fontsize=7, color=MID)
    fig.tight_layout()
    _save(fig, "fig5_h1_coefficients.png", done)


def run() -> dict:
    style()
    config.FIGURES.mkdir(parents=True, exist_ok=True)
    for f in config.FIGURES.glob("*.png"):
        f.unlink()
    ds = pd.read_csv(config.PROCESSED / "dataset.csv")
    ds["month"] = pd.PeriodIndex(ds["month"], freq="M")
    ds = ds.set_index("month")
    done, skipped = [], []
    fig1_flows_nav(ds, done, skipped)
    fig2_spread_key(ds, done, skipped)
    fig3_mm_vs_bond(ds, done, skipped)
    fig4_reconciliation(done, skipped)
    fig5_coefs(done, skipped)
    res = {"done": done, "skipped": skipped}
    config.TABLES.mkdir(parents=True, exist_ok=True)
    (config.TABLES / "figures_status.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    for f in done:
        print(f"  [    ok] {f}")
    for f, why in skipped:
        print(f"  [skipped] {f}: {why}")
    return res


if __name__ == "__main__":
    run()
