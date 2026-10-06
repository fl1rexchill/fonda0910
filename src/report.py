"""Генерация outputs/data_sources.md и outputs/results_summary.md."""
from __future__ import annotations

import datetime as dt
import json
import re

import numpy as np
import pandas as pd

from . import config
from .build_dataset import DERIVATIONS, PROVENANCE_FILE
from .common import read_fetch_log
from .models import is_dummy
from .tables import fmt, fmt_p, to_markdown


def p_eq(p) -> str:
    s = fmt_p(p)
    return f"p {s[0]} {s[1:]}" if s.startswith("<") else f"p = {s}"

ISS = config.ISS_BASE
VAR_SOURCES = [
    # переменная, ключ происхождения, источник, URL, определение
    ("flow_mm", "moex_fund_flows", "Московская биржа: публикации о рынке биржевых фондов денежного рынка (ручной ввод)",
     "source_url в data/manual/moex_fund_flows.csv",
     "Нетто-приток в рублёвые фонды денежного рынка за месяц, млрд руб.; при отсутствии — оценка по ΔСЧА (flow_is_estimated = 1)"),
    ("nav_mm", "moex_fund_flows", "Московская биржа / раскрытие УК (ручной ввод)",
     "source_url в data/manual/moex_fund_flows.csv", "СЧА фондов денежного рынка на конец месяца, млрд руб."),
    ("flow_mm_pct", None, "расчёт", "", "flow_mm / nav_mm(t−1) × 100, %"),
    ("flow_bond", "cbr_fund_flows", "Банк России, статистика ПИФ по категориям (ручной ввод); при отсутствии месячных данных ЦБ — Мосбиржа",
     "source_url в data/manual/cbr_fund_flows.csv", "Нетто-поток в облигационные фонды, млрд руб. (источник по факту — колонка flow_bond_source)"),
    ("flow_bond_pct", None, "расчёт", "", "flow_bond / nav_bond(t−1) × 100, %"),
    ("rusfar_avg", "moex_rusfar", "Московская биржа, ISS API, индекс RUSFAR (overnight)",
     f"{ISS}/history/engines/stock/markets/index/securities/RUSFAR.json", "Среднее дневных значений за месяц, % годовых"),
    ("rusfarind_ret", "moex_rusfarind", "Московская биржа, ISS API, индекс RUSFARIND",
     f"{ISS}/history/engines/stock/markets/index/securities/RUSFARIND.json", "Месячное изменение индекса, % (ряд с 31.01.2025)"),
    ("rusfar_ret_calc", "moex_rusfar", "расчёт по RUSFAR", "", "Капитализация RUSFAR overnight за месяц, % (справочный ряд)"),
    ("mm_etf_ret", "moex_mm_etf_prices", "Московская биржа, ISS API, цены закрытия БПИФ " + ", ".join(config.MM_ETF_TICKERS),
     f"{ISS}/history/engines/stock/markets/shares/boards/{config.MM_ETF_BOARD}/securities/<тикер>.json",
     "Средняя (равновзвешенная) месячная доходность цен фондов, %; используется только как r при оценке потока"),
    ("key_rate_avg / key_rate_eom", "cbr_key_rate", "Банк России, официальная страница «Ключевая ставка Банка России»",
     config.CBR_KEYRATE_PAGE, "Среднее по календарным дням месяца и значение на последний день месяца, %"),
    ("d_key_rate", "cbr_key_rate", "расчёт", "", "key_rate_eom(t) − key_rate_eom(t−1), п.п."),
    ("dep_rate", "cbr_dep_rate", "Банк России, средневзвешенные процентные ставки по вкладам физлиц в рублях",
     config.CBR_DEPOSIT_PAGE, "Ставка по вкладам сроком до 1 года, кроме «до востребования», % годовых (точное название ряда — ниже)"),
    ("spread_dep", None, "расчёт", "", "rusfar_avg − dep_rate, п.п."),
    ("spread_key", None, "расчёт", "", "rusfar_avg − key_rate_avg, п.п."),
    ("ofz_slope", "moex_zcyc", "Московская биржа, ISS API, кривая бескупонной доходности ОФЗ",
     f"{ISS}/engines/stock/zcyc.json?date=YYYY-MM-DD", "Доходность 1 год − 3 месяца на последний торговый день месяца, п.п."),
    ("imoex_ret / imoex_vol", "moex_imoex", "Московская биржа, ISS API, индекс IMOEX",
     f"{ISS}/history/engines/stock/markets/index/securities/IMOEX.json", "Месячная доходность, %; реализованная волатильность, % годовых"),
    ("fx_vol", "cbr_fx_usd", "Банк России, официальные курсы (USD, R01235)",
     config.CBR_FX_URL.format(start="dd/mm/yyyy", end="dd/mm/yyyy", code="R01235"),
     "Реализованная волатильность официального курса USD/RUB, % годовых"),
    ("fx_vol_cny", "cbr_fx_cny", "Банк России, официальные курсы (CNY, R01375)",
     config.CBR_FX_URL.format(start="dd/mm/yyyy", end="dd/mm/yyyy", code="R01375"),
     "То же для CNY/RUB (запасной контроль)"),
]

ORIGIN_RU = {"auto": "автозагрузка", "manual": "ручной ввод", "missing": "НЕТ ДАННЫХ"}

RECON_TEXT = """\
Ряды потоков Мосбиржи и Банка России не обязаны совпадать. Возможные причины
расхождения (их нужно подтвердить по методическим комментариям к конкретным
публикациям, ссылки на которые указаны в ручных файлах):

1. **Охват фондов.** Мосбиржа считает биржевые фонды (БПИФ и, в части
   публикаций, ОПИФ, торгуемые на бирже). Статистика Банка России охватывает
   все ПИФ для неквалифицированных инвесторов, включая открытые фонды, паи
   которых погашаются через УК, а не на бирже.
2. **Классификация «денежного рынка».** ЦБ относит фонд к категории по
   инвестиционной декларации и составу активов. Мосбиржа обычно берёт список
   фондов с бенчмарком RUSFAR или со стратегией репо с ЦК. Валютные (юаневые)
   фонды денежного рынка в одних выборках есть, в других нет.
3. **Метод расчёта потока.** Поток можно считать по выдаче и погашению паёв
   (число паёв × расчётная стоимость пая) или как ΔСЧА за вычетом доходности.
   Второй способ зависит от выбора r и от комиссий фонда.
4. **Периодичность и даты.** Если ЦБ публикует данные поквартально, сверка
   идёт по кварталам, а месячные данные Мосбиржи суммируются. Квартальная сумма
   считается только при наличии всех трёх месяцев. Возможен и сдвиг дат:
   биржевая сделка и выдача паёв УК фиксируются в разные дни (T+1).
5. **Пересмотры.** ЦБ может уточнять данные в следующих выпусках обзоров.
   В сверке используется версия, указанная в `source_url`.
"""


def _prov() -> dict:
    return json.loads(PROVENANCE_FILE.read_text(encoding="utf-8")) if PROVENANCE_FILE.exists() else {}


def _results() -> dict:
    p = config.TABLES / "model_results.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _read(name: str) -> pd.DataFrame:
    p = config.TABLES / f"{name}.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def _md(name: str) -> str:
    p = config.TABLES / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else "_Таблица не построена (см. статус шагов)._\n"


# --- data_sources.md -----------------------------------------------------------------

def data_sources() -> str:
    prov = _prov()
    rows = []
    ds = pd.read_csv(config.PROCESSED / "dataset.csv") if (config.PROCESSED / "dataset.csv").exists() else pd.DataFrame()
    bond_src = ds["flow_bond_source"].dropna().unique() if "flow_bond_source" in ds else []
    for var, key, src, url, definition in VAR_SOURCES:
        if var == "flow_bond" and len(bond_src) and bond_src[0] == "moex":
            key, src = "moex_fund_flows", "Московская биржа (ручной ввод): помесячных данных ЦБ нет"
            url = "source_url в data/manual/moex_fund_flows.csv"
        pv = prov.get(key, {}) if key else {}
        rows.append({
            "Ряд": var, "Источник": src, "URL": url or "—",
            "Получение": ORIGIN_RU.get(pv.get("origin"), "расчёт") if key else "расчёт",
            "Файл": pv.get("file", "") or "—", "Дата загрузки": pv.get("download_date", "") or "—",
            "Определение": definition,
        })
    dep_name = prov.get("cbr_dep_rate", {}).get("series_name", "")
    log = read_fetch_log()
    failed = log[log["status"] == "failed"].drop_duplicates("series", keep="last")
    ok_later = set(log[log["status"] == "ok"]["series"])
    failed = failed[~failed["series"].isin(ok_later)]

    miss = pd.read_csv(config.PROCESSED / "missing_values.csv") if (config.PROCESSED / "missing_values.csv").exists() else pd.DataFrame()
    n_est = int(ds.loc[ds.get("in_model_sample", 0) == 1, "flow_is_estimated"].sum()) if "flow_is_estimated" in ds else 0
    rec = pd.read_csv(config.DATA / "reconciliation.csv") if (config.DATA / "reconciliation.csv").exists() else pd.DataFrame()
    dmy = pd.read_csv(config.MANUAL / "dummies.csv")

    out = [f"# Источники данных\n\nСформировано: {dt.date.today().isoformat()}. "
           f"Модельный период: {config.MODEL_START}…{config.last_full_month()}; данные загружаются "
           f"с {config.FETCH_START.to_period('M')} для лагов и разностей.\n",
           "## 1. Ряды, источники и определения\n", to_markdown(pd.DataFrame(rows)),
           f"\nРяд ставки по вкладам: {dep_name or '_не задан (нет данных)_'}.\n",
           "\nПриоритет: автоматическая выгрузка из `data/raw/` (в имени файла дата загрузки), "
           "при её отсутствии ручной файл из `data/manual/`. Агрегаторы и новостные сайты "
           "как источник рядов не используются.\n",
           "## 2. Недоступные источники при последнем запуске\n"]
    if len(failed):
        out.append(to_markdown(failed[["series", "url", "error", "run_ts"]].rename(columns={
            "series": "Ряд", "url": "URL", "error": "Ошибка", "run_ts": "Время"})))
        out.append("\nЧто скачать вручную и куда положить, описано в `data/manual/README.md`.\n")
    else:
        out.append("Все автоматические загрузки успешны.\n")
    out += ["\n## 3. Расчётные преобразования\n",
            "\n".join(f"- {d}" for d in DERIVATIONS) + "\n",
            "\n## 4. Интерполяция и замены\n",
            "Пропуски не интерполируются и ничем не заменяются. Единственная замена — "
            f"оценка потока по ΔСЧА с флагом `flow_is_estimated = 1`: в модельном периоде "
            f"таких месяцев {n_est}.\n",
            "\n## 5. Пропуски в модельном периоде\n",
            to_markdown(miss.rename(columns={"variable": "Переменная", "n_months": "Месяцев",
                                             "n_missing": "Пропусков", "missing_months": "Месяцы с пропуском"}))
            if len(miss) else "_dataset.csv не построен._\n",
            "\nНаблюдения с пропуском в любой переменной спецификации исключаются из "
            "соответствующей регрессии (listwise). Число наблюдений указано в каждой таблице.\n",
            "\n## 6. Сверка Мосбиржи и Банка России (`data/reconciliation.csv`)\n", RECON_TEXT]
    pairs = rec.dropna(subset=["flow_moex_bln", "flow_cbr_bln"]) if len(rec) else rec
    if len(pairs):
        mm = pairs[pairs["category"] == "money_market"]
        if len(mm):
            corr = mm["flow_moex_bln"].corr(mm["flow_cbr_bln"]) if len(mm) > 2 else np.nan
            out.append(f"\nФонды денежного рынка: сопоставимых периодов {len(mm)}; средняя разница "
                       f"(Мосбиржа − ЦБ) {fmt(mm['diff_bln'].mean(), 1)} млрд руб.; средняя абсолютная "
                       f"разница {fmt(mm['diff_bln'].abs().mean(), 1)} млрд руб.; корреляция рядов "
                       f"{fmt(corr, 2)}.\n")
        out.append("\n" + to_markdown(pairs, digits=1))
    else:
        out.append("\n**Сверка не выполнена: нет пар значений.** `data/reconciliation.csv` содержит "
                   "только заголовки. Нужно заполнить `data/manual/moex_fund_flows.csv` и "
                   "`data/manual/cbr_fund_flows.csv`.\n")
    out += ["\n## 7. Дамми-переменные: включённые и предложенные\n",
            "Регуляторные и налоговые даты предложены для проверки. Решение о включении "
            "принимает автор (колонка `include` в `data/manual/dummies.csv`).\n\n",
            to_markdown(dmy)]
    return "".join(out)


# --- results_summary.md --------------------------------------------------------------

def _row(tab: list[dict], term: str) -> dict | None:
    for r in tab:
        if r.get("term") == term:
            return r
    return None


def _ci(r: dict, d: int | None = None) -> str:
    if d is None:
        d = 2 if abs(r["coef"]) >= 0.1 else 4
    return (f"{fmt(r['coef'], d)} [95% ДИ {fmt(r['ci95_low'], d)}; {fmt(r['ci95_high'], d)}], "
            f"{p_eq(r['p_value'])}")


def results_summary() -> str:
    res = _results()
    status = pd.DataFrame(res.get("status", []))
    fig_status = {}
    fp = config.TABLES / "figures_status.json"
    if fp.exists():
        fig_status = json.loads(fp.read_text(encoding="utf-8"))
    out = [f"# Краткие результаты\n\nСформировано автоматически: {dt.date.today().isoformat()}. "
           "Источник каждой цифры указан в скобках: файл в `outputs/tables/` и строка.\n"]

    if "h1" not in res:
        out.append("\n## Результаты не получены\n\nОсновная регрессия не оценена. Причины:\n\n")
        out.append(to_markdown(status) if len(status) else "_статус отсутствует_\n")
        out.append("\nПрежде всего нужны помесячные ряды нетто-притока и СЧА фондов денежного "
                   "рынка (`data/manual/moex_fund_flows.csv`) и рыночные ряды. Список "
                   "недоступных источников приведён в `outputs/data_sources.md`, раздел 2, "
                   "инструкция по ручной загрузке — в `data/manual/README.md`.\n")
        out.append(_limitations(res))
        return "".join(out)

    h1, k = res["h1"], res["h1_k"]
    key = f"spread_dep_L{k}"
    s = _row(h1["coefs"], key)
    sent = []
    sent.append(f"Основная регрессия H1 оценена по {h1['n']} месячным наблюдениям за "
                f"{h1['period']} (h1_main_fit.csv, строка «N»); доля месяцев с оценённым, а не "
                f"наблюдаемым потоком — {fmt(100 * h1['flow_estimated_share'], 0)}% (dataset.csv, flow_is_estimated).")
    sent.append(f"Лаг спреда выбран по BIC: k = {k} (lag_selection_h1.csv, строка selected = 1).")
    sig = s["p_value"] < 0.05
    sent.append(f"Коэффициент при спреде RUSFAR − ставка по вкладам: {_ci(s)} (h1_main.csv, строка {key}); "
                f"{'на уровне 5% отличается от нуля' if sig else 'на уровне 5% статистически от нуля не отличается'}.")
    others = [r for r in h1["coefs"] if r["term"] not in ("const", key) and r["p_value"] < 0.05
              and not is_dummy(r["term"])]
    sent.append("Из прочих регрессоров на уровне 5% значимы: " +
                (", ".join(f"{r['label']} ({fmt(r['coef'], 2)}, {p_eq(r['p_value'])})" for r in others)
                 if others else "ни один") + " (h1_main.csv).")
    rob = [r for r in res.get("robust", []) if pd.notna(r.get("coef"))]
    if rob:
        pos = sum(r["coef"] > 0 for r in rob)
        sg = sum(r["p_value"] < 0.05 for r in rob)
        sent.append(f"В {len(rob)} вариантах проверки устойчивости коэффициент при спреде положителен в "
                    f"{pos} и значим на 5% в {sg} (robustness.csv).")
    h2 = res.get("h2", {})
    o = _row(h2.get("coefs", []), f"ofz_slope_L{h2.get('k')}") if h2.get("coefs") else None
    if o:
        b = _row(h2["coefs"], "flow_bond_pct_L0")
        txt = f"В H2 коэффициент при наклоне кривой ОФЗ: {_ci(o)} (h2_main.csv, строка ofz_slope_L{h2['k']})"
        txt += (f"; при потоке в облигационные фонды: {_ci(b)} (строка flow_bond_pct_L0)." if b else
                "; поток в облигационные фонды в модель не вошёл: нет помесячных данных.")
        sent.append(txt)
    br = res.get("breaks", [])
    if br:
        parts = [f"{b['test']}: F = {fmt(b.get('F'), 2)}, {p_eq(b.get('p_value'))}"
                 + (f", максимум в {b['break_at']}" if isinstance(b.get("break_at"), str) else "")
                 for b in br if b.get("F") is not None and not pd.isna(b.get("F"))]
        if parts:
            sent.append("Тесты на структурный сдвиг (structural_break.csv): " + "; ".join(parts) + ".")
    diag = [d for d in res.get("h1_diag", []) if d.get("reject_h0_5pct") is True]
    sent.append("Диагностика остатков H1 (h1_diagnostics.csv): нулевая гипотеза отвергается на 5% в тестах: "
                + (", ".join(d["test"] for d in diag) if diag else "ни в одном") + ".")
    out.append("\n## Главные результаты\n\n" + " ".join(sent[:8]) + "\n")

    out += ["\n## Основная регрессия (H1)\n\n", _md("h1_main"),
            "\n## Проверки устойчивости\n\n", _md("robustness"),
            "\n## Структурный сдвиг\n\n", _md("structural_break"),
            "\n## Альтернативные спецификации\n\n", _alt_text(res),
            "\n## Статус шагов\n\n", to_markdown(status)]
    if fig_status.get("skipped"):
        out.append("\nНе построены графики: " + "; ".join(f"{f} ({w})" for f, w in fig_status["skipped"]) + ".\n")
    out.append(_limitations(res))
    return "".join(out)


def _alt_text(res: dict) -> str:
    lines = []
    a = res.get("ardl") or {}
    if a:
        lines.append(f"- ARDL({a['p']}, {a['q']}) по BIC (ardl_h1.csv, ardl_selection.csv): N = {a['n']}, "
                     f"{a['period']}; долгосрочный эффект спреда {_ci(a['lr'])} "
                     "(строка LR_spread_dep, дельта-метод).")
    d = res.get("diff", {})
    ns = d.get("nonstationary_in_levels", [])
    lines.append("- По ADF/KPSS (stationarity.csv) нестационарными или неоднозначными в уровнях признаны: "
                 + (", ".join(ns) if ns else "ни Y, ни spread_dep") + ".")
    if "dspread" in d:
        r = d["dspread"]
        lines.append(f"- Спецификация в разностях (h1_differences.csv): коэффициент при Δспреда "
                     f"{fmt(r['coef'], 2)} [95% ДИ {fmt(r['lo'], 2)}; {fmt(r['hi'], 2)}], {p_eq(r['p'])}, N = {r['n']}.")
    if "eg" in d:
        e = d["eg"]
        verdict = "отвергается" if e["p"] < 0.05 else "не отвергается"
        lines.append(f"- Тест Энгла–Грейнджера (cointegration.csv): статистика {fmt(e['stat'], 2)}, "
                     f"{p_eq(e['p'])}, N = {e['n']}; гипотеза об отсутствии коинтеграции {verdict} на 5%."
                     + ("" if ns else " Тест справочный: в уровнях ряды не признаны нестационарными."))
    if res.get("vif_max") is not None:
        lines.append(f"- Максимальный VIF регрессоров H1: {fmt(res['vif_max'], 1)} (vif_h1.csv).")
    return "\n".join(lines) + "\n"


def _limitations(res: dict) -> str:
    n = res.get("h1", {}).get("n")
    items = [
        f"Короткая выборка ({n if n else 'около 44'} месячных наблюдений). Мощность тестов "
        "низкая, оценки чувствительны к отдельным месяцам; смотреть нужно на доверительные "
        "интервалы, а не только на p-значения.",
        "Стандартные ошибки Ньюи–Уэста при N < 50 могут быть смещены вниз; "
        "асимптотические p-значения приблизительны.",
        "ADF и KPSS на 40–45 наблюдениях слабо различают единичный корень и высокую "
        "персистентность, поэтому выводы о стационарности предварительные.",
        "Лаг спреда выбирался по информационному критерию на тех же данных, на которых "
        "оценивается модель; p-значения не скорректированы на этот выбор.",
        "Дамми пиковых месяцев поглощают ровно одно наблюдение каждая. Месяцы пиков "
        "фактически исключаются из оценки остальных коэффициентов (ср. вариант «Без дамми пиков»).",
        "Тест Чоу в июне 2026 г.: после точки 4 наблюдения (июнь–сентябрь), поэтому "
        "проверка полного сдвига возможна только в прогнозном варианте, а частичный сдвиг "
        "оценивается по малому числу точек.",
        "Потоки Мосбиржи и Банка России различаются по охвату и методике (см. data_sources.md, "
        "раздел 6); результаты относятся к выборке фондов Мосбиржи.",
        "Если часть потоков оценена по ΔСЧА, ошибка в r (доходность фондов, комиссии) "
        "переходит в зависимую переменную.",
        "Регрессии описывают условные корреляции и не идентифицируют причинный эффект: "
        "спред и приток могут зависеть от общих шоков (решения по ключевой ставке).",
    ]
    if res.get("h2") and not res["h2"].get("with_bond", True):
        items.append("H2 оценена без потока в облигационные фонды: нет помесячного ряда.")
    return "\n## Ограничения\n\n" + "\n".join(f"- {i}" for i in items) + "\n"


def _kind(line: str) -> str:
    if not line.strip():
        return ""
    if line.startswith("#"):
        return "h"
    if line.startswith("|"):
        return "t"
    if line.startswith("- ") or re.match(r"^\d+\. ", line) or line.startswith("   "):
        return "l"
    return "p"


def tidy_md(text: str) -> str:
    """Пустая строка между блоками разного вида (заголовок, таблица, список, абзац)."""
    out: list[str] = []
    for line in text.splitlines():
        k, pk = _kind(line), _kind(out[-1]) if out else ""
        if k and pk and (k != pk or k == "h"):
            out.append("")
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def run() -> None:
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    (config.OUTPUTS / "data_sources.md").write_text(tidy_md(data_sources()), encoding="utf-8")
    (config.OUTPUTS / "results_summary.md").write_text(tidy_md(results_summary()), encoding="utf-8")
    print("  [    ok] outputs/data_sources.md, outputs/results_summary.md")


if __name__ == "__main__":
    run()
