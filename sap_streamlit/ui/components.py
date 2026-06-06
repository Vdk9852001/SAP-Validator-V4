"""Shared Streamlit UI components."""
import streamlit as st

STATUS_COLORS = {"PASS":"#16a34a","WARNING":"#d97706","FAIL":"#dc2626","NO DATA":"#6b7280"}

def status_pill(status: str):
    color = STATUS_COLORS.get(status, "#6b7280")
    st.markdown(
        f'<div style="display:inline-block;background:{color};color:#fff;'
        f'padding:5px 18px;border-radius:20px;font-weight:700;font-size:15px">'
        f'{status}</div>',
        unsafe_allow_html=True,
    )

def metric_card(label: str, value, color: str = "#4f46e5", suffix: str = ""):
    st.markdown(
        f'<div style="background:#f4f6fa;border-radius:10px;padding:14px 18px;'
        f'text-align:center;border:1px solid #e5e7eb">'
        f'<div style="font-size:28px;font-weight:700;color:{color}">{value}{suffix}</div>'
        f'<div style="font-size:12px;color:#6b7280;margin-top:4px">{label}</div></div>',
        unsafe_allow_html=True,
    )

def section_header(title: str, subtitle: str = ""):
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)
    st.divider()

def info_box(msg: str, kind: str = "info"):
    icons = {"info":"ℹ️","success":"✅","warning":"⚠️","error":"❌"}
    colors = {"info":"#dbeafe","success":"#dcfce7","warning":"#fef3c7","error":"#fee2e2"}
    st.markdown(
        f'<div style="background:{colors.get(kind,"#f3f4f6")};border-radius:8px;'
        f'padding:10px 14px;margin:6px 0;font-size:14px">'
        f'{icons.get(kind,"")} {msg}</div>',
        unsafe_allow_html=True,
    )

def step_indicator(steps: list, current: int):
    cols = st.columns(len(steps))
    for i, (col, step) in enumerate(zip(cols, steps)):
        done    = i < current
        active  = i == current
        bg      = "#4f46e5" if active else "#dcfce7" if done else "#f4f6fa"
        tc      = "#fff"    if active else "#16a34a" if done else "#6b7280"
        border  = "2px solid #4f46e5" if active else "2px solid #16a34a" if done else "1px solid #e5e7eb"
        col.markdown(
            f'<div style="background:{bg};border:{border};border-radius:8px;'
            f'padding:8px 4px;text-align:center;font-size:11px;color:{tc};font-weight:{"700" if active else "400"}">'
            f'{"✓ " if done else f"{i+1}. "}{step}</div>',
            unsafe_allow_html=True,
        )
