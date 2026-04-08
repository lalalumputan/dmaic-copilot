import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import json
import copy
import streamlit as st
from app.utils import auth
auth.require_login()
import pandas as pd

from app.agents.measure_agent import run_measure_agent, finalize_measure_agent, self_critique
from app.utils import memory, reporting, audit


# ============================================
# Config
# ============================================
st.set_page_config(page_title="MEASURE", layout="wide")
st.markdown("""
<style>

/* =======================
   GLOBAL BACKGROUND
   ======================= */
.stApp {
    background-color: #f4f8fb;
    color: #0f172a;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
}

/* =======================
   SIDEBAR
   ======================= */
section[data-testid="stSidebar"] {
    background-color: #ffffff;
    border-right: 1px solid #e2e8f0;
}

/* =======================
   PHASE HEADER (Current Phase)
   ======================= */
.phase-header {
    background: linear-gradient(90deg, #e0f2fe, #f0f9ff);
    border: 1px solid #bae6fd;
    color: #075985;
    padding: 14px 20px;
    border-radius: 12px;
    font-size: 20px;
    font-weight: 600;
    text-align: center;
    margin-bottom: 20px;
}

/* =======================
   CARD CONTAINER
   ======================= */
.card {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 18px 20px;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
    margin-bottom: 18px;
}

/* =======================
   SECTION TITLES
   ======================= */
h1, h2, h3 {
    color: #0f172a;
}
h2 {
    font-size: 22px;
    margin-bottom: 12px;
}
h3 {
    font-size: 18px;
    margin-bottom: 8px;
}

/* =======================
   BUTTONS
   ======================= */
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

/* =======================
   INPUTS
   ======================= */
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div,
div[data-baseweb="select"] > div {
    background-color: #ffffff !important;
    border: 1px solid #cbd5e1 !important;
    border-radius: 10px !important;
    color: #0f172a !important;
}

/* =======================
   TABS
   ======================= */
button[data-baseweb="tab"] {
    background-color: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    padding: 8px 12px !important;
    color: #0f172a !important;
}

button[data-baseweb="tab"][aria-selected="true"] {
    background-color: #e0f2fe !important;
    border-color: #38bdf8 !important;
    color: #075985 !important;
    font-weight: 600;
}

/* =======================
   DATA TABLE
   ======================= */
div[data-testid="stDataFrame"],
div[data-testid="stDataEditor"] {
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    background-color: #ffffff;
}

/* =======================
   DIVIDER BETWEEN COLUMNS
   ======================= */
.v-divider {
    width: 1px;
    min-height: 1200px;
    margin: 0 auto;
    background: linear-gradient(
        to bottom,
        rgba(15,23,42,0.00),
        rgba(15,23,42,0.20),
        rgba(15,23,42,0.00)
    );
}

</style>
""", unsafe_allow_html=True)



# ============================================
# Session defaults
# ============================================
st.session_state.setdefault("active_project_id", None)

# wrappers like DEFINE page
st.session_state.setdefault("measure_draft", None)  # wrapper result
st.session_state.setdefault("measure_final", None)  # wrapper result

# cache upstream define final for current project
st.session_state.setdefault("define_final_state", None)

# form helper defaults
st.session_state.setdefault("measure_feedback_text", "")

# dataset cache in-session (so re-run doesn't drop it)
st.session_state.setdefault("measure_baseline_df", None)
st.session_state.setdefault("measure_uploaded_name", None)


# ============================================
# Helper: reporting md compatibility
# ============================================
def _build_measure_md(state: dict) -> str:
    """Compatibility bridge for measure summary markdown."""
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


def _safe_get(d: dict | None, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _render_define_context_readonly(define_final: dict):
    st.subheader("DEFINE Context (read-only)")

    inputs = (define_final or {}).get("inputs") or {}
    outs = (define_final or {}).get("outputs") or {}

    project_name = inputs.get("project_name") or define_final.get("project_name") or "-"
    problem = outs.get("problem_statement") or outs.get("business_problem") or inputs.get("problem_text") or "-"
    goal = outs.get("goal_statement") or outs.get("project_goal") or inputs.get("goal_text") or "-"
    cand_y = outs.get("y_variable") or inputs.get("y_variable") or "-"

    st.markdown(f"- **Project name:** {project_name}")
    st.markdown(f"- **Problem:** {problem}")
    st.markdown(f"- **Goal:** {goal}")
    st.markdown(f"- **Candidate Y (from DEFINE):** {cand_y}")

    with st.expander("DEFINE detail (JSON)", expanded=False):
        st.json(define_final)


def _seed_measure_inputs_from_define(project_id: str, define_final: dict) -> dict:
    inputs = (define_final or {}).get("inputs") or {}
    meta = (define_final or {}).get("meta") or {}
    outs = (define_final or {}).get("outputs") or {}

    return {
        "project_id": project_id,
        "project_name": inputs.get("project_name", project_id) or project_id,
        "industry": meta.get("industry") or inputs.get("industry") or "Manufacturing",
        "pain_theme": meta.get("pain_theme") or inputs.get("pain_theme") or "Quality",
        "process_area": meta.get("process_area") or inputs.get("process_area") or "Production",
        "y_variable": outs.get("y_variable") or inputs.get("y_variable") or "",
    }

def _seed_operational_definitions_from_define(define_final: dict) -> list[dict]:
    inputs = (define_final or {}).get("inputs") or {}
    outs = (define_final or {}).get("outputs") or {}

    y = outs.get("y_variable") or inputs.get("y_variable") or ""

    # Generic, cross-industry starter set (user can edit)
    rows = [
        {
            "metric_name": y or "Primary Y",
            "what_measured": "Primary outcome metric (from DEFINE)",
            "instrument_system": "ERP / MES / LIMS / Source system",
            "method_formula": "Define formula here (numerator/denominator clear)",
            "unit": "%",
            "frequency": "Monthly",
            "decision_criteria": "Target/spec from DEFINE (if known); otherwise set with owner",
        },
        {
            "metric_name": "Supporting Metric 1",
            "what_measured": "A key driver/companion measure that affects Y",
            "instrument_system": "Source system / log",
            "method_formula": "Define formula here",
            "unit": "Count",
            "frequency": "Weekly",
            "decision_criteria": "Lower/Upper is better; set threshold later",
        },
    ]
    return rows


def _render_measure_outputs_detail(measure_state: dict):
    outs = (measure_state or {}).get("outputs") or {}

    # Deterministic blocks (from patched agent)
    prof = outs.get("dataset_profile") or {}
    dq = outs.get("data_quality_results") or {}
    perf = outs.get("process_performance_summary") or {}

    if prof.get("available"):
        with st.expander("Dataset profile (deterministic)", expanded=False):
            st.json(prof)

    if dq.get("available"):
        with st.expander("Data quality results (deterministic)", expanded=False):
            st.json(dq)

    if isinstance(perf, dict) and (perf.get("summary") or perf.get("available") is True):
        with st.expander("Process performance (descriptive)", expanded=False):
            st.json(perf)

    with st.expander("MEASURE outputs (JSON)", expanded=False):
        st.json(outs)

def _render_gate_card(measure_state: dict):
    gate = (measure_state or {}).get("gate") or {}
    status = gate.get("status", "UNKNOWN")
    policy = gate.get("policy", "-")

    if status == "PASS":
        st.success(f"Gate: PASS  •  Policy: {policy}")
    elif status == "WEAK_PASS":
        st.warning(f"Gate: WEAK_PASS  •  Policy: {policy}")
    elif status == "FAIL":
        st.error(f"Gate: FAIL  •  Policy: {policy}")
    else:
        st.info(f"Gate: {status}  •  Policy: {policy}")

    missing = gate.get("missing_evidence") or []
    risks = gate.get("risk_if_proceed") or []
    actions = gate.get("next_actions") or []

    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Missing evidence**")
        if missing:
            for m in missing[:8]:
                st.write(f"- {m}")
        else:
            st.write("—")

    with cols[1]:
        st.markdown("**Risk if proceed**")
        if risks:
            for r in risks[:8]:
                st.write(f"- {r}")
        else:
            st.write("—")

    with cols[2]:
        st.markdown("**Next smallest actions**")
        if actions:
            for a in actions[:8]:
                st.write(f"- {a}")
        else:
            st.write("—")

    return status



# ============================================
# Sidebar: Project picker (FINAL DEFINE only)
# ============================================
st.sidebar.title("Navigation")
st.sidebar.caption("Agentic AI DMAIC Copilot")

st.sidebar.divider()
active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")
else:
    st.sidebar.warning("Belum ada project aktif.")
    st.sidebar.caption("Kembali ke halaman **Main** untuk memilih project.")

# ============================================
# Gate: empty page until project chosen
# ============================================
active_pid = st.session_state.get("active_project_id")
if not active_pid:
    st.title("📏 MEASURE Phase")
    st.info("Belum ada project aktif. Kembali ke halaman **Main** untuk memilih project.")
    st.stop()

# Load define_final_state hanya jika sesuai active_pid
if active_pid and st.session_state.get("_loaded_pid") == active_pid:
    if not st.session_state.get("define_final_state"):
        st.session_state["define_final_state"] = memory.load_define_final(active_pid)
else:
    st.session_state["define_final_state"] = None

define_final = st.session_state.get("define_final_state")
if not define_final:
    st.title("MEASURE Phase")
    st.warning("DEFINE FINAL tidak bisa diload untuk project ini.")
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
    st.caption("### Agentic AI for Continuous Improvement Projects using Lean Six Sigma DMAIC Methodology")


st.divider()


# ============================================
# Project Info Bar
# ============================================
h1, h2, h3 = st.columns([2, 1, 1])
with h1:
    st.caption(f"#### Active Project: `{active_pid}`")

with h2:
    # badge based on stored FINAL (if your memory has it); safe getattr to avoid crash
    load_final_fn = getattr(memory, "load_measure_final", None)
    final_on_disk = load_final_fn(active_pid) if callable(load_final_fn) else None
    view_badge = "FINAL" if final_on_disk else "DRAFT"
    st.markdown(
        f"<span style='padding:6px 12px;border-radius:20px;"
        f"background:#0f5132;color:#d1e7dd;font-weight:600;'>"
        f"{view_badge}</span>",
        unsafe_allow_html=True,
    )

with h3:
    st.markdown(
        "<span style='padding:6px 12px;border-radius:20px;"
        "background:#1f2937;color:#e5e7eb;font-weight:500;'>"
        "MODE: OPEN</span>",
        unsafe_allow_html=True,
    )


# ============================================
# Main layout: two columns like DEFINE
# ============================================
left_col, divider_col, right_col = st.columns([1, 0.03, 1], gap="large")

with divider_col:
    st.markdown('<div class="v-divider"></div>', unsafe_allow_html=True)

with left_col:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("MEASURE Inputs")
    # form / inputs di sini
    st.markdown('</div>', unsafe_allow_html=True)

    # --- Revision actions (optional, same pattern as DEFINE) ---
    st.subheader("Revision Actions")

    discard_draft_clicked = st.button("🧹 Discard MEASURE Draft", key="measure_discard_draft_btn")
    st.caption("ℹ️ Discard deletes the current MEASURE draft (FINAL remains unchanged if exists).")

    if discard_draft_clicked:
        delete_draft_fn = getattr(memory, "delete_measure_draft", None)
        if callable(delete_draft_fn):
            delete_draft_fn(active_pid)
        st.session_state["measure_draft"] = None
        st.rerun()

    st.divider()

    # --- Input Form (MAIN PANEL, not sidebar) ---
    st.subheader("MEASURE Inputs")
    seed = _seed_measure_inputs_from_define(active_pid, define_final)

    # keep stable keys like DEFINE form
    st.session_state.setdefault("measure_industry_input", seed["industry"])
    st.session_state.setdefault("measure_process_area_input", seed["process_area"])
    st.session_state.setdefault("measure_pain_theme_input", seed["pain_theme"])
    st.session_state.setdefault("measure_y_text_input", seed["y_variable"])

    challenge = st.toggle("Challenge mode (agent will stress-test assumptions)", value=False)
    with st.form("measure_input_form"):
        colA, colB, colC = st.columns(3)
        industry = colA.selectbox(
            "Industry",
            ["Manufacturing", "Chemical", "FMCG", "Service", "Other"],
            key="measure_industry_input",
        )

        process_area = colB.selectbox(
            "Process Area",
            ["Production", "Supply Chain", "Quality", "Lab", "Service"],
            key="measure_process_area_input",
        )

        pain_theme = colC.selectbox(
            "Primary Pain Theme",
            ["Quality", "Cost", "Delivery", "Safety", "Compliance"],
            key="measure_pain_theme_input",
        )
        submitted = st.form_submit_button("Apply MEASURE Inputs")
        y_variable = st.text_input("Candidate/Confirmed Y", key="measure_y_text_input")

        y_op_def = st.text_area(
            "Operational definition notes",
            height=110,
            key="measure_y_opdef_notes_input",
            placeholder="Unit, calculation, spec/target, sampling frame, inclusion/exclusion",
        )
        
        st.subheader("CTQ → Measurement Mapping")

        # Get measure_state from session
        _, res = _get_current_result()
        measure_state = res["measure_state"] if (res and res.get("measure_state")) else None

        pm = (measure_state or {}).get("outputs", {}).get("primary_measurement_output", {})
        ctq_used = (measure_state or {}).get("outputs", {}).get("ctq_final_used", {})

        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)

        if ctq_used.get("available"):
            st.markdown("**CTQ used from DEFINE (read-only):**")
        for i, c in enumerate(ctq_used.get("ctq_items", []), start=1):
            st.write(f"{i}. {c.get('ctq', c)}")
        else:
            st.warning("No CTQ detected from DEFINE FINAL. MEASURE will be blocked.")
        st.markdown("---")

        st.markdown("**Proposed primary measurement output:**")
        st.write(f"➡️ **{pm.get('metric_name', '—')}**")
        st.caption(f"Basis: {pm.get('basis', '—')}")

    # Optional override
        override_metric = st.text_input(
            "Override primary metric (optional, Project Leader decision)",
            value="",
            key="chosen_primary_metric_input"
        )

        confirm_primary = st.checkbox(
            "I confirm this primary measurement output for MEASURE",
            value=False,
            key="confirm_primary_metric_input"
        )

        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("#### Operational Definition (table)")
        st.caption("Add/edit rows. Agent will validate and improve. If left empty, agent will propose.")

        # default table only once per project/session
        st.session_state.setdefault(
            "measure_operational_defs",
            _seed_operational_definitions_from_define(define_final)
        )

        st.caption("Define operational definition for the **primary measurement output** (row 1). Secondary measures are optional.")

        op_defs = st.data_editor(
            st.session_state["measure_operational_defs"],
            num_rows="dynamic",
            width='stretch',
            key="measure_opdefs_editor",
        )

        st.markdown("#### Measurement data (optional)")
        uploaded = st.file_uploader(
            "Upload measurement dataset (CSV/XLSX)",
            type=["csv", "xlsx", "xls"],
            key="measure_upload",
        )

        baseline_df = st.session_state.get("measure_baseline_df")
        if uploaded is not None:
            try:
                if uploaded.name.lower().endswith(".csv"):
                    baseline_df = pd.read_csv(uploaded)
                else:
                    baseline_df = pd.read_excel(uploaded)

                st.session_state["measure_baseline_df"] = baseline_df
                st.session_state["measure_uploaded_name"] = uploaded.name

                st.caption(f"Loaded: {uploaded.name} | rows={baseline_df.shape[0]:,} cols={baseline_df.shape[1]}")
            except Exception as e:
                st.error(f"Failed to read file: {e}")
                baseline_df = None
                st.session_state["measure_baseline_df"] = None
                st.session_state["measure_uploaded_name"] = None

        cols = list(baseline_df.columns.astype(str)) if isinstance(baseline_df, pd.DataFrame) else []
        y_col = st.selectbox("Y column in dataset (for baseline metrics)", options=[""] + cols, index=0, key="measure_y_col")
        date_col = st.selectbox("Date/time column (for timeliness)", options=[""] + cols, index=0, key="measure_date_col")
        seg_cols = st.multiselect(
            "Segment columns (optional)",
            options=cols,
            default=[c for c in ["site", "product", "shift"] if c in cols],
            key="measure_seg_cols",
        )

        feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            height=80,
            key="measure_feedback_text",
            placeholder="If you disagree with the draft, write what to change.",
        )

        submitted = st.form_submit_button("🔍 Generate / Update Draft")

    if submitted:
        user_inputs = {
            "project_name": seed.get("project_name") or active_pid,
            "industry": industry,
            "process_area": process_area,
            "pain_theme": pain_theme,
            "y_variable": y_variable,
            "y_operational_definition_notes": y_op_def,
            "operational_definitions": op_defs or [],
            "scaffolding_level": "challenge" if challenge else "guided",
            # dataset mapping for deterministic measure calculations
            "y_column_name": y_col or "",
            "date_column": (date_col or None),
            "segment_columns": seg_cols or [],
            "confirm_primary_metric": st.session_state.get("confirm_primary_metric_input", False),
            "chosen_primary_metric": st.session_state.get("chosen_primary_metric_input", "").strip(),
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
        st.rerun()

    # --- Finalize (near form, like DEFINE) ---
    if st.session_state.get("measure_draft") and st.session_state["measure_draft"].get("measure_state"):
        st.subheader("Finalize")
        gate_status = None
try:
    gate_status = (st.session_state.get("measure_draft") or {}).get("measure_state", {}).get("gate", {}).get("status")
except Exception:
    gate_status = None

finalize_disabled = (gate_status == "FAIL")

if st.button(
    "✅ Finalize Draft (Lock Final)",
    key="measure_finalize_btn",
    disabled=finalize_disabled
):
    draft_state = st.session_state["measure_draft"]["measure_state"]
    final_result = finalize_measure_agent(
        project_id=active_pid,
        measure_state=draft_state,
        user_feedback=None,
    )

    # If agent blocks finalize (gate FAIL), show reason and keep draft
    if final_result.get("status") == "blocked":
        st.error(final_result.get("reason", "Finalize blocked by gate."))
        st.info("Fix missing evidence in Gate Card, then regenerate draft.")
    else:
        st.session_state["measure_final"] = final_result
        st.session_state["measure_draft"] = None
        st.rerun()

if finalize_disabled:
    st.caption("Finalize disabled: Gate is FAIL. Resolve missing evidence first.")

st.markdown('</div>', unsafe_allow_html=True)
st.divider()


with right_col:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    # tabs + report
    st.markdown('</div>', unsafe_allow_html=True)

    st.subheader("Report View")

    view, res = _get_current_result()
    measure_state = res["measure_state"] if (res and res.get("measure_state")) else None

        # Gate card (decision-gated)
    gate_status = _render_gate_card(measure_state)


    tab_define, tab_report, tab_detail, tab_json = st.tabs(
        ["🧩 DEFINE Context", "📄 Report (Short)", "📊 Detail (DQ & Performance)", "🧾 JSON (trimmed)"]
    )

    with tab_define:
        _render_define_context_readonly(define_final)

    with tab_report:
        if not measure_state:
            st.info("No MEASURE draft or final yet. Generate a draft to start.")
        else:
            smd = _build_measure_md(measure_state)
            if smd.strip():
                st.markdown(smd)
            else:
                st.markdown(f"## MEASURE — {measure_state.get('project_id','-')}")
                st.markdown(f"- **Status**: {measure_state.get('status','-')}")
                yconf = _safe_get(measure_state, "outputs", "y_variable_confirmed") or "-"
                st.markdown(f"- **Y (confirmed)**: {yconf}")

            ins = measure_state.get("insight_md") if measure_state else ""
            if isinstance(ins, str) and ins.strip():
                with st.expander("Agent insight (draft-time)", expanded=False):
                    st.markdown(ins)

        st.subheader("MEASURE Conclusion")

        concl = (measure_state or {}).get("outputs", {}).get("measure_conclusion", {})

        if concl:
            st.markdown('<div class="card">', unsafe_allow_html=True)

            st.write(f"**Problem validated:** `{concl.get('problem_validated', 'unknown')}`")
            st.write(f"**Confidence:** `{concl.get('confidence', 'low')}`")
            st.markdown("**Basis:**")
            for b in concl.get("basis", []):
                st.write(f"- {b}")

            st.markdown("**What would change this conclusion:**")
            for w in concl.get("what_would_change_mind", []):
                st.write(f"- {w}")

            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.caption("Conclusion will be available after baseline & gate evaluation.")

    with tab_detail:

        st.subheader("Measurement System Analysis (MSA-lite)")

        msa = (measure_state or {}).get("outputs", {}).get("measurement_system_analysis", {})

        if msa:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.write(f"Approach: **{msa.get('approach', '—')}**")

        checks = msa.get("checks", [])
        if checks:
            for c in checks:
                st.write(f"- {c}")
        else:
            st.caption("No MSA checks evaluated yet.")
        ev = msa.get("evidence_needed", [])
        if ev:
            st.markdown("**Evidence needed to pass MSA:**")
        for e in ev:
            st.write(f"- {e}")
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.caption("MSA will be evaluated once data & definitions are available.")

        if not measure_state:
            st.info("No MEASURE draft or final yet.")
        else:
            _render_measure_outputs_detail(measure_state)

        st.subheader("Recommended Graphical Displays (MEASURE)")

        graphs = (measure_state or {}).get("outputs", {}).get("graphical_displays", [])

        if graphs:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            for g in graphs:
                if g.get("chart_type") == "note":
                    st.info(g.get("message"))
            else:
                st.write(
                f"• **{g.get('chart_type')}** | "
                f"x: {g.get('x')} | y: {g.get('y')} | "
                f"segment: {g.get('segment')}  \n"
                f"Purpose: {g.get('purpose')}"
            )

            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.caption("Graphical displays will be proposed after data is provided.")


    with tab_json:
        if not measure_state:
            st.info("No MEASURE draft or final yet.")
        else:
            s = copy.deepcopy(measure_state or {})
            for k in ["perception_trace", "decision_trace"]:
                s.pop(k, None)
            st.json(s)

st.markdown('</div>', unsafe_allow_html=True)
st.divider()

# ============================================
# Self-critique button (optional but useful)
# ============================================
st.subheader("Review (Self-critique)")

c1, c2 = st.columns([1, 1])
with c1:
    if st.button("🧠 Run Self-critique (patch outputs)", key="measure_self_critique_btn"):
        view, res = _get_current_result()
        if not (res and res.get("measure_state")):
            st.warning("Generate a draft first.")
        else:
            improved = self_critique(res["measure_state"])
            # keep as draft wrapper
            st.session_state["measure_draft"] = {
                "status": "draft",
                "measure_state": improved["improved"],
                "critique": improved.get("issues", []),
                "summary": improved["improved"].get("summary_md", ""),
                "message": "Self-critique applied",
            }
            st.session_state["measure_final"] = None
            st.rerun()

with c2:
    view, res = _get_current_result()
    issues = (res or {}).get("critique") or []
    if issues:
        with st.expander("Critique issues", expanded=False):
            st.json(issues)

st.divider()

# ============================================
# Audit (Measure)
# ============================================
st.subheader("🧾 Audit Log — MEASURE Phase")
events = audit.get_recent_events(project_id=active_pid, phase="Measure", limit=30)
if events:
    st.dataframe(events, width="stretch")
else:
    st.write("No audit events found.")
