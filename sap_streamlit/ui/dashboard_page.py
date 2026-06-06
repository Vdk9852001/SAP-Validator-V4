"""Step 7 — Results dashboard and export."""
import streamlit as st
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.report_generator import generate_excel_report, generate_html_report
from ui.components          import section_header, metric_card, status_pill, info_box


def render():
    summary = st.session_state.get("validation_summary")
    if not summary:
        st.info("No validation results yet. Run validation first.")
        if st.button("← Back to Validation"):
            st.session_state["step"] = 3
            st.rerun()
        return

    ltmc_name    = st.session_state.get("ltmc_filename", "")
    pl_name      = st.session_state.get("postload_filename", "")
    sap_object   = st.session_state.get("detected_object", "")
    mapping      = st.session_state.get("final_field_map", {})

    # ── Header ─────────────────────────────────────────────────────────────
    st.markdown("## 📊 Validation Results")
    col_status, col_meta = st.columns([1,3])
    with col_status:
        status_pill(summary.overall_status)
    with col_meta:
        st.markdown(
            f"**{ltmc_name}** vs **{pl_name}**  \n"
            f"Object: **{sap_object}** &nbsp;|&nbsp; "
            f"Keys: **{' + '.join(summary.join_keys)}** &nbsp;|&nbsp; "
            f"Avg Match: **{summary.avg_match_pct}%**"
        )

    st.divider()

    # ── KPI cards ──────────────────────────────────────────────────────────
    cols = st.columns(7)
    kpis = [
        ("LTMC Records",      summary.ltmc_records,      "#4f46e5"),
        ("Post-Load Records", summary.postload_records,   "#4f46e5"),
        ("Matched Keys",      summary.matched_keys,       "#16a34a"),
        ("Only in LTMC",      summary.only_in_ltmc,       "#dc2626"),
        ("Only in Post-Load", summary.only_in_postload,   "#d97706"),
        ("Dup. Keys (LTMC)",  summary.duplicate_ltmc,     "#d97706"),
        ("Avg Match %",       f"{summary.avg_match_pct}%","#16a34a"),
    ]
    for col, (lbl, val, color) in zip(cols, kpis):
        with col:
            metric_card(lbl, val, color)

    st.divider()

    # ── Tabs ───────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📋 Field Results", "🔍 Mismatch Details",
        "⚠️ Key Issues", "📤 Export Reports"
    ])

    with tab1:
        _render_field_results(summary)

    with tab2:
        _render_mismatch_details(summary)

    with tab3:
        _render_key_issues(summary)

    with tab4:
        _render_exports(summary, ltmc_name, pl_name, sap_object, mapping)

    # Back button
    st.divider()
    if st.button("← Run Another Validation"):
        st.session_state["step"] = 3
        st.rerun()


def _render_field_results(summary):
    if not summary.field_results:
        st.info("No fields were validated.")
        return

    rows = []
    for fr in summary.field_results:
        bar_pct = int(fr.match_pct)
        bar     = "█" * (bar_pct // 10) + "░" * (10 - bar_pct // 10)
        rows.append({
            "LTMC Field":      fr.field_ltmc,
            "Post-Load Field": fr.field_postload,
            "Matched":         fr.matched,
            "Mismatch":        fr.mismatched,
            "Missing in S/4":  fr.missing_in_postload,
            "Match %":         fr.match_pct,
            "Visual":          bar,
            "Status":          fr.status,
        })

    df = pd.DataFrame(rows)

    # Colour status column
    def _colour(val):
        if val == "PASS":    return "background-color: #dcfce7"
        if val == "WARNING": return "background-color: #fef3c7"
        if val == "FAIL":    return "background-color: #fee2e2"
        return ""

    st.dataframe(
        df.style.applymap(_colour, subset=["Status"]),
        use_container_width=True,
        height=400,
    )

    # Worst fields chart
    worst = sorted(summary.field_results, key=lambda x: x.match_pct)[:10]
    if worst:
        st.markdown("**Top 10 Fields with Lowest Match %:**")
        chart_data = pd.DataFrame({
            "Field": [f.field_ltmc for f in worst],
            "Match %": [f.match_pct for f in worst],
        }).set_index("Field")
        st.bar_chart(chart_data)


def _render_mismatch_details(summary):
    failing = [fr for fr in summary.field_results if fr.mismatches]
    if not failing:
        st.success("No mismatches found!")
        return

    field_sel = st.selectbox(
        "Select field to inspect",
        [fr.field_ltmc for fr in failing],
        key="mismatch_field_sel"
    )
    selected = next((fr for fr in failing if fr.field_ltmc == field_sel), None)
    if selected:
        st.markdown(
            f"**{selected.field_ltmc}** ↔ **{selected.field_postload}** — "
            f"{selected.mismatched} mismatches / {selected.total_matched_keys} records "
            f"({selected.match_pct}% match)"
        )
        mdf = pd.DataFrame(selected.mismatches)
        if not mdf.empty:
            st.dataframe(mdf, use_container_width=True, height=350)


def _render_key_issues(summary):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"#### Records only in LTMC ({summary.only_in_ltmc})")
        st.caption("These records were in the source LTMC but not loaded into SAP")
        if summary.only_in_ltmc > 0:
            info_box(f"{summary.only_in_ltmc} records missing from post-load", "warning")

        st.markdown(f"#### Duplicate Keys in LTMC ({summary.duplicate_ltmc})")
        if summary.duplicate_ltmc_samples:
            st.dataframe(pd.DataFrame(summary.duplicate_ltmc_samples), use_container_width=True)
        else:
            st.success("No duplicate keys in LTMC")

    with c2:
        st.markdown(f"#### Records only in Post-Load ({summary.only_in_postload})")
        st.caption("These records are in SAP but not in the LTMC source file")
        if summary.only_in_postload > 0:
            info_box(f"{summary.only_in_postload} extra records in post-load", "info")

        st.markdown(f"#### Duplicate Keys in Post-Load ({summary.duplicate_postload})")
        if summary.duplicate_postload_samples:
            st.dataframe(pd.DataFrame(summary.duplicate_postload_samples), use_container_width=True)
        else:
            st.success("No duplicate keys in post-load")


def _render_exports(summary, ltmc_name, pl_name, sap_object, mapping):
    st.markdown("### Download Reports")
    c1, c2, c3 = st.columns(3)

    with c1:
        xlsx = generate_excel_report(summary, ltmc_name, pl_name, sap_object, mapping)
        st.download_button(
            "📥 Download Excel Report",
            data=xlsx,
            file_name=f"validation_report_{sap_object.replace(' ','_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with c2:
        html = generate_html_report(summary, ltmc_name, pl_name, sap_object)
        st.download_button(
            "🌐 Download HTML Report",
            data=html.encode("utf-8"),
            file_name=f"validation_report_{sap_object.replace(' ','_')}.html",
            mime="text/html",
            use_container_width=True,
        )

    with c3:
        # Mismatch CSV
        rows = []
        for fr in summary.field_results:
            for m in fr.mismatches:
                rows.append({"Field": fr.field_ltmc, **m})
        if rows:
            csv = pd.DataFrame(rows).to_csv(index=False)
            st.download_button(
                "📋 Download Mismatch CSV",
                data=csv,
                file_name="mismatches.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.success("No mismatches to export")
