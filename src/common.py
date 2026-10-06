"""HTTP-сессия, журнал загрузок и работа с сырыми файлами."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

FETCH_LOG = config.RAW / "fetch_log.csv"
LOG_FIELDS = ["run_ts", "series", "status", "rows", "file", "url", "error"]


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def reachable(url: str) -> tuple[bool, str]:
    """Быстрая проверка доступности источника (без повторов)."""
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": config.USER_AGENT})
        return (r.status_code < 400, f"HTTP {r.status_code}")
    except requests.RequestException as e:
        return False, repr(e)


def today_tag() -> str:
    return dt.date.today().strftime("%Y%m%d")


def raw_path(series: str, ext: str = "csv") -> Path:
    """Имя сырого файла содержит дату загрузки: data/raw/<series>_<YYYYMMDD>.<ext>."""
    config.RAW.mkdir(parents=True, exist_ok=True)
    return config.RAW / f"{series}_{today_tag()}.{ext}"


def latest_raw(series: str, ext: str = "csv") -> Path | None:
    """Последний по дате загрузки сырой файл ряда (или None)."""
    files = sorted(config.RAW.glob(f"{series}_[0-9]*.{ext}"))
    return files[-1] if files else None


def raw_download_date(path: Path | None) -> str | None:
    if path is None:
        return None
    tag = path.stem.rsplit("_", 1)[-1]
    try:
        return dt.datetime.strptime(tag, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def log_fetch(series: str, status: str, url: str = "", rows: int = 0,
              file: Path | None = None, error: str = "") -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    new = not FETCH_LOG.exists()
    with FETCH_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "run_ts": dt.datetime.now().isoformat(timespec="seconds"),
            "series": series, "status": status, "rows": rows,
            "file": file.relative_to(config.WORK).as_posix() if file else "",
            "url": url, "error": error[:300].replace("\n", " "),
        })
    print(f"  [{status:>6}] {series}: rows={rows} {('ERR: ' + error[:120]) if error else ''}")


def read_fetch_log() -> pd.DataFrame:
    if not FETCH_LOG.exists():
        return pd.DataFrame(columns=LOG_FIELDS)
    return pd.read_csv(FETCH_LOG, dtype=str).fillna("")


def save_raw(df: pd.DataFrame, series: str, url: str) -> Path:
    path = raw_path(series)
    df.to_csv(path, index=False)
    log_fetch(series, "ok", url=url, rows=len(df), file=path)
    return path
