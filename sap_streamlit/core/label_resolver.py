"""
Label Resolver — V3 (Universal)
================================
Resolves post-load file column names → SAP technical field codes for
any SAP object, not just Product and Work Center.

The post-load file may use:
  A. Friendly names:          "Work Center", "Plant", "Valid-From Date"
  B. SAP technical names:     ARBPL, WERKS, BEGDA  → already correct
  C. Long compound names:     WORKCENTERFMLAPARAMUNIT1  → pass-through
  D. Mixed:                   some of each

Resolution pipeline (in priority order):
  1. Exact SAP code match against LTMC columns          → exact
  2. Exact label match (case-insensitive)               → label
  3. Normalised label match (strip punctuation/spaces)  → label_norm
  4. Suffix-stripped match ("Valid-From Date" → "Valid From") → label_stripped
  5. Fuzzy label similarity ≥ 0.82                      → fuzzy
  6. Fuzzy SAP code similarity ≥ 0.92                   → fuzzy_code
  7. Pass-through (return as-is)                        → original

Universal design principles:
  - Loads ALL labels from config/field_labels.json at runtime (no cache miss)
  - Merges with built-in SAP_FIELD_LABELS dict as fallback
  - Works for any SAP object because it tries all 645 known labels
  - Numbered fields (LSTAR1..6, PAR01..06) resolved by stripping the number
    suffix and matching the base label, then re-appending the number
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

_LABELS_JSON = Path(__file__).parent.parent / "config" / "field_labels.json"


# ── Label loading ──────────────────────────────────────────────────────────────

def _load_labels(custom_labels: dict = None) -> dict:
    """
    Load the full label dictionary:
    field_labels.json  +  SAP_FIELD_LABELS (Python dict)  +  custom
    JSON wins over Python dict; custom wins over JSON.
    Result is always fresh — no module-level cache so new JSON is picked up.
    """
    merged: dict = {}

    # 1. Built-in Python dict
    try:
        from core.field_labels import SAP_FIELD_LABELS
        merged.update(SAP_FIELD_LABELS)
    except Exception:
        pass

    # 2. JSON file (overrides Python dict)
    if _LABELS_JSON.exists():
        try:
            merged.update(json.loads(_LABELS_JSON.read_text(encoding="utf-8")))
        except Exception:
            pass

    # 3. Custom labels from user upload (highest priority)
    if custom_labels:
        merged.update({k.upper(): v for k, v in custom_labels.items()})

    return merged


def build_reverse_label_map(all_labels: dict) -> Dict[str, str]:
    """
    Build reverse map: normalised_label → SAP_FIELD_CODE.

    For numbered fields like VGM01..06 we also add entries for
    the base label so "Standard Work Quantity Unit" matches VGM01.
    """
    reverse: Dict[str, str] = {}
    for code, label in all_labels.items():
        key = _norm(label)
        if key and key not in reverse:
            reverse[key] = code.upper()

    # Also add self-referencing entries for long technical names
    # so WORKCENTERFMLAPARAMUNIT1 matches itself exactly
    for code in all_labels:
        key = _norm(code)
        if key and key not in reverse:
            reverse[key] = code.upper()

    return reverse


# ── Text normalisation ─────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase, collapse whitespace and common punctuation."""
    s = str(s).lower().strip()
    s = re.sub(r"[_\-/\.\(\)\*:\#]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _similarity(a: str, b: str) -> float:
    """LCS-based string similarity in [0,1]."""
    a, b = _norm(a), _norm(b)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    # Fast path: very different lengths
    if abs(la - lb) / max(la, lb) > 0.6:
        return 0.0
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            dp[i][j] = (dp[i-1][j-1] + 1 if a[i-1] == b[j-1]
                        else max(dp[i-1][j], dp[i][j-1]))
    return (2.0 * dp[la][lb]) / (la + lb)


# ── Core resolver ──────────────────────────────────────────────────────────────

def resolve_column(
    col_name: str,
    ltmc_col_set: set,         # set of SAP codes in the LTMC sheet
    reverse_map: Dict[str, str],
    all_labels: dict,          # full {SAP_CODE: label}
    fuzzy_threshold: float = 0.82,
) -> Tuple[str, str]:
    """
    Resolve one post-load column name to a SAP field code.
    Returns (resolved_code, method).
    """
    col_upper = col_name.strip().upper()
    col_norm  = _norm(col_name)

    # ── 1. Exact SAP code match ────────────────────────────────────────────
    if col_upper in ltmc_col_set:
        return col_upper, "exact"

    # ── 2. Exact normalised label match ───────────────────────────────────
    if col_norm in reverse_map:
        resolved = reverse_map[col_norm]
        if resolved in ltmc_col_set:
            return resolved, "label"

    # ── 3. Strip common suffixes and retry ────────────────────────────────
    # "Setup Type Ref." → "Setup Type"
    # "Valid-From Date" → "Valid From"
    stripped = re.sub(
        r"\s*(ref\.?|reference|indicator|flag|date|id|number|no\.?)\s*$",
        "", col_norm, flags=re.I
    ).strip()
    if stripped and stripped != col_norm and stripped in reverse_map:
        resolved = reverse_map[stripped]
        if resolved in ltmc_col_set:
            return resolved, "label_stripped"
        # Try all codes whose stripped label matches
        for code, label in all_labels.items():
            if _norm(label) == stripped and code.upper() in ltmc_col_set:
                return code.upper(), "label_stripped_alt"

    # ── 4. Handle numbered fields: "Activity Type 3" → LSTAR3 ────────────
    # Extract trailing number, try base match, re-apply number
    num_match = re.search(r"^(.+?)\s*(\d+)\s*$", col_name.strip())
    if num_match:
        base_name = num_match.group(1).strip()
        suffix    = num_match.group(2)
        base_norm = _norm(base_name)
        if base_norm in reverse_map:
            base_code = reverse_map[base_norm]
            # Try adding the number suffix to the base code
            numbered_code = base_code.rstrip("0123456789") + suffix
            numbered_code2 = base_code + suffix
            for candidate in [numbered_code, numbered_code2]:
                if candidate in ltmc_col_set:
                    return candidate, "label_numbered"

    # ── 5. Fuzzy label match (only against LTMC-present codes) ───────────
    best_code: Optional[str] = None
    best_score = 0.0
    for label_norm, code in reverse_map.items():
        if code not in ltmc_col_set:
            continue
        score = _similarity(col_norm, label_norm)
        if score > best_score:
            best_score = score
            best_code  = code
    if best_code and best_score >= fuzzy_threshold:
        return best_code, f"fuzzy({best_score:.0%})"

    # ── 6. Fuzzy SAP code match ────────────────────────────────────────────
    # For long compound names like WRKCTRSTDVALMAINTRULE1
    if len(col_upper) > 6:
        for ltmc_col in ltmc_col_set:
            if _similarity(col_upper, ltmc_col) >= 0.92:
                return ltmc_col, "fuzzy_code"

    return col_upper, "original"


# ── Public API ─────────────────────────────────────────────────────────────────

def resolve_postload_columns(
    postload_columns: List[str],
    ltmc_columns: List[str],
    field_labels: dict = None,
    custom_labels: dict = None,
    fuzzy_threshold: float = 0.82,
) -> Dict[str, dict]:
    """
    Resolve all post-load column names to SAP technical names.

    Parameters
    ----------
    postload_columns : columns from the post-load file
    ltmc_columns     : SAP technical field names from the LTMC sheet
    field_labels     : optional extra labels to merge (usually SAP_FIELD_LABELS)
    custom_labels    : user-uploaded custom label overrides
    fuzzy_threshold  : minimum similarity for fuzzy matching

    Returns
    -------
    dict  {postload_col: {original, resolved, method, matched, ltmc_col}}
    """
    # Load all labels fresh (JSON + built-in + custom + any extras passed in)
    all_labels = _load_labels(custom_labels)
    if field_labels:
        all_labels.update({k.upper(): v for k, v in field_labels.items()})

    ltmc_col_set = set(c.upper() for c in ltmc_columns)
    reverse_map  = build_reverse_label_map(all_labels)

    result = {}
    for col in postload_columns:
        resolved, method = resolve_column(
            col, ltmc_col_set, reverse_map, all_labels, fuzzy_threshold
        )
        matched  = resolved in ltmc_col_set
        ltmc_col = resolved if matched else None
        result[col] = {
            "original": col,
            "resolved": resolved,
            "method":   method,
            "matched":  matched,
            "ltmc_col": ltmc_col,
        }

    return result


def build_field_map_from_resolution(resolution: Dict[str, dict]) -> Dict[str, str]:
    """
    Build {ltmc_col: postload_col} from resolution dict.
    First match per LTMC column wins.
    """
    field_map = {}
    for postload_col, info in resolution.items():
        if info["matched"] and info["ltmc_col"]:
            ltmc_col = info["ltmc_col"]
            if ltmc_col not in field_map:
                field_map[ltmc_col] = postload_col
    return field_map
