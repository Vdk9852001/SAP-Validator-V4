"""
AI Field Mapping — uses Claude API (or falls back to fuzzy/dictionary matching).
Sends only column headers and sample values — never full records.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

_DICT_FILE = Path(__file__).parent.parent / "data" / "sap_field_dictionary.json"
_ALIAS_FILE = Path(__file__).parent.parent / "data" / "field_aliases.json"


def _load_dict() -> dict:
    if _DICT_FILE.exists():
        try:
            return json.loads(_DICT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_aliases() -> dict:
    if _ALIAS_FILE.exists():
        try:
            return json.loads(_ALIAS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _fuzzy_score(a: str, b: str) -> float:
    """Simple LCS similarity."""
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    dp = [[0]*(lb+1) for _ in range(la+1)]
    for i in range(1, la+1):
        for j in range(1, lb+1):
            dp[i][j] = dp[i-1][j-1]+1 if a[i-1]==b[j-1] else max(dp[i-1][j], dp[i][j-1])
    return 2.0*dp[la][lb]/(la+lb)


def suggest_mappings_fallback(
    ltmc_cols: List[str],
    postload_cols: List[str],
    sap_object: str = "",
) -> List[dict]:
    """
    Fallback mapping using SAP dictionary + fuzzy matching.
    Returns list of {ltmc_col, suggested_postload_col, confidence, method, explanation}
    """
    sap_dict   = _load_dict()
    aliases    = _load_aliases()
    postload_u = {c.upper(): c for c in postload_cols}
    postload_norms = {
        re.sub(r"[\s_\-/\.\(\)]", "", c.upper()): c
        for c in postload_cols
    }

    # Build alias lookup: ltmc_field -> [possible postload names]
    all_aliases: Dict[str, List[str]] = {}
    for obj_block in aliases.values():
        if isinstance(obj_block, dict):
            for src, targets in obj_block.items():
                all_aliases.setdefault(src.upper(), []).extend(
                    [t.upper() for t in (targets if isinstance(targets, list) else [targets])]
                )

    results = []
    for ltmc_col in ltmc_cols:
        lu = ltmc_col.upper()

        # 1. Exact match
        if lu in postload_u:
            results.append({
                "ltmc_col": ltmc_col,
                "suggested_postload_col": postload_u[lu],
                "confidence": 1.0,
                "method": "exact",
                "explanation": "Exact field name match",
            })
            continue

        # 2. Normalised exact
        ln = re.sub(r"[\s_\-/\.\(\)]", "", lu)
        if ln in postload_norms:
            results.append({
                "ltmc_col": ltmc_col,
                "suggested_postload_col": postload_norms[ln],
                "confidence": 0.95,
                "method": "normalised",
                "explanation": "Match after removing separators",
            })
            continue

        # 3. Alias lookup
        alias_hits = all_aliases.get(lu, [])
        found_alias = None
        for alias in alias_hits:
            if alias in postload_u:
                found_alias = postload_u[alias]
                break
        if found_alias:
            results.append({
                "ltmc_col": ltmc_col,
                "suggested_postload_col": found_alias,
                "confidence": 0.90,
                "method": "alias",
                "explanation": f"Known SAP alias: {lu} ↔ {found_alias}",
            })
            continue

        # 4. Fuzzy match
        ltmc_label = sap_dict.get(lu, "")
        best_col, best_score = None, 0.0
        for pc in postload_cols:
            s1 = _fuzzy_score(lu, pc)
            s2 = _fuzzy_score(ltmc_label, pc) if ltmc_label else 0.0
            pl = sap_dict.get(pc.upper(), "")
            s3 = _fuzzy_score(ltmc_label, pl) if (ltmc_label and pl) else 0.0
            sc = max(s1, s2, s3)
            if sc > best_score:
                best_score, best_col = sc, pc

        if best_col and best_score >= 0.75:
            results.append({
                "ltmc_col": ltmc_col,
                "suggested_postload_col": best_col,
                "confidence": round(best_score, 2),
                "method": "fuzzy",
                "explanation": f"Fuzzy similarity {best_score:.0%}",
            })
        else:
            results.append({
                "ltmc_col": ltmc_col,
                "suggested_postload_col": None,
                "confidence": 0.0,
                "method": "unmatched",
                "explanation": "No match found",
            })

    return results


def suggest_mappings_ai(
    ltmc_cols: List[str],
    postload_cols: List[str],
    sap_object: str = "",
    sample_values: Dict[str, list] = None,
) -> List[dict]:
    """
    Use Claude API to suggest field mappings.
    Only sends column names + SAP labels — never full records.
    Falls back to rule-based if API unavailable.
    """
    sap_dict = _load_dict()

    # Build context: ltmc col -> label (if known)
    ltmc_with_labels = []
    for col in ltmc_cols:
        label = sap_dict.get(col.upper(), "")
        if label:
            ltmc_with_labels.append(f"{col} ({label})")
        else:
            ltmc_with_labels.append(col)

    # Sample values (max 3 per field, anonymised)
    sample_ctx = ""
    if sample_values:
        lines = []
        for col, vals in list(sample_values.items())[:15]:
            lines.append(f"  {col}: {vals[:3]}")
        sample_ctx = "\nSample values (first 3 per field):\n" + "\n".join(lines)

    prompt = f"""You are an SAP data migration expert. Map LTMC source fields to post-load extract fields.

SAP Object: {sap_object}

LTMC (source) fields with labels:
{json.dumps(ltmc_with_labels, indent=2)}

Post-load extract fields:
{json.dumps(postload_cols, indent=2)}
{sample_ctx}

For each LTMC field find the best matching post-load field.
Use your knowledge of SAP field naming conventions.
Examples: NAME1=NAMORG1, LAND1=COUNTRY, ORT01=CITY1, KUNNR=CUSTOMER, MATNR=MATERIAL.

Respond ONLY with valid JSON:
{{
  "mappings": [
    {{
      "ltmc_col": "FIELD_NAME",
      "suggested_postload_col": "POSTLOAD_FIELD or null",
      "confidence": 0.0-1.0,
      "method": "ai",
      "explanation": "one sentence reason"
    }}
  ]
}}"""

    try:
        import urllib.request
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        raw = "".join(b.get("text","") for b in data.get("content",[]) if b.get("type")=="text")
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            postload_u = {c.upper(): c for c in postload_cols}
            result = []
            for mapping in parsed.get("mappings", []):
                pc = mapping.get("suggested_postload_col")
                # Validate suggestion exists in postload
                if pc and pc.upper() in postload_u:
                    mapping["suggested_postload_col"] = postload_u[pc.upper()]
                elif pc and pc not in postload_cols:
                    mapping["suggested_postload_col"] = None
                    mapping["confidence"] = 0.0
                    mapping["method"] = "ai_unverified"
                result.append(mapping)
            return result
    except Exception:
        pass

    # Fallback
    return suggest_mappings_fallback(ltmc_cols, postload_cols, sap_object)
