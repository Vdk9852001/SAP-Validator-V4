"""Step 6 — Validation settings and run."""
import streamlit as st
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.validation_engine import validate
from ui.components           import section_header, info_box


def render():
    section_header("⚙️ Validation Settings & Run")

    ltmc_df    = st.session_state.get("ltmc_df")
    postload_df = st.session_state.get("postload_df")
    join_keys  = st.session_state.get("join_keys", [])
    key_map    = st.session_state.get("key_map", {})
    field_map  = st.session_state.get("final_field_map", {})

    if not join_keys:
        st.warning("⚠️ No join keys selected. Go back to the Mapping step.")
        if st.button("← Back to Mapping"):
            st.session_state["step"] = 2
            st.rerun()
        return

    if not field_map:
        st.warning("⚠️ No fields mapped. Go back to the Mapping step.")
        return

    # Settings
    with st.expander("🔧 Validation Settings", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            case_sensitive = st.checkbox("Case-sensitive comparison", value=False)
        with c2:
            strip_zeros = st.checkbox("Strip leading zeros", value=False)
        with c3:
            max_mismatches = st.number_input("Max mismatch rows to capture",
                                              10, 5000, 500)

    # Summary of what will be validated
    with st.expander("📋 Validation Plan", expanded=True):
        st.markdown(f"**Join Keys:** {' + '.join(join_keys)}")
        st.markdown(f"**Fields to validate:** {len(field_map)}")
        rows = [{"LTMC Field": k, "Post-Load Field": v} for k,v in field_map.items()
                if k not in join_keys]
        if rows:
            st.dataframe(rows, use_container_width=True, height=200)

    col_back, col_run = st.columns([1,2])
    with col_back:
        if st.button("← Back to Mapping"):
            st.session_state["step"] = 2
            st.rerun()
    with col_run:
        if st.button("🚀 Run Validation", type="primary", use_container_width=True):
            _run_validation(ltmc_df, postload_df, join_keys, key_map,
                            field_map, case_sensitive, strip_zeros, int(max_mismatches))


def _run_validation(ltmc_df, postload_df, join_keys, key_map,
                    field_map, case_sensitive, strip_zeros, max_mismatches):
    with st.spinner("Running validation..."):
        try:
            summary = validate(
                ltmc_df=ltmc_df,
                postload_df=postload_df,
                join_keys=join_keys,
                field_map=field_map,
                key_map=key_map,
                case_sensitive=case_sensitive,
                strip_zeros=strip_zeros,
                max_mismatches=max_mismatches,
            )
            st.session_state["validation_summary"] = summary
            st.session_state["step"] = 4
            st.rerun()
        except ValueError as e:
            info_box(f"Validation error: {e}", "error")
        except Exception as e:
            info_box(f"Unexpected error: {e}", "error")
            raise
