"""Загрузка рядов Банка России.

Ряды: история ключевой ставки (официальная страница hd_base/KeyRate),
официальные курсы USD/RUB и CNY/RUB (XML_dynamic), средневзвешенные ставки
по рублёвым вкладам физлиц (xlsx раздела «Процентные ставки»).

Статистика ПИФ (СЧА и потоки по категориям) в машиночитаемом помесячном
виде через стабильный URL не публикуется, поэтому вводится вручную
(см. data/manual/README.md).
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import pandas as pd

from . import config
from .common import log_fetch, raw_path, reachable, save_raw, session


def fetch_key_rate(s, start: pd.Timestamp, end: pd.Timestamp) -> None:
    url = config.CBR_KEYRATE_URL.format(start=start.strftime("%d.%m.%Y"),
                                        end=end.strftime("%d.%m.%Y"))
    try:
        r = s.get(url, timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        raw_path("cbr_key_rate_page", "html").write_bytes(r.content)
        tables = pd.read_html(io.StringIO(r.text), decimal=",", thousands=" ")
        tab = next(t for t in tables if t.shape[1] >= 2 and "Дата" in str(t.columns[0]))
        df = pd.DataFrame({
            "date": pd.to_datetime(tab.iloc[:, 0], format="%d.%m.%Y", errors="coerce"),
            "key_rate": pd.to_numeric(tab.iloc[:, 1].astype(str).str.replace(",", "."),
                                      errors="coerce"),
        }).dropna().sort_values("date")
        if df.empty:
            raise ValueError("таблица ключевой ставки пуста")
        save_raw(df, "cbr_key_rate", url)
    except Exception as e:  # noqa: BLE001
        log_fetch("cbr_key_rate", "failed", url=url, error=repr(e))


def fetch_fx(s, name: str, code: str, start: pd.Timestamp, end: pd.Timestamp) -> None:
    url = config.CBR_FX_URL.format(start=start.strftime("%d/%m/%Y"),
                                   end=end.strftime("%d/%m/%Y"), code=code)
    try:
        r = s.get(url, timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        recs = []
        for rec in root.findall("Record"):
            nominal = float(rec.findtext("Nominal").replace(",", "."))
            value = float(rec.findtext("Value").replace(",", "."))
            recs.append({"date": pd.to_datetime(rec.get("Date"), format="%d.%m.%Y"),
                         "rate": value / nominal})
        df = pd.DataFrame(recs)
        if df.empty:
            raise ValueError("пустой ответ XML_dynamic")
        save_raw(df.sort_values("date"), f"cbr_fx_{name}", url)
    except Exception as e:  # noqa: BLE001
        log_fetch(f"cbr_fx_{name}", "failed", url=url, error=repr(e))


# --- Ставки по вкладам -------------------------------------------------------------

_RU_MONTHS = {m: i + 1 for i, m in enumerate(
    ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
     "сентябрь", "октябрь", "ноябрь", "декабрь"])}


def _to_month(x) -> pd.Period | None:
    if isinstance(x, (pd.Timestamp,)) or hasattr(x, "year"):
        try:
            return pd.Timestamp(x).to_period("M")
        except (ValueError, TypeError):
            return None
    sx = str(x).strip().lower()
    m = re.match(r"([а-я]+)\s+(\d{4})", sx)
    if m and m.group(1) in _RU_MONTHS:
        return pd.Period(year=int(m.group(2)), month=_RU_MONTHS[m.group(1)], freq="M")
    m = re.match(r"(\d{2})\.(\d{4})$", sx)
    if m:
        return pd.Period(year=int(m.group(2)), month=int(m.group(1)), freq="M")
    return None


def _txt(values) -> str:
    return " ".join(str(v) for v in values if pd.notna(v)).lower()


def parse_deposit_xlsx(content: bytes) -> tuple[pd.DataFrame, str]:
    """Эвристический разбор xlsx ЦБ со ставками по вкладам.

    Ищется лист про физических лиц в рублях и столбец с заголовком
    «до 1 года, кроме "до востребования"». Разбор принимается только при строгой
    проверке (≥ 24 месяцев, значения 1–30 % годовых), иначе — исключение,
    и используется ручной файл. Возвращает (ряд, описание выбранного столбца).
    """
    book = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    candidates = []
    for sheet, df in book.items():
        text = _txt(df.head(15).values.ravel())
        if "физическ" not in text and "физ" not in sheet.lower():
            continue
        if "руб" not in text and "руб" not in sheet.lower():
            continue
        for ci in range(df.shape[1]):
            header = _txt(df.iloc[:15, ci])
            if "до 1 года" in header and "кроме" in header:
                months = df.iloc[:, 0].map(_to_month)
                vals = pd.to_numeric(df.iloc[:, ci], errors="coerce")
                ser = pd.Series(vals.values, index=months.values).dropna()
                ser = ser[[isinstance(i, pd.Period) for i in ser.index]]
                if len(ser) >= 24 and ser.between(1, 30).all():
                    desc = f"лист «{sheet}», столбец «{' '.join(header.split())[:120]}»"
                    candidates.append((ser, desc))
    if len(candidates) != 1:
        raise ValueError(f"однозначный столбец не найден (кандидатов: {len(candidates)})")
    ser, desc = candidates[0]
    out = pd.DataFrame({"month": ser.index.astype(str), "dep_rate": ser.values})
    return out.drop_duplicates("month", keep="last"), desc


def fetch_deposit_rates(s) -> None:
    url = config.CBR_DEPOSIT_XLSX
    try:
        r = s.get(url, timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        raw_path("cbr_deposit_rates", "xlsx").write_bytes(r.content)
        df, desc = parse_deposit_xlsx(r.content)
        df["series_name"] = desc
        save_raw(df, "cbr_dep_rate", url)
    except Exception as e:  # noqa: BLE001
        log_fetch("cbr_dep_rate", "failed", url=url,
                  error=f"{e!r}; используйте data/manual/cbr_dep_rate.csv")


def main() -> None:
    from .config import FETCH_START, last_full_month
    end = last_full_month().end_time.normalize()
    print(f"Банк России: {FETCH_START.date()} … {end.date()}")
    ok, err = reachable("https://www.cbr.ru/")
    if not ok:
        for key in ["cbr_key_rate", "cbr_fx_usd", "cbr_fx_cny", "cbr_dep_rate"]:
            log_fetch(key, "failed", url="https://www.cbr.ru/", error=f"cbr.ru недоступен: {err}")
        return
    s = session()
    fetch_key_rate(s, FETCH_START, end)
    for name, code in config.CBR_FX_CODES.items():
        fetch_fx(s, name, code, FETCH_START, end)
    fetch_deposit_rates(s)


if __name__ == "__main__":
    main()
