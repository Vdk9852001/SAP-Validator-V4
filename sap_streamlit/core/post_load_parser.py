"""
Post-Load Extract Parser
Reads SAP post-load extract files (Excel/CSV) with flexible sheet/header detection.
"""
from __future__ import annotations
import re
import pandas as pd
from pathlib import Path
from typing import Optional, Tuple


def read_postload(
    file_path: str,
    sheet_name: str = None,
    header_row: int = None,
) -> Tuple[pd.DataFrame, str, int]:
    """
    Read a post-load extract file.
    Returns (dataframe, detected_sheet_name, detected_header_row).
    """
    p = str(file_path)
    if p.lower().endswith(".csv"):
        df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", na_filter=False)
        df.columns = [c.strip() for c in df.columns]
        return df, "CSV", 0

    xl = pd.ExcelFile(p)
    sheets = xl.sheet_names

    # Select sheet
    chosen_sheet = sheet_name or _detect_best_sheet(xl, sheets)

    # Read raw to detect header row
    raw = pd.read_excel(p, sheet_name=chosen_sheet, header=None, dtype=str,
                        nrows=20, na_values=[], keep_default_na=False)

    hr = header_row if header_row is not None else _detect_header_row(raw)

    df = pd.read_excel(p, sheet_name=chosen_sheet, header=hr, dtype=str,
                       na_values=[], keep_default_na=False)
    df = df.dropna(how="all").reset_index(drop=True)
    df.columns = [str(c).strip() for c in df.columns]
    # Strip whitespace from all values
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip().fillna("")

    return df, chosen_sheet, hr


def _detect_best_sheet(xl: pd.ExcelFile, sheets: list) -> str:
    """Pick the sheet with the most columns / most data."""
    best_sheet, best_score = sheets[0], 0
    skip_keywords = ["instruction", "readme", "introduction", "field list",
                     "guide", "help", "note", "overview"]
    for s in sheets:
        if any(kw in s.lower() for kw in skip_keywords):
            continue
        try:
            df = pd.read_excel(xl, sheet_name=s, nrows=5, header=None, dtype=str)
            score = df.shape[1] * 2 + df.shape[0]
            if score > best_score:
                best_score, best_sheet = score, s
        except Exception:
            pass
    return best_sheet


def _detect_header_row(raw: pd.DataFrame) -> int:
    """Find the row with the most non-empty, non-numeric string values."""
    best_row, best_score = 0, 0
    for i in range(min(10, len(raw))):
        row = raw.iloc[i].dropna().astype(str)
        non_numeric = sum(1 for v in row if v.strip() and not v.strip().replace(".","").isdigit())
        if non_numeric > best_score:
            best_score, best_row = non_numeric, i
    return best_row


def list_sheets(file_path: str) -> list:
    p = str(file_path)
    if p.lower().endswith(".csv"):
        return ["CSV"]
    return pd.ExcelFile(p).sheet_names


def get_sample_values(df: pd.DataFrame, max_cols: int = 20, max_vals: int = 3) -> dict:
    """Return first N non-empty values per column (for AI context)."""
    result = {}
    for col in list(df.columns)[:max_cols]:
        vals = [v for v in df[col].dropna().astype(str) if v.strip()][:max_vals]
        if vals:
            result[col] = vals
    return result
