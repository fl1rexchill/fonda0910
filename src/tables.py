"""Сохранение таблиц в CSV и Markdown (outputs/tables)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def fmt(v, digits: int = 3) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (bool, np.bool_)):
        return "да" if v else "нет"
    if isinstance(v, (int, np.integer)):
        return str(v)
    if isinstance(v, (float, np.floating)):
        if v != 0 and (abs(v) < 10 ** -digits or abs(v) >= 1e6):
            return f"{v:.2e}"
        return f"{v:.{digits}f}"
    return str(v).replace("|", "/").replace("\n", " ")


def fmt_p(p) -> str:
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return ""
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _is_p(col: str) -> bool:
    return col in ("p_value", "adf_p", "kpss_p") or col.endswith("_p")


def _int_col(s: pd.Series) -> bool:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return s.dtype.kind in "if" and len(v) > 0 and bool((v == v.round()).all()) and bool((v.abs() < 1e6).all())


def to_markdown(df: pd.DataFrame, digits: int = 3) -> str:
    if df.empty:
        return "_(нет строк)_\n"
    cols = list(df.columns)
    ints = {c for c in cols if _int_col(df[c]) and not _is_p(c)}
    lines = ["| " + " | ".join(map(str, cols)) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if _is_p(c) and isinstance(v, (float, np.floating)):
                cells.append(fmt_p(v))
            elif c in ints and pd.notna(v):
                cells.append(str(int(v)))
            else:
                cells.append(fmt(v, digits))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def save_table(df: pd.DataFrame, name: str, title: str, stats: pd.DataFrame | None = None) -> None:
    config.TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.TABLES / f"{name}.csv", index=False)
    md = f"### {title}\n\n" + to_markdown(df)
    if stats is not None:
        stats.to_csv(config.TABLES / f"{name}_fit.csv", index=False)
        md += "\n" + to_markdown(stats)
    (config.TABLES / f"{name}.md").write_text(md, encoding="utf-8")
