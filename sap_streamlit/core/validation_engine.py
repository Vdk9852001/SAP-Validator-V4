"""
Validation Engine — compares LTMC source against post-load extract.
Source LTMC = expected. Post-load = actual.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional


_CK = "__CK__"


@dataclass
class FieldValidationResult:
    field_ltmc:         str
    field_postload:     str
    total_matched_keys: int
    matched:            int
    mismatched:         int
    missing_in_postload: int
    missing_in_ltmc:    int
    match_pct:          float
    mismatches:         list = field(default_factory=list)

    @property
    def status(self):
        if self.match_pct == 100.0:
            return "PASS"
        elif self.match_pct >= 90.0:
            return "WARNING"
        return "FAIL"


@dataclass
class ValidationSummary:
    ltmc_records:       int
    postload_records:   int
    matched_keys:       int
    only_in_ltmc:       int
    only_in_postload:   int
    duplicate_ltmc:     int
    duplicate_postload: int
    join_keys:          List[str]
    field_results:      List[FieldValidationResult]
    duplicate_ltmc_samples:     list = field(default_factory=list)
    duplicate_postload_samples: list = field(default_factory=list)

    @property
    def overall_status(self):
        if not self.field_results:
            return "NO DATA"
        statuses = [f.status for f in self.field_results]
        if "FAIL" in statuses:
            return "FAIL"
        if "WARNING" in statuses:
            return "WARNING"
        return "PASS"

    @property
    def avg_match_pct(self):
        if not self.field_results:
            return 0.0
        return round(sum(f.match_pct for f in self.field_results) / len(self.field_results), 1)


def validate(
    ltmc_df:      pd.DataFrame,
    postload_df:  pd.DataFrame,
    join_keys:    List[str],           # LTMC column names used as join key
    field_map:    Dict[str, str],      # {ltmc_col: postload_col}
    key_map:      Dict[str, str] = None,  # {ltmc_key: postload_key} if keys have diff names
    case_sensitive: bool = False,
    strip_zeros:    bool = False,
    max_mismatches: int  = 500,
) -> ValidationSummary:
    """
    Core validation.
    ltmc_df  = source (expected)
    postload_df = actual loaded data
    """
    key_map = key_map or {k: k for k in join_keys}

    # Normalise all string columns
    def _norm_df(df):
        df = df.copy()
        for c in df.select_dtypes(include="object").columns:
            df[c] = df[c].astype(str).str.strip()
            if strip_zeros:
                try:
                    df[c] = df[c].str.lstrip("0").replace("", "0")
                except Exception:
                    pass
        return df

    ltmc      = _norm_df(ltmc_df)
    postload  = _norm_df(postload_df)

    # Build composite key
    SEP = "||"
    # LTMC key from ltmc join_keys
    ltmc_key_cols     = join_keys
    postload_key_cols = [key_map.get(k, k) for k in join_keys]

    # Validate key columns exist
    missing_ltmc_keys = [k for k in ltmc_key_cols     if k not in ltmc.columns]
    missing_pl_keys   = [k for k in postload_key_cols if k not in postload.columns]
    if missing_ltmc_keys or missing_pl_keys:
        raise ValueError(
            f"Join key columns missing — "
            f"LTMC: {missing_ltmc_keys}, Post-load: {missing_pl_keys}"
        )

    # Normalise key values
    for k in ltmc_key_cols:
        ltmc[k] = ltmc[k].astype(str).str.strip().str.upper()
    for k in postload_key_cols:
        postload[k] = postload[k].astype(str).str.strip().str.upper()

    ltmc[_CK]    = ltmc[ltmc_key_cols].agg(SEP.join, axis=1)
    postload[_CK] = postload[postload_key_cols].agg(SEP.join, axis=1)

    # Duplicate detection
    src_dup_mask  = ltmc[_CK].duplicated(keep=False)
    tgt_dup_mask  = postload[_CK].duplicated(keep=False)
    dup_ltmc      = int(src_dup_mask.sum())
    dup_postload  = int(tgt_dup_mask.sum())
    dup_ltmc_samp     = ltmc[src_dup_mask][ltmc_key_cols].head(5).to_dict("records")
    dup_postload_samp = postload[tgt_dup_mask][postload_key_cols].head(5).to_dict("records")

    ltmc_keys    = set(ltmc[_CK].unique())
    postload_keys = set(postload[_CK].unique())
    matched_keys = ltmc_keys & postload_keys

    # Field-level validation
    field_results = []
    jk_set = set(join_keys)

    for ltmc_col, postload_col in field_map.items():
        if ltmc_col in jk_set:
            continue
        if ltmc_col not in ltmc.columns or postload_col not in postload.columns:
            continue

        # 2-column merge on composite key
        if ltmc_col == postload_col:
            m = ltmc[[_CK, ltmc_col]].merge(
                postload[[_CK, postload_col]],
                on=_CK, how="inner", suffixes=("_ltmc","_pl")
            )
            sv_col = ltmc_col + "_ltmc"
            tv_col = postload_col + "_pl"
        else:
            m = ltmc[[_CK, ltmc_col]].merge(
                postload[[_CK, postload_col]],
                on=_CK, how="inner"
            )
            sv_col = ltmc_col
            tv_col = postload_col

        total = len(m)
        if total == 0:
            continue

        sv = m[sv_col].astype(str).str.strip()
        tv = m[tv_col].astype(str).str.strip()

        if not case_sensitive:
            sv = sv.str.upper()
            tv = tv.str.upper()

        # Null normalisation
        NULL = {"", "NAN", "NONE", "NULL"}
        sv_null = sv.isin(NULL)
        tv_null = tv.isin(NULL)
        both_null  = sv_null & tv_null
        miss_ltmc  = sv_null & ~tv_null
        miss_pl    = ~sv_null & tv_null
        valid      = ~sv_null & ~tv_null

        sv_v = sv[valid]
        tv_v = tv[valid]

        match_mask    = sv_v == tv_v
        mismatch_mask = ~match_mask

        matched      = int(both_null.sum()) + int(match_mask.sum())
        miss_pl_cnt  = int(miss_pl.sum())
        miss_ltmc_cnt = int(miss_ltmc.sum())
        mismatched   = int(mismatch_mask.sum())
        match_pct    = round(matched / total * 100, 2) if total else 0.0

        # Mismatch details
        mismatches = []
        if mismatched > 0:
            mdf = m[valid][mismatch_mask].head(max_mismatches)
            for r in mdf.to_dict("records"):
                key_decoded = " / ".join(
                    f"{k}={v}" for k, v in zip(join_keys, str(r[_CK]).split("||"))
                )
                mismatches.append({
                    "key":          key_decoded,
                    "ltmc_value":   str(r.get(sv_col, "")),
                    "postload_value": str(r.get(tv_col, "")),
                })

        field_results.append(FieldValidationResult(
            field_ltmc=ltmc_col,
            field_postload=postload_col,
            total_matched_keys=total,
            matched=matched,
            mismatched=mismatched,
            missing_in_postload=miss_pl_cnt,
            missing_in_ltmc=miss_ltmc_cnt,
            match_pct=match_pct,
            mismatches=mismatches,
        ))

    return ValidationSummary(
        ltmc_records=len(ltmc),
        postload_records=len(postload),
        matched_keys=len(matched_keys),
        only_in_ltmc=len(ltmc_keys - postload_keys),
        only_in_postload=len(postload_keys - ltmc_keys),
        duplicate_ltmc=dup_ltmc,
        duplicate_postload=dup_postload,
        join_keys=join_keys,
        field_results=field_results,
        duplicate_ltmc_samples=dup_ltmc_samp,
        duplicate_postload_samples=dup_postload_samp,
    )
