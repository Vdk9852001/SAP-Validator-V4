"""Report generator — Excel + HTML."""
from __future__ import annotations
import io
from datetime import datetime
from typing import Optional
import pandas as pd

try:
    import xlsxwriter
    _HAS_XW = True
except ImportError:
    _HAS_XW = False


def generate_excel_report(summary, ltmc_name="", postload_name="",
                           sap_object="", mapping: dict = None) -> bytes:
    """Return Excel workbook as bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:

        # ── Summary sheet ──────────────────────────────────────────────────
        summary_data = {
            "Metric": [
                "LTMC File", "Post-Load File", "SAP Object", "Timestamp",
                "Join Keys", "Overall Status",
                "LTMC Records", "Post-Load Records", "Matched Keys",
                "Only in LTMC (missing in S/4)", "Only in Post-Load (extra)",
                "Duplicate Keys in LTMC", "Duplicate Keys in Post-Load",
                "Average Field Match %",
            ],
            "Value": [
                ltmc_name, postload_name, sap_object,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                " + ".join(summary.join_keys),
                summary.overall_status,
                summary.ltmc_records, summary.postload_records,
                summary.matched_keys, summary.only_in_ltmc, summary.only_in_postload,
                summary.duplicate_ltmc, summary.duplicate_postload,
                f"{summary.avg_match_pct}%",
            ],
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

        # ── Field results sheet ────────────────────────────────────────────
        rows = []
        for fr in summary.field_results:
            rows.append({
                "LTMC Field":       fr.field_ltmc,
                "Post-Load Field":  fr.field_postload,
                "Matched Keys":     fr.total_matched_keys,
                "Matched Values":   fr.matched,
                "Mismatches":       fr.mismatched,
                "Missing in S/4":   fr.missing_in_postload,
                "Missing in LTMC":  fr.missing_in_ltmc,
                "Match %":          f"{fr.match_pct}%",
                "Status":           fr.status,
            })
        if rows:
            pd.DataFrame(rows).to_excel(writer, sheet_name="Field Results", index=False)

        # ── Mismatch detail sheet ──────────────────────────────────────────
        mismatch_rows = []
        for fr in summary.field_results:
            for m in fr.mismatches:
                mismatch_rows.append({
                    "Field":             fr.field_ltmc,
                    "Key":               m["key"],
                    "LTMC Value":        m["ltmc_value"],
                    "Post-Load Value":   m["postload_value"],
                })
        if mismatch_rows:
            pd.DataFrame(mismatch_rows).to_excel(
                writer, sheet_name="Mismatches", index=False
            )

    buf.seek(0)
    return buf.read()


def generate_html_report(summary, ltmc_name="", postload_name="",
                          sap_object="") -> str:
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = summary.overall_status
    color  = {"PASS":"#16a34a","WARNING":"#d97706","FAIL":"#dc2626",
               "NO DATA":"#6b7280"}.get(status,"#6b7280")

    field_rows = ""
    for fr in summary.field_results:
        sc = {"PASS":"#dcfce7","WARNING":"#fef3c7","FAIL":"#fee2e2"}.get(fr.status,"#fff")
        field_rows += f"""<tr style="background:{sc}">
          <td>{fr.field_ltmc}</td><td>{fr.field_postload}</td>
          <td>{fr.total_matched_keys}</td><td>{fr.matched}</td>
          <td>{fr.mismatched}</td><td>{fr.missing_in_postload}</td>
          <td>{fr.match_pct}%</td>
          <td><b style="color:{color}">{fr.status}</b></td></tr>"""

    return f"""<!DOCTYPE html><html><head>
<title>SAP LTMC Validation Report</title>
<style>
body{{font-family:Arial,sans-serif;margin:30px;color:#1a1f36}}
h1{{color:#4f46e5}}h2{{color:#374151;border-bottom:2px solid #e5e7eb;padding-bottom:6px}}
table{{border-collapse:collapse;width:100%}}
th{{background:#4f46e5;color:#fff;padding:8px 12px;text-align:left}}
td{{padding:7px 12px;border-bottom:1px solid #e5e7eb}}
.pill{{display:inline-block;padding:4px 14px;border-radius:20px;font-weight:700;color:#fff;background:{color}}}
.metric{{display:inline-block;min-width:160px;background:#f4f6fa;border-radius:8px;
  padding:12px 16px;margin:6px;text-align:center}}
.metric .val{{font-size:2em;font-weight:700;color:#4f46e5}}
.metric .lbl{{font-size:12px;color:#6b7280}}
</style></head><body>
<h1>SAP LTMC Validation Report</h1>
<p>Generated: {ts}</p>
<div class="pill">{status}</div>&nbsp;
<p><b>LTMC File:</b> {ltmc_name} &nbsp;|&nbsp; <b>Post-Load:</b> {postload_name}
&nbsp;|&nbsp; <b>Object:</b> {sap_object}
&nbsp;|&nbsp; <b>Join Keys:</b> {' + '.join(summary.join_keys)}</p>

<h2>Summary</h2>
<div class="metric"><div class="val">{summary.ltmc_records}</div><div class="lbl">LTMC Records</div></div>
<div class="metric"><div class="val">{summary.postload_records}</div><div class="lbl">Post-Load Records</div></div>
<div class="metric"><div class="val">{summary.matched_keys}</div><div class="lbl">Matched Keys</div></div>
<div class="metric"><div class="val" style="color:#dc2626">{summary.only_in_ltmc}</div><div class="lbl">Only in LTMC</div></div>
<div class="metric"><div class="val" style="color:#d97706">{summary.only_in_postload}</div><div class="lbl">Only in Post-Load</div></div>
<div class="metric"><div class="val">{summary.avg_match_pct}%</div><div class="lbl">Avg Match %</div></div>

<h2>Field Results</h2>
<table><thead><tr><th>LTMC Field</th><th>Post-Load Field</th><th>Keys</th>
<th>Matched</th><th>Mismatch</th><th>Missing</th><th>Match %</th><th>Status</th></tr></thead>
<tbody>{field_rows}</tbody></table>
</body></html>"""
