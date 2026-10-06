"""Проверка разборщиков ответов источников на образцах формата (без сети).

Образцы воспроизводят структуру ответов ISS и ЦБ; значения в них условные.
    python tests/test_parsers.py
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import pandas as pd

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
    fetch_cbr.raw_path = lambda series, ext="csv": Path("/dev/null")
    fetch_cbr.fetch_key_rate(S(), pd.Timestamp("2022-05-01"), pd.Timestamp("2022-06-30"))
    df = saved["cbr_key_rate"]
    assert df["key_rate"].tolist() == [11.0, 11.0, 9.5] and df["date"].is_monotonic_increasing


def test_deposit_xlsx():
    months = pd.period_range("2022-01", "2024-06", freq="M")
    head = pd.DataFrame([["Ставки по вкладам физических лиц в рублях", "", ""],
                         ["", "до востребования", 'до 1 года, кроме "до востребования"']])
    body = pd.DataFrame({0: [m.to_timestamp() for m in months], 1: 3.0, 2: [8 + i / 10 for i in range(len(months))]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        pd.concat([head, body], ignore_index=True).to_excel(w, sheet_name="физлица руб", header=False, index=False)
    out, desc = fetch_cbr.parse_deposit_xlsx(buf.getvalue())
    assert len(out) == len(months) and out["month"].iloc[0] == "2022-01" and "кроме" in desc


def test_compounding():
    d = pd.DataFrame({"date": pd.bdate_range("2024-01-01", "2024-02-29"), "value": 16.0})
    r = compounded_month_return(calendar_daily(d, "value", pd.Timestamp("2024-02-29")))
    assert math.isclose(r[pd.Period("2024-01", "M")], ((1 + 16 / 36500) ** 31 - 1) * 100, rel_tol=1e-9)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
