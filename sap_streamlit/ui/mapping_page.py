"""Steps 3-5 — Object detection, field mapping review and join key selection."""
import streamlit as st
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.object_detector  import detect_object
from core.ai_mapping        import suggest_mappings_ai, suggest_mappings_fallback
from core.mapping_memory    import get_remembered_mapping, save_bulk, get_all_memories
from core.key_detector      import detect_composite_key
from ui.components          import section_header, info_box, status_pill


def render():
    ltmc_df    = st.session_state.get("ltmc_df")
    postload_df = st.session_state.get("postload_df")
    if ltmc_df is None or postload_df is None:
        st.warning("Please upload both files first.")
        return

    ltmc_cols    = list(ltmc_df.columns)
    postload_cols = list(postload_df.columns)

    # ── Step 3: Object Detection ──────────────────────────────────────────
    section_header("🔍 SAP Object Detection")
    det = detect_object(ltmc_cols, postload_cols)
    st.session_state.setdefault("detected_object", det["object"])

    c1, c2, c3 = st.columns([2,1,2])
    with c1:
        obj = st.text_input("Detected SAP Object (editable)",
                            value=st.session_state.get("detected_object", det["object"]),
                            key="sap_object_input")
        st.session_state["detected_object"] = obj
    with c2:
        conf_color = {"High":"#16a34a","Medium":"#d97706","Low":"#dc2626"}.get(det["confidence"],"#6b7280")
        st.markdown(
            f'<div style="margin-top:28px;background:{conf_color}22;color:{conf_color};'
            f'border-radius:8px;padding:8px 12px;font-weight:600">'
            f'{det["confidence"]} confidence</div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(f"**Description:** {det['description']}")
        if det["matched_fields"]:
            st.caption(f"Key fields found: {', '.join(det['matched_fields'][:6])}")

    st.divider()

    # ── Step 4: Field Mapping ─────────────────────────────────────────────
    section_header("🗺️ Field Mapping Review",
                   "Review and correct AI-suggested mappings between LTMC and post-load fields")

    use_ai = st.toggle("Use AI for field mapping (Claude)", value=True, key="use_ai_toggle")

    if st.button("🔄 Generate / Refresh Mapping", type="primary"):
        with st.spinner("Generating field mappings..."):
            _generate_mappings(ltmc_cols, postload_cols, obj,
                               st.session_state.get("postload_samples", {}), use_ai)

    if "field_mappings" not in st.session_state or not st.session_state["field_mappings"]:
        _generate_mappings(ltmc_cols, postload_cols, obj,
                           st.session_state.get("postload_samples", {}), use_ai)

    _render_mapping_table(postload_cols, obj)

    st.divider()

    # ── Step 5: Join Key Selection ────────────────────────────────────────
    section_header("🔑 Join Key Selection",
                   "Select the fields that uniquely identify each record")

    _render_join_keys(ltmc_cols, postload_cols, obj)

    # Navigation
    st.divider()
    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← Back to Upload", use_container_width=True):
            st.session_state["step"] = 1
            st.rerun()
    with col_next:
        can_proceed = bool(st.session_state.get("join_keys") and
                           st.session_state.get("final_field_map"))
        if st.button("→ Run Validation", type="primary",
                     disabled=not can_proceed, use_container_width=True):
            st.session_state["step"] = 3
            st.rerun()
        if not can_proceed:
            st.caption("⚠️ Select at least one join key and review field mapping first")


def _generate_mappings(ltmc_cols, postload_cols, sap_object, samples, use_ai):
    existing = st.session_state.get("field_mappings", [])
    existing_map = {m["ltmc_col"]: m for m in existing}
    mappings = []

    # Apply memory first
    for col in ltmc_cols:
        remembered = get_remembered_mapping(col, sap_object)
        if remembered and remembered in postload_cols:
            mappings.append({
                "ltmc_col": col,
                "suggested_postload_col": remembered,
                "confidence": 0.99,
                "method": "memory",
                "explanation": "Saved from previous session",
                "user_override": remembered,
            })
        else:
            mappings.append(existing_map.get(col, {"ltmc_col": col,
                "suggested_postload_col": None, "confidence": 0.0,
                "method": "pending", "explanation": ""}))

    # Run AI/fallback for unmapped
    unmapped = [m["ltmc_col"] for m in mappings if not m.get("suggested_postload_col")]
    if unmapped:
        if use_ai:
            suggestions = suggest_mappings_ai(unmapped, postload_cols, sap_object, samples)
        else:
            suggestions = suggest_mappings_fallback(unmapped, postload_cols, sap_object)
        sug_map = {s["ltmc_col"]: s for s in suggestions}
        for m in mappings:
            if not m.get("suggested_postload_col") and m["ltmc_col"] in sug_map:
                m.update(sug_map[m["ltmc_col"]])

    st.session_state["field_mappings"] = mappings
    # Build final map
    _rebuild_final_map()


def _render_mapping_table(postload_cols, sap_object):
    mappings = st.session_state.get("field_mappings", [])
    if not mappings:
        return

    none_opt   = ["— unmapped —"]
    pl_options = none_opt + postload_cols

    st.markdown("**Field Mapping Table** — change the Post-Load column to correct any mapping:")

    # Headers
    hc = st.columns([2,2,2,1,2])
    for h, col in zip(["LTMC Field","Post-Load Column","AI Suggestion","Conf","Method"], hc):
        col.markdown(f"**{h}**")
    st.markdown("---")

    changed = False
    for i, m in enumerate(mappings):
        ltmc_col = m["ltmc_col"]
        sugg     = m.get("suggested_postload_col")
        current  = m.get("user_override", sugg) or none_opt[0]
        if current not in pl_options:
            current = none_opt[0]

        conf     = m.get("confidence", 0)
        method   = m.get("method", "")
        conf_col = "#16a34a" if conf >= 0.9 else "#d97706" if conf >= 0.6 else "#dc2626"

        c1,c2,c3,c4,c5 = st.columns([2,2,2,1,2])
        c1.markdown(f"`{ltmc_col}`")
        sel = c2.selectbox("", pl_options, index=pl_options.index(current),
                           key=f"map_{i}_{ltmc_col}", label_visibility="collapsed")
        c3.markdown(f"`{sugg or '—'}`")
        c4.markdown(
            f'<span style="color:{conf_col};font-weight:600">{int(conf*100)}%</span>',
            unsafe_allow_html=True,
        )
        badge = "🤖" if method=="ai" else "📚" if method=="memory" else "🔗" if method=="alias" else "≈"
        c5.markdown(f"{badge} {method}")

        if sel != current:
            changed = True
        m["user_override"] = None if sel == none_opt[0] else sel

    if changed:
        _rebuild_final_map()
        # Save corrections to memory
        corrected = {m["ltmc_col"]: m["user_override"]
                     for m in mappings if m.get("user_override")}
        save_bulk(corrected, sap_object, source="user")

    total = len(mappings)
    mapped = sum(1 for m in mappings if m.get("user_override"))
    info_box(f"Mapped {mapped} / {total} fields ({int(mapped/total*100)}% coverage)", "info")


def _rebuild_final_map():
    mappings = st.session_state.get("field_mappings", [])
    final = {
        m["ltmc_col"]: m["user_override"]
        for m in mappings
        if m.get("user_override")
    }
    st.session_state["final_field_map"] = final


def _render_join_keys(ltmc_cols, postload_cols, sap_object):
    # Auto-suggest
    try:
        import pandas as pd
        ltmc_sample = st.session_state["ltmc_df"].head(500)
        pl_sample   = st.session_state["postload_df"].head(500)

        # Only use columns common to both (via final map)
        fmap = st.session_state.get("final_field_map", {})
        common_ltmc = [c for c in ltmc_cols if fmap.get(c) in postload_cols or c in postload_cols]
        if len(common_ltmc) >= 2:
            kd = detect_composite_key(
                ltmc_sample[[c for c in common_ltmc if c in ltmc_sample.columns]].rename(
                    columns={c: c for c in common_ltmc}
                ),
                pl_sample,
                object_name=sap_object,
            )
            suggested_keys = kd.join_keys
        else:
            suggested_keys = []
    except Exception:
        suggested_keys = []

    if suggested_keys:
        info_box(f"Auto-suggested join keys: {' + '.join(suggested_keys)}", "info")

    # User selection — join keys are LTMC column names
    selected_keys = st.multiselect(
        "Select Join Keys (LTMC field names)",
        options=ltmc_cols,
        default=st.session_state.get("join_keys", suggested_keys[:3] if suggested_keys else []),
        key="join_key_select",
        help="These fields will be used to match LTMC records to post-load records",
    )
    st.session_state["join_keys"] = selected_keys

    if selected_keys:
        # Show uniqueness preview
        fmap = st.session_state.get("final_field_map", {})
        ltmc_df = st.session_state["ltmc_df"]
        pl_df   = st.session_state["postload_df"]

        key_cols_ltmc = [k for k in selected_keys if k in ltmc_df.columns]
        if key_cols_ltmc:
            ck = ltmc_df[key_cols_ltmc].astype(str).agg("||".join, axis=1)
            unique_pct = round(ck.nunique() / max(len(ck), 1) * 100, 1)
            dup_count  = int((ck.duplicated(keep=False)).sum())
            c1,c2,c3 = st.columns(3)
            c1.metric("LTMC Records", len(ltmc_df))
            c2.metric("Unique Key Combinations", ck.nunique(),
                      help="Higher = better key selection")
            c3.metric("Duplicate Keys in LTMC", dup_count,
                      delta=f"-{dup_count}" if dup_count else None,
                      delta_color="inverse")

        # Join key postload mapping
        st.markdown("**Post-load column for each join key:**")
        key_map = {}
        fmap = st.session_state.get("final_field_map", {})
        for jk in selected_keys:
            default_pl = fmap.get(jk, jk if jk in postload_cols else None)
            options = [c for c in postload_cols]
            idx = options.index(default_pl) if default_pl in options else 0
            sel = st.selectbox(
                f"LTMC `{jk}` maps to post-load column:",
                options, index=idx, key=f"jkmap_{jk}"
            )
            key_map[jk] = sel
        st.session_state["key_map"] = key_map
