"""
SAP Field Mapper — V4
======================
Maps source column names to target column names across different SAP naming
conventions (SAP 4.7 → S/4HANA Business Partner, etc.).

Priority order:
  1. Exact match           MATNR  -> MATNR
  2. Object-specific alias NAME1  -> NAMORG1  (from field_aliases.json CUSTOMER block)
  3. Global alias          LAND1  -> COUNTRY  (from GLOBAL block)
  4. Fuzzy match           similarity >= 0.90 auto-mapped, 0.70-0.90 suggested only

To add new mappings: edit config/field_aliases.json — zero code changes needed.
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

FUZZY_AUTO_THRESHOLD    = 0.90
FUZZY_SUGGEST_THRESHOLD = 0.70

_ALIASES_FILE = Path(__file__).parent.parent / "config" / "field_aliases.json"


@dataclass
class MappedField:
    source_field:  str
    target_field:  str
    method:        str    # exact | alias_object | alias_global | fuzzy
    confidence:    float
    source_label:  str = ""
    target_label:  str = ""


@dataclass
class MappingResult:
    mapped_fields:        Dict[str, str]
    mapped_details:       List[MappedField]
    unmapped_source:      List[str]
    unmapped_target:      List[str]
    suggested_mappings:   List[MappedField]
    object_type:          str = ""
    total_source_fields:  int = 0
    total_target_fields:  int = 0


def _load_aliases() -> dict:
    if _ALIASES_FILE.exists():
        try:
            return json.loads(_ALIASES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: could not load field_aliases.json: {e}")
    return {}


def _normalize(name: str) -> str:
    return re.sub(r"[_\-\s]", "", name.upper().strip())


def _similarity(a: str, b: str) -> float:
    """LCS-based similarity score."""
    a, b = _normalize(a), _normalize(b)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            dp[i][j] = dp[i-1][j-1] + 1 if a[i-1] == b[j-1] else max(dp[i-1][j], dp[i][j-1])
    return (2.0 * dp[la][lb]) / (la + lb)


def _best_fuzzy_match(src: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    best, conf = None, 0.0
    for c in candidates:
        s = _similarity(src, c)
        if s > conf:
            conf, best = s, c
    return best, conf


def build_field_mapping(
    source_cols:     List[str],
    target_cols:     List[str],
    object_type:     str = "",
    selected_fields: List[str] = None,
    fuzzy_threshold: float = FUZZY_AUTO_THRESHOLD,
    custom_labels:   dict = None,
) -> MappingResult:
    """
    Build a complete field mapping between source and target columns.

    Parameters
    ----------
    source_cols     : column names from source file
    target_cols     : column names from target file
    object_type     : SAP object e.g. CUSTOMER, VENDOR — picks alias block
    selected_fields : if set, only map these source fields
    fuzzy_threshold : min similarity for auto-mapping (default 0.90)
    custom_labels   : {FIELD: label} dict for display enrichment

    Returns
    -------
    MappingResult
    """
    from core.field_labels import get_label

    aliases        = _load_aliases()
    custom_labels  = custom_labels or {}

    src_upper = [s.upper() for s in source_cols]
    tgt_upper = [t.upper() for t in target_cols]
    tgt_set   = set(tgt_upper)

    if selected_fields:
        sel_upper = [s.upper() for s in selected_fields]
        src_upper = [s for s in src_upper if s in sel_upper]

    # Resolve alias blocks
    obj_key = object_type.upper() if object_type else ""
    obj_aliases = aliases.get(obj_key, {})
    if not obj_aliases and obj_key:
        for key in aliases:
            if key != "GLOBAL" and (key in obj_key or obj_key in key):
                obj_aliases = aliases[key]
                break
    global_aliases = aliases.get("GLOBAL", {})

    mapped_details     = []
    mapped_fields      = {}
    used_targets       = set()
    unmapped_source    = []
    suggested_mappings = []

    for src in src_upper:
        if src in mapped_fields:
            continue
        src_lbl = get_label(src, custom_labels)

        # Priority 1: Exact match
        if src in tgt_set and src not in used_targets:
            mapped_fields[src] = src
            used_targets.add(src)
            mapped_details.append(MappedField(
                source_field=src, target_field=src, method="exact", confidence=1.0,
                source_label=src_lbl, target_label=get_label(src, custom_labels),
            ))
            continue

        # Priority 2: Object-specific alias
        found = False
        for alias_tgt in obj_aliases.get(src, []):
            aup = alias_tgt.upper()
            if aup in tgt_set and aup not in used_targets:
                mapped_fields[src] = aup
                used_targets.add(aup)
                mapped_details.append(MappedField(
                    source_field=src, target_field=aup,
                    method="alias_object", confidence=1.0,
                    source_label=src_lbl, target_label=get_label(aup, custom_labels),
                ))
                found = True
                break
        if found:
            continue

        # Priority 3: Global alias
        for alias_tgt in global_aliases.get(src, []):
            aup = alias_tgt.upper()
            if aup in tgt_set and aup not in used_targets:
                mapped_fields[src] = aup
                used_targets.add(aup)
                mapped_details.append(MappedField(
                    source_field=src, target_field=aup,
                    method="alias_global", confidence=1.0,
                    source_label=src_lbl, target_label=get_label(aup, custom_labels),
                ))
                found = True
                break
        if found:
            continue

        # Priority 4: Fuzzy match
        remaining = [t for t in tgt_upper if t not in used_targets]
        best_tgt, conf = _best_fuzzy_match(src, remaining)

        if best_tgt and conf >= fuzzy_threshold:
            mapped_fields[src] = best_tgt
            used_targets.add(best_tgt)
            mapped_details.append(MappedField(
                source_field=src, target_field=best_tgt,
                method="fuzzy", confidence=round(conf, 3),
                source_label=src_lbl, target_label=get_label(best_tgt, custom_labels),
            ))
        elif best_tgt and conf >= FUZZY_SUGGEST_THRESHOLD:
            suggested_mappings.append(MappedField(
                source_field=src, target_field=best_tgt,
                method="fuzzy_suggested", confidence=round(conf, 3),
                source_label=src_lbl, target_label=get_label(best_tgt, custom_labels),
            ))
            unmapped_source.append(src)
        else:
            unmapped_source.append(src)

    unmapped_target = [t for t in tgt_upper if t not in used_targets]

    return MappingResult(
        mapped_fields=mapped_fields,
        mapped_details=mapped_details,
        unmapped_source=unmapped_source,
        unmapped_target=unmapped_target,
        suggested_mappings=suggested_mappings,
        object_type=obj_key,
        total_source_fields=len(src_upper),
        total_target_fields=len(tgt_upper),
    )


def mapping_result_to_dict(mr: MappingResult) -> dict:
    """Serialise MappingResult to plain dict for JSON storage."""
    return {
        "object_type":         mr.object_type,
        "total_source_fields": mr.total_source_fields,
        "total_target_fields": mr.total_target_fields,
        "mapped_count":        len(mr.mapped_fields),
        "mapped_fields": [
            {
                "source_field":  d.source_field,
                "target_field":  d.target_field,
                "method":        d.method,
                "confidence":    d.confidence,
                "source_label":  d.source_label,
                "target_label":  d.target_label,
            }
            for d in mr.mapped_details
        ],
        "unmapped_source":  mr.unmapped_source,
        "unmapped_target":  mr.unmapped_target,
        "suggested_mappings": [
            {
                "source_field": s.source_field,
                "target_field": s.target_field,
                "confidence":   s.confidence,
                "source_label": s.source_label,
                "target_label": s.target_label,
            }
            for s in mr.suggested_mappings
        ],
    }
