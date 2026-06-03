"""
SAP Migration Post-Load Validator — Core Engine V4
===================================================
- Fully vectorized comparisons (pandas, no row-by-row loops)
- Accepts explicit field_map {source_col: target_col} for cross-name mapping
  e.g. NAME1 -> NAMORG1,  LAND1 -> COUNTRY
- Auto-detects join key, numeric columns, tolerances
- Resolves SAP field names to friendly labels via field_labels module
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

DEFAULT_JOIN_KEY       = "MATNR"
DEFAULT_PASS_THRESHOLD = 100.0


@dataclass
class FieldResult:
    field_source:      str
    field_target:      str
    field_label:       str
    total_records:     int
    matched:           int
    mismatched:        int
    missing_in_target: int
    missing_in_source: int
    is_numeric:        bool  = False
    tolerance_used:    float = None
    pass_threshold:    float = DEFAULT_PASS_THRESHOLD
    mismatch_details:  list  = field(default_factory=list)

    @property
    def match_pct(self) -> float:
        if self.total_records == 0:
            return 0.0
        return round(self.matched / self.total_records * 100, 2)

    @property
    def status(self) -> str:
        return "PASS" if self.match_pct >= self.pass_threshold else "FAIL"


@dataclass
class MappingReport:
    join_key:           str
    join_key_label:     str
    matched_fields:     list
    source_only_fields: list
    target_only_fields: list
    numeric_fields:     list
    tolerance_map:      dict
    total_source_cols:  int
    total_target_cols:  int
    selected_fields:    list  = field(default_factory=list)
    pass_threshold:     float = DEFAULT_PASS_THRESHOLD


@dataclass
class ValidationResult:
    source_file:             str
    target_file:             str
    total_source_records:    int
    total_target_records:    int
    records_matched:         int
    records_only_in_source:  int
    records_only_in_target:  int
    mapping:                 MappingReport = None
    field_results:           list = field(default_factory=list)
    errors:                  list = field(default_factory=list)

    @property
    def overall_status(self) -> str:
        if self.errors:
            return "ERROR"
        return "FAIL" if any(f.status == "FAIL" for f in self.field_results) else "PASS"

    @property
    def summary_stats(self) -> dict:
        total  = len(self.field_results)
        passed = sum(1 for f in self.field_results if f.status == "PASS")
        return {
            "total_fields_validated": total,
            "fields_passed":          passed,
            "fields_failed":          total - passed,
            "pass_rate_pct":          round(passed / total * 100, 1) if total else 0,
        }


class MaterialValidator:
    """
    Validates SAP 4.7 CSV vs S/4HANA export for any SAP object.

    field_map: {src_col: tgt_col} — pass from field_mapper for cross-name matching.
               e.g. {"NAME1": "NAMORG1", "LAND1": "COUNTRY"}
               If None, only exact column name matches are validated.
    """

    def __init__(
        self,
        field_map:           Dict[str, str] = None,
        tolerance_map:       dict  = None,
        join_key:            str   = None,
        pass_threshold:      float = DEFAULT_PASS_THRESHOLD,
        selected_fields:     list  = None,
        numeric_sample_rows: int   = 200,
        numeric_threshold:   float = 0.80,
        custom_labels              = None,
    ):
        self.field_map            = field_map
        self.tolerance_overrides  = tolerance_map or {}
        self.join_key             = join_key
        self.pass_threshold       = pass_threshold
        self.selected_fields      = [f.strip().upper() for f in (selected_fields or [])]
        self.numeric_sample_rows  = numeric_sample_rows
        self.numeric_threshold    = numeric_threshold

        from core.field_labels import load_custom_labels
        if isinstance(custom_labels, str):
            self.custom_labels = load_custom_labels(custom_labels)
        elif isinstance(custom_labels, dict):
            self.custom_labels = {k.upper(): v for k, v in custom_labels.items()}
        else:
            self.custom_labels = {}

    def _label(self, field_name: str) -> str:
        from core.field_labels import get_label
        return get_label(field_name, self.custom_labels)

    def validate(
        self,
        source_path:       str,
        target_path:       str,
        source_delimiter:  str = ",",
        target_delimiter:  str = ",",
        max_mismatch_rows: int = 500,
    ) -> ValidationResult:

        try:
            src_df = self._load_file(source_path, source_delimiter)
        except Exception as e:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=0, total_target_records=0,
                records_matched=0, records_only_in_source=0,
                records_only_in_target=0,
                errors=[f"Cannot load source file: {e}"]
            )

        try:
            tgt_df = self._load_file(target_path, target_delimiter)
        except Exception as e:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=len(src_df), total_target_records=0,
                records_matched=0, records_only_in_source=len(src_df),
                records_only_in_target=0,
                errors=[f"Cannot load target file: {e}"]
            )

        join_key = self._detect_join_key(src_df, tgt_df)
        if not join_key:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=len(src_df), total_target_records=len(tgt_df),
                records_matched=0, records_only_in_source=0, records_only_in_target=0,
                errors=[
                    f"No join key found.\n"
                    f"  Source: {src_df.columns.tolist()}\n"
                    f"  Target: {tgt_df.columns.tolist()}"
                ]
            )

        field_map, mapping_report = self._build_field_map(src_df, tgt_df, join_key)

        src_df = self._normalise_key(src_df, join_key)
        tgt_df = self._normalise_key(tgt_df, join_key)

        src_keys = set(src_df[join_key].dropna())
        tgt_keys = set(tgt_df[join_key].dropna())

        merged = src_df.merge(tgt_df, on=join_key, how="inner", suffixes=("_src", "_tgt"))

        field_results = []
        for src_col, tgt_col in field_map.items():
            tolerance = mapping_report.tolerance_map.get(src_col)
            fr = self._validate_field(merged, src_col, tgt_col, join_key, tolerance, max_mismatch_rows)
            if fr:
                field_results.append(fr)

        return ValidationResult(
            source_file=source_path, target_file=target_path,
            total_source_records=len(src_df), total_target_records=len(tgt_df),
            records_matched=len(src_keys & tgt_keys),
            records_only_in_source=len(src_keys - tgt_keys),
            records_only_in_target=len(tgt_keys - src_keys),
            mapping=mapping_report,
            field_results=field_results,
        )

    def _build_field_map(self, src_df, tgt_df, join_key):
        src_cols = set(src_df.columns) - {join_key}
        tgt_cols = set(tgt_df.columns) - {join_key}

        if self.field_map:
            # External map from field_mapper — supports cross-name pairs
            validate_map = {}
            for src_col, tgt_col in self.field_map.items():
                src_col, tgt_col = src_col.upper(), tgt_col.upper()
                if src_col in src_df.columns and tgt_col in tgt_df.columns:
                    validate_map[src_col] = tgt_col

            pairs   = [(s, t) for s, t in validate_map.items()]
            tol_map = self._detect_numeric_mapped(src_df, tgt_df, pairs)
            tol_map.update(self.tolerance_overrides)

            used_tgt = set(validate_map.values())
            only_src = sorted(src_cols - set(validate_map.keys()) - tgt_cols)
            only_tgt = sorted(tgt_cols - used_tgt - src_cols)

            report = MappingReport(
                join_key=join_key, join_key_label=self._label(join_key),
                matched_fields=list(validate_map.keys()),
                source_only_fields=only_src,
                target_only_fields=only_tgt,
                numeric_fields=sorted(tol_map.keys()),
                tolerance_map=tol_map,
                total_source_cols=len(src_df.columns),
                total_target_cols=len(tgt_df.columns),
                selected_fields=self.selected_fields,
                pass_threshold=self.pass_threshold,
            )
            return validate_map, report

        # No external map: exact column matches only
        common   = sorted(src_cols & tgt_cols)
        only_src = sorted(src_cols - tgt_cols)
        only_tgt = sorted(tgt_cols - src_cols)

        validate_cols = (
            [c for c in common if c in self.selected_fields]
            if self.selected_fields else common
        )

        tol_map = self._detect_numeric_columns(src_df, tgt_df, validate_cols)
        tol_map.update(self.tolerance_overrides)

        report = MappingReport(
            join_key=join_key, join_key_label=self._label(join_key),
            matched_fields=common,
            source_only_fields=only_src,
            target_only_fields=only_tgt,
            numeric_fields=sorted(tol_map.keys()),
            tolerance_map=tol_map,
            total_source_cols=len(src_df.columns),
            total_target_cols=len(tgt_df.columns),
            selected_fields=self.selected_fields,
            pass_threshold=self.pass_threshold,
        )
        return {col: col for col in validate_cols}, report

    def _detect_numeric_mapped(self, src_df, tgt_df, pairs):
        numeric_cols = {}
        for src_col, tgt_col in pairs:
            src_vals = src_df[src_col].dropna().head(self.numeric_sample_rows) if src_col in src_df.columns else pd.Series([], dtype=str)
            tgt_vals = tgt_df[tgt_col].dropna().head(self.numeric_sample_rows) if tgt_col in tgt_df.columns else pd.Series([], dtype=str)
            if len(src_vals) == 0 or len(tgt_vals) == 0:
                continue

            def parse_rate(s):
                return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce").notna().sum() / len(s)

            if parse_rate(src_vals) >= self.numeric_threshold and parse_rate(tgt_vals) >= self.numeric_threshold:
                if src_col in self.tolerance_overrides:
                    tol = self.tolerance_overrides[src_col]
                else:
                    all_v = pd.to_numeric(
                        pd.concat([src_vals, tgt_vals]).astype(str).str.replace(",", ".", regex=False),
                        errors="coerce"
                    ).dropna().abs()
                    median = float(all_v.median()) if len(all_v) > 0 else 0.0
                    tol    = self._scale_tolerance(median)
                numeric_cols[src_col] = tol
        return numeric_cols

    def _detect_numeric_columns(self, src_df, tgt_df, columns):
        numeric_cols = {}
        for col in columns:
            src_vals = src_df[col].dropna().head(self.numeric_sample_rows)
            tgt_vals = tgt_df[col].dropna().head(self.numeric_sample_rows)
            if len(src_vals) == 0 or len(tgt_vals) == 0:
                continue

            def parse_rate(s):
                return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce").notna().sum() / len(s)

            if parse_rate(src_vals) >= self.numeric_threshold and parse_rate(tgt_vals) >= self.numeric_threshold:
                if col in self.tolerance_overrides:
                    tol = self.tolerance_overrides[col]
                else:
                    all_v = pd.to_numeric(
                        pd.concat([src_vals, tgt_vals]).astype(str).str.replace(",", ".", regex=False),
                        errors="coerce"
                    ).dropna().abs()
                    median = float(all_v.median()) if len(all_v) > 0 else 0.0
                    tol    = self._scale_tolerance(median)
                numeric_cols[col] = tol
        return numeric_cols

    @staticmethod
    def _scale_tolerance(median_val: float) -> float:
        if median_val == 0:     return 0.0
        elif median_val < 1:    return 0.0001
        elif median_val < 10:   return 0.001
        elif median_val < 1000: return 0.01
        else:                   return 0.1

    def _detect_join_key(self, src_df, tgt_df) -> Optional[str]:
        if self.join_key:
            jk = self.join_key.upper()
            if jk in src_df.columns and jk in tgt_df.columns:
                return jk
            return None
        PRIORITY = ["MATNR","LIFNR","KUNNR","SAKNR","BELNR","EBELN","VBELN","ANLN1","KOSTL","PRCTR","BANKL"]
        common = set(src_df.columns) & set(tgt_df.columns)
        for pk in PRIORITY:
            if pk in common:
                return pk
        if common:
            return sorted(common, key=lambda c: (0 if c in ("ID","KEY","CODE") else 1, len(c)))[0]
        return None

    def _load_file(self, path: str, delimiter: str) -> pd.DataFrame:
        if str(path).lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(path, dtype=str)
        else:
            df = pd.read_csv(path, delimiter=delimiter, dtype=str, encoding="utf-8-sig", low_memory=False)
        df.columns = df.columns.str.strip().str.upper()
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].apply(lambda c: c.str.strip())
        return df

    def _normalise_key(self, df: pd.DataFrame, col: str) -> pd.DataFrame:
        if col in df.columns:
            df = df.copy()
            df[col] = df[col].astype(str).str.strip().str.lstrip("0").str.upper()
        return df

    def _validate_field(self, merged, src_col, tgt_col, join_key, tolerance, max_rows):
        # Resolve actual column names after merge suffixes
        if src_col == tgt_col:
            src_actual = src_col + "_src" if src_col + "_src" in merged.columns else src_col
            tgt_actual = tgt_col + "_tgt" if tgt_col + "_tgt" in merged.columns else tgt_col
        else:
            src_actual = src_col if src_col in merged.columns else src_col + "_src"
            tgt_actual = tgt_col if tgt_col in merged.columns else tgt_col + "_tgt"

        if src_actual not in merged.columns and tgt_actual not in merged.columns:
            return None

        total = len(merged)
        if total == 0:
            return FieldResult(
                field_source=src_col, field_target=tgt_col, field_label=self._label(src_col),
                total_records=0, matched=0, mismatched=0, missing_in_target=0, missing_in_source=0,
                is_numeric=(tolerance is not None), tolerance_used=tolerance, pass_threshold=self.pass_threshold,
            )

        sv = merged[src_actual] if src_actual in merged.columns else pd.Series([""] * total, index=merged.index)
        tv = merged[tgt_actual] if tgt_actual in merged.columns else pd.Series([""] * total, index=merged.index)
        keys = merged[join_key]

        NULL_VALS  = {"", "nan", "none", "null"}
        sv_null    = sv.isna() | sv.astype(str).str.strip().str.lower().isin(NULL_VALS)
        tv_null    = tv.isna() | tv.astype(str).str.strip().str.lower().isin(NULL_VALS)
        both_null  = sv_null & tv_null
        miss_src_m = sv_null & ~tv_null
        miss_tgt_m = ~sv_null & tv_null
        valid      = ~sv_null & ~tv_null

        sv_v, tv_v = sv[valid], tv[valid]

        if tolerance is not None:
            sv_f = pd.to_numeric(sv_v.astype(str).str.replace(",", ".", regex=False), errors="coerce")
            tv_f = pd.to_numeric(tv_v.astype(str).str.replace(",", ".", regex=False), errors="coerce")
            ok   = sv_f.notna() & tv_f.notna()
            diff = (sv_f - tv_f).abs()
            match_mask    = ok & (diff <= tolerance)
            mismatch_mask = ok & (diff > tolerance)
        else:
            sv_s = sv_v.astype(str).str.strip().str.upper()
            tv_s = tv_v.astype(str).str.strip().str.upper()
            match_mask    = sv_s == tv_s
            mismatch_mask = ~match_mask

        matched        = int(both_null.sum()) + int(match_mask.sum())
        miss_src_count = int(miss_src_m.sum())
        miss_tgt_count = int(miss_tgt_m.sum())
        mismatched     = int(mismatch_mask.sum())

        mismatches = []

        if miss_src_count > 0:
            for _, row in merged[miss_src_m].head(max_rows).iterrows():
                mismatches.append({"material": str(row[join_key]), "source_value": "(blank)",
                                    "target_value": str(row.get(tgt_actual, "")), "issue": "Missing in source"})

        if miss_tgt_count > 0 and len(mismatches) < max_rows:
            for _, row in merged[miss_tgt_m].head(max_rows - len(mismatches)).iterrows():
                mismatches.append({"material": str(row[join_key]),
                                    "source_value": str(row.get(src_actual, "")),
                                    "target_value": "(blank)", "issue": "Missing in target"})

        if mismatched > 0 and len(mismatches) < max_rows:
            mdf = merged[valid][mismatch_mask].head(max_rows - len(mismatches))
            if tolerance is not None:
                sv_mf   = sv_f[mismatch_mask].reindex(mdf.index)
                tv_mf   = tv_f[mismatch_mask].reindex(mdf.index)
                diff_mf = diff[mismatch_mask].reindex(mdf.index)
                for idx, row in mdf.iterrows():
                    mismatches.append({"material": str(row[join_key]),
                                       "source_value": float(sv_mf.get(idx, 0)),
                                       "target_value": float(tv_mf.get(idx, 0)),
                                       "issue": f"Delta={float(diff_mf.get(idx,0)):.4f} (tol+-{tolerance})"})
            else:
                for _, row in mdf.iterrows():
                    mismatches.append({"material": str(row[join_key]),
                                       "source_value": str(row.get(src_actual, "")),
                                       "target_value": str(row.get(tgt_actual, "")),
                                       "issue": "Value mismatch"})

        return FieldResult(
            field_source=src_col, field_target=tgt_col, field_label=self._label(src_col),
            total_records=total, matched=matched, mismatched=mismatched,
            missing_in_target=miss_tgt_count, missing_in_source=miss_src_count,
            is_numeric=(tolerance is not None), tolerance_used=tolerance,
            pass_threshold=self.pass_threshold, mismatch_details=mismatches,
        )
