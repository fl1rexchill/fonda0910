"""Проверка разборщиков ответов источников на образцах формата (без сети).

Образцы воспроизводят структуру ответов ISS и ЦБ; значения в них условные.
    python tests/test_parsers.py
"""
from __future__ import annotations

import io
import math
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

# Все записи (журнал загрузок, сырые файлы) — во временный каталог, не в проект.
_TMP = tempfile.TemporaryDirectory(prefix="fdr_parsers_")
os.environ["FDR_WORKDIR"] = _TMP.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import fetch_cbr, fetch_moex  # noqa: E402
from src.build_dataset import compounded_month_return, calendar_daily  # noqa: E402


class FakeResp:
    def __init__(self, js=None, content=b"", text=""):
        self._js, self.content, self.text = js, content, text

    def raise_for_status(self):
        pass

    def json(self):
        return self._js


class FakeSession:
    """Отдаёт историю ISS страницами по 2 строки."""
    def __init__(self, rows):
        self.rows = rows

    def get(self, url, params=None, timeout=None):
        start = int(params.get("start", 0))
        page = self.rows[start:start + 2]
        return FakeResp([{"charsetinfo": {"name": "utf-8"}},
                         {"history": page,
                          "history.cursor": [{"INDEX": start, "TOTAL": len(self.rows), "PAGESIZE": 2}]}])


def test_iss_paging():
    rows = [{"BOARDID": "SNDX", "TRADEDATE": f"2025-01-0{i}", "CLOSE": 20 + i} for i in range(1, 6)]
    df = fetch_moex._close_series(fetch_moex.iss_history(FakeSession(rows), "/x", "a", "b"))
    assert len(df) == 5 and df["value"].iloc[-1] == 25


def test_gcurve_flat():
    p = {"B1": 1500, "B2": 0, "B3": 0, "T1": 1.0, **{f"G{i}": 0 for i in range(1, 10)}}
    y = fetch_moex.gcurve_yield(1.0, p)
    assert math.isclose(y, (math.exp(0.15) - 1) * 100, rel_tol=1e-9)


def test_fx_xml():
    xml = ('<?xml version="1.0" encoding="windows-1251"?><ValCurs ID="R01375">'
           '<Record Date="01.06.2022" Id="R01375"><Nominal>1</Nominal><Value>9,5000</Value></Record>'
           '<Record Date="02.06.2022" Id="R01375"><Nominal>10</Nominal><Value>95,1000</Value></Record>'
           '</ValCurs>').encode("windows-1251")

    class S:
        def get(self, url, timeout=None):
            return FakeResp(content=xml)
    saved = {}
    fetch_cbr.save_raw = lambda df, series, url: saved.setdefault(series, df)
    fetch_cbr.fetch_fx(S(), "cny", "R01375", pd.Timestamp("2022-06-01"), pd.Timestamp("2022-06-02"))
    df = saved["cbr_fx_cny"]
    assert list(df["rate"].round(4)) == [9.5, 9.51]


def test_key_rate_html():
    html = ("<table class='data'><tr><th>Дата</th><th>Ставка</th></tr>"
            "<tr><td>02.06.2022</td><td>11,00</td></tr><tr><td>27.05.2022</td><td>11,00</td></tr>"
            "<tr><td>14.06.2022</td><td>9,50</td></tr></table>")

    class S:
        def get(self, url, timeout=None):
            return FakeResp(content=html.encode(), text=html)
    saved = {}
    fetch_cbr.save_raw = lambda df, series, url: saved.setdefault(series, df)
    fetch_cbr.raw_path = lambda series, ext="csv": Path(_TMP.name) / f"{series}.{ext}"
    fetch_cbr.fetch_key_rate(S(), pd.Timestamp("2022-05-01"), pd.Timestamp("2022-06-30"))
    df = saved["cbr_key_rate"]
    assert df["key_rate"].tolist() == [11.0, 11.0, 9.5] and df["date"].is_monotonic_increasing


def test_deposit_xlsx():
    """Макет листа ЦБ: заголовок, двухуровневая шапка (физлица / организации)."""
    months = ["Январь 2022", "Февраль 2022"] + [f"{m} 2023" for m in
              ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
               "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]] + [f"{m} 2024" for m in
              ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
               "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]]
    title = ("Средневзвешенные процентные ставки по привлеченным кредитными организациями "
             "вкладам (депозитам) физических лиц и нефинансовых организаций в рублях*")
    rows = [[title, None, None, None, None, None],
            ["(в целом по Российской Федерации)"] + [None] * 5,
            ["% годовых"] + [None] * 5,
            [None, "Физических лиц, со сроком привлечения", None, None,
             "Нефинансовых организаций, со сроком привлечения", None],
            [None, "''до востребова-ния''", "до 1 года, кроме ''до востребова-ния''", "свыше 1 года",
             "до 1 года, включая ''до востребова-ния''", "свыше 1 года"]]
    rows += [[m, 3.0, 8 + i / 10, 9.0, 7.0, "-"] for i, m in enumerate(months)]
    rows += [[None] * 6, ["* сноска"] + [None] * 5]
    struct = [r[:] for r in rows]
    struct[0][0] = "Структура объема по привлеченным вкладам физических лиц в рублях"
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="ставки_руб", header=False, index=False)
        pd.DataFrame(struct).to_excel(w, sheet_name="cтруктура_руб", header=False, index=False)
    out, desc = fetch_cbr.parse_deposit_xlsx(buf.getvalue())
    assert len(out) == len(months) and out["month"].iloc[0] == "2022-01"
    assert out["dep_rate"].iloc[1] == 8.1 and out["dep_rate_long"].iloc[0] == 9.0
    assert "кроме" in desc and "ставки_руб" in desc


def test_month_end_check():
    from src.build_dataset import pct_change_eom
    d = pd.DataFrame({"date": pd.to_datetime(["2026-04-30", "2026-05-29", "2026-06-19"]),
                      "value": [100.0, 101.0, 102.0]})
    r = pct_change_eom(d, "value")
    assert math.isclose(r[pd.Period("2026-05", "M")], 1.0) and pd.isna(r[pd.Period("2026-06", "M")])


def test_fund_panel():
    """Сумма по фондам: смена состава данных не даёт «притока», запуск фонда даёт."""
    from src.build_dataset import fund_panel
    idx = pd.period_range("2024-11", "2025-03", freq="M")
    reg = pd.DataFrame({"ticker": ["AAA", "BBB", "CCC"], "launch_month": ["2020-01", "2020-01", "2025-02"],
                        "include": [1, 1, 1]})
    nav = pd.DataFrame([("2024-11", "AAA", 100.0), ("2024-12", "AAA", 110.0), ("2025-01", "AAA", 111.0),
                        ("2025-02", "AAA", 112.0), ("2025-03", "AAA", 113.0),
                        # BBB: данных за 2024-11 нет — 2024-11 и 2024-12 должны выпасть
                        ("2024-12", "BBB", 50.0), ("2025-01", "BBB", 50.5), ("2025-02", "BBB", 51.0),
                        ("2025-03", "BBB", 51.0),
                        ("2025-02", "CCC", 20.0), ("2025-03", "CCC", 20.0)],
                       columns=["month", "ticker", "nav_bln_eom"])
    rets = pd.DataFrame({"AAA": 1.0, "BBB": 1.0, "CCC": 1.0}, index=idx)
    p = fund_panel(idx, nav, reg, rets, pd.Series(1.0, index=idx))
    f = pd.to_numeric(p["flow"], errors="coerce")
    assert pd.isna(f[pd.Period("2024-11", "M")]) and pd.isna(f[pd.Period("2024-12", "M")])
    # 2025-01: AAA 111 − 110·1.01 = −0.1; BBB 50.5 − 50·1.01 = 0
    assert math.isclose(f[pd.Period("2025-01", "M")], -0.1, abs_tol=1e-9)
    # 2025-02: запуск CCC (+20) + AAA (112 − 111·1.01) + BBB (51 − 50.5·1.01)
    assert math.isclose(f[pd.Period("2025-02", "M")], 20 - 0.11 - 0.005, abs_tol=1e-9)
    assert p.at[pd.Period("2024-12", "M"), "nav"] == 160.0 and p.at[pd.Period("2025-02", "M"), "n_funds"] == 3


def test_compounding():
    d = pd.DataFrame({"date": pd.bdate_range("2024-01-01", "2024-02-29"), "value": 16.0})
    r = compounded_month_return(calendar_daily(d, "value", pd.Timestamp("2024-02-29")))
    assert math.isclose(r[pd.Period("2024-01", "M")], ((1 + 16 / 36500) ** 31 - 1) * 100, rel_tol=1e-9)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
