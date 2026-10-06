"""Общие настройки проекта: период, пути, тикеры, URL первоисточников."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# Рабочий каталог данных и результатов. Переопределяется переменной окружения
# FDR_WORKDIR только в тесте на синтетических данных (tests/), чтобы тестовые
# файлы никогда не попадали в data/ и outputs/ проекта.
WORK = Path(os.environ.get("FDR_WORKDIR", ROOT))
DATA = WORK / "data"
RAW = DATA / "raw"
MANUAL = DATA / "manual"
PROCESSED = DATA / "processed"
OUTPUTS = WORK / "outputs"
TABLES = OUTPUTS / "tables"
FIGURES = OUTPUTS / "figures"

# Фиксированный seed: используется в бутстрапе p-значения теста Quandt–Andrews.
RANDOM_STATE = 42
QA_BOOTSTRAP_REPS = 999
QA_TRIM = 0.15
# Точка теста Чоу (первый месяц второго режима); можно поменять.
CHOW_BREAK = pd.Period("2026-06", freq="M")
HAC_MAXLAGS = 3
LAG_CHOICES = (0, 1, 2)

# Период загрузки (с запасом для лагов и разностей) и период модели.
FETCH_START = pd.Timestamp("2022-06-01")
MODEL_START = pd.Period("2023-01", freq="M")
# Последний месяц не позже сентября 2026; фактически берётся последний
# полностью завершившийся месяц на дату запуска (см. last_full_month()).
MODEL_END_CAP = pd.Period("2026-09", freq="M")


def last_full_month(today: pd.Timestamp | None = None) -> pd.Period:
    today = pd.Timestamp.today().normalize() if today is None else today
    prev = (today.to_period("M") - 1)
    return min(prev, MODEL_END_CAP)


# --- Мосбиржа, ISS API -------------------------------------------------------
ISS_BASE = "https://iss.moex.com/iss"
MOEX_INDEX_SECIDS = {
    "rusfar": "RUSFAR",        # RUSFAR overnight, % годовых
    "rusfarind": "RUSFARIND",  # индекс накопленной доходности RUSFAR (с 31.01.2025)
    "imoex": "IMOEX",
}
# Биржевые фонды денежного рынка (рублёвые, стратегия «репо с ЦК / RUSFAR»).
# Используются только для оценки месячной доходности фондов r при расчёте
# потока по формуле ΔСЧА − r·СЧА(t−1), если прямой ряд потоков отсутствует.
# Список можно править; тикеры, которых нет в ISS, просто пропускаются.
MM_ETF_TICKERS = ["LQDT", "AKMM", "SBMM", "TMON"]
MM_ETF_BOARD = "TQTF"

# --- Банк России ---------------------------------------------------------------
CBR_KEYRATE_URL = (
    "https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True"
    "&UniDbQuery.From={start}&UniDbQuery.To={end}"
)
CBR_KEYRATE_PAGE = "https://www.cbr.ru/hd_base/KeyRate/"
CBR_FX_URL = (
    "https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={start}"
    "&date_req2={end}&VAL_NM_RQ={code}"
)
CBR_FX_CODES = {"usd": "R01235", "cny": "R01375"}
# Средневзвешенные ставки по вкладам физлиц в рублях (раздел «Процентные ставки
# по кредитам и депозитам»). Точное расположение файла на сайте ЦБ меняется;
# при ошибке загрузки используется ручной файл data/manual/cbr_dep_rate.csv.
CBR_DEPOSIT_XLSX = "https://www.cbr.ru/vfs/statistics/pdko/int_rat/deposits.xlsx"
CBR_DEPOSIT_PAGE = "https://www.cbr.ru/statistics/bank_sector/int_rat/"

HTTP_TIMEOUT = 40
USER_AGENT = "Mozilla/5.0 (research script; money-market-fund-flows)"

# Годовой коэффициент для волатильности по дневным данным.
ANNUALIZATION_DAYS = 252
