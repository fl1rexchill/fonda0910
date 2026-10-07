#!/usr/bin/env python3
"""
Автоматическое скачивание PDF-справок о СЧА фондов денежного рынка.
Оставлены только SBMM (УК «Первая») и AKMM (Альфа-Капитал).
LQDT и TMON убраны — пользователь скачал их вручную.
"""

import os
import time
import re
import requests
from bs4 import BeautifulSoup

BASE_DIR = "scha_pdfs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
DELAY = 0.4  # пауза между запросами


def ensure_dir(ticker):
    d = os.path.join(BASE_DIR, ticker)
    os.makedirs(d, exist_ok=True)
    return d


def download_pdf(url, filepath):
    """Скачивает PDF, если файла ещё нет."""
    if os.path.exists(filepath):
        print(f"  [SKIP] уже скачан: {os.path.basename(filepath)}")
        return True
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if r.status_code != 200:
            print(f"  [ERR {r.status_code}] {url}")
            return False
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size_kb = os.path.getsize(filepath) / 1024
        print(f"  [OK] {os.path.basename(filepath)} ({size_kb:.0f} KB)")
        return True
    except Exception as e:
        print(f"  [ERR] {url}: {e}")
        return False


def get_pdf_links(url, filter_regex=None):
    """Парсит страницу и возвращает список (href, text) для PDF-ссылок."""
    time.sleep(DELAY)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  [ERR] не удалось открыть страницу {url}: {e}")
        return []

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if not href.lower().endswith(".pdf"):
            continue
        # делаем абсолютный URL
        if href.startswith("/"):
            from urllib.parse import urlparse
            base = urlparse(url)
            href = f"{base.scheme}://{base.netloc}{href}"
        elif not href.startswith("http"):
            # относительный путь без ведущего /
            from urllib.parse import urljoin
            href = urljoin(url, href)

        if filter_regex and not re.search(filter_regex, href + " " + text, re.I):
            continue
        links.append((href, text))
    return links


# ────────────────────────────────────────────
# SBMM — УК «Первая»
# ────────────────────────────────────────────
def download_sbmm():
    """
    УК «Первая», фонд SBMM (Сберегательный).
    Страница раскрытия: first-am.ru/individuals/etf/etf-sbmm/documents
    Возможно есть пагинация (/?PAGEN_1=2 и т.д.).
    """
    ticker = "SBMM"
    print(f"\n=== {ticker} (УК «Первая») ===")
    out_dir = ensure_dir(ticker)

    pages = [
        "https://first-am.ru/individuals/etf/etf-sbmm/documents",
        "https://first-am.ru/individuals/etf/etf-sbmm/documents/?PAGEN_1=2",
        "https://first-am.ru/individuals/etf/etf-sbmm/documents/?PAGEN_1=3",
    ]

    all_links = []
    for page_url in pages:
        print(f"  Парсинг страницы: {page_url}")
        links = get_pdf_links(page_url, filter_regex=r"(сча|справк|cost|net|asset|scha|sbmm)")
        if links:
            all_links.extend(links)
            print(f"    Найдено {len(links)} PDF-ссылок")
        else:
            print(f"    PDF не найдены (возможно, конец списка или нет пагинации)")

    # если ничего не нашли с фильтром — пробуем без фильтра
    if not all_links:
        print("  Фильтр не сработал, пробую все PDF без фильтра...")
        for page_url in pages:
            links = get_pdf_links(page_url)
            all_links.extend(links)
            print(f"    Найдено {len(links)} PDF-ссылок (без фильтра)")

    # убираем дубликаты
    seen = set()
    unique = []
    for href, text in all_links:
        if href not in seen:
            seen.add(href)
            unique.append((href, text))

    print(f"  Всего уникальных PDF: {len(unique)}")
    ok = 0
    for href, text in unique:
        # имя файла из URL
        fname = href.split("/")[-1].split("?")[0]
        if not fname.lower().endswith(".pdf"):
            fname = f"SBMM_{fname}.pdf"
        else:
            # добавим префикс тикера, если его нет
            if "sbmm" not in fname.lower() and "scha" not in fname.lower():
                fname = f"SBMM_{fname}"
        filepath = os.path.join(out_dir, fname)
        if download_pdf(href, filepath):
            ok += 1
    print(f"  Итого {ticker}: скачано {ok} из {len(unique)} файлов")


# ────────────────────────────────────────────
# AKMM — Альфа-Капитал
# ────────────────────────────────────────────
def download_akmm():
    """
    Альфа-Капитал, фонд AKMM (Денежный рынок).
    Страница раскрытия: alfacapital.ru/disclosure/pifs/bpif-akmm/monthly
    Переключатель по годам: ?year=2022 ... 2026
    """
    ticker = "AKMM"
    print(f"\n=== {ticker} (Альфа-Капитал) ===")
    out_dir = ensure_dir(ticker)

    base_url = "https://alfacapital.ru/disclosure/pifs/bpif-akmm/monthly"
    years = [2022, 2023, 2024, 2025, 2026]

    all_links = []
    for year in years:
        url = f"{base_url}?year={year}"
        print(f"  Парсинг страницы: {url}")
        links = get_pdf_links(url, filter_regex=r"(сча|справк|cost|net|asset|akmm|расчетн)")
        if links:
            all_links.extend(links)
            print(f"    Найдено {len(links)} PDF-ссылок за {year} год")
        else:
            print(f"    PDF не найдены за {year} год (возможно, нет справок за этот период)")

    # если ничего не нашли с фильтром — пробуем без фильтра
    if not all_links:
        print("  Фильтр не сработал, пробую все PDF без фильтра...")
        for year in years:
            url = f"{base_url}?year={year}"
            links = get_pdf_links(url)
            all_links.extend(links)
            print(f"    Найдено {len(links)} PDF-ссылок за {year} год (без фильтра)")

    # убираем дубликаты
    seen = set()
    unique = []
    for href, text in all_links:
        if href not in seen:
            seen.add(href)
            unique.append((href, text))

    print(f"  Всего уникальных PDF: {len(unique)}")
    ok = 0
    for href, text in unique:
        fname = href.split("/")[-1].split("?")[0]
        if not fname.lower().endswith(".pdf"):
            fname = f"AKMM_{fname}.pdf"
        else:
            if "akmm" not in fname.lower() and "сч" not in fname.lower():
                fname = f"AKMM_{fname}"
        filepath = os.path.join(out_dir, fname)
        if download_pdf(href, filepath):
            ok += 1
    print(f"  Итого {ticker}: скачано {ok} из {len(unique)} файлов")


# ────────────────────────────────────────────
# Главный запуск
# ────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Скачивание PDF-справок о СЧА фондов денежного рынка")
    print("Остались только SBMM и AKMM (LQDT и TMON уже скачаны)")
    print("=" * 60)

    download_sbmm()
    download_akmm()

    # итоговая сводка
    print("\n" + "=" * 60)
    print("ИТОГО")
    print("=" * 60)
    for ticker in ["SBMM", "AKMM"]:
        d = os.path.join(BASE_DIR, ticker)
        if os.path.isdir(d):
            files = [f for f in os.listdir(d) if f.lower().endswith(".pdf")]
            print(f"  {ticker}: {len(files)} файлов в {d}")
        else:
            print(f"  {ticker}: папка не создана (ничего не скачано)")
    print("\nГотово!")
