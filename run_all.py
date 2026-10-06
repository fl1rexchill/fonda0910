"""Полный прогон: загрузка → сборка таблицы → модели → графики → отчёты.

    python run_all.py              # загрузить данные и посчитать всё
    python run_all.py --skip-fetch # без загрузки, по уже скачанным и ручным файлам
"""
from __future__ import annotations

import argparse
import random
import warnings

import numpy as np

from src import build_dataset, config, fetch_cbr, fetch_moex, models, plots, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-fetch", action="store_true", help="не обращаться к источникам")
    args = ap.parse_args()
    random.seed(config.RANDOM_STATE)
    np.random.seed(config.RANDOM_STATE)
    warnings.filterwarnings("ignore", category=FutureWarning)

    print(f"Период модели: {config.MODEL_START} … {config.last_full_month()}")
    if not args.skip_fetch:
        print("\n1. Загрузка данных")
        fetch_moex.main()
        fetch_cbr.main()
    print("\n2. Сборка помесячной таблицы")
    build_dataset.build()
    print("\n3. Эконометрический анализ")
    models.run()
    print("\n4. Графики")
    plots.run()
    print("\n5. Отчёты")
    report.run()
    print("\nГотово. См. outputs/results_summary.md и outputs/data_sources.md")


if __name__ == "__main__":
    main()
