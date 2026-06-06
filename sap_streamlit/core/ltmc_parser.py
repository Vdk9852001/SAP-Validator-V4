"""
SAP LTMC / SpreadsheetML XML Parser  —  V3
==========================================
Handles SAP S/4HANA Migration Cockpit XML exports for ANY object.

LTMC Worksheet Structure — two variants exist:

  VARIANT A (hidden rows — e.g. Product S/4HANA Cloud):
    Row 1  visible  : "Source Data for Migration Object: Product"
    Row 2  visible  : "Version SAP S/4HANA CLOUD 2602..."
    Row 3  visible  : blank spacer
    Row 4  HIDDEN   : SAP table name  e.g. "S_MARA"
    Row 5  HIDDEN   : SAP field names  PRODUCT, MTART, MAKTL...   ← HEADER
    Row 6  HIDDEN   : field type specs  ETE;80;0;C;80;0
    Row 7  visible  : group labels  "Key", "Header Data"...
    Row 8  visible  : verbose descriptions with newlines
    Row 9+ visible  : DATA ROWS

  VARIANT B (visible rows — e.g. Work Center S/4HANA Standard):
    Row 1  visible  : "Source Data for Migration Object: Work center"
    Row 2  visible  : "Version SAP S/4HANA CLOUD 2508..."
    Row 3  visible  : blank spacer (sometimes blank row with many cells)
    Row 4  VISIBLE  : SAP table name  e.g. "S_WORK_CNTR_HDR"
    Row 5  VISIBLE  : SAP field names  ARBPL, WERKS, VERWE...      ← HEADER
    Row 6  VISIBLE  : field type specs  ETE;80;0;C;80;0
    Row 7  visible  : group labels  "Key", "General Data"...
    Row 8  visible  : verbose descriptions with newlines
    Row 9+ visible  : DATA ROWS

The parser detects WHICH variant by scanning ALL rows (not just hidden)
for the SAP field name pattern, then extracts data rows regardless.
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
import xml.etree.ElementTree as ET


SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"


def _tag(local: str) -> str:
    return f"{{{SS_NS}}}{local}"


def _cell_value(cell_el: ET.Element) -> str:
    data = cell_el.find(_tag("Data"))
    if data is not None and data.text:
        return data.text.strip()
    return ""


def _row_values(row_el: ET.Element) -> List[str]:
    """Extract cell values respecting ss:Index gaps (sparse rows)."""
    vals: List[str] = []
    for cell in row_el.findall(_tag("Cell")):
        idx_attr = cell.get(_tag("Index"))
        if idx_attr:
            target = int(idx_attr) - 1
            while len(vals) < target:
                vals.append("")
        vals.append(_cell_value(cell))
    return vals


def _is_hidden(row_el: ET.Element) -> bool:
    return row_el.get(_tag("Hidden"), "0") == "1"


def _is_blank_row(vals: List[str]) -> bool:
    return not any(v.strip() for v in vals)


def _is_field_spec_row(vals: List[str]) -> bool:
    """Detect type spec rows like 'ETE;80;0;C;80;0'."""
    specs = sum(1 for v in vals if v and re.match(r"^E[A-Z]{2};\d+;\d+;[A-Z];\d+;\d+$", v))
    non_empty = sum(1 for v in vals if v)
    return non_empty > 0 and specs / non_empty >= 0.7


def _is_sap_fieldname_row(vals: List[str]) -> Tuple[bool, float]:
    """
    Check if a row looks like a SAP field name row.
    SAP field codes: uppercase letters + digits + underscore, 1-30 chars,
    must start with a letter.
    Returns (is_match, confidence_ratio).
    """
    non_empty = [v for v in vals if v.strip()]
    if len(non_empty) < 2:
        return False, 0.0
    sap_count = sum(
        1 for v in non_empty
        if re.match(r"^[A-Z][A-Z0-9_]{0,29}$", v.strip())
    )
    ratio = sap_count / len(non_empty)
    return ratio >= 0.80, ratio


def _is_table_name_row(vals: List[str]) -> bool:
    """Detect SAP table name row like 'S_MARA', 'S_WORK_CNTR_HDR'."""
    non_empty = [v.strip() for v in vals if v.strip()]
    if not non_empty:
        return False
    return bool(re.match(r"^S_[A-Z0-9_]+$", non_empty[0]))


def _is_description_row(vals: List[str]) -> bool:
    """Detect verbose description rows (long text or newlines)."""
    non_empty = [v for v in vals if v.strip()]
    if not non_empty:
        return False
    long_count = sum(1 for v in non_empty if len(v) > 60 or "\n" in v)
    return long_count >= max(1, len(non_empty) // 2)


def _is_group_label_row(vals: List[str]) -> bool:
    """
    Detect group label rows like ['Key', 'General Data', 'MRP Data'].
    These are short strings, no SAP codes, no specs.
    """
    non_empty = [v.strip() for v in vals if v.strip()]
    if not non_empty:
        return False
    # Short human-readable phrases, not SAP codes or specs
    readable = sum(
        1 for v in non_empty
        if len(v) <= 50 and not re.match(r"^[A-Z][A-Z0-9_]{2,}$", v)
        and not re.match(r"^E[A-Z]{2};", v)
    )
    return readable >= max(1, len(non_empty) // 2)


# Sheets that are documentation only — always skip
_SKIP_SHEETS = {"introduction", "field list"}


def parse_ltmc_xml(file_path: str) -> Dict[str, pd.DataFrame]:
    """
    Parse any SAP LTMC SpreadsheetML XML file.

    Handles both Variant A (hidden field-name rows) and
    Variant B (visible field-name rows) automatically.

    Returns
    -------
    dict {worksheet_name: DataFrame}
      - Columns = SAP technical field names from the field-name row
      - Data    = actual migration data rows
      - df.attrs["table_name"] = e.g. "S_MARA"
      - df.attrs["sheet_name"] = e.g. "Basic Data"
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Not found: {file_path}")

    content = path.read_bytes().lstrip(b"\xef\xbb\xbf")

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"Cannot parse XML: {e}")

    worksheets = root.findall(f".//{_tag('Worksheet')}")
    if not worksheets:
        raise ValueError("No worksheets found in XML.")

    sheets: Dict[str, pd.DataFrame] = {}

    for ws in worksheets:
        name = ws.get(_tag("Name"), f"Sheet{len(sheets)+1}").strip()
        if name.lower() in _SKIP_SHEETS:
            continue
        table = ws.find(_tag("Table"))
        if table is None:
            continue
        rows = table.findall(_tag("Row"))
        if not rows:
            continue

        df, meta = _parse_data_sheet(name, rows)
        if df is not None and not df.empty:
            df.attrs.update(meta)
            sheets[name] = df

    if not sheets:
        raise ValueError("No data found in any worksheet.")

    return sheets


def _parse_data_sheet(
    sheet_name: str,
    rows: List[ET.Element],
) -> Tuple[Optional[pd.DataFrame], dict]:
    """
    Parse a single data worksheet.

    Strategy:
    1. Scan ALL rows (hidden or not) for the SAP field-name row.
       It is the row with the highest proportion of SAP field codes.
    2. The row immediately before it contains the SAP table name.
    3. The row immediately after it is the field-spec row — skip.
    4. Skip the next visible group-label row and description row.
    5. All remaining non-blank rows are data.
    """
    meta = {
        "table_name": "",
        "sheet_name": sheet_name,
        "field_count": 0,
    }

    # ── Step 1: Find the best candidate for the field-name row ───────────
    best_idx   = None
    best_ratio = 0.0

    for i, row in enumerate(rows):
        vals = _row_values(row)
        is_match, ratio = _is_sap_fieldname_row(vals)
        if is_match and ratio > best_ratio:
            best_ratio = ratio
            best_idx   = i

    if best_idx is None:
        return None, meta

    # ── Step 2: Capture table name from the row before the header ────────
    if best_idx > 0:
        prev_vals = _row_values(rows[best_idx - 1])
        if _is_table_name_row(prev_vals):
            meta["table_name"] = prev_vals[0].strip()

    # ── Step 3: Extract column headers ───────────────────────────────────
    header_vals = _row_values(rows[best_idx])
    # Trim trailing empty headers
    while header_vals and not header_vals[-1].strip():
        header_vals.pop()
    headers = [v.strip().upper() for v in header_vals]
    meta["field_count"] = len(headers)

    if not headers:
        return None, meta

    # ── Step 4: Collect data rows ─────────────────────────────────────────
    # Rows after the header fall into these categories (in order):
    #   - field spec row  (ETE;80;0;C;80;0...)  → skip 1 row
    #   - group label row ("Key", "Header Data") → skip 1 row
    #   - description row (verbose text with \n) → skip 1 row
    #   - blank rows                             → skip always
    #   - DATA rows                              → capture
    #
    # We use a state machine: skip until we see a "real data" row.

    data_rows: List[List[str]] = []
    skip_budget = 3  # max metadata rows to skip after header

    for i in range(best_idx + 1, len(rows)):
        row  = rows[i]
        vals = _row_values(row)

        if _is_blank_row(vals):
            continue

        if skip_budget > 0:
            # Still in the skip zone — check if this looks like metadata
            if (_is_field_spec_row(vals) or
                    _is_group_label_row(vals) or
                    _is_description_row(vals)):
                skip_budget -= 1
                continue
            # Doesn't look like metadata — start collecting data
            skip_budget = 0

        # Data row
        padded = vals + [""] * max(0, len(headers) - len(vals))
        data_rows.append(padded[:len(headers)])

    if not data_rows:
        return pd.DataFrame(columns=headers), meta

    df = pd.DataFrame(data_rows, columns=headers)
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip().replace("nan", "")

    return df, meta


def get_sheet_summary(sheets: Dict[str, pd.DataFrame]) -> List[dict]:
    """Return UI-friendly summary of parsed sheets."""
    return [
        {
            "sheet_name": name,
            "table_name": df.attrs.get("table_name", ""),
            "row_count":  len(df),
            "col_count":  len(df.columns),
            "columns":    list(df.columns),
        }
        for name, df in sheets.items()
    ]
