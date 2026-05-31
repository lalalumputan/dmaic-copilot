import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import json
import copy
import streamlit as st
import pandas as pd

from app.agents.measure_agent import (
    run_measure_agent,
    finalize_measure_agent,
    _propose_output_measurements_from_ctq,
    _extract_ctq_final_from_define,
    _evaluate_measurement_candidates_llm,
     _propose_operational_definitions_llm,
)
from app.utils import memory, reporting, audit
from app.utils import auth
from app.utils import charts as _charts
from app.utils.ui_helpers import render_sidebar_header

# ============================================
# Helpers
# ============================================

def _build_measure_md(state: dict) -> str:
    fn = getattr(reporting, "build_measure_summary_md", None)
    if callable(fn):
        out = fn(state)
        if isinstance(out, str) and out.strip():
            return out
    fn2 = getattr(reporting, "build_measure_summary_markdown", None)
    if callable(fn2):
        out = fn2(state)
        if isinstance(out, str) and out.strip():
            return out
    smd = (state or {}).get("summary_md")
    if isinstance(smd, str) and smd.strip():
        return smd
    return ""


def _get_current_result():
    """Return ('draft'|'final'|'none', wrapper_result_or_none)."""
    if st.session_state.get("measure_draft"):
        return "draft", st.session_state["measure_draft"]
    if st.session_state.get("measure_final"):
        return "final", st.session_state["measure_final"]
    return "none", None


def _safe_get(d, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _seed_measure_inputs_from_define(project_id: str, define_final: dict) -> dict:
    inputs = (define_final or {}).get("inputs") or {}
    outs = (define_final or {}).get("outputs") or {}
    return {
        "project_id":   project_id,
        "project_name": inputs.get("project_name") or project_id,
        "industry":     inputs.get("industry") or "",
        "pain_theme":   inputs.get("pain_theme") or "",
        "process_area": inputs.get("process_area") or "",
        "y_variable":   outs.get("y_variable") or "",
    }


def _seed_operational_definitions_from_define(define_final: dict) -> list:
    inputs = (define_final or {}).get("inputs") or {}
    outs = (define_final or {}).get("outputs") or {}
    y = outs.get("y_variable") or inputs.get("y_variable") or ""
    return [
        {
            "metric_name": y or "Primary Y",
            "what_measured": "Primary outcome metric (from DEFINE)",
            "instrument_system": "ERP / MES / LIMS / Source system",
            "method_formula": "Define formula here (numerator/denominator clear)",
            "unit": "%",
            "frequency": "Monthly",
            "decision_criteria": "Target/spec from DEFINE",
        },
        {
            "metric_name": "Supporting Metric 1",
            "what_measured": "A key driver/companion measure that affects Y",
            "instrument_system": "Source system / log",
            "method_formula": "Define formula here",
            "unit": "Count",
            "frequency": "Weekly",
            "decision_criteria": "Lower/Upper is better",
        },
    ]


# ============================================
# Page Config + CSS
# ============================================
st.set_page_config(page_title="MEASURE", layout="wide")
st.markdown("""
<style>
:root {
    color-scheme: light !important;
}
.stApp {
    background-color: #f4f8fb;
    color: #0f172a;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
}
section[data-testid="stSidebar"] {
    background-color: #ffffff;
    border-right: 1px solid #e2e8f0;
}
.card {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 18px 20px;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
    margin-bottom: 18px;
}
h1, h2, h3 { color: #0f172a; }
h2 { font-size: 22px; margin-bottom: 12px; }
h3 { font-size: 18px; margin-bottom: 8px; }
.stButton > button {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    color: #0f172a;
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 500;
}
.stButton > button:hover {
    background-color: #e0f2fe;
    border-color: #38bdf8;
}
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div,
div[data-baseweb="select"] > div {
    background-color: #ffffff !important;
    border: 1px solid #cbd5e1 !important;
    border-radius: 10px !important;
    color: #0f172a !important;
}
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
div[data-testid="stDataFrame"],
div[data-testid="stDataEditor"] {
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    background-color: #ffffff;
}
</style>
""", unsafe_allow_html=True)

auth.require_login()
auth.render_user_badge()

# ============================================
# Session defaults
# ============================================
st.session_state.setdefault("active_project_id", None)
st.session_state.setdefault("measure_draft", None)
st.session_state.setdefault("measure_final", None)
st.session_state.setdefault("define_final_state", None)
st.session_state.setdefault("measure_baseline_df", None)
st.session_state.setdefault("measure_uploaded_name", None)
st.session_state.setdefault("measure_feedback_text", "")

# ============================================
# Sidebar
# ============================================
render_sidebar_header()
st.sidebar.divider()

active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")

    # Phase status ringkas (same as Define)
    phases = ["define", "measure", "analyze", "improve", "control"]
    labels = ["DEF", "MEA", "ANA", "IMP", "CON"]
    icons = []
    for ph in phases:
        f = memory._load_state(active_pid, ph, "final")
        d = memory._load_state(active_pid, ph, "draft")
        a = memory.get_phase_approval_status(active_pid, ph)
        if f and a.get("can_advance"):  icons.append("✅")
        elif f:                          icons.append("🔒")
        elif d:                          icons.append("🔄")
        else:                            icons.append("⬜")

    phase_row = " | ".join([f"{icons[i]} {labels[i]}" for i in range(5)])
    st.sidebar.caption(phase_row)
    st.sidebar.divider()

    # Governance ringkas
    appr = memory.get_phase_approval_status(active_pid, "measure")
    auto_adv = appr.get("auto_advance", False)
    if auto_adv:
        st.sidebar.success("🚀 Measure: Auto-advance")
    else:
        rev = (appr.get("reviewer") or {}).get("action") or "pending"
        chmp = (appr.get("champion") or {}).get("action") or "pending"
        rev_icon = "✅" if rev == "approve" else ("❌" if rev == "reject" else "⏳")
        chmp_icon = "✅" if chmp == "approve" else ("❌" if chmp == "reject" else "⏳")
        st.sidebar.markdown(f"**Measure Gate:**  \n{rev_icon} Reviewer  |  {chmp_icon} Champion")
        if appr.get("can_advance"):
            st.sidebar.success("🚀 Gate OPEN")
        else:
            st.sidebar.info("🔒 Gate LOCKED")
else:
    st.sidebar.warning("Belum ada project aktif.")
    st.sidebar.caption("Kembali ke **Main** untuk memilih project.")

# ============================================
# Gate: no active project
# ============================================
if not active_pid:
    st.title("📏 MEASURE Phase")
    st.info("Belum ada project aktif. Kembali ke halaman **Main** untuk memilih project.")
    st.stop()

# ============================================
# Auto-load (same pattern as Define)
# ============================================
loaded_pid = st.session_state.get("_loaded_pid")
if active_pid and loaded_pid != active_pid:
    st.session_state["_loaded_pid"]           = active_pid
    st.session_state["measure_draft"]         = None
    st.session_state["measure_final"]         = None
    st.session_state["define_final_state"]    = None
    st.session_state["measure_baseline_df"]   = None
    st.session_state["measure_uploaded_name"] = None

    define_disk        = memory.load_define_final(active_pid)
    measure_draft_disk = memory.load_measure_draft(active_pid)
    measure_final_disk = memory.load_measure_final(active_pid)

    st.session_state["define_final_state"] = define_disk

    if measure_draft_disk:
        st.session_state["measure_draft"] = {
            "status": "draft",
            "measure_state": measure_draft_disk,
            "summary": measure_draft_disk.get("summary_md", ""),
            "message": "Auto-loaded DRAFT from disk",
        }
    if measure_final_disk:
        st.session_state["measure_final"] = {
            "status": "finalized",
            "measure_state": measure_final_disk,
            "summary": measure_final_disk.get("summary_md", ""),
            "message": "Auto-loaded FINAL from disk",
        }

# Ensure define_final_state loaded
if active_pid and st.session_state.get("_loaded_pid") == active_pid:
    if not st.session_state.get("define_final_state"):
        st.session_state["define_final_state"] = memory.load_define_final(active_pid)

define_final = st.session_state.get("define_final_state")
if not define_final:
    st.title("MEASURE Phase")
    st.warning("DEFINE FINAL tidak ditemukan untuk project ini. Selesaikan dan finalize DEFINE terlebih dahulu.")
    st.stop()

# ============================================
# Header
# ============================================
logo_path = "app/assets/logo3.jpg"
h_title, h_logo = st.columns([5, 1])

with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)

with h_title:
    st.markdown("# DMAIC Copilot — MEASURE Phase")
    _proj_meta  = memory.load_project_meta(active_pid) if active_pid else {}
    _proj_path  = _proj_meta.get("path", "standard")
    _path_label = "🟢 Quick Improvement Project" if _proj_path == "quick" else "🔵 Standard DMAIC Project"
    st.caption(f"Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC Methodology &nbsp;|&nbsp; {_path_label}")

st.divider()

# Project info bar
_def_title_hdr = (
    (define_final or {}).get("inputs", {}).get("project_name", "")
    or (memory.load_define_draft(active_pid) or {}).get("inputs", {}).get("project_name", "")
    or st.session_state.get("active_project_name", "")
    or st.session_state.get("define_project_name_input", "")
)
if _def_title_hdr:
    st.markdown(f"<p style='font-size:1.05rem;font-weight:700;color:#0f172a;margin:0 0 4px 0;'>{_def_title_hdr}</p>", unsafe_allow_html=True)
h1, h2 = st.columns([5, 1])
with h1:
    st.caption(f"**Active Project:** `{active_pid}`")
with h2:
    view_badge = "FINAL" if memory.load_measure_final(active_pid) else "DRAFT"
    st.markdown(
        f"<div style='text-align:right'><span style='padding:6px 12px;border-radius:20px;"
        f"background:#0f5132;color:#d1e7dd;font-weight:600;'>"
        f"{view_badge}</span></div>",
        unsafe_allow_html=True,
    )

# ============================================
# Tab layout
# ============================================
view, res = _get_current_result()
measure_state = res["measure_state"] if (res and res.get("measure_state")) else None

tab_input, tab_report, tab_tables, tab_perf, tab_msa = st.tabs([
    "✏️ Input",
    "📄 Report",
    "📊 Tabel Detail",
    "📈 Performance",
    "🔍 MSA",
])

# ─── Shared state variables ───
seed          = _seed_measure_inputs_from_define(active_pid, define_final)
final_on_disk = memory.load_measure_final(active_pid) if active_pid else None
draft_on_disk = memory.load_measure_draft(active_pid) if active_pid else None
_has_final    = bool(final_on_disk)
_has_draft    = bool(draft_on_disk)

_appr_status  = memory.get_phase_approval_status(active_pid, "measure")
_req_roles    = _appr_status.get("required_approvers", [])
_is_rejected  = any(
    (_appr_status.get(r) or {}).get("action") == "reject"
    for r in _req_roles
)
_revise_mode_key = f"measure_revise_mode_{active_pid}"
st.session_state.setdefault(_revise_mode_key, False)
_in_revise_mode = st.session_state.get(_revise_mode_key, False)
is_locked = _has_final and not _in_revise_mode and not _has_draft

# ============================================
# INPUT TAB
# ============================================
with tab_input:

    # ── Revision Actions (same pattern as Define) ──
    revise_clicked        = False
    discard_draft_clicked = False

    if _has_final and _is_rejected and not _has_draft and not _in_revise_mode:
        revise_clicked = st.button("✏️ Revise Final", key="measure_revise_btn",
                                   help="Buka form untuk merevisi dan membuat draft baru.")
    elif _has_draft:
        discard_draft_clicked = st.button("🧹 Discard Draft", key="measure_discard_draft_btn",
                                          help="Hapus draft ini dan kembali ke final terakhir.")

    if revise_clicked:
        st.session_state[_revise_mode_key] = True
        st.session_state["measure_draft"]  = None
        st.rerun()

    if discard_draft_clicked:
        delete_fn = getattr(memory, "delete_measure_draft", None)
        if callable(delete_fn):
            delete_fn(active_pid)
        st.session_state["measure_draft"] = None
        st.session_state["measure_final"] = None
        st.session_state[_revise_mode_key] = False
        if final_on_disk:
            st.session_state["measure_final"] = {
                "status": "finalized",
                "measure_state": final_on_disk,
                "summary": final_on_disk.get("summary_md", ""),
                "message": "Reverted to final.",
            }
        st.rerun()

# ── Step 1: Brought forward from DEFINE ──
    st.markdown("### Step 1 — Brought Forward from DEFINE")
    _def_outs_lc  = (define_final or {}).get("outputs") or {}
    _def_ins_lc   = (define_final or {}).get("inputs") or {}
    _voc_tbl      = _def_ins_lc.get("voc_table") or []
    _ctq_lc       = _def_outs_lc.get("ctq_list") or []

    with st.expander("📋 VOC/VOB → CTQ/CTB (dari DEFINE)", expanded=True):
        if _voc_tbl:
            st.dataframe(pd.DataFrame(_voc_tbl), use_container_width=True, hide_index=True)
        if _ctq_lc:
            st.markdown("**CTQ/CTB:**")
            _ctq_rows = []
            for c in _ctq_lc:
                if isinstance(c, dict):
                    _ctq_rows.append({
                        "Name":   c.get("name") or c.get("ctq") or str(c),
                        "Metric": c.get("metric") or "-",
                        "Unit":   c.get("unit") or "-",
                    })
                else:
                    _ctq_rows.append({"Name": str(c), "Metric": "-", "Unit": "-"})
            st.dataframe(pd.DataFrame(_ctq_rows), use_container_width=True, hide_index=True)
        if not _voc_tbl and not _ctq_lc:
            st.caption("VOC/VOB dan CTQ tidak ditemukan di DEFINE output.")

    st.divider()
    st.markdown("### Step 2 — Output Measurement Selection")


    # ── CTQ → Measurement Candidates (Standard path, OUTSIDE form) ──
# Key definitions — harus di luar if block agar tersedia di semua scope
    _opdef_key      = f"measure_operational_defs_{active_pid}"
    _candidates_key = f"measure_ctq_candidates_{active_pid}"
    _selected_key   = f"measure_selected_measurements_{active_pid}"
    _eval_key       = f"measure_ctq_evaluated_{active_pid}"

    if _proj_path == "standard":
        define_final_state = st.session_state.get("define_final_state")

        if _candidates_key not in st.session_state:
            ctq_list = _extract_ctq_final_from_define(define_final_state).get("ctq_items", [])
            define_ctx_for_cand = {
                "goal_statement":    _safe_get(define_final_state, "outputs", "goal_statement") or "",
                "problem_statement": _safe_get(define_final_state, "outputs", "problem_statement") or "",
            }
            st.session_state[_candidates_key] = _propose_output_measurements_from_ctq(
                ctq_list, define_ctx_for_cand
            )

        if _selected_key not in st.session_state:
            _saved = memory.load_measure_final(active_pid) or memory.load_measure_draft(active_pid) or {}
            st.session_state[_selected_key] = _safe_get(_saved, "inputs", "selected_measurements") or []

        candidates = st.session_state.get(_candidates_key, [])

        if candidates:
            # LLM evaluation — run once, cached
            if _eval_key not in st.session_state:
                with st.spinner("Agent mengevaluasi kandidat measurement terhadap 4 kriteria LSS..."):
                    define_ctx_eval = {
                        "problem_statement": _safe_get(define_final_state, "outputs", "problem_statement") or "",
                        "goal_statement":    _safe_get(define_final_state, "outputs", "goal_statement") or "",
                        "ctq":               _safe_get(define_final_state, "outputs", "ctq") or "",
                    }
                    st.session_state[_eval_key] = _evaluate_measurement_candidates_llm(
                        candidates, define_ctx_eval, _proj_path
                    )
                candidates = st.session_state[_eval_key]
                st.session_state[_candidates_key] = candidates
            else:
                candidates = st.session_state[_eval_key]

            st.markdown("##### Kandidat Output Measurement (dari CTQ)")
            st.caption("Evaluasi 4 kriteria LSS per kandidat. Pilih yang paling representatif.")

            col_re, _ = st.columns([1, 3])
            with col_re:
                if st.button("🔄 Re-evaluate", key=f"reset_eval_{active_pid}"):
                    st.session_state.pop(_eval_key, None)
                    st.rerun()

            _SCORE_LABEL = {1: "🔴 Weak", 2: "🟡 Moderate", 3: "🟢 Strong"}
            _REC_COLOR   = {"recommended": "🟢", "acceptable": "🟡", "not_recommended": "🔴"}

            prev_selected = [m.get("measurement_name", "") for m in st.session_state.get(_selected_key, [])]
            new_selected  = []

            for i, cand in enumerate(candidates):
                mname   = cand.get("measurement_name", f"Measurement {i+1}")
                ctq_src = cand.get("ctq_source", "-")
                rep     = cand.get("representation", "-")
                ev      = cand.get("criteria_evaluation") or {}
                rec     = ev.get("recommendation", "")
                summary = ev.get("summary", "")
                total   = ev.get("total_score", "-")
                criteria = ev.get("criteria") or {}

                rec_icon = _REC_COLOR.get(rec, "⚪")
                exp_label = f"{rec_icon} **{mname}** — CTQ: _{ctq_src}_ | `{rep}` | Score: {total}/12"

                with st.expander(exp_label, expanded=(rec == "recommended")):
                    if summary:
                        st.caption(f"💬 {summary}")
                    if criteria:
                        c1, c2 = st.columns(2)
                        _crit_items = [
                            ("voc_vob_alignment",   "VOC/VOB Alignment"),
                            ("process_sensitivity", "Process Sensitivity"),
                            ("predictive_power",    "Predictive Power"),
                            ("ctq_specificity",     "CtQ/CtB Specificity"),
                        ]
                        for idx, (key, lbl) in enumerate(_crit_items):
                            col = c1 if idx % 2 == 0 else c2
                            with col:
                                crit = criteria.get(key) or {}
                                score = crit.get("score", "-")
                                st.markdown(f"**{lbl}:** {_SCORE_LABEL.get(score, str(score))}")
                                if crit.get("rationale"):
                                    st.caption(crit["rationale"])
                    elif ev.get("error"):
                        st.caption(f"⚠️ {ev['error']}")
                    st.markdown(f"📦 *{cand.get('data_requirement', '')}*")

                checked = st.checkbox(
                    f"Pilih: {mname}",
                    value=(mname in prev_selected),
                    key=f"meas_cand_{active_pid}_{i}",
                    disabled=is_locked,
                )
                if checked:
                    new_selected.append(cand)

            # Detect perubahan selection → reset opdef agar auto-sync
            _prev_names = sorted([m.get("measurement_name","") for m in st.session_state.get(_selected_key,[])])
            _new_names  = sorted([m.get("measurement_name","") for m in new_selected])
            if _prev_names != _new_names:
                # Reset opdef — akan di-seed ulang dari new_selected di bawah
                st.session_state.pop(_opdef_key, None)

            st.session_state[_selected_key] = new_selected
            if new_selected:
                st.success(f"✅ {len(new_selected)} measurement dipilih.")
            else:
                st.warning("⚠️ Pilih minimal 1 measurement sebelum generate draft.")
                
        else:
            st.info("Tidak ada CTQ dari Define yang bisa diparse untuk kandidat measurement. Isi Y variable secara manual di form.")

        st.divider()

    # ── Operational Definition Table (OUTSIDE form — Streamlit constraint) ──
    if _proj_path == "standard":
        st.divider()
        st.markdown("### Step 3 — Operational Definition")
        st.caption("Klik 'Propose' untuk mendapatkan usulan dari agent. Edit langsung di tabel, lalu klik 'Simpan Perubahan'.")

        _opdef_proposed_key = f"measure_opdef_proposed_{active_pid}"
        _sel_now = st.session_state.get(_selected_key, [])

        # Tombol propose — hanya trigger LLM saat diklik eksplisit
        col_propose, col_save, _ = st.columns([1, 1, 2])
        with col_propose:
            propose_clicked = st.button(
                "🤖 Propose dari Agent",
                key=f"opdef_propose_btn_{active_pid}",
                disabled=is_locked or not _sel_now,
            )
        with col_save:
            save_opdef_clicked = st.button(
                "💾 Simpan Perubahan",
                key=f"opdef_save_btn_{active_pid}",
                disabled=is_locked,
            )

        if propose_clicked and _sel_now and not is_locked:
            with st.spinner("Agent menyusun proposal..."):
                define_ctx_opdef = {
                    "problem_statement": _safe_get(define_final, "outputs", "problem_statement") or "",
                    "goal_statement":    _safe_get(define_final, "outputs", "goal_statement") or "",
                    "ctq":               _safe_get(define_final, "outputs", "ctq_list") or "",
                }
                proposals = _propose_operational_definitions_llm(
                    _sel_now, define_ctx_opdef, _proj_path
                )
                # Merge: jangan timpa baris yang sudah diisi user
                existing = {
                    r.get("metric_name", ""): r
                    for r in (st.session_state.get(_opdef_key) or [])
                    if isinstance(r, dict) and any(str(v).strip() for k, v in r.items() if k != "metric_name")
                }
                merged = []
                for p in proposals:
                    mname = p.get("metric_name", "")
                    merged.append(existing[mname] if mname in existing else p)
                st.session_state[_opdef_key] = merged
                st.session_state[_opdef_proposed_key] = True
            st.success("✅ Proposal tersedia. Edit di tabel lalu klik Simpan Perubahan.")

        # Seed fallback jika belum ada sama sekali
        if not st.session_state.get(_opdef_key):
            if _sel_now:
                st.session_state[_opdef_key] = [
                    {
                        "metric_name":       m.get("measurement_name", ""),
                        "what_measured":     "",
                        "instrument_system": "",
                        "method_formula":    "",
                        "unit":              m.get("unit", ""),
                        "frequency":         "",
                        "decision_criteria": "",
                    }
                    for m in _sel_now
                ]
            else:
                st.session_state[_opdef_key] = _seed_operational_definitions_from_define(define_final)

        # Data editor — fixed rows, tidak dynamic agar tidak auto-tambah row kosong
        _opdef_display = st.session_state.get(_opdef_key, [])
        op_defs_edited = st.data_editor(
            pd.DataFrame(_opdef_display) if _opdef_display else pd.DataFrame(columns=[
                "metric_name","what_measured","instrument_system",
                "method_formula","unit","frequency","decision_criteria"
            ]),
            num_rows="dynamic",
            use_container_width=True,
            key=f"measure_opdefs_editor_{active_pid}",
            disabled=is_locked,
            column_config={
                "metric_name":       st.column_config.TextColumn("Metric Name", width="medium"),
                "what_measured":     st.column_config.TextColumn("What is Measured", width="large"),
                "instrument_system": st.column_config.TextColumn("Instrument / System", width="medium"),
                "method_formula":    st.column_config.TextColumn("Formula / Method", width="large"),
                "unit":              st.column_config.TextColumn("Unit", width="small"),
                "frequency":         st.column_config.TextColumn("Frequency", width="small"),
                "decision_criteria": st.column_config.TextColumn("Decision Criteria", width="medium"),
            }
        )

        # Hanya update session state saat tombol Simpan diklik — tidak auto-overwrite
        if save_opdef_clicked:
            if isinstance(op_defs_edited, pd.DataFrame):
                st.session_state[_opdef_key] = op_defs_edited.to_dict(orient="records")
            elif isinstance(op_defs_edited, list):
                st.session_state[_opdef_key] = op_defs_edited
            st.success("✅ Perubahan tersimpan.")

        op_defs = st.session_state.get(_opdef_key, [])
    else:
        op_defs = []

    st.divider()
    st.markdown("### Step 4 — Measurement System Analysis (MSA)")
    st.caption("Diisi oleh Project Leader. Agent akan memvalidasi completeness, bukan mengisi.")

    _msa_key = f"measure_msa_{active_pid}"
    if _msa_key not in st.session_state:
        _saved_msa = {}
        _saved = memory.load_measure_draft(active_pid) or memory.load_measure_final(active_pid) or {}
        _saved_msa = (_saved.get("outputs") or {}).get("measurement_system_analysis") or {}
        st.session_state[_msa_key] = _saved_msa

    _msa = st.session_state[_msa_key]

    _msa_approach = st.text_area(
        "Measurement approach",
        value=_msa.get("approach", ""),
        height=80,
        key=f"msa_approach_{active_pid}",
        disabled=is_locked,
        placeholder="Contoh: Data diambil dari ERP SAP modul AR, extract bulanan oleh Finance team.",
    )

    _msa_error_src = st.text_area(
        "Possible error sources",
        value="\n".join(_msa.get("error_sources") or []),
        height=80,
        key=f"msa_error_src_{active_pid}",
        disabled=is_locked,
        placeholder="Satu per baris. Contoh: Manual entry error\nSystem downtime causing data gap",
    )

    _msa_cross = st.text_area(
        "Cross-checks performed",
        value="\n".join(_msa.get("cross_checks") or []),
        height=80,
        key=f"msa_cross_{active_pid}",
        disabled=is_locked,
        placeholder="Satu per baris. Contoh: Reconcile dengan bank statement bulanan",
    )

    _msa_impr = st.text_area(
        "Improvement steps undertaken",
        value="\n".join(_msa.get("improvement_steps") or []),
        height=80,
        key=f"msa_impr_{active_pid}",
        disabled=is_locked,
        placeholder="Satu per baris. Contoh: Data cleaning script dijalankan sebelum extract",
    )

    _msa_alt = st.text_area(
        "Alternative data sources (jika ada)",
        value="\n".join(_msa.get("alternative_data_sources") or []),
        height=60,
        key=f"msa_alt_{active_pid}",
        disabled=is_locked,
        placeholder="Satu per baris. Contoh: Manual AR aging report dari Finance",
    )

    _msa_dq = st.selectbox(
        "Data quality self-assessment",
        options=["", "good", "fair", "poor"],
        index=["", "good", "fair", "poor"].index(_msa.get("data_quality_rating", "") or ""),
        key=f"msa_dq_{active_pid}",
        disabled=is_locked,
    )

    _msa_dq_rat = st.text_input(
        "Rationale for data quality rating",
        value=_msa.get("data_quality_rationale", ""),
        key=f"msa_dq_rat_{active_pid}",
        disabled=is_locked,
        placeholder="Contoh: Data lengkap 12 bulan, tidak ada gap, sudah direconcile dengan Finance.",
    )

    # Update session state saat user mengedit
    st.session_state[_msa_key] = {
        "approach":                _msa_approach,
        "error_sources":           [x.strip() for x in _msa_error_src.splitlines() if x.strip()],
        "cross_checks":            [x.strip() for x in _msa_cross.splitlines() if x.strip()],
        "improvement_steps":       [x.strip() for x in _msa_impr.splitlines() if x.strip()],
        "alternative_data_sources":[x.strip() for x in _msa_alt.splitlines() if x.strip()],
        "data_quality_rating":     _msa_dq,
        "data_quality_rationale":  _msa_dq_rat,
    }

    # ── Dataset Upload (OUTSIDE form — Streamlit constraint) ──
    st.divider()
    st.markdown("### Step 5 — 📂 Measurement Data")
    uploaded = st.file_uploader(
        "Upload measurement dataset (CSV/XLSX)",
        type=["csv", "xlsx", "xls"],
        key="measure_upload",
        disabled=is_locked,
    )

    baseline_df = st.session_state.get("measure_baseline_df")
    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith(".csv"):
                baseline_df = pd.read_csv(uploaded)
            else:
                baseline_df = pd.read_excel(uploaded)
            st.session_state["measure_baseline_df"]   = baseline_df
            st.session_state["measure_uploaded_name"] = uploaded.name
            st.success(f"✅ {uploaded.name} — {baseline_df.shape[0]:,} rows, {baseline_df.shape[1]} columns")
        except Exception as e:
            st.error(f"Gagal membaca file: {e}")
            baseline_df = None
            st.session_state["measure_baseline_df"]   = None
            st.session_state["measure_uploaded_name"] = None

    cols = list(baseline_df.columns.astype(str)) if isinstance(baseline_df, pd.DataFrame) else []
    if cols:
        st.caption(f"File: `{st.session_state.get('measure_uploaded_name','')}` | {len(baseline_df)} rows")
        y_col    = st.selectbox("Y column (untuk baseline metrics)", options=[""] + cols,
                                index=0, key=f"measure_y_col_{active_pid}", disabled=is_locked)
        date_col = st.selectbox("Date/time column (untuk time series)", options=[""] + cols,
                                index=0, key=f"measure_date_col_{active_pid}", disabled=is_locked)
        seg_cols = st.multiselect(
            "Segment columns (opsional)", options=cols,
            default=[c for c in ["site", "product", "shift"] if c in cols],
            key=f"measure_seg_cols_{active_pid}", disabled=is_locked,
        )
    else:
        y_col    = ""
        date_col = None
        seg_cols = []

 
    st.divider()


    # ── Input Form ──
    with st.form(key="measure_input_form", clear_on_submit=False):
        # Read-only context from Define
        _def_industry    = seed.get("industry") or "-"
        _def_process     = seed.get("process_area") or "-"
        _def_pain        = seed.get("pain_theme") or "-"
        st.caption(f"📌 From Define: **{_def_industry}** / **{_def_process}** / **{_def_pain}**")

        industry     = _def_industry
        process_area = _def_process
        pain_theme   = _def_pain

        y_variable = st.text_input(
            "Confirmed Y Variable",
            key=f"measure_y_text_{active_pid}",
            value=seed.get("y_variable", ""),
            disabled=is_locked,
            help="Y variable utama yang akan diukur — harus konsisten dengan CTQ dari Define.",
        )
        st.markdown("#### Target Reference (opsional)")
        st.caption("Isi jika target tidak terdeteksi otomatis dari goal statement Define.")
        tc1, tc2 = st.columns([2, 1])
        with tc1:
            target_value_input = st.text_input(
                "Target value", value="", key=f"measure_target_val_{active_pid}",
                placeholder="contoh: 12.8", disabled=is_locked,
            )
        with tc2:
            target_dir_input = st.selectbox(
                "Direction", options=[">=", "<="],
                key=f"measure_target_dir_{active_pid}", disabled=is_locked,
            )

        st.markdown("#### Chart (opsional)")
        _m_ct_opts = ["performance", "auto"] + _charts.CHART_TYPES
        _m_ct_label = {
            "performance": "Performance Bar (default)",
            "auto": "Auto (rekomendasi)",
            **_charts.CHART_LABELS,
        }
        chart_type_input = st.selectbox(
            "Jenis chart",
            options=_m_ct_opts,
            format_func=lambda x: _m_ct_label.get(x, x),
            key=f"measure_chart_type_{active_pid}",
            disabled=is_locked,
            help="Performance Bar = grafik per periode + garis target. Histogram butuh LSL/USL untuk Cp/Cpk.",
        )
        _scl, _scu = st.columns(2)
        with _scl:
            lsl_input = st.text_input("LSL (opsional)", value="", key=f"measure_lsl_{active_pid}",
                                      placeholder="batas spesifikasi bawah", disabled=is_locked)
        with _scu:
            usl_input = st.text_input("USL (opsional)", value="", key=f"measure_usl_{active_pid}",
                                      placeholder="batas spesifikasi atas", disabled=is_locked)

        feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            height=80,
            key="measure_feedback_text",
            placeholder="Jika ada yang perlu diperbaiki dari draft sebelumnya, tulis di sini.",
            disabled=is_locked,
        )

        submitted = st.form_submit_button(
            "🔍 Generate / Update Draft" if not is_locked else "🔒 Locked — Cannot Generate Draft",
            type="primary",
            disabled=is_locked,
        )

    if submitted:
        _opdef_key_read = f"measure_operational_defs_{active_pid}" if _proj_path == "standard" else None
        op_defs_submit = st.session_state.get(_opdef_key_read, []) if _opdef_key_read else []

        user_inputs = {
            "project_name":                  seed.get("project_name") or active_pid,
            "industry":                      industry,
            "process_area":                  process_area,
            "pain_theme":                    pain_theme,
            "y_variable":                    y_variable,
            "target_value":                  target_value_input.strip() or None,
            "target_direction":              target_dir_input,
            "chart_type":                    chart_type_input,
            "lsl":                           (lsl_input.strip() or None),
            "usl":                           (usl_input.strip() or None),
            "operational_definitions":       op_defs_submit or [],
            "measurement_system_analysis": st.session_state.get(_msa_key, {}),
            "scaffolding_level":             "guided",
            "y_column_name":                 y_col or "",
            "date_column":                   (date_col or None),
            "segment_columns":               seg_cols or [],
            "selected_measurements":         st.session_state.get(_selected_key, []),
            "project_path":                  _proj_path,
        }

        user_feedback = {"text": feedback_text.strip()} if feedback_text and feedback_text.strip() else None
        draft_result = run_measure_agent(
            project_id=active_pid,
            user_inputs=user_inputs,
            define_final=define_final,
            baseline_df=baseline_df,
            user_feedback=user_feedback,
        )
        st.session_state["measure_draft"] = draft_result
        st.session_state["measure_final"] = None
        st.session_state[_revise_mode_key] = False
        st.rerun()

    # ── Agent Coaching (below form, in left col — same as Define) ──
    _current_result = st.session_state.get("measure_draft") or st.session_state.get("measure_final")
    _ms = (_current_result or {}).get("measure_state") if isinstance(_current_result, dict) else None

    if isinstance(_ms, dict):
        gate = _ms.get("gate") or {}
        status = gate.get("status", "-")
        st.markdown("### 🧭 Agent Coaching")
        st.markdown(f"**Gate Status:** `{status}`")

        coaching = (_ms.get("coaching_md") or _ms.get("insight_md") or "").strip()
        if coaching:
            st.markdown(coaching)
        else:
            st.info("Belum ada coaching dari agent. Jalankan Generate / Update Draft.")
        st.divider()

# ── TAB 1: Report ──
with tab_report:
    if not measure_state:
        st.info("Generate draft untuk melihat report.")
    else:
        inputs = measure_state.get("inputs") or {}
        outs   = measure_state.get("outputs") or {}

        # ── DEFINE Context lengkap ──
        with st.expander("🔗 DEFINE Context (read-only — brought forward)", expanded=True):
            _def_outs   = (define_final or {}).get("outputs") or {}
            _def_inputs = (define_final or {}).get("inputs") or {}

            st.markdown(f"**Problem:** {_def_outs.get('problem_statement') or '-'}")
            st.markdown(f"**Goal:** {_def_outs.get('goal_statement') or '-'}")
            st.markdown(f"**Y Variable (Define):** {_def_outs.get('y_variable') or '-'}")

            # VOC/VOB → CTQ table
            voc_tbl = _def_inputs.get("voc_table") or []
            if voc_tbl:
                st.markdown("**VOC/VOB → CTQ/CTB:**")
                st.dataframe(pd.DataFrame(voc_tbl), use_container_width=True, hide_index=True)

            # CTQ list
            ctq_list = _def_outs.get("ctq_list") or []
            if ctq_list:
                st.markdown("**CTQ/CTB:**")
                ctq_rows = []
                for c in ctq_list:
                    if isinstance(c, dict):
                        ctq_rows.append({
                            "Name": c.get("name") or c.get("ctq") or str(c),
                            "Metric": c.get("metric") or "-",
                            "Unit": c.get("unit") or "-",
                            "Description": c.get("description") or "-",
                        })
                    else:
                        ctq_rows.append({"Name": str(c), "Metric": "-", "Unit": "-", "Description": "-"})
                st.dataframe(pd.DataFrame(ctq_rows), use_container_width=True, hide_index=True)

            # Scope
            scope = _def_outs.get("project_scope") or {}
            if isinstance(scope, dict) and (scope.get("in_scope") or scope.get("out_of_scope")):
                st.markdown("**Scope:**")
                in_s  = scope.get("in_scope") or []
                out_s = scope.get("out_of_scope") or []
                max_len = max(len(in_s), len(out_s), 1)
                scope_rows = [{"In Scope": in_s[i] if i < len(in_s) else "",
                               "Out of Scope": out_s[i] if i < len(out_s) else ""}
                              for i in range(max_len)]
                st.dataframe(pd.DataFrame(scope_rows), use_container_width=True, hide_index=True)

        st.divider()

        # ── MEASURE Outputs ──
        st.markdown("### 📏 MEASURE Outputs")
        st.markdown(f"**Y Confirmed:** {outs.get('y_variable_confirmed') or inputs.get('y_variable') or '-'}")

        # Primary Measurement Output
        _primary = outs.get("primary_measurement_output") or {}
        if _primary and _primary.get("metric_name"):
            with st.expander("📐 Primary Measurement Output", expanded=False):
                st.markdown(f"**Metric:** {_primary.get('metric_name', '-')}")
                if _primary.get("basis"):
                    st.markdown(f"**Basis:** {_primary['basis']}")
                if _primary.get("rationale"):
                    st.caption(_primary["rationale"])

        gate = measure_state.get("gate") or {}
        g_status = gate.get("status", "-")
        if g_status == "PASS":
            st.success(f"Gate: **{g_status}** — {gate.get('policy','')}")
        elif g_status == "WEAK_PASS":
            st.warning(f"Gate: **{g_status}** — {gate.get('policy','')}")
        elif g_status == "FAIL":
            st.error(f"Gate: **{g_status}** — {gate.get('policy','')}")

        missing = gate.get("missing_evidence") or []
        actions = gate.get("next_actions") or []
        risks   = gate.get("risk_if_proceed") or []
        if missing or risks or actions:
            c1, c2 = st.columns(2)
            with c1:
                if missing:
                    st.markdown("**Evidence yang Kurang:**")
                    for m in missing:
                        st.write(f"- `{m}`")
            with c2:
                if risks:
                    st.markdown("**Risiko jika dilanjutkan:**")
                    for r in risks:
                        st.write(f"- {r}")
            if actions:
                st.markdown("**Tindakan Selanjutnya:**")
                for i, a in enumerate(actions, 1):
                    st.write(f"{i}. {a}")

        st.divider()

        # ── Measure Conclusion ──
        concl = outs.get("measure_conclusion") or {}
        if concl:
            st.markdown("### 🔍 Measure Conclusion")
            validated = concl.get("problem_validated", "unknown")
            color_map = {"true": "🔴 Problem confirmed", "false": "🟢 Problem not confirmed", "unknown": "⚪ Unknown"}
            st.markdown(f"**Status:** {color_map.get(validated, validated)}")
            for b in concl.get("basis", []):
                st.write(f"- {b}")

        _coaching = (measure_state.get("coaching_md") or measure_state.get("insight_md") or "").strip()
        if _coaching:
            st.divider()
            st.markdown("### 🧭 Agent Coaching")
            st.markdown(_coaching)

# ── TAB 2: Tables ──
with tab_tables:
    if not measure_state:
        st.info("Generate draft untuk melihat tabel.")
    else:
        outs = measure_state.get("outputs") or {}

        # CTQ final used
        ctq_used = outs.get("ctq_final_used") or {}
        if ctq_used.get("available"):
            st.markdown("### CTQ dari DEFINE")
            ctq_items = ctq_used.get("ctq_items", [])
            ctq_rows = []
            for c in ctq_items:
                if isinstance(c, dict):
                    ctq_rows.append({
                        "Name": c.get("name") or c.get("ctq") or str(c),
                        "Metric": c.get("metric") or "-",
                        "Unit": c.get("unit") or "-",
                    })
                else:
                    ctq_rows.append({"Name": str(c), "Metric": "-", "Unit": "-"})
            if ctq_rows:
                st.dataframe(pd.DataFrame(ctq_rows), use_container_width=True, hide_index=True)

        # Operational definitions
        op_defs = outs.get("operational_definitions") or []
        if op_defs:
            st.markdown("### Operational Definitions")
            st.dataframe(pd.DataFrame(op_defs), use_container_width=True, hide_index=True)

            # Validation issues
            op_issues = outs.get("operational_definitions_issues") or []
            high_issues = [i for i in op_issues if isinstance(i, dict) and i.get("severity") == "high"]
            if high_issues:
                st.warning(f"⚠️ {len(high_issues)} field(s) wajib belum terisi di operational definitions.")
            elif op_issues:
                st.info(f"ℹ️ {len(op_issues)} catatan ringan di operational definitions.")
            else:
                st.success("✅ Operational definitions complete.")

        # Measurement plan
        mplan = outs.get("measurement_plan") or []
        if mplan:
            st.markdown("### Measurement Plan")
            st.dataframe(pd.DataFrame(mplan), use_container_width=True, hide_index=True)

        # Data availability map
        dmap = outs.get("data_availability_map") or []
        if dmap:
            st.markdown("### Data Availability")
            st.dataframe(pd.DataFrame(dmap) if isinstance(dmap, list) else pd.DataFrame([dmap]),
                         use_container_width=True, hide_index=True)

# ── TAB 3: Process Performance ──
with tab_perf:
    if not measure_state:
        st.info("Generate draft untuk melihat grafik performa.")
    else:
        outs = measure_state.get("outputs") or {}
        perf = outs.get("process_performance_summary") or {}
        target_info = perf.get("target_info") or {}

        if target_info.get("available") and target_info.get("value") is not None:
            t_val   = target_info["value"]
            t_dir   = target_info.get("direction", ">=")
            t_label = "Target minimum" if t_dir == ">=" else "Target maksimum"
            t_src   = target_info.get("source_text", "")
            st.caption(f"📌 {t_label}: **{t_val}** — dari goal Define: _{t_src}_")
        else:
            st.caption("📌 Target belum terdeteksi dari goal Define. Isi Target Reference di form input.")

        _mc = perf.get("measure_chart") or {}
        _mc_type = _mc.get("chart_type", "performance")
        chart_b64 = perf.get("chart_b64")
        if _mc_type != "performance" and _mc.get("available"):
            st.markdown(f"#### {_charts.CHART_LABELS.get(_mc_type, 'Chart')}")
            _charts.render_chart_streamlit(_mc, title="Process Performance",
                                           y_label=(perf.get("y_label") or "Nilai"))
            commentary = outs.get("chart_commentary", "")
            if commentary:
                st.markdown(
                    f"""<div style='background:#f0f9ff;border:1px solid #bae6fd;
                    border-radius:10px;padding:14px 18px;margin-top:12px;
                    font-size:14px;color:#0f172a;line-height:1.7'>{commentary}</div>""",
                    unsafe_allow_html=True,
                )
        elif chart_b64:
            import base64 as _b64
            st.markdown("#### Process Performance Chart")
            st.image(_b64.b64decode(chart_b64), use_container_width=True)

            commentary = outs.get("chart_commentary", "")
            if commentary:
                st.markdown(
                    f"""<div style='background:#f0f9ff;border:1px solid #bae6fd;
                    border-radius:10px;padding:14px 18px;margin-top:12px;
                    font-size:14px;color:#0f172a;line-height:1.7'>{commentary}</div>""",
                    unsafe_allow_html=True,
                )
        else:
            if not isinstance(baseline_df, pd.DataFrame):
                st.info("Upload dataset dan pilih Y column + Date column untuk generate grafik performa.")
            elif not y_col:
                st.info("Pilih Y column di input form untuk generate grafik.")
            elif not date_col:
                st.info("Pilih Date column untuk generate grafik time series.")
            else:
                st.info("Generate draft untuk melihat grafik performa.")

        # Baseline descriptives
        summary = perf.get("summary") or {}
        if summary and isinstance(summary, dict) and summary.get("available"):
            st.markdown("#### Baseline Descriptives")
            _desc_cols = ["count", "mean", "median", "std", "min", "max", "p25", "p75"]
            desc_data = {k: summary.get(k) for k in _desc_cols if k in summary}
            st.dataframe(
                pd.DataFrame([desc_data]).rename(columns={"count": "n"}),
                use_container_width=True, hide_index=True
            )

# ── TAB 4: MSA & Data Quality ──
with tab_msa:
    if not measure_state:
        st.info("Generate draft untuk melihat MSA dan data quality.")
    else:
        outs = measure_state.get("outputs") or {}

        # MSA structured
        msa = outs.get("measurement_system_analysis") or {}
        st.markdown("#### 🔬 Measurement System Analysis")
        if msa:
            st.write(f"**Approach:** {msa.get('approach', '—')}")

            err_src = msa.get("error_sources") or []
            if err_src:
                st.markdown("**Possible Error Sources:**")
                for e in err_src:
                    st.write(f"- {e}")

            cross = msa.get("cross_checks") or []
            if cross:
                st.markdown("**Cross-checks Performed:**")
                for c in cross:
                    st.write(f"- {c}")

            impr = msa.get("improvement_steps") or []
            if impr:
                st.markdown("**Improvement Steps Undertaken:**")
                for i in impr:
                    st.write(f"- {i}")

            alt = msa.get("alternative_data_sources") or []
            if alt:
                st.markdown("**Alternative Data Sources:**")
                for a in alt:
                    st.write(f"- {a}")

            dq_rating = msa.get("data_quality_rating", "")
            dq_rat    = msa.get("data_quality_rationale", "")
            if dq_rating:
                color = "🟢" if dq_rating == "good" else ("🟡" if dq_rating == "fair" else "🔴")
                st.markdown(f"**Data Quality Rating:** {color} `{dq_rating}`")
            if dq_rat:
                st.caption(dq_rat)
        else:
            st.caption("MSA akan tersedia setelah generate draft dengan data yang diupload.")

        st.divider()

        # Data Quality Results
        dq = outs.get("data_quality_results") or {}
        if dq.get("available"):
            st.markdown("#### 📊 Data Quality Results")

            comp = (dq.get("completeness") or {}).get("columns_with_missing_over_5pct") or []
            if comp:
                st.warning(f"Missing >5%: {comp}")
            else:
                st.success("✅ Completeness: tidak ada kolom dengan missing >5%")

            cons = (dq.get("consistency") or {}).get("issues") or []
            if cons:
                st.warning("Consistency issues:")
                for i in cons:
                    if isinstance(i, dict):
                        st.write(f"- **{i.get('issue','')}** pada `{i.get('field','')}`: {i.get('count','')} records")
                    else:
                        st.write(f"- {i}")
            else:
                st.success("✅ Consistency: tidak ada issue yang terdeteksi")

            timeliness = dq.get("timeliness") or {}
            if timeliness.get("date_col") and not timeliness.get("note"):
                st.markdown(f"**Timeliness:** data dari `{timeliness.get('min','?')}` sampai `{timeliness.get('max','?')}`")
                if timeliness.get("freshness_days") is not None:
                    st.caption(f"Freshness: {timeliness['freshness_days']:.1f} hari sejak data terakhir")
            else:
                st.caption("⚠️ Tidak ada kolom tanggal — timeliness tidak bisa dievaluasi.")

        # Dataset profile (compact)
        prof = outs.get("dataset_profile") or {}
        if prof.get("available"):
            st.markdown("#### Dataset Profile")
            st.write(f"Rows: **{prof.get('rows')}** | Columns: **{prof.get('cols')}** | Duplicates: **{prof.get('duplicate_rows', 0)}** | Missing cells: **{prof.get('missing_cells', 0)}**")

with tab_input:
    # ============================================
    # Finalize & Downloads (full width, same as Define)
    # ============================================
    st.divider()
    st.subheader("Finalize & Downloads")

    _draft_wrapper = st.session_state.get("measure_draft")
    _has_draft_now = bool(_draft_wrapper and _draft_wrapper.get("measure_state"))

    try:
        gate_status_now = (_draft_wrapper or {}).get("measure_state", {}).get("gate", {}).get("status")
    except Exception:
        gate_status_now = None

    c1, c2, c3 = st.columns([1, 1, 1])

    with c1:
        if _has_draft_now:
            finalize_disabled = (gate_status_now == "FAIL")
            if st.button("✅ Finalize (Lock Final)", key="measure_finalize_btn", disabled=finalize_disabled):
                draft_state = _draft_wrapper["measure_state"]
                final_result = finalize_measure_agent(
                    project_id=active_pid,
                    measure_state=draft_state,
                    user_feedback=None,
                )
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason", "Finalize diblokir oleh gate."))
                    st.info("Perbaiki A-rules terlebih dahulu, lalu generate ulang draft.")
                else:
                    st.session_state["measure_final"] = final_result
                    st.session_state["measure_draft"]  = None
                    st.session_state[_revise_mode_key]  = False
                    st.success("Finalized. Final tersimpan.")
                    st.rerun()
            if gate_status_now == "FAIL":
                st.caption("⚠️ Finalize diblokir: Gate FAIL. Perbaiki A-rules dulu.")
        elif _has_final:
            st.button("✅ Finalized", key="measure_finalized_badge_btn", disabled=True)
        else:
            st.button("✅ Finalize (Lock Final)", key="measure_finalize_disabled_btn", disabled=True)
            st.caption("Generate draft terlebih dahulu.")

    with c2:
        _word_source = final_on_disk or draft_on_disk
        if _word_source:
            _word_label = "🛠️ Generate Word (Final)" if final_on_disk else "🛠️ Generate Word (Draft)"
            if st.button(_word_label, key="measure_generate_word_btn"):
                _export_state = copy.deepcopy(_word_source)
                export_fn = getattr(reporting, "export_measure_to_word", None)
                if callable(export_fn):
                    try:
                        _docx_fname = f"Project_{active_pid}_DEF_MEA_{'Final' if final_on_disk else 'Draft'}.docx"
                        out_path = export_fn(_export_state, path=_docx_fname)
                        st.session_state["measure_last_docx_path"] = out_path
                        st.success("File Word berhasil dibuat.")
                    except Exception as e:
                        st.warning(f"Word export belum tersedia: {e}")
                else:
                    st.warning("export_measure_to_word belum diimplementasi di reporting.py.")
        else:
            st.button("🛠️ Generate Word", key="measure_generate_word_disabled_btn", disabled=True)

    with c3:
        _docx_fname_mea = f"Project_{active_pid}_DEF_MEA_{'Final' if final_on_disk else 'Draft'}.docx"
        docx_path = st.session_state.get("measure_last_docx_path") or _docx_fname_mea
        _label = "⬇️ Download Final Word Report" if final_on_disk else "⬇️ Download Draft Word Report"
        try:
            with open(docx_path, "rb") as f:
                doc_bytes = f.read()
            st.download_button(
                label=_label,
                data=doc_bytes,
                file_name=_docx_fname_mea,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="measure_download_word_btn",
            )
        except FileNotFoundError:
            st.button(_label, key="measure_download_word_disabled_btn", disabled=True)

    # ============================================
    # Governance (same as Define)
    # ============================================
    st.divider()
    st.subheader("🏛️ Governance — Stage Gate Approval")

    final_exists    = memory.load_measure_final(active_pid) if active_pid else None
    approval_status = memory.get_phase_approval_status(active_pid, "measure") if active_pid else {}

    reviewer_action = (approval_status.get("reviewer") or {}).get("action")
    champion_action = (approval_status.get("champion") or {}).get("action")
    can_advance     = approval_status.get("can_advance", False)
    auto_advance    = approval_status.get("auto_advance", False)
    required        = approval_status.get("required_approvers", [])

    _prev_approved = approval_status.get("prev_approved", True)

    if not final_exists:
        st.info("Finalize MEASURE dulu sebelum bisa di-approve.")
    elif auto_advance:
        if _prev_approved:
            st.success("🚀 Measure gate **auto-advance** — tidak perlu approval. Lanjut ke Analyze.")
        else:
            st.warning("⏳ Gate terkunci — DEFINE belum disetujui. Selesaikan approval DEFINE terlebih dahulu.")
    else:
        # Status display — hanya setelah finalized
        col_r, col_c, col_adv = st.columns(3)
        with col_r:
            if "reviewer" in required:
                if reviewer_action == "approve":  st.success("✅ Reviewer: Approved")
                elif reviewer_action == "reject": st.error("❌ Reviewer: Rejected")
                else:                              st.warning("⏳ Reviewer: Pending")
            else:
                st.info("— Reviewer: tidak diperlukan")
        with col_c:
            if "champion" in required:
                if champion_action == "approve":  st.success("✅ Champion: Approved")
                elif champion_action == "reject": st.error("❌ Champion: Rejected")
                else:                              st.warning("⏳ Champion: Pending")
            else:
                st.info("— Champion: tidak diperlukan")
        with col_adv:
            if can_advance: st.success("🚀 Gate: OPEN — Lanjut ke Analyze")
            else:           st.info("🔒 Gate: LOCKED")

        role = auth.get_current_role()
        if not _prev_approved:
            st.warning("⚠️ DEFINE belum disetujui. Approval fase ini belum bisa dilakukan.")
        elif role in required:
            _role_action = (approval_status.get(role) or {}).get("action")
            st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
            if _role_action == "approve":
                st.success("✅ Kamu sudah **Approve** fase ini.")
            elif _role_action == "reject":
                st.error("❌ Kamu sudah **Reject** fase ini.")
            note = st.text_area("Catatan (opsional)", key=f"gov_note_{role}_measure", height=80)
            col_app, col_rej = st.columns(2)
            with col_app:
                if st.button("✅ Approve", key=f"gov_approve_{role}_measure",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid, "measure", role, "approve", note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid, "measure", f"approved_by_{role}")
                    st.success("Approval disimpan.")
                    st.rerun()
            with col_rej:
                if st.button("❌ Reject", key=f"gov_reject_{role}_measure",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid, "measure", role, "reject", note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid, "measure", f"rejected_by_{role}")
                    st.warning("Rejection disimpan.")
                    st.rerun()
        elif role == "project_leader":
            if can_advance: st.success("✅ Semua approval lengkap. Lanjut ke Analyze.")
            else:           st.info("Menunggu approval dari approver yang dibutuhkan.")

    # ============================================
    # Audit Log
    # ============================================
    st.divider()
    st.subheader("🧾 Audit Log — MEASURE Phase")
    events = audit.get_recent_events(project_id=active_pid, phase="Measure", limit=30)
    if events:
        st.dataframe(events, use_container_width=True)
    else:
        st.write("Belum ada audit event.")
