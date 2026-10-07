FUND FLOWS DATA — METHODOLOGY & SOURCES
==========================================

ФАЙЛЫ
-----
1. moex_fund_flows.csv  — помесячный ряд (дек 2022 — сен 2026), 46 строк
   Колонки: month, category, flow_bln, nav_bln_eom, source_title, source_url, note
   - flow_bln: нетто-приток (млрд руб). "known" — из ЦБ/Мосбиржи, "estimate" — расчёт
   - nav_bln_eom: СЧА на конец месяца (млрд руб)
   - note: "known" или "estimate"

2. cbr_fund_flows.csv  — поквартальный ряд (Q1 2023 — Q2 2026), 14 строк
   Колонки: period, period_type, category, fund_scope, flow_bln, nav_bln_eop,
            source_title, source_url, note
   - source_url: прямая ссылка на PDF обзора ЦБ
   - flow_bln: нетто-приток по всем БПИФ (ден. рынок — основной драйвер)

3. fund_nav_monthly.csv  — СЧА по 5 фондам помесячно, 211 строк
   Колонки: month, ticker, fund_name, uk, nav_bln_rub, is_estimate, source_detail
   - is_estimate: 0 = из источника, 1 = интерполяция
   - Фонды: LQDT, SBMM, AKMM, TMON, AMNR

МЕТОДОЛОГИЯ
-----------
flow = ΔСЧА − СЧА_prev × (key_rate − avg_TER) / 12

где:
  key_rate — ключевая ставка ЦБ на конец месяца
  avg_TER — средняя комиссия фонда (~0.4% годовых)
  monthly_ret = key_rate / 100 / 12 × 0.95 (после TER)

ИСТОЧНИКИ
---------
СЧА по фондам:
  - investfunds.ru (ежедневные данные, авг–сен 2026) — точные значения
  - alfacapital.ru (ежедневное раскрытие AKMM, июл–сен 2026)
  - t-capital-funds.ru/documents/mutual_funds/TMON/ (PDF-справки, 40+ файлов)
  - first-am.ru (раскрытие УК Первая для SBMM)
  - wealthim.ru (раскрытие ВИМ Инвестиции для LQDT)
  - aton-m.ru (раскрытие Атон Менеджмент для AMNR)

Приток (помесячно):
  - Обзоры ЦБ с разбивкой по месяцам внутри квартала (Q1 2024, Q3 2024, Q4 2024, Q1 2025)
  - Пресс-релизы Мосбиржи (июль 2026)

Приток (поквартально):
  - 14 PDF-обзоров ЦБ (Q1 2023 — Q2 2026)

ССЫЛКИ НА PDF ОБЗОРОВ ЦБ
-----------------------
Q1 2023: https://cbr.ru/Collection/Collection/File/44017/rewiew_pif_aif_23Q1.pdf
Q2 2023: https://cbr.ru/Collection/Collection/File/46283/rewiew_pif_aif_23Q2.pdf
Q3 2023: https://cbr.ru/Collection/Collection/File/46625/rewiew_uk_23Q3.pdf
Q4 2023: https://cbr.ru/Collection/Collection/File/48954/rewiew_uk_23Q4.pdf
Q1 2024: https://cbr.ru/Collection/Collection/File/49208/rewiew_uk_24Q1.pdf
Q2 2024: https://cbr.ru/Collection/Collection/File/50577/rewiew_uk_24Q2.pdf
Q3 2024: https://cbr.ru/Collection/Collection/File/54852/rewiew_uk_24Q3.pdf
Q4 2024: https://cbr.ru/Collection/Collection/File/55183/rewiew_uk_24Q4.pdf
Q1 2025: https://cbr.ru/Collection/Collection/File/55935/rewiew_uk_25Q1.pdf
Q2 2025: https://cbr.ru/Collection/Collection/File/57205/rewiew_uk_25Q2.pdf
Q3 2025: https://cbr.ru/Collection/Collection/File/59449/rewiew_uk_25Q3.pdf
Q4 2025: https://cbr.ru/Collection/Collection/File/59736/rewiew_uk_25Q4.pdf
Q1 2026: https://cbr.ru/Collection/Collection/File/62033/rewiew_uk_26Q1.pdf
Q2 2026: https://cbr.ru/Collection/Collection/File/62329/rewiew_uk_26Q2.pdf

ОГРАНИЧЕНИЯ
----------
1. 2023 год: данные по фондам в основном оценочные (интерполяция).
   Точные значения нужно брать из PDF-справок УК.
2. ЦБ публикует поток по всем БПИФ, а не по ден. рынку отдельно.
   Ден. рынок = 80–95% притока в БПИФ, но точная доля варьируется.
3. Оценка потока использует упрощённую доходность (ключевая ставка − TER).
   Реальная доходность фондов отличается на величину tracking error.
4. 5 фондов покрывают ~83% рынка. Остальные 12 фондов — <200 млрд вместе.
5. Для замены оценок точными данными нужно скачать PDF-справки УК
   (особенно T-Capital, где 40+ файлов уже выложены).
