import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import json
import copy
import streamlit as st
import pandas as pd

from app.agents.improve_agent import run_improve_agent, finalize_improve_agent
from app.utils import memory, reporting, audit, auth
from app.utils.ui_helpers import render_sidebar_header

# ============================================
# Helpers
# ============================================

def _get_current_result():
    if st.session_state.get("improve_draft"):
        return "draft", st.session_state["improve_draft"]
    if st.session_state.get("improve_final"):
        return "final", st.session_state["improve_final"]
    return "none", None

def _safe_get(d, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _build_graphviz_process(should_be_text: str) -> str:
    """Simple graphviz from should-be process text — split by period/semicolon as steps."""
    if not should_be_text:
        return ""
    steps = [s.strip() for s in re.split(r"[.;]", should_be_text) if s.strip() and len(s.strip()) > 10]
    if len(steps) < 2:
        return ""
    import re as _re
    lines = [
        'digraph should_be {',
        '  rankdir=LR;',
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fillcolor="#d1fae5", color="#6ee7b7", fontcolor="#065f46", fontname="Helvetica", fontsize=11, margin="0.2,0.15"];',
        '  edge [color="#94a3b8", arrowsize=0.8];',
    ]
    node_ids = []
    for i, step in enumerate(steps[:8]):
        nid   = f"p{i}"
        label = step[:40] + ("..." if len(step) > 40 else "")
        lines.append(f'  {nid} [label="{label}"];')
        node_ids.append(nid)
    for i in range(len(node_ids)-1):
        lines.append(f"  {node_ids[i]} -> {node_ids[i+1]};")
    lines.append("}")
    return "\n".join(lines)

import re

# ============================================
# Page Config + CSS
# ============================================
st.set_page_config(page_title="IMPROVE", layout="wide")
st.markdown("""
<style>
.stApp { background-color:#f4f8fb; color:#0f172a;
  font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial; }
section[data-testid="stSidebar"] { background-color:#ffffff; border-right:1px solid #e2e8f0; }
.card { background:#ffffff; border:1px solid #e2e8f0; border-radius:14px;
  padding:18px 20px; box-shadow:0 6px 18px rgba(15,23,42,0.08); margin-bottom:18px; }
h1,h2,h3 { color:#0f172a; }
h2 { font-size:22px; margin-bottom:12px; }
h3 { font-size:18px; margin-bottom:8px; }
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
st.session_state.setdefault("improve_draft", None)
st.session_state.setdefault("improve_final", None)

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
    appr = memory.get_phase_approval_status(active_pid, "improve")
    if appr.get("auto_advance"):
        st.sidebar.success("🚀 Improve: Auto-advance")
    else:
        chmp = (appr.get("champion") or {}).get("action") or "pending"
        chmp_icon = "✅" if chmp=="approve" else "❌" if chmp=="reject" else "⏳"
        st.sidebar.markdown(f"**Improve Gate:**  \n{chmp_icon} Champion")
        if appr.get("can_advance"):
            st.sidebar.success("🚀 Gate OPEN")
        else:
            st.sidebar.info("🔒 Gate LOCKED")
else:
    st.sidebar.warning("Belum ada project aktif.")

# ============================================
# Gate checks
# ============================================
if not active_pid:
    st.title("💡 IMPROVE Phase")
    st.info("Belum ada project aktif. Kembali ke **Main** untuk memilih project.")
    st.stop()

analyze_final = memory.load_analyze_final(active_pid)
if not analyze_final:
    st.title("IMPROVE Phase")
    st.warning("ANALYZE belum di-finalize. Selesaikan fase Analyze terlebih dahulu.")
    st.stop()

measure_final = memory.load_measure_final(active_pid)
define_final  = memory.load_define_final(active_pid)

# ============================================
# Auto-load (_improve_loaded_pid pattern)
# ============================================
loaded_pid = st.session_state.get("_improve_loaded_pid")
if active_pid and loaded_pid != active_pid:
    st.session_state["_improve_loaded_pid"] = active_pid
    st.session_state["improve_draft"]       = None
    st.session_state["improve_final"]       = None

    draft = memory.load_improve_draft(active_pid)
    final = memory.load_improve_final(active_pid)
    if draft:
        st.session_state["improve_draft"] = {
            "status":"draft","improve_state":draft,
            "summary":draft.get("summary_md",""),"message":"Auto-loaded draft",
        }
    if final:
        st.session_state["improve_final"] = {
            "status":"finalized","improve_state":final,
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
    st.markdown("# DMAIC Copilot — IMPROVE Phase")
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
    _view_badge = "FINAL" if memory.load_improve_final(active_pid) else "DRAFT"
    st.markdown(
        f"<div style='text-align:right'><span style='padding:6px 12px;border-radius:20px;"
        f"background:#0f5132;color:#d1e7dd;font-weight:600;'>{_view_badge}</span></div>",
        unsafe_allow_html=True,
    )

# ============================================
# Shared state
# ============================================
final_on_disk = memory.load_improve_final(active_pid)
draft_on_disk = memory.load_improve_draft(active_pid)
_has_final    = bool(final_on_disk)
_has_draft    = bool(draft_on_disk)
_appr_status  = memory.get_phase_approval_status(active_pid, "improve")
_req_roles_ip = _appr_status.get("required_approvers", [])
_is_rejected  = any(
    (_appr_status.get(r) or {}).get("action") == "reject"
    for r in _req_roles_ip
)
_revise_key   = f"improve_revise_mode_{active_pid}"
st.session_state.setdefault(_revise_key, False)
is_locked = _has_final and not st.session_state.get(_revise_key) and not _has_draft

# Session state keys
_must_key        = f"improve_must_criteria_{active_pid}"
_sol_key         = f"improve_solutions_{active_pid}"
_sel_key         = f"improve_selected_{active_pid}"
_pilot_key       = f"improve_pilot_{active_pid}"
_impl_key        = f"improve_impl_{active_pid}"
_comm_key        = f"improve_comm_{active_pid}"
_status_key      = f"improve_impl_status_{active_pid}"
_must_skipped_key = f"improve_must_skipped_{active_pid}"
st.session_state.setdefault(_must_skipped_key, False)

# Pre-load from saved state
_saved = (final_on_disk or draft_on_disk or {})
_saved_outs = _saved.get("outputs") or {}
for key, field in [
    (_must_key,   "must_criteria"),
    (_sol_key,    "potential_solutions"),
    (_sel_key,    "selected_solutions"),
    (_impl_key,   "implementation_plan"),
    (_comm_key,   "communication_plan"),
    (_status_key, "implementation_status"),
]:
    if key not in st.session_state and _saved_outs.get(field):
        st.session_state[key] = _saved_outs[field]

# Auto-load FINAL if nothing in session (fallback untuk page reload/session reset)
if _get_current_result()[0] == "none" and active_pid:
    _fb_final = memory.load_improve_final(active_pid)
    if _fb_final:
        st.session_state["improve_final"] = {
            "status": "finalized",
            "improve_state": _fb_final,
            "summary": _fb_final.get("summary_md", ""),
            "message": "Auto-loaded FINAL from disk",
        }

# ============================================
# TAB LAYOUT
# ============================================
tab_input, tab_report, tab_solutions, tab_impl, tab_comm, tab_should_be = st.tabs([
    "✏️ Input",
    "📄 Report",
    "💡 Solutions & Selection",
    "📋 Implementation",
    "📢 Communication Plan",
    "🔄 Should-Be Process",
])

# ============================================
# TAB INPUT
# ============================================
with tab_input:

    # ── Revision Actions ──
    if _has_final and _is_rejected and not _has_draft and not st.session_state.get(_revise_key):
        if st.button("✏️ Revise Final", key="improve_revise_btn"):
            st.session_state[_revise_key]     = True
            st.session_state["improve_draft"] = None
            st.rerun()
    elif _has_draft:
        if st.button("🧹 Discard Draft", key="improve_discard_btn"):
            memory.delete_improve_draft(active_pid)
            st.session_state["improve_draft"] = None
            st.session_state["improve_final"] = None
            st.session_state[_revise_key]     = False
            if final_on_disk:
                st.session_state["improve_final"] = {
                    "status":"finalized","improve_state":final_on_disk,
                    "summary":final_on_disk.get("summary_md",""),"message":"Reverted to final",
                }
            st.rerun()

    # ── Step 1: Upstream Context ──
    st.markdown("### Step 1 — Confirmed Root Causes (from Analyze)")
    ana_outs  = (analyze_final or {}).get("outputs") or {}
    def_outs  = (define_final  or {}).get("outputs") or {}
    mea_outs  = (measure_final or {}).get("outputs") or {}
    perf_sum  = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}
    target_info = (mea_outs.get("process_performance_summary") or {}).get("target_info") or {}
    rca_table = ana_outs.get("root_cause_summary_table") or []
    confirmed_rc = [r for r in rca_table if "Yes" in str(r.get("is_root_cause",""))]
    _no_confirmed_rc = not confirmed_rc

    if _no_confirmed_rc:
        st.error(
            "⛔ **Tidak ada confirmed root cause dari fase Analyze.**\n\n"
            "Kembali ke fase **Analyze → Step 7 (Verification Results)** dan pastikan minimal "
            "1 root cause ditandai `is_root_cause = ✅ Yes` sebelum melanjutkan ke Improve.\n\n"
            "Improve gate akan otomatis FAIL selama kondisi ini belum terpenuhi."
        )

    _y_confirmed = mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "-"
    _baseline_m  = perf_sum.get("mean")
    _target_val  = target_info.get("value")
    _target_dir  = target_info.get("direction","<=")

    st.markdown(
        f"""<div style='background:#fffbeb;border:1px solid #fbbf24;border-radius:12px;
        padding:14px 18px;margin-bottom:12px;'>
        <b>🎯 Output Measurement (Y):</b> <span style='font-weight:600'>{_y_confirmed}</span>
        &nbsp;|&nbsp; Baseline: <b>{_baseline_m if _baseline_m is not None else '-'}</b>
        &nbsp;|&nbsp; Target: <b>{_target_dir} {_target_val if _target_val is not None else '-'}</b>
        </div>""",
        unsafe_allow_html=True,
    )

    with st.expander("📊 Confirmed Root Causes + All RCA", expanded=True):
        if confirmed_rc:
            st.markdown("**Confirmed root causes to address:**")
            for r in confirmed_rc:
                impact_color = "🔴" if r.get("impact","").lower()=="high" else ("🟡" if r.get("impact","").lower()=="medium" else "🟢")
                st.markdown(f"- **{r.get('xn','')}** {r.get('root_cause','')} — {impact_color} {r.get('impact','')}")
        else:
            st.info("Tidak ada confirmed root cause dari Analyze. Pastikan Analyze sudah lengkap.")
        if rca_table:
            st.dataframe(pd.DataFrame(rca_table), use_container_width=True, hide_index=True)

    st.divider()

    # ── Step 2: Must Criteria ──
    st.markdown("### Step 2 — Must Criteria")
    st.caption("Kriteria wajib yang SEMUA solusi harus penuhi. Isi manual, atau klik Propose untuk usulan agent, atau Skip jika belum perlu.")

    # Seed empty table (TIDAK auto-generate); user isi manual / propose / skip
    if _must_key not in st.session_state:
        st.session_state[_must_key] = _saved_outs.get("must_criteria") or []

    col_mc_propose, col_mc_skip, col_mc_save = st.columns([1,1,1])
    with col_mc_propose:
        if st.button("🤖 Propose Must Criteria", key=f"mc_propose_{active_pid}", disabled=is_locked or _no_confirmed_rc):
            with st.spinner("Agent menyusun must criteria..."):
                from app.agents.improve_agent import _propose_must_criteria_llm, _extract_upstream_context
                _upstream_mc = _extract_upstream_context(define_final, measure_final, analyze_final)
                st.session_state[_must_key] = _propose_must_criteria_llm(_upstream_mc, "")
                st.session_state[_must_skipped_key] = False
            st.rerun()
    with col_mc_skip:
        if st.button("⏭️ Skip Must Criteria", key=f"mc_skip_{active_pid}", disabled=is_locked):
            st.session_state[_must_key] = []
            st.session_state[_must_skipped_key] = True
            st.success("Must criteria di-skip. Solusi tidak akan dievaluasi terhadap must criteria.")

    _mc_list = st.session_state.get(_must_key, [])
    _mc_seed = _mc_list if _mc_list else [{"criterion": "", "category": "", "rationale": ""}]
    _mc_df = pd.DataFrame([{
        "criterion": m.get("criterion",""),
        "category":  m.get("category",""),
        "rationale": m.get("rationale",""),
    } for m in _mc_seed])
    _mc_edit = st.data_editor(
        _mc_df, num_rows="dynamic", use_container_width=True,
        key=f"mc_editor_{active_pid}", disabled=is_locked,
    )
    with col_mc_save:
        if st.button("💾 Simpan Must Criteria", key=f"mc_save_{active_pid}", disabled=is_locked):
            if isinstance(_mc_edit, pd.DataFrame):
                _rows = [r for r in _mc_edit.to_dict(orient="records") if str(r.get("criterion","")).strip()]
                st.session_state[_must_key] = _rows
                st.session_state[_must_skipped_key] = (len(_rows) == 0)
            st.success("Must criteria tersimpan.")
    if st.session_state.get(_must_skipped_key) and not _mc_list:
        st.info("⏭️ Must criteria di-skip — Step 3 solutions tidak akan divalidasi terhadap must criteria.")

    st.divider()

    # ── Step 3: Potential Solutions ──
    st.markdown("### Step 3 — Potential Solutions")
    st.caption("Ketik proposed solution untuk SETIAP confirmed root cause (boleh >1 solusi per root cause). Klik Generate jika butuh ide dari agent.")

    # Dropdown options: confirmed root cause labels
    _rc_options = [f"{r.get('xn','')} — {r.get('root_cause','')}".strip(" —") for r in confirmed_rc]

    col_sol_gen, col_sol_save = st.columns([1,1])
    with col_sol_gen:
        if st.button("🤖 Generate Solutions", key=f"sol_gen_{active_pid}",
                     disabled=is_locked or _no_confirmed_rc):
            with st.spinner("Agent menyusun potential solutions..."):
                from app.agents.improve_agent import _propose_solutions_llm, _extract_upstream_context
                _upstream_sol = _extract_upstream_context(define_final, measure_final, analyze_final)
                st.session_state[_sol_key] = _propose_solutions_llm(
                    _upstream_sol,
                    st.session_state.get(_must_key, []),
                    "",
                    _proj_path,
                )
            st.rerun()

    if _sol_key not in st.session_state:
        st.session_state[_sol_key] = _saved_outs.get("potential_solutions") or []

    _solutions = st.session_state.get(_sol_key, [])
    _sol_seed = _solutions if _solutions else [{}]
    _sol_df = pd.DataFrame([{
        "Idea":         s.get("idea",""),
        "Root Cause":   s.get("addresses_root_cause",""),
        "Type":         s.get("type",""),
        "Investment":   s.get("investment_needed",""),
        "Est. Cost":    s.get("investment_estimate",""),
        "Comments":     s.get("comments",""),
    } for s in _sol_seed])
    _sol_edit = st.data_editor(
        _sol_df, num_rows="dynamic", use_container_width=True,
        key=f"sol_editor_{active_pid}", disabled=is_locked,
        column_config={
            "Root Cause": st.column_config.SelectboxColumn(
                "Root Cause (confirmed)", options=_rc_options, required=False,
                help="Pilih confirmed root cause yang diatasi solusi ini",
            ),
        },
    )
    with col_sol_save:
        if st.button("💾 Konfirmasi Solutions", key=f"sol_save_{active_pid}", disabled=is_locked):
            if isinstance(_sol_edit, pd.DataFrame):
                _rows = []
                for idx, r in enumerate(_sol_edit.to_dict(orient="records"), 1):
                    if not str(r.get("Idea","")).strip():
                        continue
                    _rows.append({
                        "solution_id":        f"S{idx}",
                        "idea":               r.get("Idea",""),
                        "addresses_root_cause": r.get("Root Cause",""),
                        "type":               r.get("Type",""),
                        "investment_needed":  r.get("Investment",""),
                        "investment_estimate":r.get("Est. Cost",""),
                        "comments":           r.get("Comments",""),
                    })
                st.session_state[_sol_key] = _rows
            st.success("Solutions dikonfirmasi.")
            st.rerun()

    # Coverage validation: pastikan semua confirmed RC punya >=1 solusi
    _saved_sols = st.session_state.get(_sol_key, [])
    if _saved_sols and _rc_options:
        _covered = {str(s.get("addresses_root_cause","")).strip() for s in _saved_sols}
        _uncovered = [rc for rc in _rc_options if rc not in _covered]
        if _uncovered:
            st.warning(
                "⚠️ Root cause berikut belum punya solusi — setiap confirmed root cause harus diberi minimal 1 solusi:\n"
                + "\n".join(f"- {rc}" for rc in _uncovered)
            )
        else:
            st.success("✅ Semua confirmed root cause sudah punya minimal 1 solusi.")

    st.divider()

    # ── Step 4: Solution Selection ──
    st.markdown("### Step 4 — Selected Solution")
    st.caption("Pilih solusi yang akan diimplementasikan. Solution & CTQ/CTB dari item confirmed sebelumnya.")

    # Dropdown sources
    _sol_idea_options = [str(s.get("idea","")).strip() for s in st.session_state.get(_sol_key, []) if str(s.get("idea","")).strip()]
    _ctq_list_def = def_outs.get("ctq_list") or []
    _ctq_options = [str(c.get("name","")).strip() for c in _ctq_list_def if str(c.get("name","")).strip()]
    if not _ctq_options:
        # fallback ke approved_ctq_list
        _ctq_options = [str(c.get("ctq_name","")).strip() for c in (def_outs.get("approved_ctq_list") or []) if str(c.get("ctq_name","")).strip()]

    if _sel_key not in st.session_state:
        st.session_state[_sel_key] = _saved_outs.get("selected_solutions") or []

    _sel_rows = st.session_state.get(_sel_key, [])
    _sel_seed = _sel_rows if _sel_rows else [{}]
    _sel_df = pd.DataFrame([{
        "No":               i + 1,
        "Solution":         r.get("solution",""),
        "CTQ/CTB Impacted": r.get("ctq_ctb_impacted",""),
        "Comments":         r.get("comments",""),
    } for i, r in enumerate(_sel_seed)])
    _sel_edit = st.data_editor(
        _sel_df, num_rows="dynamic", use_container_width=True,
        key=f"sel_editor_{active_pid}", disabled=is_locked or _no_confirmed_rc,
        column_config={
            "No": st.column_config.NumberColumn("No", disabled=True),
            "Solution": st.column_config.SelectboxColumn(
                "Solution", options=_sol_idea_options, required=False,
                help="Pilih dari solusi yang sudah dikonfirmasi di Step 3",
            ),
            "CTQ/CTB Impacted": st.column_config.SelectboxColumn(
                "CTQ/CTB Impacted", options=_ctq_options, required=False,
                help="Pilih CTQ/CTB confirmed dari fase Define",
            ),
        },
    )
    if st.button("💾 Simpan Selected Solution", key=f"sel_save_{active_pid}", disabled=is_locked or _no_confirmed_rc):
        if isinstance(_sel_edit, pd.DataFrame):
            _rows = []
            for idx, r in enumerate(_sel_edit.to_dict(orient="records"), 1):
                if not str(r.get("Solution","")).strip():
                    continue
                _rows.append({
                    "no":               idx,
                    "solution":         r.get("Solution",""),
                    "ctq_ctb_impacted": r.get("CTQ/CTB Impacted",""),
                    "comments":         r.get("Comments",""),
                })
            st.session_state[_sel_key] = _rows
        st.success("Selected solution tersimpan.")
        st.rerun()

    st.divider()

    # ── Step 5: Pilot & Implementation Plan ──
    st.markdown("### Step 5 — Pilot & Implementation Plan")
    st.caption("Diisi oleh Project Leader (bukan agent). Setelah simpan, agent otomatis menyiapkan usulan Communication Plan di Step 6.")

    _pilot_scope = st.text_area(
        "Pilot scope",
        key=f"pilot_scope_{active_pid}",
        placeholder="Contoh: Pilot di line 3, shift pagi, selama 2 minggu",
        height=60, disabled=is_locked,
    )
    _pilot_criteria = st.text_area(
        "Pilot success criteria",
        key=f"pilot_criteria_{active_pid}",
        placeholder="Contoh: A/R Overdue % turun dari 27% ke < 20% selama periode pilot",
        height=60, disabled=is_locked,
    )
    _pilot_owner = st.text_input(
        "Pilot owner",
        key=f"pilot_owner_{active_pid}",
        placeholder="Nama/role", disabled=is_locked,
    )
    _pilot_rollback = st.text_area(
        "Rollback plan",
        key=f"pilot_rollback_{active_pid}",
        placeholder="Jika pilot gagal, langkah apa yang diambil?",
        height=60, disabled=is_locked,
    )

    # Implementation plan table — diisi user (starter empty rows)
    if _impl_key not in st.session_state:
        st.session_state[_impl_key] = _saved_outs.get("implementation_plan") or []

    _impl_list = st.session_state.get(_impl_key, [])
    _impl_seed = _impl_list if _impl_list else [{}]
    _impl_df = pd.DataFrame([{
        "Step":         str(i.get("step","")) if i.get("step") not in (None, "") else "",
        "Action":       i.get("action",""),
        "Owner":        i.get("owner",""),
        "Timeline":     i.get("timeline",""),
        "Deliverable":  i.get("deliverable",""),
        "Dependencies": i.get("dependencies",""),
    } for i in _impl_seed])
    _impl_edit = st.data_editor(
        _impl_df, num_rows="dynamic", use_container_width=True,
        key=f"impl_editor_{active_pid}", disabled=is_locked,
    )
    if st.button("💾 Simpan Implementation Plan", key=f"impl_save_{active_pid}", disabled=is_locked):
        if isinstance(_impl_edit, pd.DataFrame):
            _rows = [r for r in _impl_edit.to_dict(orient="records") if str(r.get("Action","")).strip()]
            st.session_state[_impl_key] = [{
                "step":         str(idx),
                "action":       r.get("Action",""),
                "owner":        r.get("Owner",""),
                "timeline":     r.get("Timeline",""),
                "deliverable":  r.get("Deliverable",""),
                "dependencies": r.get("Dependencies",""),
            } for idx, r in enumerate(_rows, 1)]
        # Trigger Step 6: auto-generate communication plan
        if not is_locked:
            with st.spinner("Agent menyiapkan usulan Communication Plan..."):
                try:
                    from app.agents.improve_agent import _build_communication_plan_llm, _extract_upstream_context
                    _upstream_comm = _extract_upstream_context(define_final, measure_final, analyze_final)
                    _sel_for_comm = st.session_state.get(_sel_key, [])
                    st.session_state[_comm_key] = _build_communication_plan_llm(
                        _sel_for_comm, _upstream_comm, {"stakeholders": ""}
                    )
                except Exception as _e:
                    st.warning(f"Comm plan auto-generate gagal: {_e}")
        st.success("Implementation plan tersimpan. Communication plan disiapkan di Step 6.")
        st.rerun()

    st.divider()

    # ── Step 6: Communication Plan ──
    st.markdown("### Step 6 — Communication Plan")
    st.caption("Agent menyiapkan usulan otomatis setelah Step 5 disimpan. User bisa edit atau langsung simpan.")

    if _comm_key not in st.session_state:
        st.session_state[_comm_key] = _saved_outs.get("communication_plan") or []

    if st.button("🤖 Generate Communication Plan", key=f"comm_gen_{active_pid}", disabled=is_locked):
        with st.spinner("Agent menyusun communication plan..."):
            try:
                from app.agents.improve_agent import _build_communication_plan_llm, _extract_upstream_context
                _upstream_comm2 = _extract_upstream_context(define_final, measure_final, analyze_final)
                st.session_state[_comm_key] = _build_communication_plan_llm(
                    st.session_state.get(_sel_key, []), _upstream_comm2, {"stakeholders": ""}
                )
            except Exception as _e:
                st.warning(f"Generate gagal: {_e}")
        st.rerun()

    _comm_list = st.session_state.get(_comm_key, [])
    if _comm_list:
        _comm_df = pd.DataFrame([{
            "Who Informed":  c.get("who_informed",""),
            "About What":    c.get("about_what",""),
            "Who Informs":   c.get("who_informs",""),
            "Channel":       c.get("channel",""),
            "Due Date":      c.get("due_date",""),
            "Status":        c.get("status","pending"),
        } for c in _comm_list])
        _comm_edit = st.data_editor(
            _comm_df, num_rows="dynamic", use_container_width=True,
            key=f"comm_editor_{active_pid}", disabled=is_locked,
            column_config={
                "Status": st.column_config.SelectboxColumn(
                    "Status", options=["pending","done","in progress"]
                ),
            }
        )
        if st.button("💾 Simpan Communication Plan", key=f"comm_save_{active_pid}", disabled=is_locked):
            if isinstance(_comm_edit, pd.DataFrame):
                st.session_state[_comm_key] = [{
                    "who_informed": r.get("Who Informed",""),
                    "about_what":   r.get("About What",""),
                    "who_informs":  r.get("Who Informs",""),
                    "channel":      r.get("Channel",""),
                    "due_date":     r.get("Due Date",""),
                    "status":       r.get("Status","pending"),
                } for r in _comm_edit.to_dict(orient="records")]
            st.success("Communication plan tersimpan.")
            st.rerun()
    else:
        st.info("Communication plan akan diisi oleh agent setelah generate draft.")

    st.divider()

    # ── Step 7: Improved Process Map (Future State) ──
    st.markdown("### Step 7 — Future State Process Map")
    st.caption("Agent langsung generate Future State swimlane dari selected solution. Bisa di-edit, atau upload diagram milik sendiri. Baseline dari Opsi C ANALYZE jika tersedia.")

    _imp_dot_key = f"improve_pm_dot_{active_pid}"

    # Load analyze WIP untuk baseline current state
    _ana_wip_imp = memory.load_analyze_wip(active_pid)
    _ana_dot_baseline = (
        _ana_wip_imp.get("pm_dot_code") or
        _ana_wip_imp.get("pm_mermaid_code", "")  # fallback lama
    )

    # Load improved dot dari session state
    if _imp_dot_key not in st.session_state:
        st.session_state[_imp_dot_key] = (
            _ana_wip_imp.get("pm_dot_improved") or
            _ana_wip_imp.get("pm_mermaid_improved", "")  # fallback lama
        )

    if _ana_dot_baseline:
        st.caption("✅ Current State map dari ANALYZE tersedia sebagai baseline.")
        with st.expander("📊 Lihat Current State Map", expanded=False):
            try:
                st.graphviz_chart(_ana_dot_baseline, use_container_width=True)
            except Exception:
                st.code(_ana_dot_baseline, language="text")
    else:
        st.info("Belum ada Current State map dari Opsi C ANALYZE. Tetap bisa deskripsikan future state — agent akan buat dari awal.")

    # Selected solutions dari tabel Step 4 (list dict) — dipakai agent untuk transformasi proses
    _sel_for_map_rows = [
        r for r in st.session_state.get(_sel_key, []) if str(r.get("solution","")).strip()
    ]

    # Upload diagram milik sendiri (opsional)
    _imp_upload_key = f"improve_pm_upload_{active_pid}"
    _uploaded_future = st.file_uploader(
        "📤 (Opsional) Upload Future State diagram buatan sendiri (PNG/JPG)",
        type=["png", "jpg", "jpeg"], key=_imp_upload_key, disabled=is_locked,
    )
    if _uploaded_future is not None:
        st.image(_uploaded_future, caption="Future State (uploaded)", use_container_width=True)

    _imp_map_desc = st.text_area(
        "Deskripsi tambahan perubahan proses (opsional — agent sudah pakai selected solution):",
        key=f"improve_map_desc_{active_pid}",
        height=100,
        placeholder=(
            "Opsional. Contoh:\n"
            "- Input manual Excel dieliminasi, diganti sistem otomatis\n"
            "- Notifikasi ke gudang real-time via sistem\n"
            "- Tambahkan automated QC check setelah receiving"
        ),
        disabled=is_locked,
    )

    _col_imp_gen, _col_imp_clr = st.columns([2, 1])
    with _col_imp_gen:
        _imp_gen_disabled = is_locked or not (str(_imp_map_desc or "").strip() or _sel_for_map_rows)
        if st.button("🤖 Generate Future State Swimlane", key=f"imp_map_gen_{active_pid}", disabled=_imp_gen_disabled):
            with st.spinner("Agent membuat Future State swimlane..."):
                from app.agents.improve_agent import generate_improved_process_map_graphviz
                _imp_result = generate_improved_process_map_graphviz(
                    current_dot=_ana_dot_baseline,
                    improvement_description=_imp_map_desc,
                    selected_solution=_sel_for_map_rows,
                )
                if _imp_result.get("dot_code"):
                    st.session_state[_imp_dot_key] = _imp_result["dot_code"]
                    _wip_imp_save = memory.load_analyze_wip(active_pid)
                    _wip_imp_save["pm_dot_improved"] = _imp_result["dot_code"]
                    _wip_imp_save["pm_dot_improved_summary"] = _imp_result.get("summary", "")
                    memory.save_analyze_wip(active_pid, _wip_imp_save)
                    st.rerun()
                else:
                    st.error("Agent tidak berhasil generate diagram. Coba perjelas deskripsi.")
    with _col_imp_clr:
        if st.button("🗑️ Hapus Diagram", key=f"imp_map_clr_{active_pid}", disabled=is_locked):
            st.session_state[_imp_dot_key] = ""
            _wip_imp_clr = memory.load_analyze_wip(active_pid)
            _wip_imp_clr["pm_dot_improved"] = ""
            memory.save_analyze_wip(active_pid, _wip_imp_clr)
            st.rerun()

    _cur_imp_dot = st.session_state.get(_imp_dot_key, "")
    if _cur_imp_dot:
        st.markdown("**Future State Process Map:**")
        st.caption("🟢 Node hijau = baru/berubah setelah improvement")
        try:
            st.graphviz_chart(_cur_imp_dot, use_container_width=True)
        except Exception as _e:
            st.error(f"Diagram tidak valid: {_e}")
            st.code(_cur_imp_dot, language="text")

        with st.expander("🔄 Revisi Future State Diagram", expanded=False):
            _rev_imp_desc = st.text_area(
                "Apa yang perlu diubah?",
                key=f"imp_map_revise_{active_pid}",
                height=80,
                placeholder="Contoh: Tambahkan approval step dari manager sebelum pengiriman...",
                disabled=is_locked,
            )
            if st.button("🔄 Apply Revisi", key=f"imp_map_rev_btn_{active_pid}",
                         disabled=is_locked or not str(_rev_imp_desc or "").strip()):
                with st.spinner("Agent merevisi..."):
                    from app.agents.analyze_agent import revise_process_map_graphviz
                    _rev_imp_r = revise_process_map_graphviz(_cur_imp_dot, _rev_imp_desc)
                    if _rev_imp_r.get("dot_code"):
                        st.session_state[_imp_dot_key] = _rev_imp_r["dot_code"]
                        _wip_rrev = memory.load_analyze_wip(active_pid)
                        _wip_rrev["pm_dot_improved"] = _rev_imp_r["dot_code"]
                        memory.save_analyze_wip(active_pid, _wip_rrev)
                        st.rerun()

        with st.expander("📋 Lihat / Copy DOT Code", expanded=False):
            st.code(_cur_imp_dot, language="text")

    st.divider()

    # ── Generate Draft (tanpa input tambahan) ──
    _def_ins = (define_final or {}).get("inputs") or {}
    _industry    = _def_ins.get("industry","") or "-"
    _process     = _def_ins.get("process_area","") or "-"
    _pain        = _def_ins.get("pain_theme","") or "-"
    st.caption(f"📌 From Define: **{_industry}** / **{_process}** / **{_pain}**")
    st.caption("Draft dirangkai dari data Step 1–7 yang sudah kamu isi. Tidak perlu input tambahan.")

    _form_disabled = is_locked or _no_confirmed_rc
    submitted = st.button(
        "🔍 Generate / Update Draft" if not is_locked else "🔒 Locked",
        key="improve_generate_draft_btn",
        type="primary", disabled=_form_disabled,
    )

    if submitted:
        user_inputs = {
            "industry":             _industry,
            "process_area":         _process,
            "pain_theme":           _pain,
            "solution_ideas":       "",
            "constraints":          "",
            "must_criteria":        st.session_state.get(_must_key, []),
            "must_skipped":         st.session_state.get(_must_skipped_key, False),
            "potential_solutions":  st.session_state.get(_sol_key, []),
            "selected_solutions":   st.session_state.get(_sel_key, []),
            "communication_plan":   st.session_state.get(_comm_key, []),
            "implementation_plan":  st.session_state.get(_impl_key, []),
            "implementation_status": st.session_state.get(_status_key, []),
            "pilot_inputs": {
                "scope":            _pilot_scope,
                "success_criteria": _pilot_criteria,
                "owner":            _pilot_owner,
                "rollback_plan":    _pilot_rollback,
            },
            "comm_inputs": {"stakeholders": ""},
            "improved_process_map_dot":     st.session_state.get(_imp_dot_key, ""),
            "improved_process_map_summary": (memory.load_analyze_wip(active_pid) or {}).get("pm_dot_improved_summary", ""),
            "project_path": _proj_path,
        }
        user_feedback = None

        with st.spinner("Running IMPROVE agent..."):
            result = run_improve_agent(
                project_id=active_pid,
                user_inputs=user_inputs,
                define_final=define_final,
                measure_final=measure_final,
                analyze_final=analyze_final,
                user_feedback=user_feedback,
                project_path=_proj_path,
            )

        # Sync agent outputs back to session state
        _agent_outs = result.get("improve_state",{}).get("outputs",{})
        if _agent_outs.get("must_criteria"):
            st.session_state[_must_key] = _agent_outs["must_criteria"]
        if _agent_outs.get("potential_solutions"):
            st.session_state[_sol_key]  = _agent_outs["potential_solutions"]
        if _agent_outs.get("selected_solutions"):
            st.session_state[_sel_key]  = _agent_outs["selected_solutions"]
        if _agent_outs.get("implementation_plan"):
            st.session_state[_impl_key] = _agent_outs["implementation_plan"]
        if _agent_outs.get("communication_plan"):
            st.session_state[_comm_key] = _agent_outs["communication_plan"]
        if _agent_outs.get("implementation_status"):
            st.session_state[_status_key] = _agent_outs["implementation_status"]

        st.session_state["improve_draft"] = result
        st.session_state["improve_final"] = None
        st.session_state[_revise_key]     = False
        st.rerun()

    # ── Agent Coaching ──
    _cur = st.session_state.get("improve_draft") or st.session_state.get("improve_final")
    _is  = (_cur or {}).get("improve_state") if isinstance(_cur, dict) else None
    if isinstance(_is, dict):
        gate     = _is.get("gate") or {}
        status   = gate.get("status","-")
        coaching = (_is.get("coaching_md") or "").strip()
        st.markdown("### 🧭 Agent Coaching")
        st.markdown(f"**Gate Status:** `{status}`")
        if coaching:
            st.markdown(coaching)
        else:
            st.info("Generate draft untuk melihat coaching.")

    st.divider()

    # ── Step 8: Implementation Status ──
    st.markdown("### Step 8 — Implementation Status")
    st.caption("Update status implementasi tiap solusi. Fase tidak bisa di-finalize sampai SEMUA status = done.")

    _STATUS_OPTS = ["not started", "in progress", "done"]

    # Seed dari selected_solutions jika belum ada
    if _status_key not in st.session_state:
        st.session_state[_status_key] = _saved_outs.get("implementation_status") or []

    _sel_for_status = st.session_state.get(_sel_key, [])
    _status_rows = st.session_state.get(_status_key, [])
    # Build/merge: pastikan setiap selected solution punya baris status
    _existing_by_sol = {str(s.get("solution","")).strip(): s for s in _status_rows}
    _merged_status = []
    for r in _sel_for_status:
        _sol_name = str(r.get("solution","")).strip()
        if not _sol_name:
            continue
        _prev = _existing_by_sol.get(_sol_name, {})
        _merged_status.append({
            "solution": _sol_name,
            "status":   _prev.get("status","not started"),
            "notes":    _prev.get("notes",""),
        })
    # tambahkan baris status lama yang tidak terkait selected (jaga-jaga)
    if _merged_status:
        _status_rows = _merged_status

    if _status_rows:
        _status_df = pd.DataFrame([{
            "Solution": s.get("solution",""),
            "Status":   s.get("status","not started"),
            "Notes":    s.get("notes",""),
        } for s in _status_rows])
        _status_edit = st.data_editor(
            _status_df, num_rows="fixed", use_container_width=True,
            key=f"status_editor_{active_pid}", disabled=is_locked,
            column_config={
                "Solution": st.column_config.TextColumn("Solution", disabled=True),
                "Status":   st.column_config.SelectboxColumn("Status", options=_STATUS_OPTS, required=True),
            },
        )
        if st.button("💾 Simpan Implementation Status", key=f"status_save_{active_pid}", disabled=is_locked):
            if isinstance(_status_edit, pd.DataFrame):
                st.session_state[_status_key] = [{
                    "solution": r.get("Solution",""),
                    "status":   r.get("Status","not started"),
                    "notes":    r.get("Notes",""),
                } for r in _status_edit.to_dict(orient="records")]
            st.success("Implementation status tersimpan.")
            st.rerun()

        _all_done = bool(_status_rows) and all(
            str(s.get("status","")).strip().lower() == "done" for s in st.session_state.get(_status_key, _status_rows)
        )
        if _all_done:
            st.success("✅ Semua solusi sudah **done** — fase Improve siap di-finalize.")
        else:
            st.warning("⏳ Belum semua solusi **done** — Finalize masih diblokir.")
    else:
        st.info("Isi & simpan Selected Solution (Step 4) terlebih dahulu agar status implementasi muncul di sini.")

# ============================================
# REPORT TABS (view, res, improve_state shared across tabs)
# ============================================
view, res = _get_current_result()
improve_state = res["improve_state"] if (res and res.get("improve_state")) else None

# ── TAB 2: Report ──
with tab_report:
    if not improve_state:
        st.info("Generate draft untuk melihat report.")
    else:
        outs     = improve_state.get("outputs") or {}
        upstream = improve_state.get("upstream") or {}

        with st.expander("🔗 Context from DEFINE + MEASURE + ANALYZE", expanded=True):
            st.markdown(f"**Problem:** {upstream.get('problem_statement') or '-'}")
            st.markdown(f"**Goal:** {upstream.get('goal_statement') or '-'}")
            st.markdown(f"**Y Variable:** {upstream.get('y_variable') or '-'}")
            st.markdown(f"**Baseline:** {upstream.get('baseline_mean') or '-'}")
            confirmed = upstream.get("confirmed_root_causes") or []
            if confirmed:
                st.markdown(f"**Confirmed RCs:** {len(confirmed)} causes to address")

        gate     = improve_state.get("gate") or {}
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

        _coaching = (improve_state.get("coaching_md") or "").strip()
        if _coaching:
            st.divider()
            st.markdown("### 🧭 Agent Coaching")
            st.markdown(_coaching)

        selected_list = outs.get("selected_solutions") or []
        if selected_list:
            st.divider()
            st.markdown("### ✅ Selected Solutions")
            st.dataframe(pd.DataFrame([{
                "No":               s.get("no", i + 1),
                "Solution":         s.get("solution",""),
                "CTQ/CTB Impacted": s.get("ctq_ctb_impacted",""),
                "Comments":         s.get("comments",""),
            } for i, s in enumerate(selected_list)]), use_container_width=True, hide_index=True)

        impl_status = outs.get("implementation_status") or []
        if impl_status:
            st.divider()
            st.markdown("### 📊 Implementation Status")
            st.dataframe(pd.DataFrame([{
                "Solution": s.get("solution",""),
                "Status":   s.get("status","not started"),
                "Notes":    s.get("notes",""),
            } for s in impl_status]), use_container_width=True, hide_index=True)

        _improved_dot_rep = outs.get("improved_process_map_dot","")
        should_be = outs.get("should_be_process","")
        if _improved_dot_rep or should_be:
            st.divider()
            st.markdown("### 🔄 Future State / Should-Be Process")
            if should_be:
                st.markdown(should_be)
            if _improved_dot_rep:
                # Render diagram Future State yang SAMA dengan Step 7
                try:
                    st.graphviz_chart(_improved_dot_rep, use_container_width=True)
                except Exception:
                    st.code(_improved_dot_rep, language="text")
            elif should_be:
                # fallback: bangun diagram dari teks bila swimlane belum digenerate
                _dot = _build_graphviz_process(should_be)
                if _dot:
                    try:
                        st.graphviz_chart(_dot, use_container_width=True)
                    except Exception:
                        pass

# ── TAB 3: Solutions & Selection ──
with tab_solutions:
    if not improve_state:
        st.info("Generate draft untuk melihat solutions.")
    else:
        outs = improve_state.get("outputs") or {}

        must_criteria = outs.get("must_criteria") or []
        if must_criteria:
            st.markdown("### Must Criteria")
            st.dataframe(pd.DataFrame(must_criteria), use_container_width=True, hide_index=True)
            st.divider()

        solutions = outs.get("potential_solutions") or []
        if solutions:
            st.markdown("### Potential Solutions")
            _sol_display = pd.DataFrame([{
                "ID":           s.get("solution_id",""),
                "Idea":         s.get("idea",""),
                "Root Cause":   s.get("addresses_root_cause",""),
                "All MC Met":   "✅" if s.get("all_must_criteria_met") else "❌",
                "Type":         s.get("type",""),
                "Investment":   s.get("investment_needed",""),
                "Est. Cost":    s.get("investment_estimate",""),
            } for s in solutions])
            st.dataframe(_sol_display, use_container_width=True, hide_index=True)
            st.divider()

        selected_list = outs.get("selected_solutions") or []
        if selected_list:
            st.markdown("### ✅ Selected Solutions")
            st.dataframe(pd.DataFrame([{
                "No":               s.get("no", i + 1),
                "Solution":         s.get("solution",""),
                "CTQ/CTB Impacted": s.get("ctq_ctb_impacted",""),
                "Comments":         s.get("comments",""),
            } for i, s in enumerate(selected_list)]), use_container_width=True, hide_index=True)

# ── TAB 4: Implementation ──
with tab_impl:
    if not improve_state:
        st.info("Generate draft untuk melihat implementation plan.")
    else:
        outs = improve_state.get("outputs") or {}

        pilot = outs.get("pilot_plan") or {}
        if pilot:
            st.markdown("### Pilot Plan")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Scope:** {pilot.get('scope','-')}")
                st.markdown(f"**Duration:** {pilot.get('duration','-')}")
                st.markdown(f"**Owner:** {pilot.get('owner','-')}")
            with c2:
                st.markdown(f"**Success criteria:** {pilot.get('success_criteria','-')}")
                st.markdown(f"**Rollback:** {pilot.get('rollback_plan','-')}")
                resources = pilot.get("resources_needed") or []
                if resources:
                    st.markdown(f"**Resources:** {', '.join(resources)}")
            st.divider()

        impl = outs.get("implementation_plan") or []
        if impl:
            st.markdown("### Implementation Plan")
            st.dataframe(pd.DataFrame(impl), use_container_width=True, hide_index=True)
            st.divider()

        risks = outs.get("risks_and_assumptions") or []
        if risks:
            st.markdown("### Risks & Assumptions")
            for r in risks:
                st.write(f"- {r}")

# ── TAB 5: Communication Plan ──
with tab_comm:
    if not improve_state:
        st.info("Generate draft untuk melihat communication plan.")
    else:
        outs = improve_state.get("outputs") or {}
        comm = outs.get("communication_plan") or []
        if comm:
            st.markdown("### Communication Plan")
            st.dataframe(
                pd.DataFrame(comm),
                use_container_width=True,
                hide_index=True,
            )
            # Summary stats
            pending = sum(1 for c in comm if c.get("status","").lower() == "pending")
            done    = sum(1 for c in comm if c.get("status","").lower() == "done")
            st.caption(f"Total: {len(comm)} items | ⏳ Pending: {pending} | ✅ Done: {done}")
        else:
            st.info("Communication plan akan tersedia setelah generate draft.")

# ── TAB: Should-Be Process (Future State) ──
with tab_should_be:
    if not improve_state:
        st.info("Generate draft untuk melihat Should-Be / Future State process.")
    else:
        outs = improve_state.get("outputs") or {}
        _sb_dot     = outs.get("improved_process_map_dot", "")
        _sb_summary = outs.get("should_be_process", "") or outs.get("improved_process_map_summary", "")

        st.markdown("### 🔄 Should-Be / Future State Process")

        # Current State baseline (untuk perbandingan)
        _sb_wip = memory.load_analyze_wip(active_pid) or {}
        _sb_baseline = _sb_wip.get("pm_dot_code") or _sb_wip.get("pm_mermaid_code", "")
        if _sb_baseline:
            with st.expander("📊 Current State Map (baseline dari ANALYZE)", expanded=False):
                try:
                    st.graphviz_chart(_sb_baseline, use_container_width=True)
                except Exception:
                    st.code(_sb_baseline, language="text")

        if _sb_summary:
            st.markdown(_sb_summary)

        if _sb_dot:
            st.caption("🟢 Node hijau = baru/berubah setelah improvement")
            try:
                st.graphviz_chart(_sb_dot, use_container_width=True)
            except Exception:
                st.code(_sb_dot, language="text")
            with st.expander("📋 Lihat / Copy DOT Code", expanded=False):
                st.code(_sb_dot, language="text")
        elif _sb_summary:
            # fallback: bangun diagram dari teks bila swimlane belum digenerate
            _sb_fallback = _build_graphviz_process(_sb_summary)
            if _sb_fallback:
                try:
                    st.graphviz_chart(_sb_fallback, use_container_width=True)
                except Exception:
                    pass
        else:
            st.info("Future State swimlane belum dibuat. Buat di tab Input → Step 7.")

with tab_input:
    # ============================================
    # Finalize & Downloads (full width)
    # ============================================
    st.divider()
    st.subheader("Finalize & Downloads")

    _draft_wrapper  = st.session_state.get("improve_draft")
    _has_draft_now  = bool(_draft_wrapper and _draft_wrapper.get("improve_state"))
    try:
        _gate_status_now = (_draft_wrapper or {}).get("improve_state",{}).get("gate",{}).get("status")
    except Exception:
        _gate_status_now = None

    c1, c2, c3 = st.columns([1,1,1])

    with c1:
        # Cek implementation status: semua harus done
        _fin_status_rows = st.session_state.get(_status_key, [])
        _impl_all_done = bool(_fin_status_rows) and all(
            str(s.get("status","")).strip().lower() == "done" for s in _fin_status_rows
        )
        if _has_draft_now:
            _gate_fail_block = (_gate_status_now == "FAIL") and not _appr_status.get("can_advance", False)
            fin_disabled = _gate_fail_block or (not _impl_all_done)
            if st.button("✅ Finalize (Lock Final)", key="improve_finalize_btn", disabled=fin_disabled):
                draft_state  = _draft_wrapper["improve_state"]
                final_result = finalize_improve_agent(active_pid, draft_state)
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason","Blocked by gate."))
                else:
                    st.session_state["improve_final"] = final_result
                    st.session_state["improve_draft"] = None
                    st.session_state[_revise_key]     = False
                    st.success("Finalized.")
                    st.rerun()
            if _gate_fail_block:
                st.caption("⚠️ Finalize diblokir: Gate FAIL.")
            elif not _impl_all_done:
                st.caption("⚠️ Finalize diblokir: semua Implementation Status (Step 8) harus **done**.")
        elif _has_final:
            st.button("✅ Finalized", key="improve_finalized_badge", disabled=True)
        else:
            st.button("✅ Finalize (Lock Final)", key="improve_fin_disabled", disabled=True)
            st.caption("Generate draft terlebih dahulu.")

    with c2:
        _word_src = final_on_disk or draft_on_disk
        _word_lbl = "🛠️ Generate Word (Final)" if final_on_disk else "🛠️ Generate Word (Draft)"
        if _word_src:
            if st.button(_word_lbl, key="improve_gen_word"):
                export_fn = getattr(reporting, "export_improve_to_word", None)
                if callable(export_fn):
                    try:
                        _docx_fname = f"Project_{active_pid}_DEF_MEA_ANA_IMP_{'Final' if final_on_disk else 'Draft'}.docx"
                        out_path = export_fn(copy.deepcopy(_word_src), path=_docx_fname)
                        st.session_state["improve_last_docx"] = out_path
                        st.success("Word file generated.")
                    except Exception as e:
                        st.warning(f"Word export belum tersedia: {e}")
                else:
                    st.warning("export_improve_to_word belum diimplementasi.")
        else:
            st.button("🛠️ Generate Word", key="improve_gen_word_dis", disabled=True)

    with c3:
        _docx_fname_imp = f"Project_{active_pid}_DEF_MEA_ANA_IMP_{'Final' if final_on_disk else 'Draft'}.docx"
        _docx_path = st.session_state.get("improve_last_docx") or _docx_fname_imp
        _dl_label  = "⬇️ Download Final Word Report" if final_on_disk else "⬇️ Download Draft Word Report"
        try:
            with open(_docx_path,"rb") as f:
                doc_bytes = f.read()
            st.download_button(
                label=_dl_label, data=doc_bytes,
                file_name=_docx_fname_imp,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="improve_dl_word",
            )
        except FileNotFoundError:
            st.button(_dl_label, key="improve_dl_word_dis", disabled=True)

    # ============================================
    # Governance — Reviewer (standard) / Auto (quick) for Improve
    # ============================================
    st.divider()
    st.subheader("🏛️ Governance — Stage Gate Approval")

    final_exists    = memory.load_improve_final(active_pid)
    approval_status = memory.get_phase_approval_status(active_pid, "improve")
    reviewer_action = (approval_status.get("reviewer") or {}).get("action")
    champion_action = (approval_status.get("champion") or {}).get("action")
    can_advance     = approval_status.get("can_advance", False)
    auto_advance    = approval_status.get("auto_advance", False)
    required        = approval_status.get("required_approvers", [])
    _prev_approved_ip = approval_status.get("prev_approved", True)

    if not final_exists:
        st.info("Finalize IMPROVE dulu sebelum bisa di-approve.")
    elif auto_advance:
        if _prev_approved_ip:
            st.success("🚀 Improve gate **auto-advance** — tidak perlu approval. Lanjut ke Control.")
        else:
            st.warning("⏳ Gate terkunci — ANALYZE belum disetujui. Selesaikan approval ANALYZE terlebih dahulu.")
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
                st.success("🚀 Gate: OPEN — Lanjut ke Control")
            else:
                st.info("🔒 Gate: LOCKED")

        role = auth.get_current_role()
        if not _prev_approved_ip:
            st.warning("⚠️ ANALYZE belum disetujui. Approval fase ini belum bisa dilakukan.")
        elif role in required:
            _role_action = (approval_status.get(role) or {}).get("action")
            st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
            if _role_action == "approve":
                st.success("✅ Kamu sudah **Approve** fase ini.")
            elif _role_action == "reject":
                st.error("❌ Kamu sudah **Reject** fase ini.")
            note = st.text_area("Catatan (opsional)", key=f"gov_note_{role}_improve", height=80)
            col_app, col_rej = st.columns(2)
            with col_app:
                if st.button("✅ Approve", key=f"gov_approve_{role}_improve",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid,"improve",role,"approve",note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid,"improve",f"approved_by_{role}")
                    st.success("Approval disimpan.")
                    st.rerun()
            with col_rej:
                if st.button("❌ Reject", key=f"gov_reject_{role}_improve",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid,"improve",role,"reject",note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid,"improve",f"rejected_by_{role}")
                    st.warning("Rejection disimpan.")
                    st.rerun()
        elif role == "project_leader":
            if can_advance:
                st.success("✅ Approval lengkap. Lanjut ke Control.")
            else:
                st.info("Menunggu approval dari approver yang dibutuhkan.")

    # ============================================
    # Audit Log
    # ============================================
    st.divider()
    st.subheader("🧾 Audit Log — IMPROVE Phase")
    events = audit.get_recent_events(project_id=active_pid, phase="Improve", limit=30)
    if events:
        st.dataframe(events, use_container_width=True)
    else:
        st.write("No audit events found.")