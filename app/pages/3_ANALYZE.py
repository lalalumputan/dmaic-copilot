import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import json
import copy
import streamlit as st
import pandas as pd

from app.agents.analyze_agent import run_analyze_agent, finalize_analyze_agent
from app.utils import memory, reporting, audit, auth
from app.utils.ui_helpers import render_sidebar_header, render_tab_quicknav, render_phase_end_date, compact_dot

# ============================================
# Helpers
# ============================================

def _get_current_result():
    if st.session_state.get("analyze_draft"):
        return "draft", st.session_state["analyze_draft"]
    if st.session_state.get("analyze_final"):
        return "final", st.session_state["analyze_final"]
    return "none", None

def _render_sipoc_html(sipoc: dict) -> str:
    """Render SIPOC dict sebagai 5-kolom card HTML."""
    cols = [
        ("S", "Suppliers",  "suppliers",  "#ede9fe", "#c4b5fd", "#5b21b6", "#6d28d9"),
        ("I", "Inputs",     "inputs",     "#dbeafe", "#93c5fd", "#1e40af", "#1d4ed8"),
        ("P", "Process",    "process",    "#d1fae5", "#6ee7b7", "#065f46", "#047857"),
        ("O", "Outputs",    "outputs",    "#fef3c7", "#fcd34d", "#92400e", "#b45309"),
        ("C", "Customers",  "customers",  "#fee2e2", "#fca5a5", "#991b1b", "#b91c1c"),
    ]

    def _items(key):
        val = sipoc.get(key) or sipoc.get(key.capitalize()) or []
        if isinstance(val, str):
            val = [v.strip() for v in val.split(",") if v.strip()]
        elif not isinstance(val, list):
            val = [str(val)]
        return val

    cards = ""
    for letter, label, key, bg, border, letter_color, label_color in cols:
        items = _items(key)
        rows = "".join(
            f'<div style="font-size:12px;color:var(--color-text-primary);'
            f'padding:4px 0;border-bottom:0.5px solid var(--color-border-tertiary);">{item}</div>'
            for item in items
        ) or '<div style="font-size:11px;color:var(--color-text-secondary);padding:4px 0;">—</div>'

        cards += f"""
        <div style="border-radius:10px;overflow:hidden;border:0.5px solid var(--color-border-tertiary);min-width:120px;">
          <div style="background:{bg};padding:8px 10px;text-align:center;border-bottom:0.5px solid {border};">
            <div style="font-size:20px;font-weight:500;color:{letter_color};">{letter}</div>
            <div style="font-size:11px;color:{label_color};font-weight:500;">{label}</div>
          </div>
          <div style="background:var(--color-background-primary);padding:8px 10px;">{rows}</div>
        </div>"""

    return f"""<div style="font-family:ui-sans-serif,system-ui,sans-serif;padding:8px 0;">
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;">{cards}</div>
    </div>"""

def _safe_get(d, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _build_process_map_dot(steps: list, problem_area_steps: list = None) -> str:
    filled = [s for s in (steps or [])
              if isinstance(s, dict) and str(s.get("step_name","") or "").strip()]
    if not filled:
        return ""

    problem_names = [p.lower() for p in (problem_area_steps or [])]

    def _is_problem(step):
        return any(p in str(step.get("step_name","") or "").lower() for p in problem_names)

    def _nid(i): return f"s{i}"

    def _safe_str(val):
        """Convert any value (including NaN/float) to clean string."""
        if val is None:
            return ""
        try:
            import math
            if isinstance(val, float) and math.isnan(val):
                return ""
        except Exception:
            pass
        return str(val).strip()

    lanes = []
    for s in filled:
        r = _safe_str(s.get("responsible","")) or "Unassigned"
        if r not in lanes:
            lanes.append(r)

    lines = [
        'digraph swimlane {',
        '  rankdir=LR;',
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, margin="0.15,0.1"];',
        '  edge [color="#94a3b8", arrowsize=0.7, penwidth=1.0];',
        '  splines=ortho;',
        '  nodesep=0.4;',
        '  ranksep=0.6;',
    ]

    node_ids = []
    lane_colors = [
        ("4338ca", "e0e7ff"),
        ("0f766e", "ccfbf1"),
        ("b45309", "fef3c7"),
        ("0369a1", "e0f2fe"),
        ("7c3aed", "ede9fe"),
        ("0f5132", "d1fae5"),
        ("9f1239", "fee2e2"),
    ]

    lane_node_map = {lane: [] for lane in lanes}
    for i, step in enumerate(filled):
        r = _safe_str(step.get("responsible","")) or "Unassigned"
        pg = _safe_str(step.get("parallel_group",""))
        lane_node_map[r].append(i)
        node_ids.append((_nid(i), pg))

    for li, lane in enumerate(lanes):
        hdr_c, body_c = lane_colors[li % len(lane_colors)]
        lines.append(f'  subgraph cluster_{li} {{')
        lines.append(f'    label="{lane}";')
        lines.append(f'    fontname="Helvetica"; fontsize=11; fontcolor="#{hdr_c}";')
        lines.append(f'    style="filled,rounded"; fillcolor="#{body_c}20";')
        lines.append(f'    color="#{hdr_c}"; penwidth=1.5;')

        for i in lane_node_map[lane]:
            step = filled[i]
            name = f"{i+1}. {_safe_str(step.get('step_name',''))}"[:32]
            if _is_problem(step):
                attrs = 'fillcolor="#fef3c7", color="#f59e0b", penwidth=2, fontcolor="#92400e"'
            else:
                attrs = f'fillcolor="#ffffff", color="#{hdr_c}", fontcolor="#0f172a"'
            lines.append(f'    {_nid(i)} [label="{name}", {attrs}];')
        lines.append('  }')

    processed_groups = set()
    i = 0
    while i < len(node_ids):
        nid, pg = node_ids[i]
        if pg and pg not in processed_groups:
            group_members = [j for j, (n, g) in enumerate(node_ids) if g == pg]
            processed_groups.add(pg)
            if group_members[0] > 0:
                pred = node_ids[group_members[0] - 1][0]
                for j in group_members:
                    lines.append(f'  {pred} -> {node_ids[j][0]};')
            last = max(group_members)
            if last + 1 < len(node_ids):
                succ = node_ids[last + 1][0]
                for j in group_members:
                    lines.append(f'  {node_ids[j][0]} -> {succ};')
            i = last + 1
        else:
            if i + 1 < len(node_ids):
                next_nid, next_pg = node_ids[i + 1]
                if not next_pg or next_pg in processed_groups:
                    lines.append(f'  {nid} -> {next_nid};')
                elif next_pg and next_pg not in processed_groups:
                    lines.append(f'  {nid} -> {next_nid};')
            i += 1

    lines.append('}')
    return "\n".join(lines)

# ============================================
# Page Config + CSS (identical to Measure)
# ============================================
st.set_page_config(page_title="ANALYZE", layout="wide")
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
st.session_state.setdefault("analyze_draft", None)
st.session_state.setdefault("analyze_final", None)

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
    appr = memory.get_phase_approval_status(active_pid, "analyze")
    if appr.get("auto_advance"):
        st.sidebar.success("🚀 Analyze: Auto-advance")
    else:
        rev  = (appr.get("reviewer") or {}).get("action") or "pending"
        chmp = (appr.get("champion") or {}).get("action") or "pending"
        st.sidebar.markdown(
            f"**Analyze Gate:**  \n"
            f"{'✅' if rev=='approve' else '❌' if rev=='reject' else '⏳'} Reviewer  |  "
            f"{'✅' if chmp=='approve' else '❌' if chmp=='reject' else '⏳'} Champion"
        )
        if appr.get("can_advance"):
            st.sidebar.success("🚀 Gate OPEN")
        else:
            st.sidebar.info("🔒 Gate LOCKED")
else:
    st.sidebar.warning("Belum ada project aktif.")
st.sidebar.divider()

# ============================================
# Gate: no project / no measure final
# ============================================
if not active_pid:
    st.title("🔍 ANALYZE Phase")
    st.info("Belum ada project aktif. Kembali ke **Main** untuk memilih project.")
    st.stop()

measure_final = memory.load_measure_final(active_pid)
if not measure_final:
    st.title("ANALYZE Phase")
    st.warning("MEASURE belum di-finalize. Selesaikan fase Measure terlebih dahulu.")
    st.stop()

define_final = memory.load_define_final(active_pid)

# ============================================
# Auto-load (_loaded_pid pattern)
# ============================================
loaded_pid = st.session_state.get("_analyze_loaded_pid")
if active_pid and loaded_pid != active_pid:
    st.session_state["_analyze_loaded_pid"] = active_pid
    st.session_state["analyze_draft"]       = None
    st.session_state["analyze_final"]       = None

    draft = memory.load_analyze_draft(active_pid)
    final = memory.load_analyze_final(active_pid)
    if draft:
        st.session_state["analyze_draft"] = {
            "status": "draft", "analyze_state": draft,
            "summary": draft.get("summary_md",""), "message": "Auto-loaded draft",
        }
    if final:
        st.session_state["analyze_final"] = {
            "status": "finalized", "analyze_state": final,
            "summary": final.get("summary_md",""), "message": "Auto-loaded final",
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
    st.markdown("# DMAIC Copilot — ANALYZE Phase")
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
h1, h2 = st.columns([5,1])
with h1:
    st.caption(f"**Active Project:** `{active_pid}`")
with h2:
    _view_badge = "FINAL" if memory.load_analyze_final(active_pid) else "DRAFT"
    st.markdown(
        f"<div style='text-align:right'><span style='padding:6px 12px;border-radius:20px;"
        f"background:#0f5132;color:#d1e7dd;font-weight:600;'>{_view_badge}</span></div>",
        unsafe_allow_html=True,
    )

# ============================================
# Shared state
# ============================================
final_on_disk  = memory.load_analyze_final(active_pid)
draft_on_disk  = memory.load_analyze_draft(active_pid)
_has_final     = bool(final_on_disk)
_has_draft     = bool(draft_on_disk)
_appr_status   = memory.get_phase_approval_status(active_pid, "analyze")
_req_roles_az  = _appr_status.get("required_approvers", [])
_is_rejected   = any(
    (_appr_status.get(r) or {}).get("action") == "reject"
    for r in _req_roles_az
)
_revise_key    = f"analyze_revise_mode_{active_pid}"
st.session_state.setdefault(_revise_key, False)
is_locked = _has_final and not st.session_state.get(_revise_key) and not _has_draft

# Session state keys
_process_steps_key   = f"analyze_process_steps_{active_pid}"
_fishbone_key        = f"analyze_fishbone_{active_pid}"
_pot_causes_key      = f"analyze_pot_causes_{active_pid}"
_vplan_key           = f"analyze_vplan_{active_pid}"
_vresults_key        = f"analyze_vresults_{active_pid}"
_pm_image_key        = f"analyze_pm_image_{active_pid}"
_pm_imgtype_key      = f"analyze_pm_imgtype_{active_pid}"
_gemba_key           = f"analyze_gemba_rc_{active_pid}"

# Pre-load saved state into session state keys
_saved_state = (final_on_disk or draft_on_disk or {})
_saved_outs  = _saved_state.get("outputs") or {}
_saved_ins   = _saved_state.get("inputs") or {}

# Fallback: load WIP jika belum ada draft/final
_wip = memory.load_analyze_wip(active_pid) if not (final_on_disk or draft_on_disk) else {}

for key, out_field, wip_field in [
    (_process_steps_key, "process_map_steps",    "process_map_steps"),
    (_fishbone_key,      "fishbone_inputs",       "fishbone_inputs"),
    (_pot_causes_key,    "potential_causes",      "potential_causes"),
    (_vplan_key,         "verification_plan",     "verification_plan"),
    (_vresults_key,      "verification_results",  "verification_results"),
]:
    if key not in st.session_state:
        val = _saved_outs.get(out_field) or _wip.get(wip_field)
        if val:
            # Fishbone: konversi list format → string format untuk text_area
            if key == _fishbone_key and isinstance(val, dict):
                val = {
                    cat: "\n".join(str(x) for x in v) if isinstance(v, list) else (str(v) if v else "")
                    for cat, v in val.items()
                }
            st.session_state[key] = val

# Load PM image dari WIP jika ada
_pm_image_key_pre   = f"analyze_pm_image_{active_pid}"
_pm_imgtype_key_pre = f"analyze_pm_imgtype_{active_pid}"
if _pm_image_key_pre not in st.session_state and _wip.get("pm_image_b64"):
    st.session_state[_pm_image_key_pre] = _wip["pm_image_b64"]
    st.session_state[_pm_imgtype_key_pre] = _wip.get("pm_image_type", "image/png")

# User-edited tables: selalu prioritaskan WIP di atas draft/final outputs
_wip_always = memory.load_analyze_wip(active_pid)
if _pot_causes_key not in st.session_state and _wip_always.get("potential_causes"):
    st.session_state[_pot_causes_key] = _wip_always["potential_causes"]
if _vresults_key not in st.session_state and _wip_always.get("verification_results"):
    st.session_state[_vresults_key] = _wip_always["verification_results"]
if _vplan_key not in st.session_state and _wip_always.get("verification_plan"):
    st.session_state[_vplan_key] = _wip_always["verification_plan"]

# Auto-load FINAL if nothing in session (fallback untuk page reload/session reset)
if _get_current_result()[0] == "none" and active_pid:
    _fb_final = memory.load_analyze_final(active_pid)
    if _fb_final:
        st.session_state["analyze_final"] = {
            "status": "finalized",
            "analyze_state": _fb_final,
            "summary": _fb_final.get("summary_md", ""),
            "message": "Auto-loaded FINAL from disk",
        }

# ============================================
# SETUP & TABS
# ============================================
view, res = _get_current_result()
analyze_state = res["analyze_state"] if (res and res.get("analyze_state")) else None

_TAB_LABELS = [
    "✏️ Input", "📄 Report", "🦴 Potential Causes", "🔬 Verification Analysis",
    "📊 Root Cause Summary", "🗺️ As-Is Process Map", "🦴 Fishbone",
]
tab_input, tab_report, tab_causes, tab_verification, tab_rca, tab_asis, tab_fishbone = st.tabs(_TAB_LABELS)
render_tab_quicknav(_TAB_LABELS)

# ============================================
# TAB INPUT
# ============================================
with tab_input:

    # ── Revision Actions ──
    if _has_final and _is_rejected and not _has_draft and not st.session_state.get(_revise_key):
        st.error(
            "❌ **Fase ANALYZE ditolak oleh reviewer.** Project dibuka kembali untuk direvisi. "
            "Klik **Revise Final** untuk mulai mengedit, lalu re-generate & re-finalize."
        )
        if st.button("✏️ Revise Final", key="analyze_revise_btn"):
            st.session_state[_revise_key]     = True
            st.session_state["analyze_draft"] = None
            st.rerun()
    elif _has_draft:
        if st.button("🧹 Discard Draft", key="analyze_discard_btn"):
            memory.delete_analyze_draft(active_pid)
            st.session_state["analyze_draft"] = None
            st.session_state["analyze_final"] = None
            st.session_state[_revise_key]     = False
            if final_on_disk:
                st.session_state["analyze_final"] = {
                    "status":"finalized","analyze_state":final_on_disk,
                    "summary":final_on_disk.get("summary_md",""),"message":"Reverted to final",
                }
            st.rerun()

    # ── Step 1: Upstream Context ──
    st.markdown("### Step 1 — Upstream Context (Define + Measure)")
    def_outs = (define_final or {}).get("outputs") or {}
    def_ins  = (define_final or {}).get("inputs")  or {}
    mea_outs = (measure_final or {}).get("outputs") or {}
    perf_sum = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}
    target_info = (mea_outs.get("process_performance_summary") or {}).get("target_info") or {}

    with st.expander("📋 Define + Measure Summary (read-only)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**From DEFINE:**")
            st.markdown(f"- Problem: {def_outs.get('problem_statement') or '-'}")
            st.markdown(f"- Goal: {def_outs.get('goal_statement') or '-'}")
            st.markdown(f"- Y Variable: {def_outs.get('y_variable') or '-'}")
            ctq_list = def_outs.get("ctq_list") or []
            if ctq_list:
                st.markdown("**CTQ/CTB:**")
                for c in ctq_list:
                    lbl = c.get("name") or c.get("ctq") or str(c) if isinstance(c,dict) else str(c)
                    st.write(f"- {lbl}")
        with c2:
            st.markdown("**From MEASURE:**")
            st.markdown(f"- Y Confirmed: {mea_outs.get('y_variable_confirmed') or '-'}")
            _cib = mea_outs.get("chart_image_baseline") or {}
            if perf_sum.get("mean") is not None:
                st.markdown(f"- Baseline Mean: {perf_sum.get('mean')}")
                st.markdown(f"- Baseline Std: {perf_sum.get('std') or '-'}")
            elif isinstance(_cib, dict) and _cib.get("average"):
                st.markdown(f"- Baseline Mean: {_cib.get('average')} _(estimasi visual dari gambar chart)_")
                _q = _cib.get("quartiles") or {}
                if isinstance(_q, dict) and _q.get("median"):
                    st.markdown(f"- Quartile (Q1/Median/Q3): {_q.get('q1','-')}/{_q.get('median','-')}/{_q.get('q3','-')} _(visual)_")
                if _cib.get("vs_target"):
                    st.markdown(f"- Vs Target: {_cib.get('vs_target')} _(visual)_")
            else:
                st.markdown("- Baseline Mean: -")
            if target_info.get("available"):
                st.markdown(f"- Target: {target_info.get('direction','')} {target_info.get('value','')}")
            op_defs = mea_outs.get("operational_definitions") or []
            if op_defs:
                st.markdown("**Operational Definitions:**")
                for od in op_defs:
                    if isinstance(od,dict):
                        st.write(f"- {od.get('metric_name','')} | {od.get('method_formula','')}")
# ── Measurement Anchor (dari Measure — selalu visible) ──
    _y_confirmed  = mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "-"
    _baseline_m   = perf_sum.get("mean")
    _target_val   = target_info.get("value")
    _target_dir   = target_info.get("direction","<=")
    _target_src   = target_info.get("source_text","")

    st.markdown(
        f"""<div style='background:#fffbeb;border:1px solid #fbbf24;border-radius:12px;
        padding:14px 18px;margin-bottom:12px;'>
        <b>🎯 Output Measurement (Y) dari Measure:</b><br>
        <span style='font-size:16px;font-weight:600;color:#0f172a'>{_y_confirmed}</span>
        &nbsp;|&nbsp; Baseline: <b>{_baseline_m if _baseline_m is not None else '-'}</b>
        &nbsp;|&nbsp; Target: <b>{_target_dir} {_target_val if _target_val is not None else '-'}</b>
        <br><small style='color:#78716c'>Semua potential causes harus memiliki jalur kausal langsung ke Y ini.</small>
        </div>""",
        unsafe_allow_html=True,
    )
    st.divider()

    if _proj_path != "quick":
        # ── Step 2: Process Map ──
        st.markdown("### Step 2 — Process Map")
        st.caption("Isi langkah-langkah proses yang relevan. Agent akan mengecek kelengkapan dan menandai area masalah.")

        # SIPOC dari Define sebagai referensi
        _def_outs_pm = (define_final or {}).get("outputs") or {}
        _sipoc = (
            _def_outs_pm.get("sipoc")
            or _def_outs_pm.get("sipoc_table")
            or _def_outs_pm.get("sipoc_analysis")
            or {}
        )
        with st.expander("📋 SIPOC dari DEFINE (referensi)", expanded=False):
            if _sipoc and isinstance(_sipoc, dict) and any(_sipoc.values()):
                st.markdown(
                    _render_sipoc_html(_sipoc),
                    unsafe_allow_html=True,
                )
            elif _sipoc and isinstance(_sipoc, list):
                st.dataframe(pd.DataFrame(_sipoc), use_container_width=True, hide_index=True)
            else:
                st.caption("SIPOC tidak ditemukan di output Define.")

    
        # ── Process Map: Upload OR table ──
        st.markdown("**Opsi A — Upload file/foto process map (agent akan verifikasi):**")
        pm_file = st.file_uploader(
            "Upload process map",
            type=["png","jpg","jpeg","pdf","xlsx","vsdx"],
            key=f"pm_upload_{active_pid}",
            disabled=is_locked,
        )

        _pm_image_key   = f"analyze_pm_image_{active_pid}"
        _pm_imgtype_key = f"analyze_pm_imgtype_{active_pid}"
        _pm_verified_key = f"analyze_pm_verified_{active_pid}"

        if pm_file is not None:
            import base64 as _b64

            # Handle image files via vision
            if pm_file.type.startswith("image/"):
                img_bytes = pm_file.read()
                img_b64   = _b64.b64encode(img_bytes).decode("utf-8")
                st.session_state[_pm_image_key]   = img_b64
                st.session_state[_pm_imgtype_key] = pm_file.type
                # Simpan ke WIP agar tidak hilang saat refresh
                _cur_wip_img = memory.load_analyze_wip(active_pid)
                _cur_wip_img["pm_image_b64"]  = img_b64
                _cur_wip_img["pm_image_type"] = pm_file.type
                memory.save_analyze_wip(active_pid, _cur_wip_img)
                st.image(img_bytes, caption=f"Uploaded: {pm_file.name}", width=700)

                if st.button("🤖 Analyze Process Map via Agent", key=f"pm_vision_{active_pid}", disabled=is_locked):
                    with st.spinner("Agent menganalisis process map..."):
                        from app.agents.analyze_agent import (
                            _check_process_map_from_image_llm,
                            _extract_upstream_context,
                        )
                        _upstream_pm = _extract_upstream_context(define_final, measure_final)
                        _pm_result = _check_process_map_from_image_llm(img_b64, pm_file.type, _upstream_pm)
                        st.session_state[_pm_verified_key] = _pm_result

                _pm_result = st.session_state.get(_pm_verified_key)
                if _pm_result:
                    verified = _pm_result.get("verified", False)
                    if verified:
                        st.success(f"✅ Process map **verified** oleh agent.")
                    else:
                        st.warning(f"⚠️ {_pm_result.get('verification_note','Perlu perbaikan.')}")
                    st.markdown(f"**Agent feedback:** {_pm_result.get('feedback','')}")
                    if _pm_result.get("problem_area_steps"):
                        st.markdown(f"**Problem area:** {', '.join(_pm_result['problem_area_steps'])}")
                    if _pm_result.get("missing_elements"):
                        st.markdown("**Missing elements:**")
                        for m in _pm_result["missing_elements"]:
                            st.write(f"- {m}")
            else:
                # Non-image files: store name only for documentation
                st.caption(f"📎 File dokumentasi tersimpan: {pm_file.name}")
                st.info("File non-image tidak bisa dianalisis langsung. Gunakan foto/PNG/JPG untuk analisis vision, atau isi tabel di bawah.")

        # Tampilkan image dari session state (WIP) meski file_uploader kosong setelah refresh
        elif st.session_state.get(_pm_image_key):
            import base64 as _b64
            try:
                st.image(
                    _b64.b64decode(st.session_state[_pm_image_key]),
                    caption="📎 Process map tersimpan sebelumnya",
                    width=700,
                )
            except Exception:
                pass

        # ── Opsi B: Free-form → Swimlane (Agent) ──
        st.markdown("**Opsi B — Deskripsikan proses secara bebas, agent generate swimlane diagram:**")
        st.caption("Ketik deskripsi proses (kalimat, bullet point, apapun). Agent generate swimlane diagram otomatis — bisa direvisi berkali-kali.")

        _pm_dot_key = f"analyze_pm_dot_{active_pid}"

        # Load dari WIP jika belum di session_state
        if _pm_dot_key not in st.session_state:
            _wip_dot = memory.load_analyze_wip(active_pid)
            # Support lama (pm_mermaid_code) dan baru (pm_dot_code)
            st.session_state[_pm_dot_key] = _wip_dot.get("pm_dot_code") or _wip_dot.get("pm_mermaid_code", "")

        _pm_desc_input = st.text_area(
            "Deskripsi proses (bebas — sebutkan aktor/PIC untuk swimlane yang tepat):",
            key=f"pm_desc_input_{active_pid}",
            height=140,
            placeholder=(
                "Contoh:\n"
                "- Customer kirim PO via email\n"
                "- Sales/Admin terima dan cek kelengkapan PO\n"
                "- Jika OK, CSD buat Sales Order dan koordinasi ke Planning\n"
                "- Logistic siapkan barang dan buat Delivery Note\n"
                "- Transporter kirim barang, customer tanda-tangan DN\n"
                "- Finance proses invoice dan kirim ke customer"
            ),
            disabled=is_locked,
        )

        _col_pm_gen, _col_pm_clear = st.columns([2, 1])
        with _col_pm_gen:
            _gen_disabled = is_locked or not (str(_pm_desc_input or "")).strip()
            if st.button("🤖 Generate Swimlane Diagram", key=f"pm_gen_dot_{active_pid}", disabled=_gen_disabled):
                with st.spinner("Agent membuat swimlane diagram..."):
                    from app.agents.analyze_agent import generate_process_map_graphviz, _extract_upstream_context
                    _up_ctx = _extract_upstream_context(define_final, measure_final)
                    _result_m = generate_process_map_graphviz(_pm_desc_input, _up_ctx)
                    if _result_m.get("dot_code"):
                        st.session_state[_pm_dot_key] = _result_m["dot_code"]
                        _wip_c = memory.load_analyze_wip(active_pid)
                        _wip_c["pm_dot_code"] = _result_m["dot_code"]
                        _wip_c["pm_dot_desc"] = _pm_desc_input
                        memory.save_analyze_wip(active_pid, _wip_c)
                        st.rerun()
                    else:
                        st.error("Agent tidak berhasil generate diagram. Coba perjelas deskripsi atau sebutkan aktor-aktornya.")
        with _col_pm_clear:
            if st.button("🗑️ Hapus Diagram", key=f"pm_clear_dot_{active_pid}", disabled=is_locked):
                st.session_state[_pm_dot_key] = ""
                _wip_cl = memory.load_analyze_wip(active_pid)
                _wip_cl["pm_dot_code"] = ""
                memory.save_analyze_wip(active_pid, _wip_cl)
                st.rerun()

        _cur_dot_c = st.session_state.get(_pm_dot_key, "")
        if _cur_dot_c:
            st.markdown("**Swimlane Diagram (Current State):**")
            try:
                st.graphviz_chart(compact_dot(_cur_dot_c), use_container_width=False)
            except Exception as _e:
                st.error(f"Diagram tidak valid: {_e}")
                st.code(_cur_dot_c, language="text")

            with st.expander("🔄 Revisi Diagram", expanded=False):
                _rev_desc = st.text_area(
                    "Apa yang perlu diubah/ditambah?",
                    key=f"pm_revise_desc_{active_pid}",
                    height=80,
                    placeholder="Contoh: Tambahkan decision point di CSD, pisahkan path reject kembali ke Sales...",
                    disabled=is_locked,
                )
                if st.button("🔄 Apply Revisi", key=f"pm_revise_btn_{active_pid}",
                             disabled=is_locked or not str(_rev_desc or "").strip()):
                    with st.spinner("Agent merevisi diagram..."):
                        from app.agents.analyze_agent import revise_process_map_graphviz
                        _rev_r = revise_process_map_graphviz(_cur_dot_c, _rev_desc)
                        if _rev_r.get("dot_code"):
                            st.session_state[_pm_dot_key] = _rev_r["dot_code"]
                            _wip_rv = memory.load_analyze_wip(active_pid)
                            _wip_rv["pm_dot_code"] = _rev_r["dot_code"]
                            memory.save_analyze_wip(active_pid, _wip_rv)
                            st.rerun()


        st.divider()
        # ── Step 3: Fishbone Diagram (6M) — Panduan RCA ──
        st.markdown("### Step 3 — Fishbone & Root Cause Analysis")
        st.caption("Lakukan analisis fishbone secara offline menggunakan panduan di bawah, lalu masukkan hasilnya ke tabel Xn di Step 4.")

        _rca_guide_html = (
            "<div style='font-family:ui-sans-serif,system-ui,sans-serif;padding:4px 0;'>"
            "<p style='text-align:center;font-weight:700;font-size:1rem;color:#0f172a;margin-bottom:14px;'>"
            "🎯 Diagram Analisis Akar Masalah (RCA) — Proses Kompak &amp; Efektif</p>"
            "<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px;'>"
            "<div style='background:#e0f2fe;border:1.5px solid #38bdf8;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#0369a1;font-size:0.85rem;margin-bottom:4px;'>1. DEFINISIKAN MASALAH</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>Jelas, Spesifik, Terukur:<br>"
            "&bull; <b>Lokasi</b> — di mana terjadi<br>"
            "&bull; <b>Waktu</b> — kapan / seberapa sering<br>"
            "&bull; <b>Dampak</b> — berapa besar</div></div>"
            "<div style='background:#d1fae5;border:1.5px solid #34d399;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#065f46;font-size:0.85rem;margin-bottom:4px;'>2. BRAINSTORMING</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>Maks. <b>15 Menit</b><br>"
            "Identifikasi <b>semua kemungkinan penyebab</b><br>"
            "<i>(Tanpa diskusi solusi dulu)</i></div></div>"
            "<div style='background:#ede9fe;border:1.5px solid #a78bfa;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#5b21b6;font-size:0.85rem;margin-bottom:4px;'>3. PENGELOMPOKAN 6M</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>"
            "<b>Man &middot; Machine &middot; Method</b><br>"
            "<b>Material &middot; Measurement &middot; Mother Nature</b></div></div>"
            "</div>"
            "<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px;'>"
            "<div style='background:#fce7f3;border:1.5px solid #f472b6;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#9d174d;font-size:0.85rem;margin-bottom:4px;'>4. REVIEW HASIL</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>Hapus cause yang:<br>"
            "&bull; <b>Duplikat</b><br>"
            "&bull; <b>Tidak Relevan</b><br>"
            "&bull; <b>Kurang Fakta/Data</b></div></div>"
            "<div style='background:#fef3c7;border:1.5px solid #fbbf24;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#92400e;font-size:0.85rem;margin-bottom:4px;'>5. ANALISIS 5 WHY</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>"
            "Why 1 &rarr; Why 2 &rarr; Why 3 &rarr; Why 4 &rarr; Why 5<br>"
            "<i>Temukan penyebab <b>mendasar &amp; sistemik</b></i></div></div>"
            "<div style='background:#fee2e2;border:1.5px solid #f87171;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#991b1b;font-size:0.85rem;margin-bottom:4px;'>6. TETAPKAN POTENTIAL ROOT CAUSE</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>"
            "Hasil akhir 5 Why<br>= <b>Potential Root Cause</b></div></div>"
            "</div>"
            "<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;'>"
            "<div style='background:#f0fdf4;border:2px solid #16a34a;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#14532d;font-size:0.85rem;margin-bottom:6px;'>7. KLASIFIKASI C-N-X</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>"
            "<span style='background:#e2e8f0;padding:1px 6px;border-radius:4px;font-weight:600;'>C</span> Constant — Tetap/Tidak Bisa Diubah<br>"
            "<span style='background:#bfdbfe;padding:1px 6px;border-radius:4px;font-weight:600;'>N</span> Noise — Alami/Luar Kendali<br>"
            "<span style='background:#bbf7d0;padding:1px 6px;border-radius:4px;font-weight:600;color:#14532d;'>X</span> <b>Variable — Dapat Dikendalikan ✅</b>"
            "</div></div>"
            "<div style='background:#dcfce7;border:2px solid #22c55e;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#14532d;font-size:0.85rem;margin-bottom:4px;'>8. FOKUS PADA X (VARIABLE)</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>"
            "Fokus perbaikan <b>hanya pada kategori X</b><br>"
            "yang dapat dikendalikan &amp; diperbaiki</div></div>"
            "<div style='background:#f0f9ff;border:1.5px solid #38bdf8;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#0369a1;font-size:0.85rem;margin-bottom:4px;'>9. PRIORITISASI (JIKA PERLU)</div>"
            "<div style='font-size:0.78rem;color:#0f172a;'>"
            "Prioritisasi &amp; <b>voting</b> jika X terlalu banyak<br>"
            "&rarr; Pilih yang paling kritikal<br>"
            "&rarr; <b>Rencana Tindakan Lanjut</b></div></div>"
            "</div></div>"
        )
        if _proj_path == "quick":
            st.markdown(
                "<div style='padding:8px 0;font-size:0.9rem;'>"
                "<b>🟢 Quick Path — Fishbone 3M:</b> cukup kelompokkan penyebab ke "
                "<b>Man &middot; Method &middot; Machine</b>, pilih root cause yang paling "
                "berdampak, lalu isi langsung ke tabel Xn di Step 4. "
                "<i>5-Why chain & verification plan formal tidak wajib di Quick Path.</i>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_rca_guide_html, unsafe_allow_html=True)

        # ── Upload foto fishbone (offline) ──
        st.markdown("**📸 Upload foto hasil fishbone analysis (offline):**")
        _fb_photo_key     = f"analyze_fb_photo_{active_pid}"
        _fb_phototype_key = f"analyze_fb_phototype_{active_pid}"

        if _fb_photo_key not in st.session_state:
            _wip_fb3 = memory.load_analyze_wip(active_pid)
            st.session_state[_fb_photo_key]     = _wip_fb3.get("fb_photo_b64", "")
            st.session_state[_fb_phototype_key] = _wip_fb3.get("fb_photo_type", "image/jpeg")

        _fb_photo_file = st.file_uploader(
            "Upload foto fishbone (PNG/JPG)",
            type=["png", "jpg", "jpeg"],
            key=f"fb_photo_upload_{active_pid}",
            disabled=is_locked,
        )
        if _fb_photo_file is not None:
            import base64 as _b64
            _fb_bytes = _fb_photo_file.read()
            _fb_b64   = _b64.b64encode(_fb_bytes).decode("utf-8")
            st.session_state[_fb_photo_key]     = _fb_b64
            st.session_state[_fb_phototype_key] = _fb_photo_file.type
            _wip_fb3 = memory.load_analyze_wip(active_pid)
            _wip_fb3["fb_photo_b64"]  = _fb_b64
            _wip_fb3["fb_photo_type"] = _fb_photo_file.type
            memory.save_analyze_wip(active_pid, _wip_fb3)
            st.image(_fb_bytes, caption=f"📎 {_fb_photo_file.name}", width=700)
        elif st.session_state.get(_fb_photo_key):
            import base64 as _b64
            try:
                st.image(
                    _b64.b64decode(st.session_state[_fb_photo_key]),
                    caption="📎 Foto fishbone tersimpan",
                    width=700,
                )
            except Exception:
                pass

        # Parse fishbone text → list per category (kept for backward compat with agent)
        def _parse_fb(fb_dict):
            result = {}
            for cat, text in (fb_dict or {}).items():
                lines = [l.strip() for l in str(text).splitlines() if l.strip()]
                if lines:
                    result[cat] = lines
            return result

        st.divider()

        # ── Step 4: Potential Causes (Xn) ──
        st.markdown("### Step 4 — Potential Causes (Xn List)")
        st.caption("Isi daftar potential root causes hasil analisis fishbone & 5-Why yang sudah dilakukan secara offline. Tabel ini bersifat final dan menjadi dasar verifikasi di Step 6–7.")

        col_gen, col_save_pc = st.columns([1,1])
        with col_gen:
            # Generate bisa dilakukan jika ada DOT code (Opsi B) atau image (Opsi A)
            _has_dot_for_gen = bool(st.session_state.get(_pm_dot_key, ""))
            _has_img_for_gen = bool(st.session_state.get(_pm_image_key, ""))
            _gen_causes_disabled = is_locked or (not _has_dot_for_gen and not _has_img_for_gen)
            if _gen_causes_disabled and not is_locked:
                st.caption("⚠️ Generate swimlane/upload process map di Step 2 terlebih dahulu.")

            if st.button("🤖 Generate Potential Causes", key=f"gen_pot_{active_pid}",
                        disabled=_gen_causes_disabled):
                _pm_dot_ctx = st.session_state.get(_pm_dot_key, "")
                with st.spinner("Agent generating potential causes dari fishbone & process map..."):
                    from app.agents.analyze_agent import (
                        _propose_potential_causes_llm,
                        _extract_upstream_context,
                    )
                    _upstream_pc = _extract_upstream_context(define_final, measure_final)
                    # Pass DOT code as process context (fishbone offline)
                    _pm_as_steps = [{"step_name": "Lihat DOT process map", "dot_code_context": _pm_dot_ctx[:800]}] if _pm_dot_ctx else []
                    _new_causes  = _propose_potential_causes_llm({}, _pm_as_steps, _upstream_pc)
                    if _new_causes:
                        st.session_state[_pot_causes_key] = _new_causes
                        st.session_state.pop(_vplan_key, None)
                        st.rerun()
                    else:
                        st.error("Agent tidak menghasilkan causes. Coba lagi atau isi tabel secara manual.")

        if _pot_causes_key not in st.session_state:
            st.session_state[_pot_causes_key] = []

        pot_causes = st.session_state.get(_pot_causes_key, [])

        if pot_causes:
            _pc_df = pd.DataFrame([{
                "xn":               c.get("xn", ""),
                "description":      c.get("description", "") or c.get("cause", ""),
                "in_process_step":  c.get("in_process_step", "") or c.get("source", ""),
                "direct_effect_on_y": c.get("direct_effect_on_y", ""),
            } for c in pot_causes])
            _pc_edit = st.data_editor(
                _pc_df,
                num_rows="dynamic",
                use_container_width=True,
                key=f"pc_editor_{active_pid}",
                disabled=is_locked,
                column_config={
                    "xn":               st.column_config.TextColumn("Xn", width="small", disabled=True),
                    "description":      st.column_config.TextColumn("Description", width="large"),
                    "in_process_step":  st.column_config.TextColumn("In Process Step?", width="medium"),
                    "direct_effect_on_y": st.column_config.TextColumn("Direct Effect on Y", width="large"),
                },
            )
            with col_save_pc:
                if st.button("💾 Simpan Causes", key=f"pc_save_{active_pid}", disabled=is_locked):
                    _raw = _pc_edit.to_dict(orient="records") if isinstance(_pc_edit, pd.DataFrame) else []
                    # Rebuild clean — re-number Xn, skip baris kosong
                    _saved_pc = []
                    _n = 1
                    for _row in _raw:
                        _desc = str(_row.get("description", "") or "").strip()
                        if not _desc:
                            continue  # skip baris kosong
                        _saved_pc.append({
                            "xn":               f"X{_n}",
                            "cause":            _desc,
                            "description":      _desc,
                            "in_process_step":  str(_row.get("in_process_step", "") or "").strip(),
                            "direct_effect_on_y": str(_row.get("direct_effect_on_y", "") or "").strip(),
                            "root_cause":       _desc,
                        })
                        _n += 1
                    st.session_state[_pot_causes_key] = _saved_pc
                    _wip_pc = memory.load_analyze_wip(active_pid)
                    _wip_pc["potential_causes"] = _saved_pc
                    memory.save_analyze_wip(active_pid, _wip_pc)
                    st.success(f"✅ {len(_saved_pc)} causes tersimpan (X1–X{len(_saved_pc)}).")
        else:
            st.info("Klik 'Generate Potential Causes' atau 'Tambah Baris Xn' untuk mengisi daftar causes.")

        # ── Link X causes → Process Map (⚡ markers) ──
        st.markdown("---")
        _pm_linked_dot_key = f"analyze_pm_linked_dot_{active_pid}"

        # Load dari WIP jika belum ada di session_state
        if _pm_linked_dot_key not in st.session_state:
            _wip_linked = memory.load_analyze_wip(active_pid)
            st.session_state[_pm_linked_dot_key] = _wip_linked.get("pm_linked_dot_code", "")

        _cur_base_dot  = st.session_state.get(_pm_dot_key, "")
        _cur_x_causes  = st.session_state.get(_pot_causes_key, [])
        _linked_dot    = st.session_state.get(_pm_linked_dot_key, "")

        st.markdown("### Step 5 — Link Root Causes ke Process Map")
        st.caption(
            "Setelah tabel Xn terisi, klik tombol ini — agent akan menandai node proses yang terkait "
            "dengan tiap root cause menggunakan penanda ⚡ dan warna amber."
        )

        _link_col1, _link_col2 = st.columns([2, 1])
        with _link_col1:
            _no_dot   = not _cur_base_dot
            _no_xn    = not _cur_x_causes
            _link_dis = is_locked or _no_dot or _no_xn
            _link_hint = ""
            if _no_dot:
                _link_hint = "⚠️ Generate swimlane diagram dulu (Opsi B di Step 2)."
            elif _no_xn:
                _link_hint = "⚠️ Generate atau isi tabel Xn terlebih dahulu."
            if _link_hint:
                st.caption(_link_hint)

            if st.button(
                "⚡ Link to Process Mapping",
                key=f"pm_link_causes_{active_pid}",
                disabled=_link_dis,
                help="Agent akan match Xn ke step proses dan beri tanda ⚡ pada node yang relevan.",
            ):
                with st.spinner("Agent menandai node process map yang terkait root causes..."):
                    from app.agents.analyze_agent import link_causes_to_process_map as _link_fn
                    _lnk_result = _link_fn(_cur_base_dot, _cur_x_causes)
                    if _lnk_result.get("dot_code"):
                        st.session_state[_pm_linked_dot_key] = _lnk_result["dot_code"]
                        _wip_lnk = memory.load_analyze_wip(active_pid)
                        _wip_lnk["pm_linked_dot_code"] = _lnk_result["dot_code"]
                        memory.save_analyze_wip(active_pid, _wip_lnk)
                        st.rerun()
                    else:
                        st.error("Agent tidak berhasil mapping causes ke proses. Coba lagi.")

        with _link_col2:
            if _linked_dot:
                if st.button("🗑️ Reset ⚡ Markers", key=f"pm_link_reset_{active_pid}", disabled=is_locked):
                    st.session_state[_pm_linked_dot_key] = ""
                    _wip_rl = memory.load_analyze_wip(active_pid)
                    _wip_rl["pm_linked_dot_code"] = ""
                    memory.save_analyze_wip(active_pid, _wip_rl)
                    st.rerun()

        if _linked_dot:
            st.markdown(
                "<div style='background:#fffbeb;border:1px solid #fbbf24;border-radius:10px;"
                "padding:10px 14px;margin:8px 0 4px 0;font-size:0.82rem;color:#78350f;'>"
                "⚡ <b>Node berwarna amber</b> = area proses yang terkait dengan potential root causes (Xn). "
                "Verifikasi relevansinya sebelum masuk ke tahap improvement.</div>",
                unsafe_allow_html=True,
            )
            try:
                st.graphviz_chart(compact_dot(_linked_dot), use_container_width=False)
            except Exception as _le:
                st.error(f"Diagram error: {_le}")
                st.code(_linked_dot, language="text")
        elif _cur_base_dot and _cur_x_causes:
            st.info("Klik **⚡ Link to Process Mapping** untuk menandai node yang terkait root causes.")

        st.divider()
    else:
        # -- Quick Path: Root Cause Terverifikasi (dari Gemba) --
        st.markdown("### Root Cause Terverifikasi (dari Gemba)")
        st.caption(
            "Isi root cause yang sudah diverifikasi langsung di gemba dan akan "
            "diimplementasi solusinya. Satu root cause per baris (boleh beberapa). "
            "Dibaca sebagai hipotesis: \"Target tidak tercapai karena [root cause]\"."
        )
        if _gemba_key not in st.session_state:
            _wip_g = memory.load_analyze_wip(active_pid) or {}
            st.session_state[_gemba_key] = _wip_g.get("gemba_root_causes_text", "")
        _gemba_text = st.text_area(
            "Verified root causes (satu per baris)",
            value=st.session_state.get(_gemba_key, ""),
            height=140,
            key=f"analyze_gemba_rc_input_{active_pid}",
            disabled=is_locked,
            placeholder="Satu root cause per baris. Contoh: Mesin sering downtime karena perawatan tidak terjadwal",
        )
        if _gemba_text != st.session_state.get(_gemba_key, ""):
            st.session_state[_gemba_key] = _gemba_text
            _wip_g = memory.load_analyze_wip(active_pid) or {}
            _wip_g["gemba_root_causes_text"] = _gemba_text
            memory.save_analyze_wip(active_pid, _wip_g)
        _gemba_lines = [l.strip() for l in (_gemba_text or "").splitlines() if l.strip()]
        if _gemba_lines:
            st.markdown("**Hipotesis (akan diteruskan ke Improve):**")
            for _gl in _gemba_lines:
                st.write(f"- Target tidak tercapai karena **{_gl}**")
        else:
            st.info("Isi minimal 1 root cause terverifikasi sebelum Generate Draft.")
        st.divider()

    if _proj_path != "quick":
        # ── Step 6: Verification Plan ──
        st.markdown("### Step 6 — Verification Plan")

        _pc_for_vplan = st.session_state.get(_pot_causes_key, [])
        _n_causes = len([c for c in _pc_for_vplan if (c.get("description") or c.get("cause","")).strip()])

        if _n_causes:
            st.caption(f"Agent menyusun verification plan berdasarkan **{_n_causes} potential root cause** yang tersimpan di Step 4. Review dan sesuaikan sebelum ke Step 7.")
        else:
            st.caption("Isi dan simpan tabel Xn di Step 4 terlebih dahulu — verification plan dibuat berdasarkan causes tersebut.")

        col_vp_gen, col_vp_save = st.columns([1,1])
        with col_vp_gen:
            if st.button("🤖 Generate Verification Plan", key=f"gen_vplan_{active_pid}",
                         disabled=is_locked or not _n_causes):
                if not _pc_for_vplan:
                    st.warning("Simpan causes di Step 4 terlebih dahulu.")
                else:
                    with st.spinner("Agent menyusun verification plan berdasarkan Xn list dari Step 4..."):
                        from app.agents.analyze_agent import (
                            _build_verification_plan_llm,
                            _extract_upstream_context,
                        )
                        _upstream_vp = _extract_upstream_context(define_final, measure_final)
                        _new_vplan = _build_verification_plan_llm(_pc_for_vplan, _upstream_vp)
                    if _new_vplan:
                        # Validasi: hanya simpan baris yang Xn-nya ada di Step 4
                        _valid_xns = {c.get("xn","") for c in _pc_for_vplan}
                        _filtered_vplan = [v for v in _new_vplan if v.get("xn","") in _valid_xns]
                        # Jika agent miss beberapa Xn, fallback ke semua
                        if not _filtered_vplan:
                            _filtered_vplan = _new_vplan
                        st.session_state[_vplan_key] = _filtered_vplan
                        st.rerun()
                    else:
                        st.error("Agent tidak menghasilkan verification plan. Cek koneksi LLM atau lengkapi isian Step 4.")

        if _vplan_key not in st.session_state:
            st.session_state[_vplan_key] = []

        vplan = st.session_state.get(_vplan_key, [])
        if vplan:
            _vp_df = pd.DataFrame([{
                "xn":                   v.get("xn",""),
                "root_cause":           v.get("root_cause",""),
                "input_measurement_x":  v.get("input_measurement_x",""),
                "relation_to_y":        v.get("relation_to_y",""),
                "verification_method":  v.get("verification_method",""),
                "data_needed":          v.get("data_needed",""),
            } for v in vplan])
            _vp_edit = st.data_editor(
                _vp_df,
                num_rows="dynamic",
                use_container_width=True,
                key=f"vp_editor_{active_pid}",
                disabled=is_locked,
            )
            with col_vp_save:
                if st.button("💾 Simpan Vplan", key=f"vp_save_{active_pid}", disabled=is_locked):
                    if isinstance(_vp_edit, pd.DataFrame):
                        st.session_state[_vplan_key] = _vp_edit.to_dict(orient="records")
                    st.success("Verification plan tersimpan.")
        else:
            st.info("Generate Verification Plan setelah potential causes tersedia.")

        st.divider()

        # ── Step 7: Verification Results (user fills after fieldwork) ──
        st.markdown("### Step 7 — Verification Results")
        st.caption("Diisi oleh Project Leader setelah melakukan verifikasi lapangan/data. Agent tidak mengisi bagian ini.")

        # Seed logic: prioritas WIP → vplan → pot_causes → kosong
        if _vresults_key not in st.session_state or not st.session_state.get(_vresults_key):
            _wip_vr = memory.load_analyze_wip(active_pid)
            _vr_from_wip = _wip_vr.get("verification_results", [])
            if _vr_from_wip:
                st.session_state[_vresults_key] = _vr_from_wip
            else:
                # Seed dari vplan jika ada, else dari pot_causes
                _vr_seed = []
                _vr_src  = vplan or []
                if not _vr_src:
                    _vr_src = [
                        {"xn": c.get("xn",""), "root_cause": c.get("description","") or c.get("cause","")}
                        for c in (st.session_state.get(_pot_causes_key) or [])
                    ]
                for _v in _vr_src:
                    _vr_seed.append({
                        "xn":                   _v.get("xn",""),
                        "root_cause":           _v.get("root_cause","") or _v.get("cause","") or _v.get("description",""),
                        "verification_result":  "",
                        "impact_pct_of_target": "",
                        "impact_level":         "medium",
                        "is_root_cause":        False,
                    })
                st.session_state[_vresults_key] = _vr_seed

        # Tombol sync dari vplan (jika vplan sudah di-generate setelah vresults)
        _vr_top_c1, _vr_top_c2 = st.columns([2, 1])
        with _vr_top_c2:
            if vplan and not is_locked:
                if st.button("🔄 Sync dari Verification Plan", key=f"vr_sync_{active_pid}",
                             help="Reset baris Xn dari verification plan (isi kolom verification result tidak ikut terhapus)"):
                    _cur_vr = {r.get("xn",""): r for r in (st.session_state.get(_vresults_key) or [])}
                    _synced = []
                    for _v in vplan:
                        _xn = _v.get("xn","")
                        _existing = _cur_vr.get(_xn, {})
                        _synced.append({
                            "xn":                   _xn,
                            "root_cause":           _v.get("root_cause",""),
                            "verification_result":  _existing.get("verification_result",""),
                            "impact_pct_of_target": _existing.get("impact_pct_of_target",""),
                            "impact_level":         _existing.get("impact_level","medium"),
                            "is_root_cause":        _existing.get("is_root_cause", False),
                        })
                    st.session_state[_vresults_key] = _synced
                    st.rerun()

        vresults = st.session_state.get(_vresults_key, [])

        if vresults:
            _vr_df = pd.DataFrame([{
                "xn":                   r.get("xn",""),
                "root_cause":           r.get("root_cause",""),
                "verification_result":  r.get("verification_result",""),
                "impact_pct_of_target": str(r.get("impact_pct_of_target","") or ""),
                "impact_level":         r.get("impact_level","medium"),
                "is_root_cause":        bool(r.get("is_root_cause", False)),
            } for r in vresults])
            _vr_edit = st.data_editor(
                _vr_df,
                num_rows="dynamic",
                use_container_width=True,
                key=f"vr_editor_{active_pid}",
                disabled=is_locked,
                column_config={
                    "xn":               st.column_config.TextColumn("Xn", width="small", disabled=True),
                    "root_cause":       st.column_config.TextColumn("Root Cause", width="large"),
                    "verification_result":    st.column_config.TextColumn("Verification Result", width="large"),
                    "impact_pct_of_target":   st.column_config.TextColumn("Impact % of Target Gap", width="medium"),
                    "impact_level":           st.column_config.SelectboxColumn("Impact Level", options=["high","medium","low"], width="small"),
                    "is_root_cause":          st.column_config.CheckboxColumn("Root Cause?", width="small"),
                },
            )
            if st.button("💾 Simpan Verification Results", key=f"vr_save_{active_pid}", disabled=is_locked):
                if isinstance(_vr_edit, pd.DataFrame):
                    _saved_vr = _vr_edit.to_dict(orient="records")
                    # Normalize is_root_cause → Python bool
                    for _r in _saved_vr:
                        _rc = _r.get("is_root_cause", False)
                        if isinstance(_rc, str):
                            _r["is_root_cause"] = _rc.strip().lower() in ("true","yes","1","✅")
                        else:
                            _r["is_root_cause"] = bool(_rc)
                    st.session_state[_vresults_key] = _saved_vr
                    _wip_vr2 = memory.load_analyze_wip(active_pid)
                    _wip_vr2["verification_results"] = _saved_vr
                    memory.save_analyze_wip(active_pid, _wip_vr2)
                    _confirmed_n = sum(1 for r in _saved_vr if r.get("is_root_cause"))
                    st.success(f"✅ Tersimpan — {_confirmed_n} root cause terkonfirmasi dari {len(_saved_vr)} cause.")
        else:
            st.info("Tabel kosong — klik **➕ Tambah Baris** untuk mulai mengisi, atau generate Verification Plan di Step 6 terlebih dahulu.")

        # ── Upload data pendukung verifikasi ──
        st.markdown("**📎 Upload Data Pendukung Verifikasi (opsional)**")
        st.caption("Upload foto gemba, data sheet, atau file lain sebagai bukti verifikasi. Tidak harus ada — isi jika tersedia.")

        _vr_uploads_key = f"analyze_vr_uploads_{active_pid}"
        if _vr_uploads_key not in st.session_state:
            _wip_upld = memory.load_analyze_wip(active_pid)
            st.session_state[_vr_uploads_key] = _wip_upld.get("vr_uploads", [])

        _vr_files = st.file_uploader(
            "Upload foto/data verifikasi",
            type=["png","jpg","jpeg","pdf","xlsx","csv"],
            accept_multiple_files=True,
            key=f"vr_file_uploader_{active_pid}",
            disabled=is_locked,
        )
        if _vr_files:
            import base64 as _b64
            _new_uploads = []
            for _f in _vr_files:
                _bytes = _f.read()
                _new_uploads.append({
                    "name": _f.name,
                    "type": _f.type,
                    "b64":  _b64.b64encode(_bytes).decode("utf-8"),
                })
            st.session_state[_vr_uploads_key] = _new_uploads
            _wip_u = memory.load_analyze_wip(active_pid)
            _wip_u["vr_uploads"] = _new_uploads
            memory.save_analyze_wip(active_pid, _wip_u)
            # Preview images
            for _upld in _new_uploads:
                if _upld["type"].startswith("image/"):
                    try:
                        st.image(_b64.b64decode(_upld["b64"]), caption=_upld["name"], width=400)
                    except Exception:
                        pass
                else:
                    st.caption(f"📎 {_upld['name']}")
        elif st.session_state.get(_vr_uploads_key):
            import base64 as _b64
            st.caption(f"📎 {len(st.session_state[_vr_uploads_key])} file tersimpan:")
            for _upld in st.session_state[_vr_uploads_key]:
                if _upld.get("type","").startswith("image/"):
                    try:
                        st.image(_b64.b64decode(_upld["b64"]), caption=_upld["name"], width=400)
                    except Exception:
                        pass
                else:
                    st.caption(f"📎 {_upld['name']}")

    else:
        st.info(
            "⚡ **Quick Path** — Verification Plan & Results formal tidak wajib. "
            "Cukup isi **Root Cause Terverifikasi (dari Gemba)** di atas, lalu Generate Draft "
            "untuk lanjut ke fase Improve."
        )
    st.divider()

    # ── Input Form (observations + submit) ──
    with st.form(key="analyze_input_form", clear_on_submit=False):
        _seed_ins  = (define_final or {}).get("inputs") or {}
        _industry  = _seed_ins.get("industry") or "-"
        _process   = _seed_ins.get("process_area") or "-"
        _pain      = _seed_ins.get("pain_theme") or "-"


        submitted = st.form_submit_button(
            "🔍 Generate / Update Draft" if not is_locked else "🔒 Locked",
            type="primary",
            disabled=is_locked,
        )

    if submitted:
        # _parse_fb hanya terdefinisi di Step 3 (standard). Quick tidak pakai fishbone.
        _fb_parsed = _parse_fb(st.session_state.get(_fishbone_key, {})) if _proj_path != "quick" else {}
        user_inputs = {
            "industry":              _industry,
            "process_area":          _process,
            "pain_theme":            _pain,
            "observations":          "",
            "fivewhy_iterations":    3,
            "process_map_steps":     st.session_state.get(_process_steps_key, []),
            "process_map_image_b64":  st.session_state.get(_pm_image_key, ""),
            "process_map_image_type": st.session_state.get(_pm_imgtype_key, "image/png"),
            # DOT swimlane (Opsi B) — cara utama buat process map sekarang
            "pm_dot_code":           st.session_state.get(f"analyze_pm_dot_{active_pid}", ""),
            "pm_linked_dot_code":    st.session_state.get(f"analyze_pm_linked_dot_{active_pid}", ""),
            # Fishbone dilakukan offline → foto sebagai bukti
            "fishbone_inputs":       _fb_parsed,
            "fb_photo_uploaded":     bool(st.session_state.get(f"analyze_fb_photo_{active_pid}", "")),
            "potential_causes":      st.session_state.get(_pot_causes_key, []),
            "verification_plan":     st.session_state.get(_vplan_key, []),
            # Baca vresults: prioritas session state → WIP (antisipasi user lupa klik Simpan)
            "verification_results":  (
                st.session_state.get(_vresults_key)
                or memory.load_analyze_wip(active_pid).get("verification_results")
                or []
            ),
            "project_path":          _proj_path,
        }

        # Quick path: root cause terverifikasi dari gemba → langsung jadi
        # potential_causes + verification_results (is_root_cause=True) agar
        # mengalir ke root_cause_summary & fase Improve.
        if _proj_path == "quick":
            _g_text = st.session_state.get(_gemba_key, "") or (
                memory.load_analyze_wip(active_pid).get("gemba_root_causes_text", "")
            )
            _g_lines = [l.strip() for l in (_g_text or "").splitlines() if l.strip()]
            _g_pc, _g_vr = [], []
            for _i, _rc in enumerate(_g_lines, 1):
                _xn = f"X{_i}"
                _g_pc.append({"xn": _xn, "description": _rc, "cause": _rc, "root_cause": _rc})
                _g_vr.append({
                    "xn": _xn, "cause": _rc, "description": _rc, "root_cause": _rc,
                    "is_root_cause": True,
                    "verification_method": "Gemba — terverifikasi langsung",
                    "finding": "Dikonfirmasi dari gemba; solusi akan diimplementasi",
                    "hypothesis": f"Target tidak tercapai karena {_rc}",
                })
            user_inputs["potential_causes"]     = _g_pc
            user_inputs["verification_results"] = _g_vr
            user_inputs["verification_plan"]    = []

        user_feedback = None

        with st.spinner("Menjalankan ANALYZE agent..."):
            result = run_analyze_agent(
                project_id=active_pid,
                user_inputs=user_inputs,
                define_final=define_final,
                measure_final=measure_final,
                user_feedback=user_feedback,
                project_path=_proj_path,
            )

        # Sync session state with agent outputs
        agent_outs = result.get("analyze_state",{}).get("outputs",{})
        if agent_outs.get("potential_causes"):
            st.session_state[_pot_causes_key] = agent_outs["potential_causes"]
        if agent_outs.get("verification_plan"):
            st.session_state[_vplan_key] = agent_outs["verification_plan"]
        if agent_outs.get("verification_results"):
            st.session_state[_vresults_key] = agent_outs["verification_results"]

        st.session_state["analyze_draft"] = result
        st.session_state["analyze_final"] = None
        st.session_state[_revise_key]     = False
        st.rerun()

    # ── Agent Coaching ──
    _cur_result = st.session_state.get("analyze_draft") or st.session_state.get("analyze_final")
    _as = (_cur_result or {}).get("analyze_state") if isinstance(_cur_result, dict) else None
    if isinstance(_as, dict):
        gate    = _as.get("gate") or {}
        status  = gate.get("status","-")
        coaching = (_as.get("coaching_md") or _as.get("insight_md") or "").strip()
        st.markdown("### 🧭 Agent Coaching")
        st.markdown(f"**Gate Status:** `{status}`")
        if coaching:
            st.markdown(coaching)
        else:
            st.info("Belum ada coaching. Generate / Update Draft terlebih dahulu.")

# ── TAB 2: Report ──
with tab_report:
    if not analyze_state:
        st.info("Generate draft untuk melihat report.")
    else:
        outs     = analyze_state.get("outputs") or {}
        upstream = analyze_state.get("upstream") or {}

        with st.expander("🔗 Context from DEFINE + MEASURE", expanded=True):
            st.markdown(f"**Problem:** {upstream.get('problem_statement') or '-'}")
            st.markdown(f"**Goal:** {upstream.get('goal_statement') or '-'}")
            st.markdown(f"**Y Variable:** {upstream.get('y_variable') or '-'}")
            st.markdown(f"**Baseline Mean:** {upstream.get('baseline_mean') or '-'}")
            if upstream.get("baseline_target",{}).get("available"):
                t = upstream["baseline_target"]
                st.markdown(f"**Target:** {t.get('direction','')} {t.get('value','')}")

        # Y anchor — selalu visible
        _y_anc = (analyze_state.get("upstream") or {}).get("y_variable","")
        _bm    = (analyze_state.get("upstream") or {}).get("baseline_mean")
        _ti    = (analyze_state.get("upstream") or {}).get("baseline_target") or {}
        if _y_anc:
            st.markdown(
                f"""<div style='background:#fffbeb;border:1px solid #fbbf24;
                border-radius:10px;padding:12px 16px;margin-bottom:12px;'>
                <b>Target Root Cause Analysis:</b> Semua cause yang terkonfirmasi harus memiliki jalur kausal langsung ke
                <b>{_y_anc}</b> (baseline: {_bm}, target: {_ti.get('direction','')} {_ti.get('value','')})
                </div>""",
                unsafe_allow_html=True,
            )

        st.markdown("### 🔍 ANALYZE Outputs")

        gate    = analyze_state.get("gate") or {}
        g_status = gate.get("status","-")
        if g_status == "PASS":      st.success(f"Gate: **{g_status}**")
        elif g_status == "WEAK_PASS": st.warning(f"Gate: **{g_status}**")
        elif g_status == "FAIL":    st.error(f"Gate: **{g_status}**")

        failed  = gate.get("failed_rules") or []
        actions = gate.get("next_actions") or []
        if failed:
            st.markdown("**Failed Rules:**")
            for f in failed: st.write(f"- `{f}`")
        if actions:
            st.markdown("**Next Actions:**")
            for i, a in enumerate(actions,1): st.write(f"{i}. {a}")

        # Process map check (standard only — Step 2 disembunyikan di quick)
        pm_check = outs.get("process_map_check") or {}
        if _proj_path != "quick" and pm_check.get("feedback"):
            st.divider()
            st.markdown("**📋 Process Map Check:**")
            icon = "✅" if pm_check.get("status")=="complete" else "⚠️"
            st.markdown(f"{icon} {pm_check['feedback']}")
            if pm_check.get("problem_area_steps"):
                st.markdown(f"*Problem area steps: {', '.join(pm_check['problem_area_steps'])}*")

        # Fishbone check (standard only — Step 3 disembunyikan di quick)
        fb_check = outs.get("fishbone_check") or {}
        if _proj_path != "quick" and fb_check.get("feedback"):
            st.divider()
            st.markdown("**🦴 Fishbone Check:**")
            icon = "✅" if fb_check.get("status")=="complete" else "⚠️"
            st.markdown(f"{icon} {fb_check['feedback']}")
            if fb_check.get("high_priority_causes"):
                st.markdown("*High priority causes:*")
                for c in fb_check["high_priority_causes"]:
                    st.write(f"- {c}")

        _coaching = (analyze_state.get("coaching_md") or analyze_state.get("insight_md") or "").strip()
        if _coaching:
            st.divider()
            st.markdown("### 🧭 Agent Coaching")
            st.markdown(_coaching)

# ── TAB 3: Potential Causes ──
with tab_causes:
    if not analyze_state:
        st.info("Generate draft untuk melihat causes.")
    else:
        outs = analyze_state.get("outputs") or {}

        pot_causes = outs.get("potential_causes") or []
        if pot_causes:
            st.markdown("### Potential Causes (Xn)")
            st.caption("Root cause sudah final sesuai hasil fishbone & 5-Why offline.")
            _pc_display = [{
                "Xn":                   c.get("xn",""),
                "Description":          c.get("description","") or c.get("cause",""),
                "In Process Step?":     c.get("in_process_step","") or c.get("source",""),
                "Direct Effect on Y":   c.get("direct_effect_on_y",""),
            } for c in pot_causes]
            st.dataframe(pd.DataFrame(_pc_display), use_container_width=True, hide_index=True)
        else:
            st.info("Potential causes belum tersedia. Isi tabel Xn di tab Input → Step 4.")

# ── TAB 4: Verification Analysis ──
with tab_verification:
    if _proj_path == "quick":
        st.info("🔬 Tab **Verification Analysis** tidak berlaku untuk **Quick path** — verifikasi formal root cause di-skip (B-rule).")
    elif not analyze_state:
        st.info("Generate draft untuk melihat verification analysis.")
    else:
        outs = analyze_state.get("outputs") or {}

        vplan = outs.get("verification_plan") or []
        if vplan:
            st.markdown("### Verification Plan")
            st.dataframe(pd.DataFrame(vplan), use_container_width=True, hide_index=True)
            st.divider()

        analyses = outs.get("verification_analysis") or []
        if analyses:
            st.markdown("### Detailed Verification Analysis")
            for a in analyses:
                is_rc = a.get("is_root_cause", False)
                icon  = "✅" if is_rc else "❌"
                with st.expander(f"{icon} **{a.get('xn','')}** — {a.get('root_cause','')}", expanded=is_rc):
                    st.markdown(f"**Hypothesis:** _{a.get('hypothesis_statement','')}_")
                    st.markdown(
                        f"""<div style='background:#f0f9ff;border:1px solid #bae6fd;
                        border-radius:10px;padding:12px 16px;font-size:14px;line-height:1.7'>
                        {a.get('graphical_display_description','')}</div>""",
                        unsafe_allow_html=True,
                    )
                    impact = a.get("impact_pct_of_target","")
                    if impact:
                        st.markdown(f"**Dampak pada KPI:** {impact}% dari gap ke target")
                    verdict = "🟢 ROOT CAUSE TERKONFIRMASI" if is_rc else "🔴 Tidak terkonfirmasi"
                    st.markdown(f"**Verdict:** {verdict}")
        else:
            st.info("Verification analysis tersedia setelah verification results diisi dan draft di-generate.")

# ── TAB 5: Root Cause Summary Table ──
with tab_rca:
    if not analyze_state:
        st.info("Generate draft untuk melihat root cause summary.")
    else:
        outs = analyze_state.get("outputs") or {}
        rca_table = outs.get("root_cause_summary_table") or []
        if rca_table:
            st.markdown("### Root Cause Summary Table")
            st.dataframe(
                pd.DataFrame(rca_table),
                use_container_width=True,
                hide_index=True,
            )

            confirmed = [r for r in rca_table if "Yes" in str(r.get("is_root_cause",""))]
            if confirmed:
                st.success(f"✅ {len(confirmed)} confirmed root cause(s).")
                st.markdown("**Confirmed root causes to carry forward to IMPROVE:**")
                for r in confirmed:
                    st.markdown(f"- **{r.get('xn','')}** {r.get('root_cause','')} — Impact: {r.get('impact','-')}")
            else:
                st.warning("Belum ada root cause yang terkonfirmasi.")
        else:
            st.info("Root cause summary table belum tersedia.")

        # As-Is Process Map dengan node penanda lokasi root cause
        _linked_rca = st.session_state.get(f"analyze_pm_linked_dot_{active_pid}", "")
        if not _linked_rca:
            _w_rca = memory.load_analyze_wip(active_pid) or {}
            _linked_rca = _w_rca.get("pm_linked_dot_code", "") or (outs.get("pm_linked_dot_code", "") if outs else "")
        if _linked_rca:
            st.divider()
            st.markdown("#### 🗺️ As-Is Process Map — Lokasi Root Cause")
            st.caption("Process map dengan penanda pada node tempat root cause teridentifikasi.")
            try:
                st.graphviz_chart(compact_dot(_linked_rca), use_container_width=False)
            except Exception:
                st.code(_linked_rca, language="text")

# ── TAB: As-Is Process Map ──
with tab_asis:
    st.markdown("### 🗺️ As-Is Process Map")
    _asis_wip = memory.load_analyze_wip(active_pid) or {}
    _asis_dot = (
        st.session_state.get(f"analyze_pm_dot_{active_pid}", "")
        or _asis_wip.get("pm_dot_code")
        or _asis_wip.get("pm_mermaid_code", "")
    )
    _asis_img = (
        st.session_state.get(f"analyze_pm_image_{active_pid}", "")
        or _asis_wip.get("pm_image_b64", "")
    )
    _rendered_asis = False
    if _asis_dot:
        st.caption("🟢 Node hijau = area masalah (problem area)")
        try:
            st.graphviz_chart(compact_dot(_asis_dot), use_container_width=False)
        except Exception:
            st.code(_asis_dot, language="text")
        with st.expander("📋 Lihat / Copy DOT Code", expanded=False):
            st.code(_asis_dot, language="text")
        _rendered_asis = True
    if _asis_img:
        import base64 as _b64_asis
        try:
            st.image(
                _b64_asis.b64decode(_asis_img),
                caption="📎 Process map (uploaded)",
                width=700,
            )
            _rendered_asis = True
        except Exception:
            pass
    if not _rendered_asis:
        if _proj_path == "quick":
            st.info("🗺️ Tab **As-Is Process Map** tidak berlaku untuk **Quick path** — tidak ada input process map (hanya confirmed root cause).")
        else:
            st.info("Belum ada As-Is process map. Buat/Upload di tab Input → Step 2.")

# ── TAB: Fishbone ──
with tab_fishbone:
    st.markdown("### 🦴 Fishbone Diagram")
    _fb_wip = memory.load_analyze_wip(active_pid) or {}
    _fb_img = (
        st.session_state.get(f"analyze_fb_photo_{active_pid}", "")
        or _fb_wip.get("fb_photo_b64", "")
    )
    if _fb_img:
        import base64 as _b64_fb
        try:
            st.image(
                _b64_fb.b64decode(_fb_img),
                caption="📎 Foto fishbone tersimpan",
                width=700,
            )
        except Exception:
            st.info("Foto fishbone tersimpan tapi tidak dapat ditampilkan.")
    elif _proj_path == "quick":
        st.info("🦴 Tab **Fishbone** tidak berlaku untuk **Quick path** — tidak ada input fishbone (hanya confirmed root cause).")
    else:
        st.info("Belum ada file fishbone diupload. Upload di tab Input → Step 3.")

with tab_input:
    # ============================================
    # Finalize & Downloads (full width)
    # ============================================
    st.divider()
    st.subheader("Finalize & Downloads")

    _draft_wrapper = st.session_state.get("analyze_draft")
    _has_draft_now = bool(_draft_wrapper and _draft_wrapper.get("analyze_state"))
    try:
        _gate_status_now = (_draft_wrapper or {}).get("analyze_state",{}).get("gate",{}).get("status")
    except Exception:
        _gate_status_now = None

    c1, c2, c3 = st.columns([1,1,1])

    with c1:
        if _has_draft_now:
            # Blokir finalize jika belum ada confirmed root cause
            _vr_for_fin = (
                st.session_state.get(_vresults_key)
                or (_draft_wrapper or {}).get("analyze_state", {}).get("outputs", {}).get("verification_results", [])
                or memory.load_analyze_wip(active_pid).get("verification_results", [])
            )
            _has_confirmed_for_fin = any(
                str(r.get("is_root_cause", "")).strip().lower()
                not in ("false", "", "none", "0", "❌ no", "❌")
                for r in _vr_for_fin
            )
            # Standard: wajib fase sebelumnya di-approve approver. Quick: tidak perlu approval.
            _prev_block = (_proj_path != "quick") and (not _appr_status.get("prev_approved", False))
            # Standard: wajib isi tanggal aktual selesai fase sebelum finalize.
            _has_end = render_phase_end_date(active_pid, "analyze", disabled=is_locked)
            _no_actual_end = (_proj_path != "quick") and not _has_end
            fin_disabled = (not _has_confirmed_for_fin) or _prev_block or _no_actual_end or (
                (_gate_status_now == "FAIL") and not _appr_status.get("can_advance", False)
            )
            if st.button("✅ Finalize (Lock Final)", key="analyze_finalize_btn", disabled=fin_disabled):
                draft_state  = _draft_wrapper["analyze_state"]
                final_result = finalize_analyze_agent(active_pid, draft_state)
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason","Diblokir oleh gate."))
                else:
                    st.session_state["analyze_final"] = final_result
                    st.session_state["analyze_draft"] = None
                    st.session_state[_revise_key]     = False
                    st.success("Finalized.")
                    st.rerun()
            if fin_disabled:
                if not _has_confirmed_for_fin:
                    st.caption("⚠️ Finalize diblokir: belum ada root cause terkonfirmasi di Step 7 (Verification Results). Konfirmasi minimal 1 root cause (✅ Yes) terlebih dahulu.")
                elif _prev_block:
                    st.caption("⚠️ Finalize diblokir: fase sebelumnya (MEASURE) belum di-approve oleh approver (jalur Standard).")
                elif _no_actual_end:
                    st.caption("⚠️ Finalize diblokir: isi **Tanggal aktual selesai fase** dulu.")
                else:
                    st.caption("⚠️ Finalize diblokir: Gate FAIL.")
        elif _has_final:
            st.button("✅ Finalized", key="analyze_finalized_badge", disabled=True)
        else:
            st.button("✅ Finalize (Lock Final)", key="analyze_fin_disabled", disabled=True)
            st.caption("Generate draft terlebih dahulu.")

    with c2:
        _word_src = final_on_disk or draft_on_disk
        _word_lbl = "🛠️ Generate Word (Final)" if final_on_disk else "🛠️ Generate Word (Draft)"
        if _word_src:
            if st.button(_word_lbl, key="analyze_gen_word"):
                export_fn = getattr(reporting, "export_analyze_to_word", None)
                if callable(export_fn):
                    try:
                        _docx_fname = f"Project_{active_pid}_DEF_MEA_ANA_{'Final' if final_on_disk else 'Draft'}.docx"
                        out_path = export_fn(copy.deepcopy(_word_src), path=_docx_fname)
                        st.session_state["analyze_last_docx"] = out_path
                        st.success("Word file generated.")
                    except Exception as e:
                        st.warning(f"Word export belum tersedia: {e}")
                else:
                    st.warning("export_analyze_to_word belum diimplementasi di reporting.py.")
        else:
            st.button("🛠️ Generate Word", key="analyze_gen_word_dis", disabled=True)

    with c3:
        _docx_fname_ana = f"Project_{active_pid}_DEF_MEA_ANA_{'Final' if final_on_disk else 'Draft'}.docx"
        _docx_path = st.session_state.get("analyze_last_docx") or _docx_fname_ana
        _dl_label  = "⬇️ Download Final Word Report" if final_on_disk else "⬇️ Download Draft Word Report"
        try:
            with open(_docx_path,"rb") as f:
                doc_bytes = f.read()
            st.download_button(
                label=_dl_label, data=doc_bytes,
                file_name=_docx_fname_ana,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="analyze_dl_word",
            )
        except FileNotFoundError:
            st.button(_dl_label, key="analyze_dl_word_dis", disabled=True)

    # ============================================
    # Governance (identik Measure)
    # ============================================
    st.divider()
    st.subheader("🏛️ Governance — Stage Gate Approval")

    final_exists    = memory.load_analyze_final(active_pid)
    approval_status = memory.get_phase_approval_status(active_pid, "analyze")
    reviewer_action = (approval_status.get("reviewer") or {}).get("action")
    champion_action = (approval_status.get("champion") or {}).get("action")
    can_advance     = approval_status.get("can_advance", False)
    auto_advance    = approval_status.get("auto_advance", False)
    required        = approval_status.get("required_approvers", [])

    _prev_approved_az = approval_status.get("prev_approved", True)

    # Pre-approval check: confirmed root causes wajib ada sebelum bisa approve
    _az_vr = (final_exists or {}).get("outputs", {}).get("verification_results", [])
    _az_has_confirmed_rc = any(
        str(r.get("is_root_cause", "")).strip().lower()
        not in ("false", "", "none", "0", "❌ no", "❌")
        for r in _az_vr
    )

    # Helper: apakah ada approval aktif yang bisa di-cancel
    _any_approved = reviewer_action == "approve" or champion_action == "approve"

    if not final_exists:
        st.info("Finalize ANALYZE dulu sebelum bisa di-approve.")
    elif auto_advance:
        if _prev_approved_az:
            st.success("🚀 Analyze gate **auto-advance** — tidak perlu approval. Lanjut ke Improve.")
            if not _az_has_confirmed_rc:
                st.warning(
                    "⚠️ Gate terbuka secara otomatis, namun **tidak ada confirmed root cause** di "
                    "Verification Results. Improve akan diblokir. Revisi Analyze terlebih dahulu."
                )
            with st.expander("↩️ Revisi Analyze (buka kembali untuk diedit)", expanded=False):
                st.warning(
                    "Membuka revisi akan **mengunci kembali** gate menuju Improve "
                    "hingga Analyze di-finalize ulang."
                )
                if st.button("↩️ Buka Revisi Analyze", key="az_auto_revise_btn"):
                    st.session_state[_revise_key] = True
                    audit.log_phase_event(active_pid, "analyze", "revise_opened_auto_advance")
                    st.success("Fase Analyze dibuka untuk revisi.")
                    st.rerun()
        else:
            st.warning("⏳ Gate terkunci — MEASURE belum disetujui. Selesaikan approval MEASURE terlebih dahulu.")
    else:
        # Status display — hanya setelah finalized
        col_r, col_c, col_adv = st.columns(3)
        with col_r:
            if "reviewer" in required:
                if reviewer_action=="approve":   st.success("✅ Reviewer: Approved")
                elif reviewer_action=="reject":  st.error("❌ Reviewer: Rejected")
                else:                             st.warning("⏳ Reviewer: Pending")
            else:
                st.info("— Reviewer: tidak diperlukan")
        with col_c:
            if "champion" in required:
                if champion_action=="approve":   st.success("✅ Champion: Approved")
                elif champion_action=="reject":  st.error("❌ Champion: Rejected")
                else:                             st.warning("⏳ Champion: Pending")
            else:
                st.info("— Champion: tidak diperlukan")
        with col_adv:
            if can_advance:
                st.success("🚀 Gate: OPEN — Lanjut ke Improve")
            else:
                st.info("🔒 Gate: LOCKED")

        # Block Approve jika belum ada confirmed root cause
        if final_exists and not _az_has_confirmed_rc:
            st.error(
                "⛔ **Approval diblokir:** Tidak ada confirmed root cause di Verification Results. "
                "Kembali ke Step 7 dan tandai minimal 1 root cause sebagai ✅ Yes, "
                "lalu re-generate & re-finalize sebelum bisa di-approve."
            )

        role = auth.get_current_role()
        if not _prev_approved_az:
            st.warning("⚠️ MEASURE belum disetujui. Approval fase ini belum bisa dilakukan.")
        elif role in required:
            _role_action = (approval_status.get(role) or {}).get("action")
            st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
            if _role_action == "approve":
                st.success("✅ Kamu sudah **Approve** fase ini.")
            elif _role_action == "reject":
                st.error("❌ Kamu sudah **Reject** fase ini.")
            note = st.text_area("Catatan (opsional)", key=f"gov_note_{role}_analyze", height=80)
            col_app, col_rej = st.columns(2)
            with col_app:
                _approve_disabled = (_role_action is not None) or not _az_has_confirmed_rc
                if st.button("✅ Approve", key=f"gov_approve_{role}_analyze",
                             disabled=_approve_disabled):
                    memory.save_approval(active_pid,"analyze",role,"approve",note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid,"analyze",f"approved_by_{role}")
                    st.success("Approval disimpan.")
                    st.rerun()
            with col_rej:
                if st.button("❌ Reject", key=f"gov_reject_{role}_analyze",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid,"analyze",role,"reject",note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid,"analyze",f"rejected_by_{role}")
                    st.warning("Rejection disimpan.")
                    st.rerun()
        elif role == "project_leader":
            if can_advance:
                st.success("✅ Semua approval lengkap.")
            else:
                st.info("Menunggu approval.")

        # Cancel Approval — muncul jika ada approval aktif (approved / can_advance)
        if final_exists and _any_approved:
            st.divider()
            with st.expander("↩️ Cancel Approval & Revisi Analyze", expanded=not _az_has_confirmed_rc):
                st.warning(
                    "⚠️ Membatalkan approval akan **membuka kembali** fase Analyze untuk direvisi. "
                    "Gate menuju Improve akan terkunci kembali hingga Analyze di-approve ulang."
                )
                _cancel_role = auth.get_current_role()
                _cancel_action = (approval_status.get(_cancel_role) or {}).get("action")
                # Siapa yang bisa cancel: role yang sudah approve, champion, atau project_leader
                _can_cancel = (_cancel_action == "approve") or (_cancel_role in ("champion", "project_leader"))
                if _can_cancel:
                    if st.button("↩️ Batalkan Approval & Buka Revisi", key="az_cancel_approval_btn",
                                 type="primary"):
                        memory.reset_approvals(active_pid, "analyze")
                        st.session_state[_revise_key] = True
                        audit.log_phase_event(active_pid, "analyze", f"approval_cancelled_by_{_cancel_role}")
                        st.success("✅ Approval dibatalkan. Fase Analyze sekarang terbuka untuk direvisi.")
                        st.rerun()
                else:
                    _approved_by = ", ".join(
                        r for r in required
                        if (approval_status.get(r) or {}).get("action") == "approve"
                    )
                    st.info(
                        f"Hanya **{_approved_by or 'approver yang sudah approve'}** "
                        f"atau **Champion** yang dapat membatalkan approval ini."
                    )

    # ============================================
    # Audit Log
    # ============================================
    st.divider()
    st.subheader("🧾 Audit Log — ANALYZE Phase")
    events = audit.get_recent_events(project_id=active_pid, phase="Analyze", limit=30)
    if events:
        st.dataframe(events, use_container_width=True)
    else:
        st.write("Belum ada audit event.")