"""Извлечение СЧА из справок управляющих компаний (форма 0420502).

Вход: data/raw/uk_reports/<ТИКЕР>/*.pdf|xls|xlsx — справки о стоимости чистых
активов в том виде, в каком их раскрывает УК, либо выгрузка дневной истории
СЧА и стоимости пая (берётся последний день каждого месяца). Выход:
data/manual/fund_nav_monthly.csv (одна строка на фонд и месяц, со ссылкой на
файл-источник).

Из справки берутся: отчётная дата, СЧА (строка 14), число паёв (строка 15),
расчётная стоимость пая (строка 16). Справка принимается, только если
СЧА = паи × цена пая с точностью 0,5 % (цена пая в части справок округлена до
копеек) и отчётная дата отстоит от конца месяца не более чем на 7 дней.
Иначе файл попадает в отчёт об ошибках и в CSV не пишется.

Для PDF нужна утилита pdftotext (пакет poppler-utils).

    python tools/extract_uk_reports.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "raw" / "uk_reports"
OUT = ROOT / "data" / "manual" / "fund_nav_monthly.csv"
UK = {"TMON": "Т-Капитал", "SBMM": "УК «Первая»", "AKMM": "Альфа-Капитал", "LQDT": "ВИМ Инвестиции"}
NUM = r"(\d{1,3}(?:[  ]\d{3})*(?:,\d+)?)"


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _date(s: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{4})\.(\d{2})\.(\d{2})\s*", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.fullmatch(r"\s*(\d{2})\.(\d{2})\.(\d{4})\s*", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def _pick_units_price(nav: float, c15: list[float], c16: list[float]):
    """Пара (паи, цена), для которой паи × цена ближе всего к СЧА."""
    best = None
    for u in c15:
        for p in c16:
            if u and p:
                err = abs(u * p / nav - 1)
                if best is None or err < best[0]:
                    best = (err, u, p)
    return best


def parse_pdf(path: Path) -> dict:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                          check=True).stdout.decode("utf-8", errors="replace")
    out = {"nav_date": None, "nav": None, "units": None, "unit_price": None, "err": None}
    m = re.search(r"Значения\s+(\d{2}\.\d{2}\.\d{4}|\d{4}\.\d{2}\.\d{2})\s+643", text)
    out["nav_date"] = _date(m.group(1)) if m else None
    m = re.search(r"Стоимость чистых активов\s+14\s+" + NUM, text)
    if not m:
        return out
    out["nav"] = _num(m.group(1))
    tail = text[m.end():m.end() + 3000]
    best = _pick_units_price(out["nav"], [_num(x) for x in re.findall(r"\s15\s+" + NUM, tail)],
                             [_num(x) for x in re.findall(r"\s16\s+" + NUM, tail)])
    if best:
        out["err"], out["units"], out["unit_price"] = best
    return out


def _row_value(df: pd.DataFrame, r: int, code: str) -> float | None:
    cells = list(df.iloc[r])
    for i, v in enumerate(cells):
        if str(v).strip() == code:
            for w in cells[i + 1:]:
                x = _num(w)
                if x is not None:
                    return x
    return None


def parse_excel(path: Path) -> dict:
    out = {"nav_date": None, "nav": None, "units": None, "unit_price": None, "err": None}
    for _, df in pd.read_excel(path, sheet_name=None, header=None).items():
        for r in range(len(df)):
            row = [v for v in df.iloc[r] if pd.notna(v)]
            if out["nav_date"] is None and r < 30:
                out["nav_date"] = next((d for d in map(lambda v: _date(str(v)), row) if d), None)
            if out["nav"] is None and row and str(row[0]).strip().startswith("Стоимость чистых активов") \
                    and any(str(v).strip() == "14" for v in row):
                out["nav"] = _row_value(df, r, "14")
                c15 = [x for rr in range(r + 1, min(r + 6, len(df))) if (x := _row_value(df, rr, "15"))]
                c16 = [x for rr in range(r + 1, min(r + 6, len(df))) if (x := _row_value(df, rr, "16"))]
                best = _pick_units_price(out["nav"], c15, c16)
                if best:
                    out["err"], out["units"], out["unit_price"] = best
    return out


def is_daily_history(path: Path) -> bool:
    """Выгрузка истории: строка заголовков «Дата | Стоимость пая | … | Стоимость чистых активов»."""
    if path.suffix.lower() not in (".xls", ".xlsx"):
        return False
    head = pd.read_excel(path, header=None, nrows=5)
    return any(str(head.iat[r, 0]).strip() == "Дата" and "Стоимость пая" in str(head.iat[r, 1])
               for r in range(min(5, len(head))))


def parse_daily_history(path: Path) -> list[dict]:
    """Дневная история СЧА и стоимости пая: значение на последний день каждого месяца.

    Проверки: СЧА = паи × цена пая (где число паёв указано) с точностью 0,5 %;
    дробление паёв (скачок цены более чем в 2 раза за день) внутри выборки — ошибка.
    """
    raw = pd.read_excel(path, header=None)
    hdr = next(r for r in range(5) if str(raw.iat[r, 0]).strip() == "Дата")
    df = raw.iloc[hdr + 1:, [0, 1, 3, 5]].copy()
    df.columns = ["date", "price", "nav", "units"]
    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d.%m.%Y", errors="coerce")
    for c in ("price", "nav", "units"):
        df[c] = df[c].map(_num)
    df = df.dropna(subset=["date", "price", "nav"]).sort_values("date")
    jump = df["price"] / df["price"].shift(1)
    splits = df.loc[(jump < 0.5) | (jump > 2), "date"]
    chk = (df["nav"] / (df["units"] * df["price"]) - 1).abs()
    if (chk.dropna() > 0.005).any():
        raise ValueError("СЧА ≠ паи × цена пая в части дней")
    df["month"] = df["date"].dt.to_period("M")
    last = df.groupby("month").tail(1)
    out = []
    for r in last.itertuples():
        if (r.month.end_time.normalize() - r.date).days > 7:
            continue  # месяц в выгрузке неполный
        later_split = splits[splits > r.date - pd.Timedelta(days=40)]
        out.append({"nav_date": r.date.strftime("%Y-%m-%d"), "nav": r.nav, "units": r.units,
                    "unit_price": r.price,
                    "err": float(chk.loc[r.Index]) if pd.notna(chk.loc[r.Index]) else None,
                    "split_near": bool(len(later_split[later_split <= r.date]))})
    return out


def main() -> int:
    rows, problems = [], []
    for tdir in sorted(p for p in SRC.iterdir() if p.is_dir()):
        ticker = tdir.name
        for f in sorted(tdir.iterdir()):
            rel = f.relative_to(ROOT).as_posix()
            if is_daily_history(f):
                for r in parse_daily_history(f):
                    if r["split_near"]:
                        problems.append((rel, {**r, "why": "дробление паёв рядом с датой"}))
                        continue
                    d = pd.Timestamp(r["nav_date"])
                    rows.append({
                        "month": str(d.to_period("M")), "ticker": ticker,
                        "nav_bln_eom": round(r["nav"] / 1e9, 6), "nav_date": r["nav_date"],
                        "units": r["units"], "unit_price": r["unit_price"],
                        "source_title": f"История СЧА и стоимости пая (выгрузка), {UK.get(ticker, '')}, "
                                        f"значение на {d:%d.%m.%Y}",
                        "source_url": rel,
                        "note": ("СЧА = паи × цена пая, расхождение "
                                 f"{r['err'] * 100:.3f}%" if r["err"] is not None
                                 else "число паёв в выгрузке не указано (до 09.2023)"),
                    })
                continue
            if f.suffix.lower() == ".pdf":
                r = parse_pdf(f)
            elif f.suffix.lower() in (".xls", ".xlsx"):
                r = parse_excel(f)
            else:
                continue
            if not r["nav"] or not r["nav_date"] or r["err"] is None or r["err"] > 0.005:
                problems.append((rel, r))
                continue
            d = pd.Timestamp(r["nav_date"])
            if (d.to_period("M").end_time.normalize() - d).days > 7:
                problems.append((rel, {**r, "why": "дата не конец месяца"}))
                continue
            rows.append({
                "month": str(d.to_period("M")), "ticker": ticker,
                "nav_bln_eom": round(r["nav"] / 1e9, 6), "nav_date": r["nav_date"],
                "units": r["units"], "unit_price": r["unit_price"],
                "source_title": f"Справка о СЧА (форма 0420502), {UK.get(ticker, '')}, на {d:%d.%m.%Y}",
                "source_url": rel,
                "note": f"СЧА = паи × цена пая, расхождение {r['err'] * 100:.3f}%",
            })
    df = pd.DataFrame(rows).sort_values(["ticker", "month"])
    dup = df[df.duplicated(["ticker", "month"], keep=False)]
    if len(dup):
        print("Дубли (фонд, месяц):\n", dup[["ticker", "month", "source_url"]].to_string())
        return 1
    df.to_csv(OUT, index=False)
    print(f"{OUT.relative_to(ROOT)}: {len(df)} строк")
    print(df.groupby("ticker")["month"].agg(["size", "min", "max"]).to_string())
    for rel, r in problems:
        print("НЕ ПРИНЯТ:", rel, r)
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
