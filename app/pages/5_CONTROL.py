import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import json
import copy
import streamlit as st
import pandas as pd

from app.agents.control_agent import run_control_agent, finalize_control_agent
from app.utils import memory, reporting, audit, auth
from app.utils.ui_helpers import render_sidebar_header, render_tab_quicknav, render_phase_end_date

# ============================================
# Helpers
# ============================================

def _get_current_result():
    if st.session_state.get("control_draft"):
        return "draft", st.session_state["control_draft"]
    if st.session_state.get("control_final"):
        return "final", st.session_state["control_final"]
    return "none", None

def _safe_get(d, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

# ============================================
# Page Config + CSS
# ============================================
st.set_page_config(page_title="CONTROL", layout="wide")
st.markdown("""
<style>
.stApp { background-color:#f4f8fb; color:#0f172a;
  font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial; }
section[data-testid="stSidebar"] { background-color:#ffffff; border-right:1px solid #e2e8f0; }
.card { background:#ffffff; border:1px solid #e2e8f0; border-radius:14px;
  padding:18px 20px; box-shadow:0 6px 18px rgba(15,23,42,0.08); margin-bottom:18px; }
h1,h2,h3 { color:#0f172a; }
.stButton>button { background:#ffffff; border:1px solid #cbd5e1; color:#0f172a;
  border-radius:10px; padding:8px 14px; font-weight:500; }
.stButton>button:hover { background:#e0f2fe; border-color:#38bdf8; }
div[data-baseweb="input"]>div, div[data-baseweb="textarea"]>div, div[data-baseweb="select"]>div {
  background:#ffffff !important; border:1px solid #cbd5e1 !important;
  border-radius:10px !important; color:#0f172a !important; }
/* ── Slanted Tabs (pseudo-element, no text transform) ── */
div[data-baseweb="tab-list"] {
    gap: 0 !important;
    background: #f4f8fb !important;
    border-bottom: 3px solid #16a34a !important;
    padding: 0 0 0 20px !important;
    align-items: flex-end !important;
    overflow: visible !important;
}
button[data-baseweb="tab"] {
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    color: #334155 !important;
    font-weight: 600 !important;
    font-style: normal !important;
    font-size: 0.85rem !important;
    letter-spacing: 0.015em !important;
    padding: 9px 30px !important;
    margin-right: -16px !important;
    position: relative !important;
    z-index: 1 !important;
    overflow: visible !important;
    transition: color 0.15s !important;
}
button[data-baseweb="tab"]::before {
    content: '' !important;
    position: absolute !important;
    top: 0 !important; right: 0 !important; bottom: 0 !important; left: 0 !important;
    background: #dde3ea !important;
    transform: skewX(-20deg) !important;
    z-index: -1 !important;
    transition: background 0.15s !important;
    border-radius: 3px 3px 0 0 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #ffffff !important;
    z-index: 3 !important;
    font-weight: 700 !important;
}
button[data-baseweb="tab"][aria-selected="true"]::before {
    background: #16a34a !important;
}
button[data-baseweb="tab"]:hover:not([aria-selected="true"]) {
    color: #0f172a !important;
    z-index: 2 !important;
}
button[data-baseweb="tab"]:hover:not([aria-selected="true"])::before {
    background: #c3cdd6 !important;
}
div[data-baseweb="tab-highlight"],
div[data-baseweb="tab-border"] { display: none !important; }
div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {
  border:1px solid #e2e8f0; border-radius:12px; background:#ffffff; }
</style>
""", unsafe_allow_html=True)

auth.require_login()
auth.render_user_badge()

# ============================================
# Session defaults
# ============================================
st.session_state.setdefault("active_project_id", None)
st.session_state.setdefault("control_draft", None)
st.session_state.setdefault("control_final", None)

# ============================================
# Sidebar
# ============================================
render_sidebar_header()
st.sidebar.divider()

active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")
    phases = ["define","measure","analyze","improve","control"]
    labels = ["DEF","MEA","ANA","IMP","CON"]
    icons  = []
    for ph in phases:
        f = memory._load_state(active_pid, ph, "final")
        d = memory._load_state(active_pid, ph, "draft")
        a = memory.get_phase_approval_status(active_pid, ph)
        if f and a.get("can_advance"):  icons.append("✅")
        elif f:                          icons.append("🔒")
        elif d:                          icons.append("🔄")
        else:                            icons.append("⬜")
    st.sidebar.caption(" | ".join([f"{icons[i]} {labels[i]}" for i in range(5)]))
    st.sidebar.divider()
    appr = memory.get_phase_approval_status(active_pid, "control")
    if appr.get("auto_advance"):
        st.sidebar.success("🚀 Control: Auto-advance")
    else:
        chmp = (appr.get("champion") or {}).get("action") or "pending"
        chmp_icon = "✅" if chmp=="approve" else "❌" if chmp=="reject" else "⏳"
        st.sidebar.markdown(f"**Control Gate:**  \n{chmp_icon} Champion")
        if appr.get("can_advance"):
            st.sidebar.success("🚀 Gate OPEN")
        else:
            st.sidebar.info("🔒 Gate LOCKED")
else:
    st.sidebar.warning("Belum ada project aktif.")
st.sidebar.divider()
# ============================================
# Gate checks
# ============================================
if not active_pid:
    st.title("🎛️ CONTROL Phase")
    st.info("Belum ada project aktif. Kembali ke **Main** untuk memilih project.")
    st.stop()

improve_final = memory.load_improve_final(active_pid)
if not improve_final:
    st.title("CONTROL Phase")
    st.warning("IMPROVE belum di-finalize. Selesaikan fase Improve terlebih dahulu.")
    st.stop()

analyze_final = memory.load_analyze_final(active_pid)
measure_final = memory.load_measure_final(active_pid)
define_final  = memory.load_define_final(active_pid)

# ============================================
# Auto-load
# ============================================
loaded_pid = st.session_state.get("_control_loaded_pid")
if active_pid and loaded_pid != active_pid:
    st.session_state["_control_loaded_pid"] = active_pid
    st.session_state["control_draft"]       = None
    st.session_state["control_final"]       = None

    draft = memory.load_control_draft(active_pid)
    final = memory.load_control_final(active_pid)
    if draft:
        st.session_state["control_draft"] = {
            "status":"draft","control_state":draft,
            "summary":draft.get("summary_md",""),"message":"Auto-loaded draft",
        }
    if final:
        st.session_state["control_final"] = {
            "status":"finalized","control_state":final,
            "summary":final.get("summary_md",""),"message":"Auto-loaded final",
        }

_proj_meta = memory.load_project_meta(active_pid) if active_pid else {}
_proj_path = _proj_meta.get("path","standard")

# ============================================
# Header
# ============================================
logo_path = "app/assets/logo3.jpg"
h_title, h_logo = st.columns([5,1])
with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)
with h_title:
    st.markdown("# DMAIC Copilot — CONTROL Phase")
    _path_label = "🟢 Quick Improvement Project" if _proj_path=="quick" else "🔵 Standard DMAIC Project"
    st.caption(f"Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC Methodology &nbsp;|&nbsp; {_path_label}")
st.divider()

_def_title_hdr = (
    (define_final or {}).get("inputs", {}).get("project_name", "")
    or (memory.load_define_draft(active_pid) or {}).get("inputs", {}).get("project_name", "")
    or st.session_state.get("active_project_name", "")
    or st.session_state.get("define_project_name_input", "")
)
if _def_title_hdr:
    st.markdown(f"<p style='font-size:1.05rem;font-weight:700;color:#0f172a;margin:0 0 4px 0;'>{_def_title_hdr}</p>", unsafe_allow_html=True)
h1, h2_col = st.columns([5,1])
with h1:
    st.caption(f"**Active Project:** `{active_pid}`")
with h2_col:
    _view_badge = "FINAL" if memory.load_control_final(active_pid) else "DRAFT"
    st.markdown(
        f"<div style='text-align:right'><span style='padding:6px 12px;border-radius:20px;"
        f"background:#0f5132;color:#d1e7dd;font-weight:600;'>{_view_badge}</span></div>",
        unsafe_allow_html=True,
    )

# ============================================
# Shared state
# ============================================
final_on_disk = memory.load_control_final(active_pid)
draft_on_disk = memory.load_control_draft(active_pid)
_has_final    = bool(final_on_disk)
_has_draft    = bool(draft_on_disk)
_appr_status  = memory.get_phase_approval_status(active_pid, "control")
_req_roles_ct = _appr_status.get("required_approvers", [])
_is_rejected  = any(
    (_appr_status.get(r) or {}).get("action") == "reject"
    for r in _req_roles_ct
)
_revise_key   = f"control_revise_mode_{active_pid}"
st.session_state.setdefault(_revise_key, False)
is_locked = _has_final and not st.session_state.get(_revise_key) and not _has_draft

_mon_key     = f"control_monitoring_{active_pid}"
_cp_key      = f"control_plan_{active_pid}"
_handover_key = f"control_handover_{active_pid}"
_sustain_key = f"control_sustain_{active_pid}"
_benefit_key = f"control_benefit_{active_pid}"
_closure_key = f"control_closure_{active_pid}"

_saved = (final_on_disk or draft_on_disk or {})
_saved_outs = _saved.get("outputs") or {}
for key, field in [
    (_mon_key,      "monitoring_reaction_plan"),
    (_cp_key,       "control_plan"),
    (_handover_key, "handover_protocol"),
    (_sustain_key,  "sustain_audit_protocol"),
    (_benefit_key,  "benefit_validation"),
    (_closure_key,  "project_closure_checklist"),
]:
    if key not in st.session_state and _saved_outs.get(field):
        st.session_state[key] = _saved_outs[field]

# Auto-load FINAL if nothing in session (fallback untuk page reload/session reset)
if _get_current_result()[0] == "none" and active_pid:
    _fb_final = memory.load_control_final(active_pid)
    if _fb_final:
        st.session_state["control_final"] = {
            "status": "finalized",
            "control_state": _fb_final,
            "summary": _fb_final.get("summary_md", ""),
            "message": "Auto-loaded FINAL from disk",
        }

# ============================================
# SINGLE-COLUMN LAYOUT WITH TABS
# ============================================
view, res = _get_current_result()
control_state = res["control_state"] if (res and res.get("control_state")) else None

_TAB_LABELS = [
    "✏️ Input", "📄 Report", "📈 Improved Performance",
    "📊 Monitoring & Reaction", "📋 Control Plan", "🤝 Handover",
]
tab_input, tab_report, tab_improved, tab_monitoring, tab_control_plan, tab_handover = st.tabs(_TAB_LABELS)
render_tab_quicknav(_TAB_LABELS)

# ============================================
# TAB: INPUT
# ============================================
with tab_input:

    # ── Revision Actions ──
    if _has_final and _is_rejected and not _has_draft and not st.session_state.get(_revise_key):
        if st.button("✏️ Revise Final", key="control_revise_btn"):
            st.session_state[_revise_key]     = True
            st.session_state["control_draft"] = None
            st.rerun()
    elif _has_draft:
        if st.button("🧹 Discard Draft", key="control_discard_btn"):
            memory.delete_control_draft(active_pid)
            st.session_state["control_draft"] = None
            st.session_state["control_final"] = None
            st.session_state[_revise_key]     = False
            if final_on_disk:
                st.session_state["control_final"] = {
                    "status":"finalized","control_state":final_on_disk,
                    "summary":final_on_disk.get("summary_md",""),"message":"Reverted to final",
                }
            st.rerun()

    # ── Step 1: Upstream Context ──
    st.markdown("### Step 1 — Solution & Root Causes (from Improve + Analyze)")
    imp_outs = (improve_final or {}).get("outputs") or {}
    ana_outs = (analyze_final or {}).get("outputs") or {}
    mea_outs = (measure_final or {}).get("outputs") or {}
    def_outs = (define_final  or {}).get("outputs") or {}
    perf_sum = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}
    target_info = (mea_outs.get("process_performance_summary") or {}).get("target_info") or {}

    _y_var    = mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "-"
    # Improve menyimpan 'selected_solutions' (LIST). Join untuk ringkasan.
    _sel_sols = imp_outs.get("selected_solutions") or []
    _solution = "; ".join(
        str(s.get("solution", "")).strip() for s in _sel_sols
        if isinstance(s, dict) and str(s.get("solution", "")).strip()
    ) or "-"
    _baseline = perf_sum.get("mean")
    if _baseline is None:
        _cib = mea_outs.get("chart_image_baseline") or {}
        _baseline = _cib.get("average") if isinstance(_cib, dict) else None
    _target   = target_info.get("value")
    _tdir     = target_info.get("direction","<=")

    st.markdown(
        f"""<div style='background:#fffbeb;border:1px solid #fbbf24;border-radius:12px;
        padding:14px 18px;margin-bottom:12px;'>
        <b>🎯 Y Variable:</b> {_y_var}
        &nbsp;|&nbsp; Baseline: <b>{_baseline if _baseline is not None else '-'}</b>
        &nbsp;|&nbsp; Target: <b>{_tdir} {_target if _target is not None else '-'}</b><br>
        <b>✅ Solution:</b> {_solution}
        </div>""",
        unsafe_allow_html=True,
    )

    rca_table = ana_outs.get("root_cause_summary_table") or []
    confirmed_rc = [r for r in rca_table if "Yes" in str(r.get("is_root_cause",""))]

    # Root cause terkait (dari Analyze) — tampil DULU
    if confirmed_rc:
        st.markdown("**🎯 Confirmed Root Causes terkait (dari Analyze):**")
        st.dataframe(pd.DataFrame([{
            "Xn":         r.get("xn", ""),
            "Root Cause": r.get("root_cause", ""),
        } for r in confirmed_rc]), use_container_width=True, hide_index=True)

    # Solusi yang diimplementasi (dari Improve) — setelah root cause
    if _sel_sols:
        st.markdown("**💡 Solusi yang diimplementasi (dari Improve):**")
        st.dataframe(pd.DataFrame([{
            "Solution": s.get("solution", ""),
            "CTQ/CTB":  s.get("ctq_ctb_impacted", ""),
            "Catatan":  s.get("comments", ""),
        } for s in _sel_sols if isinstance(s, dict)]), use_container_width=True, hide_index=True)
    else:
        st.info("Belum ada selected solution dari fase Improve.")

    _should_be = imp_outs.get("should_be_process", "")
    if _should_be:
        with st.expander("🔄 Should-be process (dari Improve)", expanded=False):
            st.markdown(_should_be)

    st.divider()

    if _proj_path != "quick":
        # ── Step 2: Monitoring & Reaction Plan ──
        st.markdown("### Step 2 — Monitoring & Reaction Plan")
        st.caption("Agent propose monitoring plan per KPI termasuk RC tracing template. Review dan edit.")

        col_mon_propose, col_mon_save = st.columns([1,1])
        with col_mon_propose:
            if st.button("🤖 Generate Monitoring Plan", key=f"mon_gen_{active_pid}", disabled=is_locked):
                st.session_state.pop(_mon_key, None)
                st.rerun()

        if _mon_key not in st.session_state and not is_locked:
            with st.spinner("Agent menyusun monitoring & reaction plan..."):
                from app.agents.control_agent import _build_monitoring_reaction_plan_llm, _extract_upstream_context
                _upstream_ctrl = _extract_upstream_context(define_final, measure_final, analyze_final, improve_final)
                st.session_state[_mon_key] = _build_monitoring_reaction_plan_llm(_upstream_ctrl, {})

        _mon_list = st.session_state.get(_mon_key, [])
        if _mon_list:
            # Display main monitoring table
            _mon_df = pd.DataFrame([{
                "KPI":           m.get("kpi",""),
                "Target":        m.get("target_value",""),
                "Frequency":     m.get("measurement_frequency",""),
                "Who Collects":  m.get("who_collects",""),
                "Who Analyzes":  m.get("who_analyzes",""),
                "Who Reports":   m.get("who_sends_report",""),
                "Who Acts":      m.get("who_takes_action",""),
                "Threshold":     m.get("deviation_threshold",""),
            } for m in _mon_list])
            st.dataframe(_mon_df, use_container_width=True, hide_index=True)

            # Reaction steps per KPI
            st.markdown("**Reaction Plan (Penelusuran Root Cause):**")
            for m in _mon_list:
                with st.expander(f"🔁 Reaction Plan: {m.get('kpi','')}", expanded=False):
                    st.caption(f"Pemicu: {m.get('deviation_threshold','')}")
                    react_steps = m.get("reaction_steps") or []
                    for step in react_steps:
                        _q = step.get("question","")
                        _rc = step.get("rc_ref","")
                        _label = f"Langkah {step.get('step','')}"
                        if _q or _rc:
                            _label += f" ({' · '.join([x for x in [_rc, _q] if x])})"
                        st.markdown(f"**{_label}:** {step.get('check','')}")
                        st.markdown(f"  - Jika YA → {step.get('if_yes','')}")
                        st.markdown(f"  - Jika TIDAK → {step.get('if_no','')}")
                    _esc = m.get("escalation_actions") or []
                    if _esc:
                        st.markdown("**Jika solusi gagal / semua RC sudah dicek → Eskalasi:**")
                        for _a in _esc:
                            st.markdown(f"  - {_a}")
                    st.caption(m.get("control_chart_note",""))

            with col_mon_save:
                if st.button("💾 Konfirmasi Monitoring Plan", key=f"mon_save_{active_pid}", disabled=is_locked):
                    st.success("Monitoring plan dikonfirmasi.")
        else:
            st.info("Klik 'Generate Monitoring Plan' untuk mendapatkan usulan dari agent.")

        st.divider()

    if _proj_path != "quick":
        # ── Step 3: Control Plan ──
        st.markdown("### Step 3 — Control Plan per CTQ")
        col_cp_gen, col_cp_save = st.columns([1,1])
        with col_cp_gen:
            if st.button("🤖 Generate Control Plan", key=f"cp_gen_{active_pid}", disabled=is_locked):
                st.session_state.pop(_cp_key, None)
                st.rerun()

        if _cp_key not in st.session_state and not is_locked:
            with st.spinner("Agent menyusun control plan..."):
                from app.agents.control_agent import _build_control_plan_llm, _extract_upstream_context
                _upstream_cp = _extract_upstream_context(define_final, measure_final, analyze_final, improve_final)
                st.session_state[_cp_key] = _build_control_plan_llm(_upstream_cp, {})

        _cp_list = st.session_state.get(_cp_key, [])
        if _cp_list:
            _cp_df = pd.DataFrame([{
                "CTQ":         c.get("ctq",""),
                "Metric":      c.get("metric",""),
                "Spec Limit":  c.get("spec_limit",""),
                "Method":      c.get("control_method",""),
                "Frequency":   c.get("measurement_frequency",""),
                "Owner":       c.get("owner",""),
                "System":      c.get("recording_system",""),
            } for c in _cp_list])
            _cp_edit = st.data_editor(
                _cp_df, num_rows="dynamic", use_container_width=True,
                key=f"cp_editor_{active_pid}", disabled=is_locked,
            )
            with col_cp_save:
                if st.button("💾 Simpan Control Plan", key=f"cp_save_{active_pid}", disabled=is_locked):
                    if isinstance(_cp_edit, pd.DataFrame):
                        st.session_state[_cp_key] = [{
                            "ctq":                   r.get("CTQ",""),
                            "metric":                r.get("Metric",""),
                            "spec_limit":            r.get("Spec Limit",""),
                            "control_method":        r.get("Method",""),
                            "measurement_frequency": r.get("Frequency",""),
                            "owner":                 r.get("Owner",""),
                            "recording_system":      r.get("System",""),
                        } for r in _cp_edit.to_dict(orient="records")]
                    st.success("Control plan tersimpan.")
                    st.rerun()
        else:
            st.info("Generate control plan setelah monitoring plan tersedia.")
    else:
        st.caption("⚡ **Quick Path** — Control Plan per CTQ formal di-skip. Monitoring plan & control chart sudah cukup.")

    st.divider()

    # Quick Path: handover fields default kosong (Step 4 disembunyikan)
    _ho_setup = True
    _kpis_monitoring_note = ""
    _training_note = ""
    _open_issues_note = ""
    _open_issues_responsible = ""
    _handover_comments = ""
    _responsible = ""
    _process_owner = ""
    if _proj_path != "quick":
        # ── Step 4: Handover Protocol ──
        st.markdown("### Step 4 — Handover Protocol")
        st.caption("Isi sesuai template handover protocol. Wajib ditandatangani oleh Project Leader DAN Process Owner.")

        _ho_setup_choice = st.selectbox(
            "1. Apakah should-be process sudah disiapkan (set up)?",
            options=["Yes", "No"],
            key=f"ctrl_ho_setup_{active_pid}",
            disabled=is_locked,
        )
        _ho_setup = (_ho_setup_choice == "Yes")

        _kpis_monitoring_note = st.text_area(
            "2. Apakah new process KPIs sudah disepakati & continuous monitoring terjamin? (jelaskan KPI)",
            key=f"ctrl_ho_kpis_{active_pid}",
            placeholder=(
                "Contoh:\n"
                "Yes, lihat Monitoring–Reaction plan. Track KPI:\n"
                "1. Throughput Neutralization process (Ton/hour)\n"
                "2. Output HPBE (Ton/month)"
            ),
            height=100, disabled=is_locked,
        )
        _training_note = st.text_area(
            "3. Siapa dan apa yang harus dilatih?",
            key=f"ctrl_ho_training_{active_pid}",
            placeholder="Contoh: Neutralization Process Operator — new process flow & reaction plan",
            height=80, disabled=is_locked,
        )
        _open_issues_note = st.text_area(
            "4. Apa saja open issues?",
            key=f"ctrl_ho_issues_{active_pid}",
            placeholder="Contoh: Filtration Process jadi constraint berikutnya, throughput 4.1 Ton/h...",
            height=80, disabled=is_locked,
        )
        _open_issues_responsible = st.text_input(
            "5. Siapa penanggung jawab open issues?",
            key=f"ctrl_ho_issues_resp_{active_pid}",
            placeholder="Contoh: Marla Ruby Quintine (as BB Project)",
            disabled=is_locked,
        )
        _handover_comments = st.text_area(
            "6. Comments / catatan tambahan?",
            key=f"ctrl_ho_comments_{active_pid}",
            placeholder="Contoh: akan ditangani sebagai 3rd gen project, timeline Juni–Maret 2019",
            height=60, disabled=is_locked,
        )
        _responsible = st.text_input(
            "7. Responsible? (penanggung jawab proses)",
            key=f"ctrl_ho_responsible_{active_pid}",
            placeholder="Contoh: Production Manager",
            disabled=is_locked,
        )
        # Process Owner (penanggung jawab proses)
        _process_owner = _responsible

        # ── Upload Handover Protocol Confirmation Sheet (ditandatangani Process Owner) ──
        st.markdown("**📄 Handover Protocol Confirmation Sheet (ditandatangani Process Owner):**")
        import base64 as _b64_ho
        _ho_b64_k  = f"ctrl_ho_conf_b64_{active_pid}"
        _ho_name_k = f"ctrl_ho_conf_name_{active_pid}"
        _ho_type_k = f"ctrl_ho_conf_type_{active_pid}"
        _ho_wip = memory.load_control_wip(active_pid) or {}
        if _ho_b64_k not in st.session_state:
            st.session_state[_ho_b64_k]  = _ho_wip.get("handover_conf_b64", "")
            st.session_state[_ho_name_k] = _ho_wip.get("handover_conf_name", "")
            st.session_state[_ho_type_k] = _ho_wip.get("handover_conf_type", "")
        _ho_up = st.file_uploader(
            "📤 Upload confirmation sheet (signed)",
            type=["xlsx", "xls", "csv", "pdf", "png", "jpg", "jpeg"],
            key=f"ctrl_ho_conf_uploader_{active_pid}",
            disabled=is_locked,
        )
        if _ho_up is not None:
            _ho_bytes = _ho_up.read()
            st.session_state[_ho_b64_k]  = _b64_ho.b64encode(_ho_bytes).decode("utf-8")
            st.session_state[_ho_name_k] = _ho_up.name
            st.session_state[_ho_type_k] = _ho_up.type or ""
            _ho_wip_save = memory.load_control_wip(active_pid) or {}
            _ho_wip_save["handover_conf_b64"]  = st.session_state[_ho_b64_k]
            _ho_wip_save["handover_conf_name"] = _ho_up.name
            _ho_wip_save["handover_conf_type"] = st.session_state[_ho_type_k]
            memory.save_control_wip(active_pid, _ho_wip_save)
        if st.session_state.get(_ho_name_k):
            st.caption(f"📎 Tersimpan: {st.session_state[_ho_name_k]}")
            if str(st.session_state.get(_ho_type_k, "")).startswith("image/"):
                try:
                    st.image(_b64_ho.b64decode(st.session_state[_ho_b64_k]),
                             width=700)
                except Exception:
                    pass
    else:
        st.caption("⚡ **Quick Path** — Handover Protocol formal di-skip. Fokus pada monitoring & control chart.")

    st.divider()

    # ── Step 5: Benefit Validation ──
    st.markdown("### Step 5 — Benefit Validation")
    st.caption("Diisi oleh Project Leader bersama Finance/Controller.")

    # Referensi estimasi benefit dari fase Define
    _def_benefit = def_outs.get("benefit_estimate") or {}
    _def_benefit_str = (def_outs.get("estimated_benefit") or "").strip()
    _has_def_benefit = bool(_def_benefit_str) or bool(_def_benefit)
    with st.expander("📊 Estimasi Benefit dari fase Define", expanded=False):
        if not _has_def_benefit:
            st.info("Tidak ada estimasi benefit yang tercatat di fase Define.")
        else:
            if _def_benefit_str:
                st.markdown(f"**Estimasi (ringkas):** {_def_benefit_str}")
            if isinstance(_def_benefit, dict):
                _bp = _def_benefit.get("benefit_potentials")
                if _bp:
                    st.markdown(f"**Benefit Potentials:** {_bp}")
                _cm = _def_benefit.get("calculation_method")
                if _cm:
                    st.markdown(f"**Metode Perhitungan:** {_cm}")
                _kpi_tbl = _def_benefit.get("kpi_table") or []
                if isinstance(_kpi_tbl, list) and _kpi_tbl:
                    _kpi_df = pd.DataFrame([{
                        "No":        r.get("no", ""),
                        "KPI":       r.get("kpi", ""),
                        "Periode":   r.get("period", ""),
                        "Satuan":    r.get("unit", ""),
                        "Baseline":  r.get("baseline", ""),
                        "Target":    r.get("target", ""),
                        "Delta %":   r.get("delta_pct", ""),
                        "Keterangan": r.get("comment", ""),
                    } for r in _kpi_tbl if isinstance(r, dict)])
                    st.dataframe(_kpi_df, use_container_width=True, hide_index=True)
                _assump = _def_benefit.get("assumptions") or []
                if isinstance(_assump, list) and _assump:
                    st.markdown("**Asumsi:**")
                    for _a in _assump:
                        st.markdown(f"- {_a}")
                _soft = _def_benefit.get("soft_benefits") or []
                if isinstance(_soft, list) and _soft:
                    st.markdown("**Soft Benefits:**")
                    for _s in _soft:
                        st.markdown(f"- {_s}")
            st.caption("Referensi read-only dari fase Define. Validasi angka final bersama Finance/Controller di bawah.")

    _validated_benefit = st.text_area(
        "Validated benefit (quantitative)",
        key=f"ctrl_benefit_val_{active_pid}",
        placeholder="Contoh: A/R Overdue turun dari 27% ke 11%, setara penghematan Rp 500jt/tahun",
        height=60, disabled=is_locked,
    )
    _benefit_type = st.selectbox(
        "Benefit type",
        options=["cost","quality","delivery","safety","other"],
        key=f"ctrl_benefit_type_{active_pid}",
        disabled=is_locked,
    )
    _validated_by = st.text_input(
        "Validated by (Finance/Controller)",
        key=f"ctrl_benefit_by_{active_pid}",
        disabled=is_locked,
    )
    _benefit_date = st.text_input(
        "Validation date",
        key=f"ctrl_benefit_date_{active_pid}",
        placeholder="DD/MM/YYYY",
        disabled=is_locked,
    )

    # ── Upload Benefit Approval Sheet & Benefit Tracking Sheet (Project Leader) ──
    st.markdown("**📄 Dokumen Benefit (diupload Project Leader):**")
    import base64 as _b64_ben

    _ben_doc_specs = [
        ("approval", "Benefit Approval Sheet (signed)"),
        ("tracking", "Benefit Tracking Sheet"),
    ]
    # Preload dari WIP bila session kosong
    _ben_wip = memory.load_control_wip(active_pid) or {}
    for _slot, _ in _ben_doc_specs:
        _b64_k  = f"ctrl_benefit_{_slot}_b64_{active_pid}"
        _name_k = f"ctrl_benefit_{_slot}_name_{active_pid}"
        _type_k = f"ctrl_benefit_{_slot}_type_{active_pid}"
        if _b64_k not in st.session_state:
            st.session_state[_b64_k]  = _ben_wip.get(f"benefit_{_slot}_b64", "")
            st.session_state[_name_k] = _ben_wip.get(f"benefit_{_slot}_name", "")
            st.session_state[_type_k] = _ben_wip.get(f"benefit_{_slot}_type", "")

    _ben_cols = st.columns(2)
    for _ci, (_slot, _label) in enumerate(_ben_doc_specs):
        _b64_k  = f"ctrl_benefit_{_slot}_b64_{active_pid}"
        _name_k = f"ctrl_benefit_{_slot}_name_{active_pid}"
        _type_k = f"ctrl_benefit_{_slot}_type_{active_pid}"
        with _ben_cols[_ci]:
            _up = st.file_uploader(
                f"📤 {_label}",
                type=["xlsx", "xls", "csv", "pdf", "png", "jpg", "jpeg"],
                key=f"ctrl_benefit_{_slot}_uploader_{active_pid}",
                disabled=is_locked,
            )
            if _up is not None:
                _ub = _up.read()
                st.session_state[_b64_k]  = _b64_ben.b64encode(_ub).decode("utf-8")
                st.session_state[_name_k] = _up.name
                st.session_state[_type_k] = _up.type or ""
                _ben_wip_save = memory.load_control_wip(active_pid) or {}
                _ben_wip_save[f"benefit_{_slot}_b64"]  = st.session_state[_b64_k]
                _ben_wip_save[f"benefit_{_slot}_name"] = _up.name
                _ben_wip_save[f"benefit_{_slot}_type"] = st.session_state[_type_k]
                memory.save_control_wip(active_pid, _ben_wip_save)
            _saved_name = st.session_state.get(_name_k, "")
            if _saved_name:
                st.caption(f"📎 Tersimpan: {_saved_name}")
                if str(st.session_state.get(_type_k, "")).startswith("image/"):
                    try:
                        st.image(_b64_ben.b64decode(st.session_state[_b64_k]),
                                 width=700)
                    except Exception:
                        pass

    st.divider()

    # ── Step 6: Post-implementation Data (for control chart) ──
    st.markdown("### Step 6 — Post-implementation Data")
    st.caption("Download template Excel, isi kolom Nilai dengan data setelah implementasi, lalu upload. "
               "Control chart akan dihitung otomatis (I-MR). Min. 20 titik untuk hasil bermakna.")

    import io as _io_pd
    import base64 as _b64_pd
    from app.utils import charts as _charts

    # 1) Template download (in-memory, tidak menulis file ke disk)
    def _build_postdata_template() -> bytes:
        _buf = _io_pd.BytesIO()
        _df_data = pd.DataFrame({"No": [1, 2, 3], "Tanggal": ["", "", ""], "Nilai": ["", "", ""]})
        _df_instr = pd.DataFrame({
            "Kolom": ["No", "Tanggal", "Nilai"],
            "Keterangan": [
                "Urutan sampel (1, 2, 3, ...). Boleh dikosongkan.",
                "Tanggal pengukuran (DD/MM/YYYY). Opsional, untuk label sumbu-x.",
                "WAJIB. Hasil pengukuran KPI / Y (numerik). 1 baris = 1 titik data.",
            ],
        })
        try:
            with pd.ExcelWriter(_buf, engine="openpyxl") as _xl:
                _df_data.to_excel(_xl, sheet_name="Data", index=False)
                _df_instr.to_excel(_xl, sheet_name="Instruksi", index=False)
        except Exception:
            _buf = _io_pd.BytesIO()
            _df_data.to_csv(_buf, index=False)
        return _buf.getvalue()

    st.download_button(
        "⬇️ Download Template Excel",
        data=_build_postdata_template(),
        file_name="template_control_chart.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"ctrl_postdata_tmpl_{active_pid}",
        disabled=is_locked,
    )

    # 2) Upload filled template + persist (session + WIP)
    _pd_b64_k  = f"ctrl_postdata_b64_{active_pid}"
    _pd_name_k = f"ctrl_postdata_name_{active_pid}"
    _pd_type_k = f"ctrl_postdata_type_{active_pid}"
    _pd_wip = memory.load_control_wip(active_pid) or {}
    if _pd_b64_k not in st.session_state:
        st.session_state[_pd_b64_k]  = _pd_wip.get("postdata_b64", "")
        st.session_state[_pd_name_k] = _pd_wip.get("postdata_name", "")
        st.session_state[_pd_type_k] = _pd_wip.get("postdata_type", "")

    _pd_up = st.file_uploader(
        "📤 Upload template yang sudah diisi (xlsx/xls/csv)",
        type=["xlsx", "xls", "csv"],
        key=f"ctrl_postdata_uploader_{active_pid}",
        disabled=is_locked,
    )
    if _pd_up is not None:
        _pd_bytes = _pd_up.read()
        st.session_state[_pd_b64_k]  = _b64_pd.b64encode(_pd_bytes).decode("utf-8")
        st.session_state[_pd_name_k] = _pd_up.name
        st.session_state[_pd_type_k] = _pd_up.type or ""
        _pd_wip_save = memory.load_control_wip(active_pid) or {}
        _pd_wip_save["postdata_b64"]  = st.session_state[_pd_b64_k]
        _pd_wip_save["postdata_name"] = _pd_up.name
        _pd_wip_save["postdata_type"] = st.session_state[_pd_type_k]
        memory.save_control_wip(active_pid, _pd_wip_save)

    # 3) Parse uploaded sheet → numeric values (kolom "Nilai", fallback kolom numerik pertama)
    _postdata_values = []
    if st.session_state.get(_pd_b64_k):
        st.caption(f"📎 Tersimpan: {st.session_state.get(_pd_name_k,'')}")
        try:
            _raw = _b64_pd.b64decode(st.session_state[_pd_b64_k])
            _nm  = str(st.session_state.get(_pd_name_k, "")).lower()
            if _nm.endswith(".csv"):
                _df_up = pd.read_csv(_io_pd.BytesIO(_raw))
            else:
                _df_up = pd.read_excel(_io_pd.BytesIO(_raw), sheet_name="Data") \
                    if "Data" in pd.ExcelFile(_io_pd.BytesIO(_raw)).sheet_names \
                    else pd.read_excel(_io_pd.BytesIO(_raw))
            _val_col = next((c for c in _df_up.columns if str(c).strip().lower() == "nilai"), None)
            if _val_col is None:
                _num_cols = [c for c in _df_up.columns if pd.api.types.is_numeric_dtype(_df_up[c])]
                _val_col = _num_cols[0] if _num_cols else None
            if _val_col is not None:
                _postdata_values = [float(v) for v in pd.to_numeric(_df_up[_val_col], errors="coerce").dropna().tolist()]
                st.caption(f"Kolom nilai terbaca: **{_val_col}** — {len(_postdata_values)} titik data.")
                st.dataframe(_df_up.head(10), use_container_width=True, hide_index=True)
            else:
                st.warning("Kolom 'Nilai' tidak ditemukan dan tidak ada kolom numerik. Periksa template.")
        except Exception as _e_pd:
            st.warning(f"Gagal membaca sheet: {_e_pd}")

    # 4) Pilih jenis chart (auto-rekomendasi + override) & preview
    _ct_key = f"ctrl_chart_type_{active_pid}"
    if _ct_key not in st.session_state:
        st.session_state[_ct_key] = _pd_wip.get("postdata_chart_type", "") or "auto"
    _ct_options = ["auto"] + _charts.CHART_TYPES
    _ct_label = {"auto": "Auto (rekomendasi)", **_charts.CHART_LABELS}
    _chart_type_sel = st.selectbox(
        "Jenis chart",
        options=_ct_options,
        index=_ct_options.index(st.session_state[_ct_key]) if st.session_state[_ct_key] in _ct_options else 0,
        format_func=lambda x: _ct_label.get(x, x),
        key=f"ctrl_chart_type_sel_{active_pid}",
        disabled=is_locked,
    )
    # Rekomendasi default bila "auto"
    _ct_effective = _chart_type_sel
    if _ct_effective == "auto":
        _ct_effective = _charts.recommend_chart_type(context={}, values=_postdata_values, phase="control")
        st.caption(f"Rekomendasi otomatis: **{_ct_label.get(_ct_effective, _ct_effective)}**")
    # Persist pilihan ke WIP
    if st.session_state.get(_ct_key) != _chart_type_sel:
        st.session_state[_ct_key] = _chart_type_sel
        _ct_wip = memory.load_control_wip(active_pid) or {}
        _ct_wip["postdata_chart_type"] = _chart_type_sel
        memory.save_control_wip(active_pid, _ct_wip)

    if _postdata_values:
        _cc_prev = _charts.build_chart(values=_postdata_values, chart_type=_ct_effective, phase="control")
        st.markdown("**📈 Preview Chart**")
        _charts.render_chart_streamlit(_cc_prev, title="Control Chart")

    # 4b) Opsi upload chart improved performance (gambar) — alternatif/pelengkap
    st.markdown("**🖼️ Upload chart improved performance (opsional):**")
    _ci_b64_k  = f"ctrl_chart_img_b64_{active_pid}"
    _ci_name_k = f"ctrl_chart_img_name_{active_pid}"
    _ci_type_k = f"ctrl_chart_img_type_{active_pid}"
    if _ci_b64_k not in st.session_state:
        st.session_state[_ci_b64_k]  = _pd_wip.get("chart_img_b64", "")
        st.session_state[_ci_name_k] = _pd_wip.get("chart_img_name", "")
        st.session_state[_ci_type_k] = _pd_wip.get("chart_img_type", "")
    _ci_up = st.file_uploader(
        "📤 Upload gambar chart (png/jpg)",
        type=["png", "jpg", "jpeg"],
        key=f"ctrl_chart_img_uploader_{active_pid}",
        disabled=is_locked,
    )
    if _ci_up is not None:
        _ci_bytes = _ci_up.read()
        st.session_state[_ci_b64_k]  = _b64_pd.b64encode(_ci_bytes).decode("utf-8")
        st.session_state[_ci_name_k] = _ci_up.name
        st.session_state[_ci_type_k] = _ci_up.type or ""
        _ci_wip = memory.load_control_wip(active_pid) or {}
        _ci_wip["chart_img_b64"]  = st.session_state[_ci_b64_k]
        _ci_wip["chart_img_name"] = _ci_up.name
        _ci_wip["chart_img_type"] = st.session_state[_ci_type_k]
        memory.save_control_wip(active_pid, _ci_wip)
    if st.session_state.get(_ci_name_k):
        st.caption(f"📎 Tersimpan: {st.session_state[_ci_name_k]}")
        try:
            st.image(_b64_pd.b64decode(st.session_state[_ci_b64_k]), width=700)
        except Exception:
            pass

    # 5) Fallback manual: text area angka koma (dipakai bila sheet tidak ada)
    with st.expander("✍️ Input manual (alternatif tanpa upload)", expanded=False):
        _ctrl_values_text = st.text_area(
            "Post-implementation values (pisahkan dengan koma)",
            key=f"ctrl_values_{active_pid}",
            placeholder="Contoh: 15.2, 14.8, 13.1, 12.5, 11.8, 12.1...",
            height=60, disabled=is_locked,
        )

    st.divider()

    # ── Input Form ──
    with st.form(key="control_input_form", clear_on_submit=False):
        _def_ins = (define_final or {}).get("inputs") or {}
        _industry    = _def_ins.get("industry","") or "-"
        _process_area = _def_ins.get("process_area","") or "-"
        _pain        = _def_ins.get("pain_theme","") or "-"
        st.caption(f"📌 From Define: **{_industry}** / **{_process_area}** / **{_pain}**")

        lessons = st.text_area(
            "Lessons learned",
            height=80, disabled=is_locked,
            placeholder="Apa yang berhasil? Apa yang tidak? Apa yang akan dilakukan berbeda?",
        )
        feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            height=60, disabled=is_locked,
        )
        submitted = st.form_submit_button(
            "🔍 Generate / Update Draft" if not is_locked else "🔒 Locked",
            type="primary", disabled=is_locked,
        )

    if submitted:
        # Parse control values — prioritas: sheet ter-upload, fallback text manual
        ctrl_values = list(_postdata_values) if _postdata_values else []
        if not ctrl_values and _ctrl_values_text.strip():
            try:
                ctrl_values = [float(x.strip()) for x in _ctrl_values_text.split(",") if x.strip()]
            except Exception:
                ctrl_values = []

        # Build handover from UI (7-question template + confirmation sheet)
        _handover_ui = {
            "should_be_process_set_up":   _ho_setup,
            "kpis_monitoring_note":        _kpis_monitoring_note,
            "monitoring_mechanism":        _kpis_monitoring_note,
            "new_kpis_agreed":             bool(_kpis_monitoring_note.strip()),
            "training_note":               _training_note,
            "open_issues_note":            _open_issues_note,
            "open_issues_responsible":     _open_issues_responsible,
            "comments":                    _handover_comments,
            "responsible":                 _responsible,
            "continuous_monitoring_assured": bool(st.session_state.get(_mon_key)),
            "confirmation_sheet_name": st.session_state.get(f"ctrl_ho_conf_name_{active_pid}", ""),
            "confirmation_sheet_b64":  st.session_state.get(f"ctrl_ho_conf_b64_{active_pid}", ""),
            "confirmation_sheet_type": st.session_state.get(f"ctrl_ho_conf_type_{active_pid}", ""),
            "handover_confirmed": bool(st.session_state.get(f"ctrl_ho_conf_name_{active_pid}", "")),
        }

        user_inputs = {
            "industry":             _industry,
            "process_area":         _process_area,
            "pain_theme":           _pain,
            "process_owner":        _process_owner,
            "training_needs":       _training_note,
            "open_issues":          _open_issues_note,
            "handover_comments":    _handover_comments,
            "control_values":       ctrl_values,
            "chart_type":           _chart_type_sel,
            "chart_image": {
                "b64":  st.session_state.get(f"ctrl_chart_img_b64_{active_pid}", ""),
                "name": st.session_state.get(f"ctrl_chart_img_name_{active_pid}", ""),
                "type": st.session_state.get(f"ctrl_chart_img_type_{active_pid}", ""),
            },
            "lessons_learned":      [l.strip() for l in lessons.splitlines() if l.strip()],
            "monitoring_reaction_plan": st.session_state.get(_mon_key, []),
            "control_plan":         st.session_state.get(_cp_key, []),
            "handover_protocol":    _handover_ui,
            "benefit_validation": {
                "validated_benefit":   _validated_benefit,
                "benefit_type":        _benefit_type,
                "validated_by":        _validated_by,
                "validation_date":     _benefit_date,
                "approval_sheet_name": st.session_state.get(f"ctrl_benefit_approval_name_{active_pid}", ""),
                "approval_sheet_b64":  st.session_state.get(f"ctrl_benefit_approval_b64_{active_pid}", ""),
                "approval_sheet_type": st.session_state.get(f"ctrl_benefit_approval_type_{active_pid}", ""),
                "tracking_sheet_name": st.session_state.get(f"ctrl_benefit_tracking_name_{active_pid}", ""),
                "tracking_sheet_b64":  st.session_state.get(f"ctrl_benefit_tracking_b64_{active_pid}", ""),
                "tracking_sheet_type": st.session_state.get(f"ctrl_benefit_tracking_type_{active_pid}", ""),
            },
            "project_path": _proj_path,
        }
        user_feedback = {"text": feedback_text.strip()} if feedback_text.strip() else None

        with st.spinner("Running CONTROL agent..."):
            result = run_control_agent(
                project_id=active_pid,
                user_inputs=user_inputs,
                define_final=define_final,
                measure_final=measure_final,
                analyze_final=analyze_final,
                improve_final=improve_final,
                user_feedback=user_feedback,
                project_path=_proj_path,
            )

        # Sync agent outputs
        _agent_outs = result.get("control_state",{}).get("outputs",{})
        if _agent_outs.get("monitoring_reaction_plan"):
            st.session_state[_mon_key] = _agent_outs["monitoring_reaction_plan"]
        if _agent_outs.get("control_plan"):
            st.session_state[_cp_key]  = _agent_outs["control_plan"]
        if _agent_outs.get("handover_protocol"):
            st.session_state[_handover_key] = _agent_outs["handover_protocol"]
        if _agent_outs.get("sustain_audit_protocol"):
            st.session_state[_sustain_key] = _agent_outs["sustain_audit_protocol"]

        st.session_state["control_draft"] = result
        st.session_state["control_final"] = None
        st.session_state[_revise_key]     = False
        st.rerun()

    # ── Agent Coaching ──
    _cur = st.session_state.get("control_draft") or st.session_state.get("control_final")
    _cs  = (_cur or {}).get("control_state") if isinstance(_cur, dict) else None
    if isinstance(_cs, dict):
        gate     = _cs.get("gate") or {}
        status   = gate.get("status","-")
        coaching = (_cs.get("coaching_md") or "").strip()
        st.markdown("### 🧭 Agent Coaching")
        st.markdown(f"**Gate Status:** `{status}`")
        if coaching:
            st.markdown(coaching)
        else:
            st.info("Generate draft untuk melihat coaching.")

# ── TAB 2: Report ──
with tab_report:
    if not control_state:
        st.info("Generate draft untuk melihat report.")
    else:
        outs     = control_state.get("outputs") or {}
        upstream = control_state.get("upstream") or {}

        with st.expander("🔗 Context from DMAIC upstream", expanded=True):
            st.markdown(f"**Problem:** {upstream.get('problem_statement') or '-'}")
            st.markdown(f"**Goal:** {upstream.get('goal_statement') or '-'}")
            st.markdown(f"**Y Variable:** {upstream.get('y_variable') or '-'}")
            st.markdown(f"**Solution:** {upstream.get('selected_solution') or '-'}")
            t_info = upstream.get("baseline_target") or {}
            if t_info.get("value"):
                st.markdown(f"**Target:** {t_info.get('direction','')} {t_info.get('value','')}")

        gate     = control_state.get("gate") or {}
        g_status = gate.get("status","-")
        if g_status == "PASS":        st.success(f"Gate: **{g_status}**")
        elif g_status == "WEAK_PASS": st.warning(f"Gate: **{g_status}**")
        elif g_status == "FAIL":      st.error(f"Gate: **{g_status}**")

        failed  = gate.get("failed_rules") or []
        actions = gate.get("next_actions") or []
        if failed:
            st.markdown("**Failed Rules:**")
            for f in failed: st.write(f"- `{f}`")
        if actions:
            st.markdown("**Next Actions:**")
            for i, a in enumerate(actions,1): st.write(f"{i}. {a}")

        # Benefit validation
        benefit = outs.get("benefit_validation") or {}
        if benefit and benefit.get("validated_benefit"):
            st.divider()
            st.markdown("### 💰 Validated Benefit")
            st.success(f"**{benefit.get('validated_benefit','-')}**")
            st.markdown(f"- Type: {benefit.get('benefit_type','-')}")
            st.markdown(f"- Validated by: {benefit.get('validated_by','-')}")
            st.markdown(f"- Date: {benefit.get('validation_date','-')}")

            # Uploaded benefit documents
            import base64 as _b64_rep
            for _slot, _label in [("approval", "Benefit Approval Sheet"),
                                  ("tracking", "Benefit Tracking Sheet")]:
                _doc_name = benefit.get(f"{_slot}_sheet_name", "")
                _doc_b64  = benefit.get(f"{_slot}_sheet_b64", "")
                _doc_type = benefit.get(f"{_slot}_sheet_type", "")
                if _doc_name and _doc_b64:
                    st.markdown(f"**📎 {_label}:** {_doc_name}")
                    try:
                        _doc_bytes = _b64_rep.b64decode(_doc_b64)
                        if str(_doc_type).startswith("image/"):
                            st.image(_doc_bytes, width=700)
                        st.download_button(
                            f"⬇️ Download {_label}",
                            data=_doc_bytes,
                            file_name=_doc_name,
                            mime=_doc_type or "application/octet-stream",
                            key=f"dl_benefit_{_slot}_{active_pid}",
                        )
                    except Exception:
                        pass

        # Control chart
        cc = outs.get("control_chart") or {}
        if cc.get("available"):
            from app.utils import charts as _charts_rep
            st.divider()
            _ct_lbl = _charts_rep.CHART_LABELS.get(cc.get("chart_type",""), "Control Chart")
            st.markdown(f"### 📈 {_ct_lbl}")
            _charts_rep.render_chart_streamlit(cc, title="Control Chart")
            st.caption(f"Based on {cc.get('n',0)} post-implementation data points.")
        else:
            st.divider()
            st.caption(f"📈 {cc.get('note', 'Control chart akan tersedia setelah 12 bulan data post-implementasi.')}")

        # Sustain audit
        sustain = outs.get("sustain_audit_protocol") or {}
        if sustain:
            st.divider()
            st.markdown("### 🔍 Sustain Audit Protocol")
            st.markdown(f"- **Frequency:** {sustain.get('audit_frequency','-')}")
            st.markdown(f"- **Auditor:** {sustain.get('auditor','-')}")
            st.markdown(f"- **30-day check:** {sustain.get('30_day_check','-')}")
            st.markdown(f"- **60-day check:** {sustain.get('60_day_check','-')}")
            st.markdown(f"- **90-day check:** {sustain.get('90_day_check','-')}")

        # Lessons learned
        lessons = outs.get("lessons_learned") or []
        if lessons:
            st.divider()
            st.markdown("### 💡 Lessons Learned")
            for l in lessons:
                st.write(f"- {l}")

        _coaching = (control_state.get("coaching_md") or "").strip()
        if _coaching:
            st.divider()
            st.markdown("### 🧭 Agent Coaching")
            st.markdown(_coaching)

# ── TAB: Improved Performance ──
with tab_improved:
    if not control_state:
        st.info("Generate draft untuk melihat improved performance.")
    else:
        from app.utils import charts as _charts_imp
        import base64 as _b64_imp
        _imp_outs = control_state.get("outputs") or {}
        _imp_cc   = _imp_outs.get("control_chart") or {}
        _imp_img  = _imp_outs.get("control_chart_image") or {}

        st.markdown("### 📈 Improved Performance")

        _shown = False
        # 1) Chart dari data (mesin chart bersama)
        if _imp_cc.get("available"):
            _shown = True
            _imp_lbl = _charts_imp.CHART_LABELS.get(_imp_cc.get("chart_type",""), "Control Chart")
            st.markdown(f"#### {_imp_lbl}")
            _charts_imp.render_chart_streamlit(_imp_cc, title="Improved Performance")
            st.caption(f"Berdasarkan {_imp_cc.get('n',0)} titik data post-implementasi.")

        # 2) Chart yang diupload (gambar)
        if _imp_img.get("b64"):
            _shown = True
            st.divider()
            st.markdown("#### 🖼️ Chart yang diupload")
            try:
                _img_bytes = _b64_imp.b64decode(_imp_img["b64"])
                st.image(_img_bytes, width=700)
                st.download_button(
                    "⬇️ Download chart",
                    data=_img_bytes,
                    file_name=_imp_img.get("name") or "improved_performance_chart.png",
                    mime=_imp_img.get("type") or "image/png",
                    key=f"dl_improved_chart_{active_pid}",
                )
            except Exception:
                pass

        if not _shown:
            st.info("Belum ada chart. Isi data post-implementasi atau upload gambar chart di Step 6 (tab Input), lalu generate draft.")

# ── TAB 3: Monitoring & Reaction Plan ──
with tab_monitoring:
    if _proj_path == "quick":
        st.info("📊 Tab **Monitoring & Reaction** tidak berlaku untuk **Quick path** — Step 2 di-skip.")
    elif not control_state:
        st.info("Generate draft untuk melihat monitoring plan.")
    else:
        outs = control_state.get("outputs") or {}
        mon_plan = outs.get("monitoring_reaction_plan") or []
        if mon_plan:
            st.markdown("### Monitoring Plan")
            _mon_display = pd.DataFrame([{
                "KPI":          m.get("kpi",""),
                "Target":       m.get("target_value",""),
                "Frequency":    m.get("measurement_frequency",""),
                "Who Collects": m.get("who_collects",""),
                "Who Analyzes": m.get("who_analyzes",""),
                "Who Reports":  m.get("who_sends_report",""),
                "Who Acts":     m.get("who_takes_action",""),
                "Threshold":    m.get("deviation_threshold",""),
            } for m in mon_plan])
            st.dataframe(_mon_display, use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("### Reaction Plan (Penelusuran Root Cause)")
            for m in mon_plan:
                with st.expander(f"**{m.get('kpi','')}** — pemicu deviasi: {m.get('deviation_threshold','')}", expanded=True):
                    react_steps = m.get("reaction_steps") or []
                    for step in react_steps:
                        _q = step.get("question","")
                        _rc = step.get("rc_ref","")
                        _label = f"Langkah {step.get('step','')}"
                        if _q or _rc:
                            _label += f" ({' · '.join([x for x in [_rc, _q] if x])})"
                        st.markdown(f"**{_label}:** {step.get('check','')}")
                        col_y, col_n = st.columns(2)
                        with col_y:
                            st.markdown(
                                f"<div style='background:#fef3c7;border-radius:8px;padding:8px;font-size:12px;'>"
                                f"✅ <b>Jika YA:</b><br>{step.get('if_yes','')}</div>",
                                unsafe_allow_html=True
                            )
                        with col_n:
                            st.markdown(
                                f"<div style='background:#f0f9ff;border-radius:8px;padding:8px;font-size:12px;'>"
                                f"❌ <b>Jika TIDAK:</b><br>{step.get('if_no','')}</div>",
                                unsafe_allow_html=True
                            )
                    _esc = m.get("escalation_actions") or []
                    if _esc:
                        st.markdown("**Jika solusi gagal / semua RC sudah dicek → Eskalasi:**")
                        for _a in _esc:
                            st.markdown(f"- {_a}")
                    st.caption(m.get("control_chart_note",""))
        else:
            st.info("Monitoring plan akan tersedia setelah generate draft.")

# ── TAB 4: Control Plan ──
with tab_control_plan:
    if _proj_path == "quick":
        st.info("📋 Tab **Control Plan** tidak berlaku untuk **Quick path** — control plan per CTQ formal di-skip (B-rule).")
    elif not control_state:
        st.info("Generate draft untuk melihat control plan.")
    else:
        outs = control_state.get("outputs") or {}
        cp   = outs.get("control_plan") or []
        if cp:
            st.markdown("### Control Plan per CTQ")
            st.dataframe(pd.DataFrame(cp), use_container_width=True, hide_index=True)
        else:
            st.info("Control plan akan tersedia setelah generate draft.")

# ── TAB 5: Handover Protocol ──
with tab_handover:
    if _proj_path == "quick":
        st.info("🤝 Tab **Handover** tidak berlaku untuk **Quick path** — handover protocol formal di-skip (B-rule).")
    elif not control_state:
        st.info("Generate draft untuk melihat handover protocol.")
    else:
        outs     = control_state.get("outputs") or {}
        handover = outs.get("handover_protocol") or {}

        if handover:
            st.markdown("### Handover Protocol")

            # Status summary
            ho_setup  = handover.get("should_be_process_set_up", False)
            kpi_agreed = handover.get("new_kpis_agreed", False)
            mon_assured = handover.get("continuous_monitoring_assured", False)
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Should-be set up", "✅" if ho_setup else "⏳")
            col_b.metric("KPIs agreed", "✅" if kpi_agreed else "⏳")
            col_c.metric("Monitoring assured", "✅" if mon_assured else "⏳")

            # ── Template Q&A (7 pertanyaan) ──
            _kpis_note = handover.get("kpis_monitoring_note") or handover.get("monitoring_mechanism") or ""
            _qa_rows = [
                ("1. Should-be process sudah disiapkan (set up)?", "Yes" if ho_setup else "No"),
                ("2. New process KPIs disepakati & continuous monitoring terjamin?", _kpis_note),
                ("3. Siapa & apa yang harus dilatih?", handover.get("training_note", "")),
                ("4. Open issues", handover.get("open_issues_note", "")),
                ("5. Penanggung jawab open issues", handover.get("open_issues_responsible", "")),
                ("6. Comments / catatan tambahan", handover.get("comments", "")),
                ("7. Responsible (penanggung jawab proses)", handover.get("responsible", "")),
            ]
            _qa_df = pd.DataFrame(
                [{"Pertanyaan": q, "Jawaban": (a if str(a).strip() else "-")} for q, a in _qa_rows]
            )
            st.dataframe(_qa_df, use_container_width=True, hide_index=True)

            # ── Confirmation sheet (signed Process Owner) ──
            _conf_name = handover.get("confirmation_sheet_name", "")
            _conf_b64  = handover.get("confirmation_sheet_b64", "")
            _conf_type = handover.get("confirmation_sheet_type", "")
            if _conf_name:
                st.divider()
                st.markdown("### 📄 Handover Protocol Confirmation Sheet (signed Process Owner)")
                st.caption(f"📎 {_conf_name}")
                if _conf_b64:
                    import base64 as _b64_rep_ho
                    try:
                        _conf_bytes = _b64_rep_ho.b64decode(_conf_b64)
                        st.download_button(
                            "⬇️ Download confirmation sheet",
                            data=_conf_bytes, file_name=_conf_name,
                            mime=_conf_type or "application/octet-stream",
                            key="ctrl_ho_conf_dl",
                        )
                        if str(_conf_type).startswith("image/"):
                            st.image(_conf_bytes, width=700)
                    except Exception:
                        pass

            # ── Backward-compat: draft lama (training_plan / open_issues list) ──
            training = handover.get("training_plan") or []
            if training:
                st.divider()
                st.markdown("### 🎓 Training Plan (draft lama)")
                st.dataframe(pd.DataFrame(training), use_container_width=True, hide_index=True)
            issues = handover.get("open_issues") or []
            if isinstance(issues, list) and issues:
                st.divider()
                st.markdown("### 🔧 Open Issues (draft lama)")
                st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)

            # Status complete berdasarkan confirmation sheet ter-upload
            _ho_done = bool(handover.get("handover_confirmed") or handover.get("confirmation_sheet_name"))
            st.divider()
            if _ho_done:
                st.success("🎉 Handover COMPLETE — confirmation sheet (ditandatangani Process Owner) sudah diupload.")
            else:
                st.warning("⏳ Handover belum complete — upload handover confirmation sheet yang ditandatangani Process Owner.")
        else:
            st.info("Handover protocol akan tersedia setelah generate draft.")

with tab_input:
    # ============================================
    # Finalize & Downloads (full width)
    # ============================================
    st.divider()
    st.subheader("Finalize & Downloads")

    _draft_wrapper  = st.session_state.get("control_draft")
    _has_draft_now  = bool(_draft_wrapper and _draft_wrapper.get("control_state"))
    try:
        _gate_status_now = (_draft_wrapper or {}).get("control_state",{}).get("gate",{}).get("status")
    except Exception:
        _gate_status_now = None

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        if _has_draft_now:
            # Standard: wajib fase sebelumnya di-approve approver. Quick: tidak perlu approval.
            _prev_block = (_proj_path != "quick") and (not _appr_status.get("prev_approved", False))
            # Standard: wajib isi tanggal aktual selesai fase sebelum finalize.
            _has_end = render_phase_end_date(active_pid, "control", disabled=is_locked)
            _no_actual_end = (_proj_path != "quick") and not _has_end
            fin_disabled = _prev_block or _no_actual_end or (
                (_gate_status_now == "FAIL") and not _appr_status.get("can_advance", False)
            )
            if st.button("✅ Finalize (Lock Final)", key="control_finalize_btn", disabled=fin_disabled):
                draft_state  = _draft_wrapper["control_state"]
                final_result = finalize_control_agent(active_pid, draft_state)
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason","Blocked by gate."))
                else:
                    st.session_state["control_final"] = final_result
                    st.session_state["control_draft"] = None
                    st.session_state[_revise_key]     = False
                    st.success("🎉 DMAIC project COMPLETE — Control finalized.")
                    st.rerun()
            if _prev_block:
                st.caption("⚠️ Finalize diblokir: fase sebelumnya (IMPROVE) belum di-approve oleh approver (jalur Standard).")
            elif _no_actual_end:
                st.caption("⚠️ Finalize diblokir: isi **Tanggal aktual selesai fase** dulu.")
            elif fin_disabled:
                st.caption("⚠️ Finalize diblokir: Gate FAIL.")
        elif _has_final:
            st.button("✅ Finalized", key="control_finalized_badge", disabled=True)
        else:
            st.button("✅ Finalize (Lock Final)", key="control_fin_disabled", disabled=True)
            st.caption("Generate draft terlebih dahulu.")

    with c2:
        _word_src = final_on_disk or draft_on_disk
        _word_lbl = "🛠️ Generate Word (Final)" if final_on_disk else "🛠️ Generate Word (Draft)"
        if _word_src:
            if st.button(_word_lbl, key="control_gen_word"):
                export_fn = getattr(reporting, "export_control_to_word", None)
                if callable(export_fn):
                    try:
                        _docx_fname = f"Project_{active_pid}_DEF_MEA_ANA_IMP_CTL_{'Final' if final_on_disk else 'Draft'}.docx"
                        out_path = export_fn(copy.deepcopy(_word_src), path=_docx_fname)
                        st.session_state["control_last_docx"] = out_path
                        st.success("Word file generated.")
                    except Exception as e:
                        st.warning(f"Word export belum tersedia: {e}")
                else:
                    st.warning("export_control_to_word belum diimplementasi.")
        else:
            st.button("🛠️ Generate Word", key="control_gen_word_dis", disabled=True)

    with c3:
        _docx_fname_ctl = f"Project_{active_pid}_DEF_MEA_ANA_IMP_CTL_{'Final' if final_on_disk else 'Draft'}.docx"
        _docx_path = st.session_state.get("control_last_docx") or _docx_fname_ctl
        _dl_label  = "⬇️ Download Final Word Report" if final_on_disk else "⬇️ Download Draft Word Report"
        try:
            with open(_docx_path,"rb") as f:
                doc_bytes = f.read()
            st.download_button(
                label=_dl_label, data=doc_bytes,
                file_name=_docx_fname_ctl,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="control_dl_word",
            )
        except FileNotFoundError:
            st.button(_dl_label, key="control_dl_word_dis", disabled=True)

    # ============================================
    # Governance — Reviewer + Champion (standard) / Champion (quick) for Control
    # ============================================
    st.divider()
    st.subheader("🏛️ Governance — Stage Gate Approval")

    final_exists    = memory.load_control_final(active_pid)
    approval_status = memory.get_phase_approval_status(active_pid, "control")
    reviewer_action = (approval_status.get("reviewer") or {}).get("action")
    champion_action = (approval_status.get("champion") or {}).get("action")
    can_advance     = approval_status.get("can_advance", False)
    auto_advance    = approval_status.get("auto_advance", False)
    required        = approval_status.get("required_approvers", [])
    _prev_approved_ct = approval_status.get("prev_approved", True)

    if not final_exists:
        st.info("Finalize CONTROL dulu sebelum bisa di-approve.")
    elif auto_advance:
        st.success("🚀 Control gate **auto-advance** — project complete.")
    else:
        # Status display — hanya setelah finalized
        col_r, col_c, col_adv = st.columns(3)
        with col_r:
            if "reviewer" in required:
                if reviewer_action=="approve":  st.success("✅ Reviewer: Approved")
                elif reviewer_action=="reject": st.error("❌ Reviewer: Rejected")
                else:                            st.warning("⏳ Reviewer: Pending")
            else:
                st.info("— Reviewer: tidak diperlukan")
        with col_c:
            if "champion" in required:
                if champion_action=="approve":  st.success("✅ Champion: Approved")
                elif champion_action=="reject": st.error("❌ Champion: Rejected")
                else:                            st.warning("⏳ Champion: Pending")
            else:
                st.info("— Champion: tidak diperlukan")
        with col_adv:
            if can_advance:
                st.success("🎉 Gate: OPEN — Project COMPLETE")
            else:
                st.info("🔒 Gate: LOCKED")

        role = auth.get_current_role()
        if not _prev_approved_ct:
            st.warning("⚠️ IMPROVE belum disetujui. Approval fase ini belum bisa dilakukan.")
        elif role in required:
            _role_action = (approval_status.get(role) or {}).get("action")
            st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
            if _role_action == "approve":
                st.success("✅ Kamu sudah **Approve** fase ini.")
            elif _role_action == "reject":
                st.error("❌ Kamu sudah **Reject** fase ini.")
            note = st.text_area("Catatan (opsional)", key=f"gov_note_{role}_control", height=80)
            col_app, col_rej = st.columns(2)
            with col_app:
                if st.button("✅ Approve", key=f"gov_approve_{role}_control",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid,"control",role,"approve",note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid,"control",f"approved_by_{role}")
                    st.success("Approval disimpan. DMAIC project complete!")
                    st.rerun()
            with col_rej:
                if st.button("❌ Reject", key=f"gov_reject_{role}_control",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid,"control",role,"reject",note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid,"control",f"rejected_by_{role}")
                    st.warning("Rejection disimpan.")
                    st.rerun()
        elif role == "project_leader":
            if can_advance:
                st.success("🎉 Semua approval lengkap. DMAIC project COMPLETE!")
            else:
                st.info("Menunggu approval dari approver yang dibutuhkan.")

    # ============================================
    # Audit Log
    # ============================================
    st.divider()
    st.subheader("🧾 Audit Log — CONTROL Phase")
    events = audit.get_recent_events(project_id=active_pid, phase="Control", limit=30)
    if events:
        st.dataframe(events, use_container_width=True)
    else:
        st.write("No audit events found.")