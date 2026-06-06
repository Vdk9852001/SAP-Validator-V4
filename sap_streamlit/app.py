"""
SAP LTMC Migration Validator — Streamlit App
============================================
Upload Source LTMC file + Post-Load Extract, detect SAP object,
map fields with AI assistance, validate and download reports.

Run:  streamlit run app.py
"""
import streamlit as st
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

st.set_page_config(
    page_title="SAP LTMC Validator",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Global */
[data-testid="stApp"] { background: #f8fafc; }
[data-testid="stSidebar"] { background: #1a1f36; }
[data-testid="stSidebar"] * { color: #c7d2fe !important; }
[data-testid="stSidebar"] .stButton button {
    background: #4f46e5; color: #fff; border: none; border-radius: 8px;
    padding: 10px; font-weight: 600; width: 100%;
}
[data-testid="stSidebar"] .stButton button:hover { background: #4338ca; }
h1 { color: #1a1f36; font-weight: 800; }
h2, h3 { color: #374151; }
.stButton > button[kind="primary"] {
    background: #4f46e5; color: #fff; font-weight: 700;
    border-radius: 8px; padding: 10px 24px;
}
.stButton > button[kind="primary"]:hover { background: #4338ca; }
.stDataFrame { border-radius: 8px; }
.stExpander { border: 1px solid #e5e7eb; border-radius: 8px; }
div[data-testid="metric-container"] {
    background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
    padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
</style>
""", unsafe_allow_html=True)

# ── Session state init ─────────────────────────────────────────────────────────
for key in ["step","ltmc_df","postload_df","field_mappings","final_field_map",
            "join_keys","key_map","validation_summary","detected_object"]:
    if key not in st.session_state:
        st.session_state[key] = None if key != "step" else 1

# ── Sidebar navigation ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 🔷 SAP LTMC Validator")
    st.markdown("---")

    steps = [
        (1, "📤 Upload Files"),
        (2, "🗺️ Map Fields"),
        (3, "⚙️ Run Validation"),
        (4, "📊 Results"),
    ]
    current_step = st.session_state["step"]
    for step_num, step_label in steps:
        done   = step_num < current_step
        active = step_num == current_step
        prefix = "✅ " if done else "▶ " if active else "   "
        weight = "font-weight:700" if active else ""
        if st.button(
            f"{prefix}{step_label}",
            key=f"nav_{step_num}",
            disabled=(step_num > current_step and not _step_reachable(step_num)),
        ):
            if step_num <= current_step:
                st.session_state["step"] = step_num
                st.rerun()

    st.markdown("---")
    st.caption("Validation Direction:")
    st.markdown("**LTMC → Expected**")
    st.markdown("**Post-Load → Actual**")

    st.markdown("---")
    # Quick reset
    if st.button("🔄 Start Over"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    # Memory stats
    st.markdown("---")
    st.caption("Mapping Memory")
    from core.mapping_memory import get_all_memories
    memories = get_all_memories()
    st.caption(f"{len(memories)} saved mappings")
    if memories and st.button("🗑 Clear Memory"):
        from core.mapping_memory import clear_memory
        clear_memory()
        st.rerun()


def _step_reachable(step_num: int) -> bool:
    if step_num == 2:
        return bool(st.session_state.get("ltmc_df") is not None and
                    st.session_state.get("postload_df") is not None)
    if step_num == 3:
        return bool(st.session_state.get("final_field_map") and
                    st.session_state.get("join_keys"))
    if step_num == 4:
        return bool(st.session_state.get("validation_summary"))
    return True


# ── Step header ────────────────────────────────────────────────────────────────
STEP_TITLES = {
    1: ("Step 1 — Upload Files",        "Upload Source LTMC and Post-Load Extract"),
    2: ("Step 2 — Map Fields",          "Detect SAP object, map fields, select join keys"),
    3: ("Step 3 — Run Validation",      "Configure settings and run comparison"),
    4: ("Step 4 — Results & Reports",   "Dashboard, drill-down, and export"),
}

title, subtitle = STEP_TITLES.get(current_step, ("SAP LTMC Validator", ""))
st.title(title)
st.caption(subtitle)

# Progress bar
step_labels = ["Upload", "Map Fields", "Run", "Results"]
progress = (current_step - 1) / (len(step_labels) - 1)
st.progress(progress)
st.markdown(
    " → ".join(
        f"**{lbl}**" if i+1 == current_step else lbl
        for i, lbl in enumerate(step_labels)
    )
)
st.markdown("---")

# ── Page routing ───────────────────────────────────────────────────────────────
from ui.upload_page     import render as render_upload
from ui.mapping_page    import render as render_mapping
from ui.validation_page import render as render_validation
from ui.dashboard_page  import render as render_dashboard

if current_step == 1:
    render_upload()
elif current_step == 2:
    render_mapping()
elif current_step == 3:
    render_validation()
elif current_step == 4:
    render_dashboard()
