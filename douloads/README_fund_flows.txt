Fund Flows Data — Sources & Methodology
==========================================

FILES
-----
1. moex_fund_flows.csv — Monthly aggregate money market fund flows (Jan 2023 – Oct 2026)
   - flow_bln: Estimated net flow = delta СЧА - СЧА_prev * r_month (r = (key_rate - 0.4%)/12)
   - flow_bln_known: Direct flow from ЦБ monthly breakdown or MOEX press release (where available)
   - nav_bln_eom: Aggregate СЧА of 6 largest funds (LQDT, SBMM, AKMM, TMON, AMNR, BCSD)
   - note: "interpolated" = NAV was linearly interpolated between known data points

2. cbr_fund_flows.csv — Quarterly BPIF flows from ЦБ reviews (Q1 2023 – Q2 2026)
   - flow_bln: Net inflow to all BPIF (money market = ~80-100% driver)
   - nav_bln_eop: СЧА of all BPIF at quarter end (where disclosed)
   - source_url: Direct PDF link to ЦБ review

3. fund_nav_monthly.csv — Per-fund monthly NAV (6 funds, 150 rows)
   - is_estimate: True = interpolated, False = direct from УК/investfunds.ru

4. fund_nav_raw_points.csv — 89 raw NAV data points with source attribution

DATA SOURCES
------------
- investfunds.ru (fund pages: /funds/5973/, /funds/8181/, /funds/8628/, /funds/7373/, /funds/10053/, /funds/10831/)
- alfacapital.ru/disclosure/pifs/bpif-akmm/cost (daily AKMM NAV, Jul-Sep 2026)
- first-am.ru/individuals/etf/etf-sbmm (SBMM current NAV)
- t-capital-funds.ru/documents/mutual_funds/TMON/ (TMON monthly СЧА report list)
- cbonds.ru (LQDT, TMON, BCSD current NAV)
- MOEX press releases (moex.com/ru/moneyfunds)
- ЦБ РФ reviews (cbr.ru/analytics/RSCI/review_uk/)
- RBC, Tbank, Alfa Investor, Fondium, rostsber.ru articles

COVERAGE
--------
LQDT:  10 direct points, Jan 2023 – Oct 2026 (launched Jan 2020)
SBMM:  6 direct points, Jan 2025 – Oct 2026 (launched Oct 2021)
AKMM: 67 direct points, Jan 2025 – Oct 2026 (launched Jul 2022)
TMON:  5 direct points, Jan 2025 – Sep 2026 (launched Jun 2023)
AMNR:  3 direct points, Jan 2025 – Sep 2026 (launched Jan 2024)
BCSD:  2 direct points, Jul 2025 – Oct 2026 (launched Jul 2024)

6 funds cover ~83% of total money market СЧА (~1.93 trln of ~2.3 trln as of Sep 2026)

KEY LIMITATIONS
---------------
1. 2023 coverage is sparse — only LQDT has data points. SBMM/AKMM/TMON data starts Jan 2025.
2. Interpolated values are linear between known points and may not capture monthly volatility.
3. ЦБ quarterly flows are for ALL BPIF, not just money market.
4. Flow estimates use approximate key rate, not actual RUSFAR/RUONIA daily rates.

NEXT STEPS TO IMPROVE DATA
-------------------------
1. Download monthly СЧА PDFs from t-capital-funds.ru for TMON (40+ monthly reports listed)
2. Scrape daily disclosure from wealthim.ru for LQDT
3. Scrape first-am.ru disclosure section for SBMM
4. Use MOEX ISS API (iss.moex.com) for daily price × issue_size to calculate NAV
5. Download all 14 ЦБ PDF reviews for quarterly cross-check
