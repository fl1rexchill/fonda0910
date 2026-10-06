"""Проверка работоспособности конвейера на СИНТЕТИЧЕСКИХ данных.

Синтетические ряды создаются только во временном каталоге (FDR_WORKDIR) и
никогда не пишутся в data/ или outputs/ проекта. Результаты теста
содержательного смысла не имеют и в статье не используются.

    python tests/test_synthetic.py            # во временном каталоге, затем удалить
    python tests/test_synthetic.py --keep DIR # оставить результаты в DIR для просмотра
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TAG = "20990101"  # фиктивная «дата загрузки» синтетических файлов


def make_synthetic(work: Path, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    raw, manual = work / "data" / "raw", work / "data" / "manual"
    raw.mkdir(parents=True)
    manual.mkdir(parents=True)
    shutil.copy(ROOT / "data" / "manual" / "dummies.csv", manual / "dummies.csv")

    days = pd.bdate_range("2022-06-01", "2026-09-30")
    steps = {"2022-06-01": 9.5, "2022-07-25": 8.0, "2023-07-24": 8.5, "2023-08-15": 12.0,
             "2023-10-30": 15.0, "2023-12-18": 16.0, "2024-07-29": 18.0, "2024-10-28": 21.0,
             "2025-06-09": 20.0, "2025-09-15": 17.0, "2026-02-16": 15.0, "2026-06-15": 13.0}
    kr = pd.Series(np.nan, index=days)
    for d, v in steps.items():
        kr[kr.index >= d] = v
    pd.DataFrame({"date": days, "key_rate": kr.values}).to_csv(raw / f"cbr_key_rate_{TAG}.csv", index=False)
    rusfar = kr - 0.3 + rng.normal(0, 0.25, len(days))
    pd.DataFrame({"date": days, "value": rusfar.values}).to_csv(raw / f"moex_rusfar_{TAG}.csv", index=False)
    idx_days = days[days >= "2025-01-31"]
    ind = 1000 * np.cumprod(1 + rusfar[idx_days].values / 36500 * 1.4)
    pd.DataFrame({"date": idx_days, "value": ind}).to_csv(raw / f"moex_rusfarind_{TAG}.csv", index=False)
    imoex = 2200 * np.exp(np.cumsum(rng.normal(0, 0.012, len(days))))
    pd.DataFrame({"date": days, "value": imoex}).to_csv(raw / f"moex_imoex_{TAG}.csv", index=False)
    usd = 80 * np.exp(np.cumsum(rng.normal(0, 0.008, len(days))))
    pd.DataFrame({"date": days, "rate": usd}).to_csv(raw / f"cbr_fx_usd_{TAG}.csv", index=False)
    pd.DataFrame({"date": days, "rate": usd / 7.2}).to_csv(raw / f"cbr_fx_cny_{TAG}.csv", index=False)
    etf = [pd.DataFrame({"date": days, "ticker": t,
                         "value": p0 * np.cumprod(1 + rusfar.values / 36500 * 1.4)})
           for t, p0 in (("LQDT", 1.2), ("SBMM", 14.0))]
    pd.concat(etf).to_csv(raw / f"moex_mm_etf_prices_{TAG}.csv", index=False)

    months = pd.period_range("2022-06", "2026-09", freq="M")
    kr_m = kr.groupby(kr.index.to_period("M")).mean()
    dep = kr_m - 2.5 + rng.normal(0, 0.4, len(months))
    pd.DataFrame({"month": months.astype(str), "dep_rate": dep.values,
                  "series_name": "СИНТЕТИКА"}).to_csv(raw / f"cbr_dep_rate_{TAG}.csv", index=False)
    zc = pd.DataFrame({"month": months.astype(str), "date": months.end_time.normalize(),
                       "y_3m_iss": kr_m.values + rng.normal(0, 0.3, len(months)),
                       "y_1y_iss": kr_m.values - 1 + rng.normal(0, 0.5, len(months))})
    zc["y_3m_param"], zc["y_1y_param"] = zc["y_3m_iss"], zc["y_1y_iss"]
    zc.to_csv(raw / f"moex_zcyc_{TAG}.csv", index=False)

    spread = (kr_m - 0.3 - dep).values
    nav, nav_b, rows = 300.0, 400.0, []
    for i, m in enumerate(months):
        flow = nav * (0.02 + 0.012 * spread[i] + rng.normal(0, 0.03))
        if str(m) in ("2026-04", "2026-07"):
            flow += nav * 0.15
        fb = nav_b * rng.normal(-0.005, 0.03) - 0.3 * flow
        nav = nav * (1 + kr_m.iloc[i] / 1200) + flow
        nav_b = nav_b * 1.008 + fb
        rows.append({"month": str(m), "category": "money_market",
                     "flow_bln": np.nan if str(m) in ("2023-03", "2023-04") else round(flow, 2),
                     "nav_bln_eom": round(nav, 2), "source_title": "СИНТЕТИКА", "source_url": ""})
        rows.append({"month": str(m), "category": "bond", "flow_bln": round(fb, 2),
                     "nav_bln_eom": round(nav_b, 2), "source_title": "СИНТЕТИКА", "source_url": ""})
    mf = pd.DataFrame(rows)
    mf.to_csv(manual / "moex_fund_flows.csv", index=False)
    mm = mf[mf["category"] == "money_market"].assign(q=pd.PeriodIndex(mf.loc[mf["category"] == "money_market", "month"], freq="M").asfreq("Q"))
    q = mm.groupby("q").agg(flow=("flow_bln", "sum"), nav=("nav_bln_eom", "last"))
    cbr = pd.DataFrame({"period": q.index.astype(str), "period_type": "Q", "category": "money_market",
                        "fund_scope": "все ПИФ", "flow_bln": (q["flow"] * 1.1).round(1).values,
                        "nav_bln_eop": (q["nav"] * 1.15).round(1).values,
                        "source_title": "СИНТЕТИКА", "source_url": ""})
    cbr.to_csv(manual / "cbr_fund_flows.csv", index=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=Path, default=None)
    args = ap.parse_args()
    tmp = None
    if args.keep:
        work = args.keep.resolve()
        if work.exists():
            shutil.rmtree(work)
    else:
        tmp = tempfile.TemporaryDirectory(prefix="fdr_synth_")
        work = Path(tmp.name) / "w"
    assert ROOT not in [work, *work.parents], "синтетические данные нельзя писать в проект"
    make_synthetic(work)
    env = {**os.environ, "FDR_WORKDIR": str(work)}
    r = subprocess.run([sys.executable, str(ROOT / "run_all.py"), "--skip-fetch"], env=env, cwd=ROOT)
    assert r.returncode == 0, "run_all.py завершился с ошибкой"
    must = ["data/processed/dataset.csv", "data/reconciliation.csv",
            "outputs/tables/h1_main.csv", "outputs/tables/robustness.csv",
            "outputs/tables/structural_break.csv", "outputs/tables/stationarity.csv",
            "outputs/tables/h2_main.csv", "outputs/tables/ardl_h1.csv",
            "outputs/results_summary.md", "outputs/data_sources.md",
            *[f"outputs/figures/{f}" for f in ("fig1_flows_nav.png", "fig2_spread_keyrate.png",
                                               "fig3_mm_vs_bond.png", "fig4_reconciliation.png",
                                               "fig5_h1_coefficients.png")]]
    missing = [m for m in must if not (work / m).exists()]
    assert not missing, f"не созданы: {missing}"
    ds = pd.read_csv(work / "data/processed/dataset.csv")
    assert ds.loc[ds["month"].isin(["2023-03", "2023-04"]), "flow_is_estimated"].eq(1).all()
    assert "Главные результаты" in (work / "outputs/results_summary.md").read_text(encoding="utf-8")
    print(f"\nСинтетический тест пройден. Каталог: {work}")
    if tmp:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
