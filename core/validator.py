"""
SAP Migration Post-Load Validator — Core Engine V4
===================================================
Key improvements:
  1. Composite key support — joins on MATNR+KSCHL+VKORG instead of just MATNR
  2. Dynamic key detection via key_detector.py
  3. Duplicate key reporting
  4. Column-only loading (usecols) for performance on lakh-scale files
  5. Per-field 2-column merge instead of full N-column merge
  6. to_dict() instead of iterrows() for mismatch collection
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

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
    join_keys:          List[str]   # composite key columns
    join_key_labels:    Dict[str, str]
    matched_fields:     list
    source_only_fields: list
    target_only_fields: list
    numeric_fields:     list
    tolerance_map:      dict
    total_source_cols:  int
    total_target_cols:  int
    selected_fields:    list  = field(default_factory=list)
    pass_threshold:     float = DEFAULT_PASS_THRESHOLD
    # Backwards compat
    @property
    def join_key(self) -> str:
        return "+".join(self.join_keys) if self.join_keys else ""
    @property
    def join_key_label(self) -> str:
        return " + ".join(self.join_key_labels.get(k, k) for k in self.join_keys)


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
    # Composite key info
    join_keys:               List[str] = field(default_factory=list)
    key_detection_method:    str = ""
    key_confidence:          str = ""
    duplicate_src:           int = 0
    duplicate_tgt:           int = 0
    duplicate_key_samples:   list = field(default_factory=list)

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


# ── Composite key constant ────────────────────────────────────────────────────
_CK = "__CK__"   # internal composite key column name


class MaterialValidator:

    def __init__(
        self,
        field_map:           Dict[str, str] = None,
        tolerance_map:       dict  = None,
        join_key:            str   = None,    # single key (legacy)
        join_keys:           List[str] = None, # composite key (new)
        manual_join_keys:    List[str] = None, # user-specified override
        pass_threshold:      float = DEFAULT_PASS_THRESHOLD,
        selected_fields:     list  = None,
        numeric_sample_rows: int   = 200,
        numeric_threshold:   float = 0.80,
        custom_labels              = None,
    ):
        self.field_map            = field_map
        self.tolerance_overrides  = tolerance_map or {}
        # Support both single and composite key
        if join_keys:
            self.join_keys = [k.upper() for k in join_keys]
        elif join_key:
            self.join_keys = [join_key.upper()]
        else:
            self.join_keys = []
        self.manual_join_keys     = [k.upper() for k in (manual_join_keys or [])]
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

    # ── Entry point ──────────────────────────────────────────────────────────

    def validate(
        self,
        source_path:       str,
        target_path:       str,
        source_delimiter:  str = ",",
        target_delimiter:  str = ",",
        max_mismatch_rows: int = 200,
        object_name:       str = "",
    ) -> ValidationResult:

        # ── Read headers first ────────────────────────────────────────────────
        try:
            src_hdrs = self._read_headers(source_path, source_delimiter)
            tgt_hdrs = self._read_headers(target_path, target_delimiter)
        except Exception as e:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=0, total_target_records=0,
                records_matched=0, records_only_in_source=0,
                records_only_in_target=0,
                errors=[f"Cannot read file headers: {e}"]
            )

        common_cols = sorted(set(src_hdrs) & set(tgt_hdrs))

        # ── Resolve field map ─────────────────────────────────────────────────
        if self.field_map:
            field_map  = {s.upper(): t.upper() for s, t in self.field_map.items()}
            needed_src = set(field_map.keys())
            needed_tgt = set(field_map.values())
        else:
            field_map  = {c: c for c in common_cols}
            needed_src = set(field_map.keys())
            needed_tgt = set(field_map.values())

        # ── Load ONLY needed columns ──────────────────────────────────────────
        all_needed_src = list(needed_src | set(src_hdrs))  # full file needed for key detection
        all_needed_tgt = list(needed_tgt | set(tgt_hdrs))

        try:
            src_df = self._load_file_cols(source_path, source_delimiter, src_hdrs)
        except Exception as e:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=0, total_target_records=0,
                records_matched=0, records_only_in_source=0, records_only_in_target=0,
                errors=[f"Cannot load source file: {e}"]
            )
        try:
            tgt_df = self._load_file_cols(target_path, target_delimiter, tgt_hdrs)
        except Exception as e:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=len(src_df), total_target_records=0,
                records_matched=0, records_only_in_source=len(src_df), records_only_in_target=0,
                errors=[f"Cannot load target file: {e}"]
            )

        # ── Detect composite join key ─────────────────────────────────────────
        from core.key_detector import detect_composite_key
        kd = detect_composite_key(
            src_df=src_df,
            tgt_df=tgt_df,
            object_name=object_name or self._infer_object(source_path),
            manual_keys=self.manual_join_keys or (self.join_keys if self.join_keys else None),
        )
        join_keys = kd.join_keys

        if not join_keys:
            return ValidationResult(
                source_file=source_path, target_file=target_path,
                total_source_records=len(src_df), total_target_records=len(tgt_df),
                records_matched=0, records_only_in_source=0, records_only_in_target=0,
                errors=["No join key could be detected. Use the dashboard to select join keys manually."]
            )

        logger.info(
            f"Join keys: {join_keys} "
            f"(method={kd.detection_method}, confidence={kd.confidence}, "
            f"uniqueness src={kd.uniqueness_src:.1%} tgt={kd.uniqueness_tgt:.1%})"
        )

        # ── Normalise key columns ─────────────────────────────────────────────
        for k in join_keys:
            if k in src_df.columns:
                src_df[k] = src_df[k].astype(str).str.strip().str.upper()
            if k in tgt_df.columns:
                tgt_df[k] = tgt_df[k].astype(str).str.strip().str.upper()

        # ── Build composite key column ─────────────────────────────────────────
        # CK = "VAL1||VAL2||VAL3" — separator unlikely to appear in data
        SEP = "||"
        src_df[_CK] = src_df[join_keys].astype(str).agg(SEP.join, axis=1)
        tgt_df[_CK] = tgt_df[join_keys].astype(str).agg(SEP.join, axis=1)

        # ── Key set analysis ──────────────────────────────────────────────────
        src_keys = set(src_df[_CK].unique())
        tgt_keys = set(tgt_df[_CK].unique())

        # Detect duplicate key samples for the dashboard
        src_dup_mask = src_df[_CK].duplicated(keep=False)
        tgt_dup_mask = tgt_df[_CK].duplicated(keep=False)
        dup_src_count = int(src_dup_mask.sum())
        dup_tgt_count = int(tgt_dup_mask.sum())

        dup_samples = []
        if dup_src_count > 0:
            sample_rows = src_df[src_dup_mask].head(10)
            dup_samples = sample_rows[join_keys].to_dict("records")

        # ── Remove field_map entries that are join keys (don't validate keys) ─
        field_map = {s: t for s, t in field_map.items()
                     if s not in join_keys and t not in join_keys}

        # ── Numeric detection ─────────────────────────────────────────────────
        pairs   = [(s, t) for s, t in field_map.items()
                   if s in src_df.columns and t in tgt_df.columns]
        tol_map = self._detect_numeric_mapped(src_df, tgt_df, pairs)
        tol_map.update(self.tolerance_overrides)

        # ── Build mapping report ──────────────────────────────────────────────
        from core.field_labels import get_label
        mapping_report = MappingReport(
            join_keys=join_keys,
            join_key_labels={k: get_label(k, self.custom_labels) for k in join_keys},
            matched_fields=list(field_map.keys()),
            source_only_fields=sorted(set(src_hdrs) - set(tgt_hdrs) - set(join_keys)),
            target_only_fields=sorted(set(tgt_hdrs) - set(src_hdrs) - set(join_keys)),
            numeric_fields=sorted(tol_map.keys()),
            tolerance_map=tol_map,
            total_source_cols=len(src_hdrs),
            total_target_cols=len(tgt_hdrs),
            selected_fields=self.selected_fields,
            pass_threshold=self.pass_threshold,
        )

        # ── Validate each field using 2-column merge ──────────────────────────
        field_results = []
        for src_col, tgt_col in field_map.items():
            tolerance = tol_map.get(src_col)
            fr = self._validate_field_fast(
                src_df, tgt_df, src_col, tgt_col, join_keys,
                tolerance, max_mismatch_rows
            )
            if fr:
                field_results.append(fr)

        return ValidationResult(
            source_file=source_path,
            target_file=target_path,
            total_source_records=len(src_df),
            total_target_records=len(tgt_df),
            records_matched=len(src_keys & tgt_keys),
            records_only_in_source=len(src_keys - tgt_keys),
            records_only_in_target=len(tgt_keys - src_keys),
            mapping=mapping_report,
            field_results=field_results,
            join_keys=join_keys,
            key_detection_method=kd.detection_method,
            key_confidence=kd.confidence,
            duplicate_src=dup_src_count,
            duplicate_tgt=dup_tgt_count,
            duplicate_key_samples=dup_samples,
        )

    # ── Per-field validation (2-column merge) ─────────────────────────────────

    def _validate_field_fast(
        self,
        src_df:    pd.DataFrame,
        tgt_df:    pd.DataFrame,
        src_col:   str,
        tgt_col:   str,
        join_keys: List[str],
        tolerance: Optional[float],
        max_rows:  int,
    ) -> Optional[FieldResult]:

        if src_col not in src_df.columns or tgt_col not in tgt_df.columns:
            return None

        # Merge on composite key (_CK) — each CK value is unique per record
        if src_col == tgt_col:
            m = src_df[[_CK, src_col]].merge(
                tgt_df[[_CK, tgt_col]],
                on=_CK, how="inner",
                suffixes=("_src", "_tgt"),
            )
            sv_col = src_col + "_src"
            tv_col = tgt_col + "_tgt"
        else:
            m = src_df[[_CK, src_col]].merge(
                tgt_df[[_CK, tgt_col]],
                on=_CK, how="inner",
            )
            sv_col = src_col
            tv_col = tgt_col

        total = len(m)
        if total == 0:
            return FieldResult(
                field_source=src_col, field_target=tgt_col,
                field_label=self._label(src_col),
                total_records=0, matched=0, mismatched=0,
                missing_in_target=0, missing_in_source=0,
                is_numeric=(tolerance is not None),
                tolerance_used=tolerance, pass_threshold=self.pass_threshold,
            )

        sv = m[sv_col]
        tv = m[tv_col]

        NULL_VALS  = {"", "nan", "none", "null"}
        sv_null    = sv.isna() | sv.astype(str).str.strip().str.lower().isin(NULL_VALS)
        tv_null    = tv.isna() | tv.astype(str).str.strip().str.lower().isin(NULL_VALS)
        both_null  = sv_null & tv_null
        miss_src_m = sv_null & ~tv_null
        miss_tgt_m = ~sv_null & tv_null
        valid      = ~sv_null & ~tv_null

        sv_v = sv[valid]
        tv_v = tv[valid]

        if tolerance is not None:
            sv_f = pd.to_numeric(sv_v.astype(str).str.replace(",", ".", regex=False), errors="coerce")
            tv_f = pd.to_numeric(tv_v.astype(str).str.replace(",", ".", regex=False), errors="coerce")
            ok            = sv_f.notna() & tv_f.notna()
            diff          = (sv_f - tv_f).abs()
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

        # ── Build mismatch list (to_dict is much faster than iterrows) ────────
        mismatches = []

        # Decode CK back to key column values for display
        def _ck_label(ck_val: str) -> str:
            parts = ck_val.split("||")
            if len(join_keys) == 1:
                return str(parts[0])
            return " / ".join(f"{k}={v}" for k, v in zip(join_keys, parts))

        if miss_src_count > 0:
            for r in m[miss_src_m].head(max_rows).to_dict("records"):
                mismatches.append({
                    "material":     _ck_label(r[_CK]),
                    "source_value": "(blank)",
                    "target_value": str(r.get(tv_col, "")),
                    "issue":        "Missing in source",
                })

        if miss_tgt_count > 0 and len(mismatches) < max_rows:
            for r in m[miss_tgt_m].head(max_rows - len(mismatches)).to_dict("records"):
                mismatches.append({
                    "material":     _ck_label(r[_CK]),
                    "source_value": str(r.get(sv_col, "")),
                    "target_value": "(blank)",
                    "issue":        "Missing in target",
                })

        if mismatched > 0 and len(mismatches) < max_rows:
            mdf = m[valid][mismatch_mask].head(max_rows - len(mismatches))
            if tolerance is not None:
                sv_mf   = sv_f[mismatch_mask].reindex(mdf.index)
                tv_mf   = tv_f[mismatch_mask].reindex(mdf.index)
                diff_mf = diff[mismatch_mask].reindex(mdf.index)
                for idx, r in zip(mdf.index, mdf.to_dict("records")):
                    mismatches.append({
                        "material":     _ck_label(r[_CK]),
                        "source_value": round(float(sv_mf.get(idx, 0)), 4),
                        "target_value": round(float(tv_mf.get(idx, 0)), 4),
                        "issue":        f"Delta (tol+-{tolerance})",
                    })
            else:
                for r in mdf.to_dict("records"):
                    mismatches.append({
                        "material":     _ck_label(r[_CK]),
                        "source_value": str(r.get(sv_col, "")),
                        "target_value": str(r.get(tv_col, "")),
                        "issue":        "Value mismatch",
                    })

        return FieldResult(
            field_source=src_col, field_target=tgt_col,
            field_label=self._label(src_col),
            total_records=total, matched=matched, mismatched=mismatched,
            missing_in_target=miss_tgt_count, missing_in_source=miss_src_count,
            is_numeric=(tolerance is not None), tolerance_used=tolerance,
            pass_threshold=self.pass_threshold, mismatch_details=mismatches,
        )

    # ── File loading helpers ──────────────────────────────────────────────────

    def _read_headers(self, path: str, delimiter: str = ",") -> list:
        import csv
        p = str(path)
        if p.lower().endswith((".xlsx", ".xls")):
            import openpyxl
            wb   = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws   = wb.active
            cols = [str(c.value).strip().upper() for c in next(ws.iter_rows(max_row=1)) if c.value]
            wb.close()
            return cols
        with open(p, encoding="utf-8-sig") as f:
            return [c.strip().upper() for c in next(csv.reader(f, delimiter=delimiter))]

    def _load_file_cols(self, path: str, delimiter: str, needed_cols: list) -> pd.DataFrame:
        p           = str(path)
        all_headers = self._read_headers(p, delimiter)
        needed_set  = set(c.upper() for c in needed_cols)
        load_cols   = [c for c in all_headers if c in needed_set]
        if not load_cols:
            load_cols = None  # load all if nothing matched

        if p.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(p, dtype=str, usecols=load_cols)
        else:
            df = pd.read_csv(
                p, delimiter=delimiter, dtype=str,
                encoding="utf-8-sig", low_memory=False,
                usecols=load_cols, na_filter=False,
            )
        df.columns = df.columns.str.strip().str.upper()
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].str.strip()
        return df

    def _load_file(self, path: str, delimiter: str) -> pd.DataFrame:
        """Legacy full-load method."""
        return self._load_file_cols(path, delimiter, [])

    @staticmethod
    def _infer_object(file_path: str) -> str:
        from pathlib import Path
        return Path(file_path).stem.upper()

    # ── Numeric detection ─────────────────────────────────────────────────────

    def _detect_numeric_mapped(self, src_df, tgt_df, pairs):
        numeric_cols = {}
        for src_col, tgt_col in pairs:
            if src_col not in src_df.columns or tgt_col not in tgt_df.columns:
                continue
            src_vals = src_df[src_col].dropna().head(self.numeric_sample_rows)
            tgt_vals = tgt_df[tgt_col].dropna().head(self.numeric_sample_rows)
            if not len(src_vals) or not len(tgt_vals):
                continue

            def parse_rate(s):
                return (pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False),
                                      errors="coerce").notna().sum() / len(s))

            if (parse_rate(src_vals) >= self.numeric_threshold and
                    parse_rate(tgt_vals) >= self.numeric_threshold):
                if src_col in self.tolerance_overrides:
                    tol = self.tolerance_overrides[src_col]
                else:
                    all_v = pd.to_numeric(
                        pd.concat([src_vals, tgt_vals]).astype(str)
                        .str.replace(",", ".", regex=False), errors="coerce"
                    ).dropna().abs()
                    tol = self._scale_tolerance(float(all_v.median()) if len(all_v) else 0.0)
                numeric_cols[src_col] = tol
        return numeric_cols

    @staticmethod
    def _scale_tolerance(v: float) -> float:
        if v == 0:      return 0.0
        elif v < 1:     return 0.0001
        elif v < 10:    return 0.001
        elif v < 1000:  return 0.01
        else:           return 0.1

    # ── Legacy single-key helpers ─────────────────────────────────────────────

    def _detect_join_key(self, src_df, tgt_df):
        if self.join_keys:
            jk = self.join_keys[0]
            if jk in src_df.columns and jk in tgt_df.columns:
                return jk
        PRIORITY = ["MATNR","LIFNR","KUNNR","SAKNR","BELNR","EBELN","VBELN",
                    "ANLN1","KOSTL","PRCTR","BANKL"]
        common = set(src_df.columns) & set(tgt_df.columns)
        for pk in PRIORITY:
            if pk in common:
                return pk
        return sorted(common)[0] if common else None

    def _normalise_key(self, df, col):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.lstrip("0").str.upper()
        return df

    def _build_field_map(self, src_df, tgt_df, join_key):
        """Legacy single-key method kept for backwards compatibility."""
        src_cols = set(src_df.columns) - {join_key}
        tgt_cols = set(tgt_df.columns) - {join_key}
        if self.field_map:
            vm = {s.upper(): t.upper() for s, t in self.field_map.items()
                  if s.upper() in src_df.columns and t.upper() in tgt_df.columns}
            tol = self._detect_numeric_mapped(src_df, tgt_df, list(vm.items()))
            tol.update(self.tolerance_overrides)
        else:
            cols = sorted(src_cols & tgt_cols)
            cols = [c for c in cols if c in self.selected_fields] if self.selected_fields else cols
            vm   = {c: c for c in cols}
            tol  = self._detect_numeric_mapped(src_df, tgt_df, list(vm.items()))
            tol.update(self.tolerance_overrides)
        from core.field_labels import get_label
        report = MappingReport(
            join_keys=[join_key], join_key_labels={join_key: get_label(join_key, self.custom_labels)},
            matched_fields=list(vm.keys()),
            source_only_fields=sorted(src_cols - tgt_cols),
            target_only_fields=sorted(tgt_cols - src_cols),
            numeric_fields=sorted(tol.keys()), tolerance_map=tol,
            total_source_cols=len(src_df.columns), total_target_cols=len(tgt_df.columns),
            selected_fields=self.selected_fields, pass_threshold=self.pass_threshold,
        )
        return vm, report
