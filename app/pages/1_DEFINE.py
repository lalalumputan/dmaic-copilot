import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import streamlit as st
import app.agents.define_agent as _da
from app.agents.define_agent import run_define_agent, finalize_define_agent
from app.utils import data_processing as dp, memory, audit, reporting, llm_engine
from app.utils import auth
from app.utils.ui_helpers import render_sidebar_header, render_tab_quicknav, render_phase_end_date
from app.utils.charter_template import (
    generate_charter_benefit_template,
    extract_charter_from_upload,
    extract_benefit_from_upload,
)

# ============================================
#helper for md

def _build_define_md(state: dict) -> str:
    """Compatibility bridge: supports build_define_summary_md OR build_define_summary_markdown."""
    fn = getattr(reporting, "build_define_summary_md", None)
    if callable(fn):
        out = fn(state)
        if isinstance(out, str) and out.strip():
            return out

    fn2 = getattr(reporting, "build_define_summary_markdown", None)
    if callable(fn2):
        out = fn2(state)
        if isinstance(out, str) and out.strip():
            return out

    # last resort: try summary_md stored in state
    smd = (state or {}).get("summary_md")
    if isinstance(smd, str) and smd.strip():
        return smd

    return ""

def _render_define_md_fallback(state: dict) -> str:
    """No-LLM fallback markdown built from define_state['outputs']."""
    s = state or {}
    inputs = s.get("inputs") or {}
    outputs = s.get("outputs") or {}

    def _as_lines(x):
        if x is None:
            return []
        if isinstance(x, list):
            return [str(i) for i in x if str(i).strip()]
        if isinstance(x, str):
            return [x] if x.strip() else []
        return [str(x)]

    def _kv(title, val):
        if val is None:
            return ""
        if isinstance(val, dict):
            return f"### {title}\n" + "\n".join([f"- **{k}**: {v}" for k, v in val.items() if str(v).strip()]) + "\n"
        if isinstance(val, list):
            lines = _as_lines(val)
            return f"### {title}\n" + "\n".join([f"- {i}" for i in lines]) + "\n" if lines else ""
        if isinstance(val, str):
            return f"### {title}\n{val}\n" if val.strip() else ""
        return f"### {title}\n{val}\n"

    md = []
    md.append(f"## DEFINE Report — {s.get('project_id','-')}")
    md.append(f"- **Project Name**: {inputs.get('project_name','-')}")
    md.append(f"- **Industry**: {inputs.get('industry','-')}")
    md.append(f"- **Process Area**: {inputs.get('process_area','-')}")
    md.append(f"- **Pain Theme**: {inputs.get('pain_theme','-')}")
    md.append("")

    # Core sections (tolerant to different keys)
    md.append(_kv("Problem Statement", outputs.get("problem_statement") or inputs.get("problem_text")))
    md.append(_kv("Goal", outputs.get("goal_statement") or inputs.get("goal_text")))
    md.append(_kv("Business Case", outputs.get("business_case")))
    md.append(_kv("Scope", outputs.get("project_scope")))
    md.append(_kv("CTQ", outputs.get("ctq_list")))
    md.append(_kv("Y Variable", outputs.get("y_variable")))
    md.append(_kv("X Categories (6M)", outputs.get("x_categories")))
    md.append(_kv("SIPOC", outputs.get("sipoc")))
    md.append(_kv("Early Measurement Plan", outputs.get("early_measure_plan")))
    md.append(_kv("Risk Level", outputs.get("risk_level")))
    md.append(_kv("Project Type", outputs.get("project_type")))

   
    # Clean empties
    out = "\n".join([x for x in md if isinstance(x, str) and x.strip() != ""])
    return out.strip()

def _render_tables_from_outputs(outputs: dict) -> None:
    """Render detailed tables from define outputs."""
    import json, ast
    import pandas as pd

    ctq = outputs.get("ctq_list")
    if not ctq:
        st.info("CTQ List kosong.")
    else:
        if not isinstance(ctq, list):
            ctq = [ctq]
    st.markdown("### CTQ List")
    rows = []

    for c in ctq:
        obj = c

        # Parse string forms
        if isinstance(obj, str):
            s = obj.strip()
            try:
                obj = json.loads(s)
            except Exception:
                try:
                    obj = ast.literal_eval(s)
                except Exception:
                    obj = {"name": s}

        # Unwrap nested dict forms
        if isinstance(obj, dict) and "ctq" in obj and isinstance(obj["ctq"], dict):
            obj = obj["ctq"]

        if isinstance(obj, dict):
            rows.append({
                "Name": str(obj.get("name", "")).strip(),
                "Metric": str(obj.get("metric", "")).strip(),
                "Unit": str(obj.get("unit", "")).strip(),
                "Description": str(obj.get("description", "")).strip(),
            })
        else:
            rows.append({
                "Name": str(obj).strip(),
                "Metric": "",
                "Unit": "",
                "Description": "",
            })

    # If still empty, show raw
    if not rows:
        st.warning("CTQ tidak dapat dirender. Menampilkan raw CTQ.")
        st.write(ctq)
    else:
        df = pd.DataFrame(rows)

        # Remove fully empty rows
        df = df[~((df["Name"] == "") & (df["Metric"] == "") & (df["Unit"] == "") & (df["Description"] == ""))]

        # Deduplicate
        df = df.drop_duplicates()

        st.dataframe(df, width="stretch")


        # X Categories (6M)
    x_categories = outputs.get("x_categories") or {}
    if x_categories:
        st.write("**X Categories (6M):**")
        if isinstance(x_categories, dict):
            st.dataframe(pd.DataFrame(list(x_categories.items()), columns=["Category", "Details"]), width="stretch")
        else:
            st.write(x_categories)
    
    # SIPOC
    sipoc = outputs.get("sipoc") or {}
    if sipoc:
        st.write("**SIPOC:**")
        if isinstance(sipoc, dict):
            st.dataframe(pd.DataFrame(list(sipoc.items()), columns=["Element", "Description"]), width="stretch")
        else:
            st.write(sipoc)

# ============================================

st.set_page_config(page_title="DEFINE", layout="wide")
st.markdown("""
<style>
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
.v-divider {
    width: 1px;
    min-height: 1200px;
    margin: 0 auto;
    background: linear-gradient(to bottom, rgba(15,23,42,0.00), rgba(15,23,42,0.20), rgba(15,23,42,0.00));
}
</style>
""", unsafe_allow_html=True)

auth.require_login()
auth.render_user_badge()

# ---------- Session defaults ----------
st.session_state.setdefault("active_project_id", None)
st.session_state.setdefault("mode", "create")  # create/open
st.session_state.setdefault("define_draft", None)  # wrapper result
st.session_state.setdefault("define_final", None)  # wrapper result
st.session_state.setdefault("define_charter_upload_data", None)
st.session_state.setdefault("define_benefit_upload_data", None)


# ---------- Form helpers ----------
def reset_define_form(pid: str):
    st.session_state["define_project_name_input"] = pid
    st.session_state["define_industry_select"] = "Manufacturing"
    st.session_state["define_process_area_select"] = "Production"
    st.session_state["define_pain_theme_select"] = "Quality"

    st.session_state["define_problem_text"] = ""
    st.session_state["define_goal_text"] = ""
    st.session_state["define_voc_raw"] = ""
    st.session_state["define_key_issue"] = ""

    st.session_state["define_charter_confirmed"] = False
    st.session_state["define_documentation_agreed"] = False
    st.session_state["define_similar_project_exists"] = False
    st.session_state["define_similar_project_note"] = ""
    st.session_state["define_parallel_projects_risk"] = ""
    st.session_state["define_benefit_estimate"] = ""

    st.session_state["define_feedback_text"] = ""



def prefill_define_form_from_state(project_id: str, state: dict) -> None:
    """
    Prefill all DEFINE form widgets from loaded draft/final state.
    IMPORTANT: Must match widget keys used in the form.
    """
    if not isinstance(state, dict):
        return

    # Some states store inputs under "inputs"
    src = state.get("inputs") if isinstance(state.get("inputs"), dict) else state
    # --- helper: ensure text widgets always receive str ---
    def _to_text(v):
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            # join as bullets per line
            return "\n".join([str(x) for x in v if str(x).strip()])
        if isinstance(v, dict):
            # keep readable for text_area
            import json
            try:
                return json.dumps(v, indent=2, ensure_ascii=False)
            except Exception:
                return str(v)
        return str(v)

    # --- Project fields ---
    st.session_state["define_project_name_input"] = src.get("project_name", st.session_state.get("define_project_name_input", ""))

    # --- Dropdowns / selects (must match your options) ---
    if src.get("industry") is not None:
        st.session_state["define_industry_select"] = src.get("industry")
    if src.get("process_area") is not None:
        st.session_state["define_process_area_select"] = src.get("process_area")
    if src.get("pain_theme") is not None:
        st.session_state["define_pain_theme_select"] = src.get("pain_theme")

        # --- Text areas ---
    if src.get("problem_text") is not None:
        st.session_state["define_problem_text"] = _to_text(src.get("problem_text"))
    if src.get("goal_text") is not None:
        st.session_state["define_goal_text"] = _to_text(src.get("goal_text"))

    # VOC: UI uses define_voc_raw (text_area) -> MUST be string
    voc_val = src.get("voc_raw")
    if voc_val is None:
        voc_val = src.get("voc_list")
    if voc_val is not None:
        st.session_state["define_voc_raw"] = _to_text(voc_val)

    if src.get("key_issue") is not None:
        st.session_state["define_key_issue"] = _to_text(src.get("key_issue"))


    # --- Gate evidence ---
    if src.get("charter_confirmed") is not None:
        st.session_state["define_charter_confirmed"] = bool(src.get("charter_confirmed"))
    if src.get("documentation_agreed") is not None:
        st.session_state["define_documentation_agreed"] = bool(src.get("documentation_agreed"))
    if src.get("similar_project_exists") is not None:
        st.session_state["define_similar_project_exists"] = bool(src.get("similar_project_exists"))
    if src.get("similar_project_note") is not None:
        st.session_state["define_similar_project_note"] = src.get("similar_project_note")
    if src.get("parallel_projects_risk") is not None:
        st.session_state["define_parallel_projects_risk"] = src.get("parallel_projects_risk")
    if src.get("benefit_estimate") is not None:
        st.session_state["define_benefit_estimate"] = src.get("benefit_estimate")

    # Prefill voc_table — key harus match dengan data_editor key
    voc_table_val = src.get("voc_table") or []
    if voc_table_val and project_id:
        _voc_key = f"define_voc_table_{project_id}"
        st.session_state[_voc_key] = voc_table_val
    # feedback box should reset on load
    st.session_state["define_feedback_text"] = ""



def _get_current_result():
    """Return ('draft'|'final'|'none', wrapper_result_or_none)."""
    if st.session_state.get("define_draft"):
        return "draft", st.session_state["define_draft"]
    if st.session_state.get("define_final"):
        return "final", st.session_state["define_final"]
    return "none", None

# ============================================
# Sidebar
# ============================================

render_sidebar_header()

st.sidebar.divider()
active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")

    # Phase status ringkas
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

    phase_row = " | ".join([f"{icons[i]} {labels[i]}" for i in range(5)])
    st.sidebar.caption(phase_row)
    st.sidebar.divider()

    # Governance ringkas untuk fase ini
    appr = memory.get_phase_approval_status(active_pid, "define")
    rev  = (appr.get("reviewer") or {}).get("action") or "pending"
    rev_icon  = "✅" if rev  == "approve" else ("❌" if rev  == "reject" else "⏳")
    # Champion hanya mandatory di Control — Define Gate cukup Reviewer
    if "champion" in appr.get("required_approvers", []):
        chmp = (appr.get("champion") or {}).get("action") or "pending"
        chmp_icon = "✅" if chmp == "approve" else ("❌" if chmp == "reject" else "⏳")
        st.sidebar.markdown(
            f"**Define Gate:**  \n"
            f"{rev_icon} Reviewer  |  {chmp_icon} Champion"
        )
    else:
        st.sidebar.markdown(
            f"**Define Gate:**  \n"
            f"{rev_icon} Reviewer"
        )
    if appr.get("can_advance"):
        st.sidebar.success("🚀 Gate OPEN")
    else:
        st.sidebar.info("🔒 Gate LOCKED")
else:
    st.sidebar.warning("Belum ada project aktif.")
    st.sidebar.caption("Kembali ke **Main** untuk memilih project.")
st.sidebar.divider()
# ==========================
# GATE: empty page until project chosen
# ==========================
active_pid = st.session_state.get("active_project_id")

if not active_pid:
    st.title("📋 DEFINE Phase")
    st.info("Belum ada project aktif. Kembali ke halaman **Main** untuk memilih atau membuat project.")
    st.stop()

# Auto-load hanya jika project dipilih dari MAIN (bukan startup kosong)
loaded_pid = st.session_state.get("_loaded_pid")
if active_pid and loaded_pid != active_pid:
    st.session_state["_loaded_pid"] = active_pid
    st.session_state["define_draft"] = None
    st.session_state["define_final"] = None

    # Prefill nama project dari MAIN kalau baru dibuat
    project_name_from_main = st.session_state.get("active_project_name", "")
    if project_name_from_main:
        st.session_state["define_project_name_input"] = project_name_from_main

    draft_state_disk = memory.load_define_draft(active_pid)
    final_state_disk = memory.load_define_final(active_pid)

    if draft_state_disk:
        st.session_state["define_draft"] = {
            "status": "draft",
            "define_state": draft_state_disk,
            "summary": draft_state_disk.get("summary_md", ""),
            "message": "Auto-loaded DRAFT from disk",
        }
        # Prefill form dari draft
        prefill_define_form_from_state(active_pid, draft_state_disk)

    if final_state_disk:
        st.session_state["define_final"] = {
            "status": "finalized",
            "define_state": final_state_disk,
            "critique": [],
            "summary": final_state_disk.get("summary_md", ""),
            "message": "Auto-loaded FINAL from disk",
        }
        # Prefill form dari final (read-only di UI)
        if not draft_state_disk:
            prefill_define_form_from_state(active_pid, final_state_disk)

# Fallback: pastikan draft/final Define tetap ter-load dari disk walau _loaded_pid
# sudah diset page lain (key _loaded_pid dipakai bersama Measure) → mencegah tab kosong.
if active_pid and not st.session_state.get("define_draft") and not st.session_state.get("define_final"):
    _dd_disk = memory.load_define_draft(active_pid)
    _df_disk = memory.load_define_final(active_pid)
    if _dd_disk:
        st.session_state["define_draft"] = {
            "status": "draft", "define_state": _dd_disk,
            "summary": _dd_disk.get("summary_md", ""), "message": "Loaded DRAFT from disk",
        }
        prefill_define_form_from_state(active_pid, _dd_disk)
    if _df_disk:
        st.session_state["define_final"] = {
            "status": "finalized", "define_state": _df_disk, "critique": [],
            "summary": _df_disk.get("summary_md", ""), "message": "Loaded FINAL from disk",
        }
        if not _dd_disk:
            prefill_define_form_from_state(active_pid, _df_disk)


# ============================================
# Main
# ============================================
# ---------- HEADER ----------


logo_path = "app/assets/logo3.jpg"
h_title, h_logo = st.columns([5, 1])

with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)

with h_title:
    st.markdown("# DMAIC Copilot — DEFINE Phase")
    _proj_meta = memory.load_project_meta(active_pid) if active_pid else {}
    _proj_path = _proj_meta.get("path", "standard")
    _path_label = "🟢 Quick Improvement Project" if _proj_path == "quick" else "🔵 Standard DMAIC Project"
    st.caption(f"Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC Methodology &nbsp;|&nbsp; {_path_label}")
    
st.divider()

# ---------- Project Info Bar ----------
_def_title_hdr = (
    st.session_state.get("define_project_name_input", "")
    or (memory.load_define_final(active_pid) or {}).get("inputs", {}).get("project_name", "")
    or (memory.load_define_draft(active_pid) or {}).get("inputs", {}).get("project_name", "")
    or st.session_state.get("active_project_name", "")
)
if _def_title_hdr:
    st.markdown(f"<p style='font-size:1.05rem;font-weight:700;color:#0f172a;margin:0 0 4px 0;'>{_def_title_hdr}</p>", unsafe_allow_html=True)
h1, h2 = st.columns([5, 1])
with h1:
    st.caption(f"**Active Project:** `{active_pid}`")
with h2:
    view_badge = "FINAL" if memory.load_define_final(active_pid) else "DRAFT"
    st.markdown(
        f"<div style='text-align:right'><span style='padding:6px 12px;border-radius:20px;"
        f"background:#0f5132;color:#d1e7dd;font-weight:600;'>"
        f"{view_badge}</span></div>",
        unsafe_allow_html=True,
    )

#

# Auto-load FINAL if nothing in session
view, res = _get_current_result()
if view == "none" and active_pid:
    final_state = memory.load_define_final(active_pid)
    if final_state:
        st.session_state["define_final"] = {
            "status": "finalized",
            "define_state": final_state,
            "critique": [],
            "summary": reporting.build_define_summary_md(final_state),
            "message": "Auto-loaded FINAL from disk",
        }
        view, res = _get_current_result()


# ---------- Single-column layout with tabs ----------
import pandas as pd
import copy

view, res = _get_current_result()

_TAB_LABELS = ["✏️ Input", "📄 Report", "Charter", "Benefit"]
tab_input, tab1, tab3, tab4 = st.tabs(_TAB_LABELS)
render_tab_quicknav(_TAB_LABELS)

with tab_input:

# ---------- Revision Actions ----------
    final_on_disk  = memory.load_define_final(active_pid) if active_pid else None
    draft_on_disk  = memory.load_define_draft(active_pid) if active_pid else None
    _has_final     = bool(final_on_disk)
    _has_draft     = bool(draft_on_disk)

    _appr_status   = memory.get_phase_approval_status(active_pid, "define")
    _is_rejected   = (
        ((_appr_status.get("reviewer") or {}).get("action") == "reject") or
        ((_appr_status.get("champion") or {}).get("action") == "reject")
    )

    # Revise mode: hanya aktif jika leader eksplisit klik "Revise Final"
    _revise_mode_key = f"define_revise_mode_{active_pid}"
    st.session_state.setdefault(_revise_mode_key, False)
    _in_revise_mode = st.session_state.get(_revise_mode_key, False)

    # Tampilkan tombol sesuai kondisi
    revise_clicked        = False
    discard_draft_clicked = False

    if _has_final and _is_rejected and not _has_draft and not _in_revise_mode:
        # Rejected, belum ada draft baru, belum dalam revise mode → tampilkan Revise Final
        revise_clicked = st.button(
            "✏️ Revise Final",
            key="define_revise_btn",
            help="Buka form untuk merevisi dan membuat draft baru."
        )
    elif _has_draft:
        # Ada draft → tampilkan Discard Draft saja
        discard_draft_clicked = st.button(
            "🧹 Discard Draft",
            key="define_discard_draft_btn",
            help="Hapus draft ini dan kembali ke final terakhir."
        )

    if revise_clicked:
        st.session_state[_revise_mode_key] = True
        if final_on_disk:
            prefill_define_form_from_state(active_pid, final_on_disk)
        st.session_state["define_draft"] = None
        st.rerun()

    if discard_draft_clicked:
        memory.delete_define_draft(active_pid)
        st.session_state["define_draft"] = None
        st.session_state["define_final"] = None
        st.session_state[_revise_mode_key] = False
        # Reload final dari disk ke session
        if final_on_disk:
            st.session_state["define_final"] = {
                "status": "finalized",
                "define_state": final_on_disk,
                "critique": [],
                "summary": final_on_disk.get("summary_md", ""),
                "message": "Reverted to final.",
            }
            prefill_define_form_from_state(active_pid, final_on_disk)
        st.rerun()

    # =========================
    # DEFINE INPUT FORM (LEFT)
    # =========================
    with st.form(key="define_input_form", clear_on_submit=False):
        # Lock check di dalam form
        _final_exists = bool(memory.load_define_final(active_pid))
        _appr = memory.get_phase_approval_status(active_pid, "define")
        _rejected = (
            ((_appr.get("reviewer") or {}).get("action") == "reject") or
            ((_appr.get("champion") or {}).get("action") == "reject")
        )
        is_locked = _has_final and not _in_revise_mode and not _has_draft

        st.subheader("Project Inputs")

        project_name = st.text_input(
            "Project Name",
            key="define_project_name_input",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            industry = st.selectbox(
                "Industry",
                options=["Manufacturing", "FMCG", "Services", "Oil & Gas", "Other"],
                key="define_industry_select",
            )
        with col2:
            process_area = st.selectbox(
                "Process Area",
                options=["Supply Chain", "Production", "Quality", "Finance", "Sales", "Other"],
                key="define_process_area_select",
            )
        with col3:
            pain_theme = st.selectbox(
                "Primary Pain Theme",
                options=["Cost", "Quality", "Delivery", "Safety", "Other"],
                key="define_pain_theme_select",
            )

        problem_text = st.text_area(
            "Problem Statement (raw)",
            key="define_problem_text",
            height=140,
        )

        goal_text = st.text_area(
            "Goal (raw, optional)",
            key="define_goal_text",
            height=100,
        )

        _form_path = memory.load_project_meta(active_pid).get("path", "standard") if active_pid else "standard"
        if _form_path == "standard":
            st.markdown("**VOC / VOB → CTQ / CTB (Tool I)**")
            st.caption("Isi tabel di bawah. VOC = suara pelanggan, VOB = suara bisnis.")

        # Locked hanya kalau final DAN sudah approved penuh
        _final_exists = bool(memory.load_define_final(active_pid))
        _appr = memory.get_phase_approval_status(active_pid, "define")
        _rejected = (
            ((_appr.get("reviewer") or {}).get("action") == "reject") or
            ((_appr.get("champion") or {}).get("action") == "reject")
        )
        
        # Default rows
        # Reset VOC table setiap kali project berubah
        _voc_key = f"define_voc_table_{active_pid}"
        if _voc_key not in st.session_state:
            _saved = memory.load_define_final(active_pid) or memory.load_define_draft(active_pid) or {}
            _saved_inputs = _saved.get("inputs") or {}
            _saved_voc_data = _saved_inputs.get("voc_table") or []
            st.session_state[_voc_key] = _saved_voc_data if _saved_voc_data else [
                {"Type": "VOC", "Voice": "", "Key Issue": "", "CTQ/CTB": ""},
                {"Type": "VOB", "Voice": "", "Key Issue": "", "CTQ/CTB": ""},
            ]

        if _form_path == "standard":
            voc_table = st.data_editor(
                st.session_state[_voc_key],
                num_rows="dynamic",
                use_container_width=True,
                key=f"define_voc_editor_{active_pid}",
                column_config={
                    "Type": st.column_config.SelectboxColumn(
                        "Type", options=["VOC", "VOB"], required=True
                    ),
                    "Voice": st.column_config.TextColumn("Voice (kutipan langsung)", width="large"),
                    "Key Issue": st.column_config.TextColumn("Key Issue / True Need"),
                    "CTQ/CTB": st.column_config.TextColumn("CTQ / CTB"),
                },
                disabled=is_locked,
            )
            key_issue = st.text_input(
                "Key Issue / Context (optional)",
                key="define_key_issue",
            )
        else:
            voc_table = []
            key_issue = ""     

        st.divider()

        if _form_path == "standard":
            with st.expander("✅ DEFINE Gate Evidence (Stage Gate)", expanded=False):
                # Status upload charter & benefit (read from session, tidak bisa diubah di sini)
                _cu = st.session_state.get("define_charter_upload_data") or {}
                _bu = st.session_state.get("define_benefit_upload_data") or {}
                col_cu, col_bu = st.columns(2)
                with col_cu:
                    if _cu.get("available"):
                        st.success("✅ Charter: uploaded & extracted")
                    elif st.session_state.get(f"define_no_upload_reason_{active_pid}", "").strip():
                        st.warning("⚠️ Charter: not uploaded (justification provided)")
                    else:
                        st.error("❌ Charter: not uploaded")
                with col_bu:
                    if _bu.get("available"):
                        kpi_ok = any(
                            str(r.get("baseline","")).strip() and str(r.get("target","")).strip()
                            for r in (_bu.get("kpi_table") or []) if isinstance(r, dict)
                        )
                        if kpi_ok:
                            st.success("✅ Benefit estimate: uploaded & KPI complete")
                        else:
                            st.warning("⚠️ Benefit estimate: uploaded but KPI incomplete")
                    elif st.session_state.get(f"define_no_upload_reason_{active_pid}", "").strip():
                        st.warning("⚠️ Benefit: not uploaded (justification provided)")
                    else:
                        st.error("❌ Benefit estimate: not uploaded")

                st.divider()

                colg1, colg2 = st.columns(2)
                with colg1:
                    documentation_agreed = st.checkbox(
                        "Dokumentasi berikut telah dipresentasikan dan disetujui",
                        key="define_documentation_agreed",
                    )
                    similar_project_exists = st.checkbox(
                        "Ada proyek sebelumnya dengan topik yang sama",
                        key="define_similar_project_exists",
                    )
                    similar_project_note = st.text_area(
                        "Catatan proyek sebelumnya (opsional)",
                        key="define_similar_project_note",
                        height=80,
                    )
                with colg2:
                    parallel_projects_risk = st.text_area(
                        "Apakah ada proyek lain yang bisa mempengaruhi proyek ini?",
                        key="define_parallel_projects_risk",
                        height=100,
                    )
        else:
            # Quick Path: stage gate evidence formal tidak wajib
            documentation_agreed = False
            similar_project_exists = False
            similar_project_note = ""
            parallel_projects_risk = ""
            st.caption(
                "⚡ **Quick Path** — Stage Gate Evidence formal (charter, dokumentasi, "
                "proyek paralel) tidak wajib. Fokus pada Problem & Goal statement di atas."
            )

        st.divider()

        user_feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            key="define_feedback_text",
            height=90,
        )

        submitted = st.form_submit_button(
            "⚡ Generate / Update Draft" if not is_locked else "🔒 Terkunci — Tidak Bisa Generate Draft",
            type="primary",
            disabled=is_locked,
        )
# ── Charter & Benefit Upload (standard path, OUTSIDE form) ──
with tab_input:
    if _form_path == "standard" and not is_locked:
        st.divider()
        st.markdown("#### 📋 Project Charter & Benefit Estimate")
        st.caption(
            "Download template, isi secara offline, lalu upload kembali. "
            "Agent akan membaca dan memvalidasi isi file."
        )

        # Download template
        _tpl_bytes = generate_charter_benefit_template()
        st.download_button(
            label="⬇️ Download Template (Charter + Benefit)",
            data=_tpl_bytes,
            file_name="DMAIC_Charter_Benefit_Template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="define_template_download_btn",
        )

        # Upload
        _uploaded_charter_file = st.file_uploader(
            "Upload completed template (.xlsx)",
            type=["xlsx"],
            key=f"define_charter_upload_{active_pid}",
        )

        if _uploaded_charter_file is not None:
            _charter_data  = extract_charter_from_upload(_uploaded_charter_file)
            _uploaded_charter_file.seek(0)
            _benefit_data  = extract_benefit_from_upload(_uploaded_charter_file)
            st.session_state["define_charter_upload_data"] = _charter_data
            st.session_state["define_benefit_upload_data"] = _benefit_data

            if _charter_data.get("available"):
                st.success("✅ Charter extracted successfully.")
                with st.expander("Preview Charter", expanded=False):
                    st.write(f"**Project Leader:** {_charter_data.get('project_leader','-')}")
                    st.write(f"**Champion:** {_charter_data.get('champion','-')}")
                    st.write(f"**Process Owner:** {_charter_data.get('process_owner','-')}")
                    st.write(f"**Controller:** {_charter_data.get('controller','-')}")
                    import pandas as pd
                    team = _charter_data.get("team_members") or []
                    if team:
                        st.dataframe(pd.DataFrame(team), use_container_width=True, hide_index=True)
                    timeline = _charter_data.get("timeline") or {}
                    if timeline:
                        tl_rows = [{"Milestone": k.replace("_"," ").title(), "Target": v}
                                   for k, v in timeline.items()]
                        st.dataframe(pd.DataFrame(tl_rows), use_container_width=True, hide_index=True)
            else:
                st.warning(f"⚠️ Charter: {_charter_data.get('error','Gagal dibaca.')}")

            if _benefit_data.get("available"):
                st.success("✅ Benefit estimate extracted successfully.")
                with st.expander("Preview Benefit Estimate", expanded=False):
                    kpi_rows = _benefit_data.get("kpi_table") or []
                    if kpi_rows:
                        st.dataframe(pd.DataFrame(kpi_rows), use_container_width=True, hide_index=True)
                    else:
                        st.caption("KPI table kosong.")
            else:
                st.warning(f"⚠️ Benefit: {_benefit_data.get('error','Gagal dibaca.')}")

        # Justifikasi jika tidak upload
        _has_upload = st.session_state.get("define_charter_upload_data") is not None
        if not _has_upload:
            st.caption("Belum upload? Berikan justifikasi:")
            _charter_reason = st.text_area(
                "Alasan belum upload Charter & Benefit template",
                key=f"define_no_upload_reason_{active_pid}",
                height=70,
                placeholder="Contoh: Data masih dalam proses validasi dengan Finance, akan diupload sebelum approval.",
            )
        else:
            _charter_reason = ""

# OUTSIDE the form, but still can be anywhere (I put it after cols)
if submitted:
    project_id = st.session_state.get("active_project_id", "").strip()
    _charter_upload = st.session_state.get("define_charter_upload_data") or {}
    _benefit_upload = st.session_state.get("define_benefit_upload_data") or {}
    _no_upload_reason = st.session_state.get(f"define_no_upload_reason_{active_pid}", "")
    user_inputs = {
        "project_name": project_name,
        "industry": industry,
        "process_area": process_area,
        "pain_theme": pain_theme,
        "problem_text": problem_text,
        "goal_text": goal_text,
        "voc_list": [
            f"{r.get('Type','')}: {r.get('Voice','')} → {r.get('Key Issue','')} → {r.get('CTQ/CTB','')}"
             for r in (voc_table or [])
            if r.get('Voice','').strip()
        ],
        "voc_table": voc_table if voc_table else [],
        "key_issue": key_issue,
        "documentation_agreed": documentation_agreed,
        "similar_project_exists": similar_project_exists,
        "similar_project_note": similar_project_note,
        "parallel_projects_risk": parallel_projects_risk,
        "charter_data_uploaded":     _charter_upload,
        "benefit_data_uploaded":     _benefit_upload,
        "charter_no_upload_reason":  _no_upload_reason,
        "benefit_no_upload_reason":  _no_upload_reason,
    }

    user_feedback = {"text": user_feedback_text} if user_feedback_text.strip() else {}

    result = run_define_agent(
        project_id=project_id,
        user_inputs=user_inputs,
        user_feedback=user_feedback,
    )
    st.session_state["define_draft"] = result
    # Refresh agar tab Report ikut draft terbaru di run yang sama (tanpa generate ulang/panggil agent lagi)
    view, res = _get_current_result()

# --- Revision Guidance (agent coaching) — ditempatkan di tab Input, bukan global ---
with tab_input:
    _current_result = st.session_state.get("define_draft") or st.session_state.get("define_final")
    latest = (_current_result or {}).get("define_state") if isinstance(_current_result, dict) else None

    if isinstance(latest, dict):
        coaching = (latest.get("coaching_md") or latest.get("insight_md") or "").strip()
        gate = latest.get("gate_result") or {}
        status = gate.get("status", "-")

        st.markdown("### 🧭 Agent Coaching")
        st.markdown(f"**Gate Status:** `{status}`")

        if coaching:
            st.markdown(coaching)
        else:
            st.info("Belum ada coaching dari agent. Jalankan Generate / Update Draft.")
        st.divider()

# ---------- Report View ----------

if not (res and res.get("define_state")):
    with tab1:
        st.info("Belum ada draft atau final. Generate draft atau muat final yang sudah ada.")
else:
    define_state = res["define_state"]
    inputs = define_state.get("inputs") or {}
    outs = define_state.get("outputs") or {}

    # ---------- SAFE VALUES ----------
    ctq_list = outs.get("ctq_list") or []
    if isinstance(ctq_list, list):
        ctq_text = ", ".join([str(x) for x in ctq_list if str(x).strip()])
    elif isinstance(ctq_list, str):
        ctq_text = ctq_list.strip()
    else:
        ctq_text = str(ctq_list).strip()
    if not ctq_text:
        ctq_text = "-"

    # ---------- TAB 1 : SHORT REPORT ----------
    with tab1:

        # ----- Scope bullets -----
        scope = outs.get("project_scope") or {}
        scope_md = ""
        if isinstance(scope, dict):
            if scope.get("in_scope"):
                scope_md += "**In Scope:**\n" + "\n".join([f"- {x}" for x in scope.get("in_scope", [])]) + "\n"
            if scope.get("out_of_scope"):
                scope_md += "\n**Out of Scope:**\n" + "\n".join([f"- {x}" for x in scope.get("out_of_scope", [])])
        elif isinstance(scope, list):
            scope_md = "\n".join([f"- {x}" for x in scope])
        else:
            scope_md = str(scope) if scope else "-"

        # ----- CTQ bullets -----
        ctq_md = ""
        ctqs = outs.get("ctq_list") or []
        for c in ctqs:
            if isinstance(c, dict):
                ctq_md += f"- **{c.get('name','')}** ({c.get('metric','')} {c.get('unit','')}): {c.get('description','')}\n"
            else:
                ctq_md += f"- {c}\n"
        if not ctq_md.strip():
            ctq_md = "- -"

        _report_path = define_state.get("project_path", "standard")

        if _report_path == "quick":
            # ── A3-style report untuk Quick Improvement ──
            xc = outs.get("x_categories") or {}
            xc_md = "\n".join([f"- **{k}**: {v}" for k, v in xc.items()]) if xc else "-"

            short_md = (
                f"## 🟢 Quick Improvement — {define_state.get('project_id','-')}\n"
                f"- **Project Name**: {inputs.get('project_name','-')}\n"
                f"- **Industry / Area / Theme**: {inputs.get('industry','-')} / "
                f"{inputs.get('process_area','-')} / {inputs.get('pain_theme','-')}\n\n"
                f"### 1. Business Case\n"
                f"{outs.get('business_case') or '-'}\n\n"
                f"### 2. Problem\n"
                f"{(outs.get('problem_statement') or inputs.get('problem_text') or '-').strip()}\n\n"
                f"### 3. Goal\n"
                f"{(outs.get('goal_statement') or inputs.get('goal_text') or '-').strip()}\n\n"
                f"### 4. Probable Root Causes\n"
                f"{xc_md}\n"
            )

        else:
            # ── Standard DMAIC report ──
            short_md = (
                f"## DEFINE — {define_state.get('project_id','-')}\n"
                f"- **Project Name**: {inputs.get('project_name','-')}\n"
                f"- **Industry / Area / Theme**: {inputs.get('industry','-')} / "
                f"{inputs.get('process_area','-')} / {inputs.get('pain_theme','-')}\n\n"
                f"### Business Case\n"
                f"{outs.get('business_case') or '-'}\n\n"
                f"### Problem\n"
                f"{(outs.get('problem_statement') or inputs.get('problem_text') or '-').strip()}\n\n"
                f"### Goal\n"
                f"{(outs.get('goal_statement') or inputs.get('goal_text') or '-').strip()}\n\n"
                f"### Scope\n"
                f"{scope_md}\n\n"
                f"### CTQ\n"
                f"{ctq_md}\n\n"
                f"### Y Variable\n"
                f"- {outs.get('y_variable') or '-'}\n\n"

            )

        st.markdown(short_md)

        _coaching = (define_state.get("coaching_md") or define_state.get("insight_md") or "").strip()
        if _coaching:
            st.divider()
            st.markdown("### 🧭 Agent Coaching")
            st.markdown(_coaching)

    with tab3:
        if _proj_path == "quick":
            st.info("📋 Tab **Charter** tidak berlaku untuk **Quick path** — charter formal di-skip. Lihat tab Report & Benefit.")
        # Project Charter — dari upload atau dari LLM output
        _charter_upload_data = st.session_state.get("define_charter_upload_data") or {}
        charter = _charter_upload_data if _charter_upload_data.get("available") else (outs.get("project_charter") or {})
        if _proj_path != "quick" and charter and charter.get("available") is not False:
            st.markdown("### 📋 Project Charter")
            _res_rows = []
            for role_label, key in [
                ("Project Leader", "project_leader"),
                ("Project Champion", "champion"),
                ("Mentoring BB", "mentor_bb"),
                ("Process Owner", "process_owner"),
                ("Controller", "controller"),
            ]:
                val = charter.get(key, "TBD")
                if val and val not in ("TBD", "nan", ""):
                    _res_rows.append({"Role": role_label, "Name": val})
            if _res_rows:
                st.dataframe(pd.DataFrame(_res_rows), use_container_width=True, hide_index=True)

            team = charter.get("team_members") or []
            if team and isinstance(team[0], dict):
                st.markdown("**Team Members:**")
                st.dataframe(pd.DataFrame(team), use_container_width=True, hide_index=True)

            timeline = charter.get("timeline") or {}
            if timeline:
                st.markdown("**Project Timeline:**")
                tl_rows = [{"Milestone": k.replace("_", " ").title(), "Target": v}
                           for k, v in timeline.items() if v]
                if tl_rows:
                    st.dataframe(pd.DataFrame(tl_rows), use_container_width=True, hide_index=True)

        # ── Ending Dates per Fase (Planned vs Actual) — rekap dari semua fase ──
        _actual_tl  = memory.load_project_timeline(active_pid) or {}
        _planned_tl = (charter.get("timeline") if isinstance(charter, dict) else {}) or {}
        _phase_rows = [
            ("Define",  "define_end",  "define"),
            ("Measure", "measure_end", "measure"),
            ("Analyze", "analyze_end", "analyze"),
            ("Improve", "improve_end", "improve"),
            ("Control", "control_end", "control"),
        ]
        st.markdown("#### 📅 Ending Dates per Fase (Planned vs Actual)")
        st.caption("Tanggal aktual diisi tiap fase sebelum Finalize, lalu direkap di sini.")
        st.dataframe(pd.DataFrame([{
            "Fase":        _lbl,
            "Planned End": _planned_tl.get(_pk) or "-",
            "Actual End":  _actual_tl.get(_ak) or "— (belum selesai)",
        } for _lbl, _pk, _ak in _phase_rows]), use_container_width=True, hide_index=True)

    with tab4:            # Benefit Estimate dari upload
        _benefit_upload_data = st.session_state.get("define_benefit_upload_data") or {}
        _benefit_source = _benefit_upload_data if _benefit_upload_data.get("available") else (outs.get("benefit_estimate") or {})
        if _benefit_source and isinstance(_benefit_source, dict):
            st.markdown("### 💰 Benefit Estimate")
            if _benefit_source.get("benefit_potentials"):
                st.markdown(f"**Benefit Potentials:** {_benefit_source['benefit_potentials']}")
            if _benefit_source.get("calculation_method"):
                st.markdown(f"**Calculation Method:** {_benefit_source['calculation_method']}")
            kpi_rows = _benefit_source.get("kpi_table") or []
            if kpi_rows:
                st.markdown("**KPI Table:**")
                st.dataframe(pd.DataFrame(kpi_rows), use_container_width=True, hide_index=True)
            assumptions = _benefit_source.get("assumptions") or []
            if assumptions:
                st.markdown("**Assumptions:**")
                for a in assumptions:
                    st.markdown(f"- {a}")
           
with tab_input:
    # ---------- Finalize + Downloads ----------
    st.subheader("Finalize & Downloads")

    final_state = memory.load_define_final(active_pid) if active_pid else None
    draft_state = memory.load_define_draft(active_pid) if active_pid else None

    c1, c2, c3 = st.columns([1, 1, 1])

    with c1:
        # Finalize: only if there is a draft AND no final yet (efficient, no double-finalize)
        if draft_state:
            # Standard: wajib isi tanggal aktual selesai fase sebelum finalize.
            _has_end = render_phase_end_date(active_pid, "define", disabled=False)
            _no_actual_end = (_proj_path != "quick") and not _has_end
            if st.button("✅ Finalize (Lock Final)", key="define_finalize_from_downloads_btn", disabled=_no_actual_end):
                final_result = finalize_define_agent(
                    project_id=active_pid,
                    define_state=draft_state,
                    user_feedback=None,
                )
                # update session view immediately
                st.session_state["define_final"] = final_result
                st.session_state["define_draft"] = None
                st.session_state[_revise_mode_key] = False
                memory.save_project_leader_name(active_pid, auth.get_current_display_name())
                st.success("Finalized. Final tersimpan dan terkunci.")
                st.rerun()
            if _no_actual_end:
                st.caption("⚠️ Finalize diblokir: isi **Tanggal aktual selesai fase** dulu.")
        elif final_state:
            st.button("✅ Finalized", key="define_finalized_badge_btn", disabled=True)
        else:
            st.button("✅ Finalize (Lock Final)", key="define_finalize_from_downloads_btn_disabled", disabled=True)

    with c2:
        # Generate dari final kalau ada, kalau tidak dari draft
        _word_source = final_state or draft_state
        if _word_source:
            _word_label = "🛠️ Generate Word (Final)" if final_state else "🛠️ Generate Word (Draft)"
            if st.button(_word_label, key="define_generate_final_word_btn"):
                import copy
                _export_state = copy.deepcopy(_word_source)

                # Inject charter/benefit dari session state jika ada
                _cu = st.session_state.get("define_charter_upload_data") or {}
                _bu = st.session_state.get("define_benefit_upload_data") or {}
                if _cu.get("available") or _bu.get("available"):
                    _export_inputs = _export_state.get("inputs") or {}
                    if _cu.get("available"):
                        _export_inputs["charter_data_uploaded"] = _cu
                    if _bu.get("available"):
                        _export_inputs["benefit_data_uploaded"] = _bu
                    _export_state["inputs"] = _export_inputs

                _docx_fname = f"Project_{active_pid}_DEF_{'Final' if final_state else 'Draft'}.docx"
                out_path = reporting.export_define_to_word(
                    _export_state,
                    path=_docx_fname
                )
                st.session_state["define_last_docx_path"] = out_path
                st.success("Word file generated.")
        else:
            st.button("🛠️ Generate Word", key="define_generate_final_word_btn_disabled", disabled=True)

    with c3:
        _docx_fname_def = f"Project_{active_pid}_DEF_{'Final' if final_state else 'Draft'}.docx"
        docx_path = st.session_state.get("define_last_docx_path") or _docx_fname_def
        _label = "⬇️ Download Final Word Report" if final_state else "⬇️ Download Draft Word Report"
        try:
            with open(docx_path, "rb") as f:
                doc_bytes = f.read()
            st.download_button(
                label=_label,
                data=doc_bytes,
                file_name=_docx_fname_def,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="define_download_word_btn",
            )
        except FileNotFoundError:
            st.button(_label, key="define_download_word_btn_disabled", disabled=True)

    # ============================================
    # GOVERNANCE PANEL
    # ============================================
    st.divider()
    st.subheader("🏛️ Governance — Stage Gate Approval")

    final_exists = memory.load_define_final(active_pid) if active_pid else None
    approval_status = memory.get_phase_approval_status(active_pid, "define") if active_pid else {}

    reviewer_action  = (approval_status.get("reviewer")  or {}).get("action")
    champion_action  = (approval_status.get("champion")   or {}).get("action")
    can_advance      = approval_status.get("can_advance", False)
    # Champion hanya mandatory di Control — di Define tidak ditampilkan sbagai gate
    _champ_required  = "champion" in approval_status.get("required_approvers", [])

    if not final_exists:
        st.info("Finalize DEFINE dulu sebelum bisa di-approve.")
    else:
        # Status display — hanya setelah finalized
        if _champ_required:
            col_r, col_c, col_adv = st.columns(3)
        else:
            col_r, col_adv = st.columns(2)

        with col_r:
            if reviewer_action == "approve":
                st.success("✅ Reviewer: Approved")
            elif reviewer_action == "reject":
                st.error("❌ Reviewer: Rejected")
            else:
                st.warning("⏳ Reviewer: Pending")

        if _champ_required:
            with col_c:
                if champion_action == "approve":
                    st.success("✅ Champion: Approved")
                elif champion_action == "reject":
                    st.error("❌ Champion: Rejected")
                else:
                    st.warning("⏳ Champion: Pending")

        with col_adv:
            if can_advance:
                st.success("🚀 Gate: OPEN — Lanjut ke Measure")
            else:
                st.info("🔒 Gate: LOCKED")

        # Approval actions
        role = auth.get_current_role()
        if role == "champion" and not _champ_required:
            st.info("ℹ️ Champion tidak diperlukan di DEFINE. Champion hanya melakukan approval di fase **Control**.")
        elif role in approval_status.get("required_approvers", []):
            _role_action = (approval_status.get(role) or {}).get("action")
            st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
            if _role_action == "approve":
                st.success("✅ Kamu sudah **Approve** fase ini.")
            elif _role_action == "reject":
                st.error("❌ Kamu sudah **Reject** fase ini.")
            note = st.text_area(
                "Catatan (opsional)",
                key=f"gov_note_{role}_define",
                height=80,
            )
            col_app, col_rej = st.columns(2)
            with col_app:
                if st.button("✅ Approve", key=f"gov_approve_{role}_define",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid, "define", role, "approve", note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid, "define", f"approved_by_{role}")
                    st.success("Approval disimpan.")
                    st.rerun()
            with col_rej:
                if st.button("❌ Reject", key=f"gov_reject_{role}_define",
                             disabled=(_role_action is not None)):
                    memory.save_approval(active_pid, "define", role, "reject", note, display_name=auth.get_current_display_name())
                    audit.log_phase_event(active_pid, "define", f"rejected_by_{role}")
                    st.warning("Rejection disimpan.")
                    st.rerun()
        elif role == "project_leader":
            if can_advance:
                st.success("✅ Approval lengkap. Kamu bisa lanjut ke fase Measure.")
            else:
                st.info("Menunggu approval dari Reviewer.")


    st.divider()

    # ---------- Audit ----------
    st.subheader("🧾 Audit Log — DEFINE Phase")
    events = audit.get_recent_events(project_id=active_pid, phase="Define", limit=30)
    if events:
        st.dataframe(events, width="stretch")
    else:
        st.write("Belum ada audit event.")

