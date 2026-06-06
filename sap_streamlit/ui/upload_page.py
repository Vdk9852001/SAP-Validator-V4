"""Step 1 & 2 — File upload and sheet/header detection."""
import streamlit as st
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ltmc_parser     import parse_ltmc_xml, get_sheet_summary
from core.post_load_parser import read_postload, list_sheets, get_sample_values
from ui.components         import section_header, info_box


def render():
    section_header("📤 Upload Files",
                   "Upload your SAP LTMC source file and the S/4HANA post-load extract")

    col1, col2 = st.columns(2)

    # ── LTMC Source File ──────────────────────────────────────────────────
    with col1:
        st.markdown("#### Source LTMC File")
        st.caption("The file prepared for upload into SAP using LTMC / Migration Cockpit")
        ltmc_file = st.file_uploader(
            "Upload LTMC Template",
            type=["xlsx","xls","xml","csv"],
            key="ltmc_upload",
            help="Excel, XML (SpreadsheetML) or CSV file from the SAP Migration Cockpit",
        )
        if ltmc_file:
            _handle_ltmc_upload(ltmc_file)

    # ── Post-Load Extract ─────────────────────────────────────────────────
    with col2:
        st.markdown("#### Post-Load Extract File")
        st.caption("Data extracted from SAP after the LTMC load was completed")
        postload_file = st.file_uploader(
            "Upload Post-Load Extract",
            type=["xlsx","xls","csv"],
            key="postload_upload",
            help="Excel or CSV export from SAP after migration",
        )
        if postload_file:
            _handle_postload_upload(postload_file)

    # ── Navigation ────────────────────────────────────────────────────────
    if st.session_state.get("ltmc_df") is not None and \
       st.session_state.get("postload_df") is not None:
        st.success("✅ Both files loaded. Proceed to object detection.")
        if st.button("→ Next: Detect SAP Object", type="primary", use_container_width=True):
            st.session_state["step"] = 2
            st.rerun()


def _handle_ltmc_upload(file):
    fname = file.name.lower()
    try:
        if fname.endswith(".xml"):
            import tempfile, os
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
                tmp.write(file.read())
                tmp_path = tmp.name
            sheets = parse_ltmc_xml(tmp_path)
            os.unlink(tmp_path)
            summary = get_sheet_summary(sheets)
            if not sheets:
                info_box("No data sheets found in XML", "error")
                return

            st.markdown("**Sheets found:**")
            for s in summary:
                st.markdown(
                    f"- **{s['sheet_name']}** — {s['row_count']} rows × {s['col_count']} cols "
                    f"({s['table_name']})"
                )

            sheet_names = list(sheets.keys())
            chosen = st.selectbox("Select sheet to validate", sheet_names, key="ltmc_sheet_sel")
            df = sheets[chosen].copy()
            st.session_state["ltmc_df"]         = df
            st.session_state["ltmc_filename"]   = file.name
            st.session_state["ltmc_all_sheets"] = sheets
            st.session_state["ltmc_sheet"]      = chosen
            st.session_state["ltmc_table_name"] = next(
                (s["table_name"] for s in summary if s["sheet_name"] == chosen), ""
            )
            st.dataframe(df.head(5), use_container_width=True)

        else:
            import tempfile, os
            suffix = ".xlsx" if "xlsx" in fname else ".xls" if "xls" in fname else ".csv"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file.read())
                tmp_path = tmp.name

            available_sheets = list_sheets(tmp_path)
            chosen = st.selectbox("Select sheet", available_sheets, key="ltmc_sheet_sel_xl")
            hr_opt = st.number_input("Header row (0-indexed)", 0, 20, 0, key="ltmc_hr")
            df, sheet, hr = read_postload(tmp_path, chosen, int(hr_opt))
            os.unlink(tmp_path)

            st.session_state["ltmc_df"]       = df
            st.session_state["ltmc_filename"] = file.name
            st.session_state["ltmc_sheet"]    = sheet
            st.dataframe(df.head(5), use_container_width=True)

        info_box(f"LTMC loaded: {len(st.session_state['ltmc_df'])} rows, "
                 f"{len(st.session_state['ltmc_df'].columns)} columns", "success")

    except Exception as e:
        info_box(f"Error reading LTMC file: {e}", "error")


def _handle_postload_upload(file):
    fname = file.name.lower()
    try:
        import tempfile, os
        suffix = ".xlsx" if "xlsx" in fname else ".xls" if "xls" in fname else ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file.read())
            tmp_path = tmp.name

        available_sheets = list_sheets(tmp_path)
        chosen = st.selectbox("Select sheet", available_sheets, key="pl_sheet_sel")
        hr_opt = st.number_input("Header row (0-indexed)", 0, 20, 0, key="pl_hr")
        df, sheet, hr = read_postload(tmp_path, chosen, int(hr_opt))
        os.unlink(tmp_path)

        st.session_state["postload_df"]       = df
        st.session_state["postload_filename"] = file.name
        st.session_state["postload_sheet"]    = sheet
        st.session_state["postload_samples"]  = get_sample_values(df)
        st.dataframe(df.head(5), use_container_width=True)
        info_box(f"Post-load loaded: {len(df)} rows, {len(df.columns)} columns", "success")

    except Exception as e:
        info_box(f"Error reading post-load file: {e}", "error")
