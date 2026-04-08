import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import streamlit as st
import app.agents.define_agent as _da
from app.agents.define_agent import run_define_agent, finalize_define_agent
from app.utils import data_processing as dp, memory, audit, reporting, llm_engine
from app.utils import auth

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

    # Agent Insight (if exists)
        
    show_agent_insight_in_report = False  # single source: keep in Revision Guidance only

    if show_agent_insight_in_report:
        ins = s.get("insight_md") or s.get("coaching_md") or ""
    if isinstance(ins, str) and ins.strip():
        md.append("### Agent Insight")
        md.append(ins.strip())
        md.append("")


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
auth.require_login()
auth.render_user_badge()

# ---------- Session defaults ----------
st.session_state.setdefault("active_project_id", None)
st.session_state.setdefault("mode", "create")  # create/open
st.session_state.setdefault("define_draft", None)  # wrapper result
st.session_state.setdefault("define_final", None)  # wrapper result


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

st.sidebar.title("Navigation")
st.sidebar.caption("Agentic AI DMAIC Copilot")

st.sidebar.divider()
active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")
else:
    st.sidebar.warning("Belum ada project aktif.")
    st.sidebar.caption("Kembali ke halaman **Main** untuk memilih project.")

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


# ============================================
# Main
# ============================================
# ---------- HEADER ----------
import os

logo_path = "app/assets/logo2.jpg"
h_title, h_logo = st.columns([5, 1])

with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)

with h_title:
    st.markdown("# DMAIC Copilot — DEFINE Phase")
    st.caption("### Agentic AI for Continuous Improvement Projects using Lean Six Sigma DMAIC Methodology")
    
st.divider()

# ---------- Project Info Bar ----------
h1, h2, h3 = st.columns([2, 1, 1])
with h1:
    st.caption(f"Active Project: `{active_pid}`")
with h2:
    view_badge = "FINAL" if memory.load_define_final(active_pid) else "DRAFT"
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


# ---------- Two-column responsive layout ----------
left_col, right_col = st.columns([1, 1], gap="large")

with left_col:
    # ---------- Final Controls ----------
    st.subheader("Revision Actions")

    final_on_disk = memory.load_define_final(active_pid) if active_pid else None

    a1, a2, = st.columns([1, 1])
    with a1:
        revise_clicked = st.button("✏️ Revise", key="define_revise_btn")

    with a2:
        discard_draft_clicked = st.button("🧹 Discard", key="define_discard_draft_btn")
# notes (informational only, not an action)
    st.caption("ℹ️ Revise creates a new draft. The finalized version remains unchanged.")
    st.caption("ℹ️ Discard deletes the current draft. Finalized version remains unchanged.") 
        
    if revise_clicked:
        if final_on_disk:
            prefill_define_form_from_state(active_pid, final_on_disk)
            st.session_state["define_draft"] = None
            st.session_state["define_final"] = {
                "status": "finalized",
                "define_state": final_on_disk,
                "critique": [],
                "summary": reporting.build_define_summary_md(final_on_disk),
                "message": "Final loaded (revise mode).",
            }
        else:
            reset_define_form(active_pid)
        st.rerun()

    if discard_draft_clicked:
        memory.delete_define_draft(active_pid)
        st.session_state["define_draft"] = None
        st.rerun()

    
    # =========================
    # DEFINE INPUT FORM (LEFT)
    # =========================
    with st.form(key="define_input_form", clear_on_submit=False):

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

        voc_raw = st.text_area(
            "VOC / VOB (bullet, satu per baris)",
            key="define_voc_raw",
            height=120,
        )

        key_issue = st.text_input(
            "Key Issue / Context (optional)",
            key="define_key_issue",
        )

        st.divider()

        with st.expander("✅ DEFINE Gate Evidence (Stage Gate)", expanded=False):


            colg1, colg2 = st.columns(2)
        with colg1:
            charter_confirmed = st.checkbox(
                "Project charter sudah diset up (confirmed)",
                key="define_charter_confirmed",  
            )
            documentation_agreed = st.checkbox(
                "Following documentation has been presented and agreed",
                key="define_documentation_agreed",   
            )
            similar_project_exists = st.checkbox(
                "Ada proyek sebelumnya dengan topik yang sama",
                key="define_similar_project_exists",
            )
            similar_project_note = st.text_area(
                "Catatan proyek sebelumnya (opsional, jika ada)",
                key="define_similar_project_note",
                height=80,
            )
        with colg2:
            parallel_projects_risk = st.text_area(
                "Apakah ada proyek lain yang bisa mempengaruhi proyek ini? (risiko/overlap)",
                key="define_parallel_projects_risk",
                height=80,
            )
            benefit_estimate = st.text_area(
                "High-level benefit estimate (jenis benefit + order of magnitude)",
                key="define_benefit_estimate",
                height=80,
            )

        st.divider()

        user_feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            key="define_feedback_text",
            height=90,
        )

        is_locked = bool(memory.load_define_final(active_pid))
        submitted = st.form_submit_button(
            "⚡ Generate / Update Draft" if not is_locked else "🔒 Locked — Cannot Generate Draft",
            type="primary",
            disabled=is_locked,
        )


# OUTSIDE the form, but still can be anywhere (I put it after cols)
if submitted:
    project_id = st.session_state.get("active_project_id", "").strip()

    user_inputs = {
        "project_name": project_name,
        "industry": industry,
        "process_area": process_area,
        "pain_theme": pain_theme,
        "problem_text": problem_text,
        "goal_text": goal_text,
        "voc_list": voc_raw,
        "key_issue": key_issue,

        "charter_confirmed": charter_confirmed,
        "documentation_agreed": documentation_agreed,
        "similar_project_exists": similar_project_exists,
        "similar_project_note": similar_project_note,
        "parallel_projects_risk": parallel_projects_risk,
        "benefit_estimate": benefit_estimate,
    }

    user_feedback = {"text": user_feedback_text} if user_feedback_text.strip() else {}

    result = run_define_agent(
        project_id=project_id,
        user_inputs=user_inputs,
        user_feedback=user_feedback,
    )
    st.session_state["define_draft"] = result

# --- Revision Guidance (agent coaching) ---
latest = None
if st.session_state.get("define_draft") and isinstance(st.session_state["define_draft"], dict):
    latest = st.session_state["define_draft"].get("define_state")
elif st.session_state.get("define_final") and isinstance(st.session_state["define_final"], dict):
    latest = st.session_state["define_final"].get("define_state")

if isinstance(latest, dict):
    coaching = (latest.get("coaching_md") or latest.get("insight_md") or "").strip()
    gate = latest.get("gate_result") or {}
    status = gate.get("status", "-")

    st.markdown("### 🧭 Revision Guidance")
    st.markdown(f"**Gate Status:** `{status}`")

    if coaching:
        st.markdown(coaching)
    else:
        st.info("Belum ada coaching dari agent. Jalankan Generate / Update Draft.")
    st.divider() 

    # ---------- Finalize (kept near form) ----------
    if st.session_state.get("define_draft") and st.session_state["define_draft"].get("define_state"):
        st.subheader("Finalize")
        if st.button("✅ Finalize Draft (Lock Final)", key="define_finalize_btn"):
            draft_state = st.session_state["define_draft"]["define_state"]
            final_result = finalize_define_agent(
                project_id=active_pid,
                define_state=draft_state,
                user_feedback=None,
            )
            st.session_state["define_final"] = final_result
            st.session_state["define_draft"] = None
            st.rerun()

with right_col:
    # ---------- Report View ----------

    st.subheader("Report View")

    import pandas as pd
    import copy

    view, res = _get_current_result()
    if not (res and res.get("define_state")):
        st.info("No draft or final yet. Generate a draft or load an existing final.")
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

        # ---------- TABS (DEFINED ONCE) ----------
        tab1, tab2, tab3 = st.tabs(
            ["📄 Report (Short)", "📊 Tables (Detail)", "🧾 JSON (trimmed)"]
        )

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

            short_md = f"""
## DEFINE — {define_state.get('project_id','-')}

- **Project Name**: {inputs.get('project_name','-')}
- **Industry / Area / Theme**: {inputs.get('industry','-')} / {inputs.get('process_area','-')} / {inputs.get('pain_theme','-')}

### Problem
{(outs.get('problem_statement') or inputs.get('problem_text') or '-').strip()}

### Goal
{(outs.get('goal_statement') or inputs.get('goal_text') or '-').strip()}

### Business Case
{outs.get('business_case') or '-'}

### Scope
{scope_md}

### CTQ
{ctq_md}

### Y Variable
- {outs.get('y_variable') or '-'}

### Risk & Type
- **Risk Level**: {outs.get('risk_level') or '-'}
- **Project Type**: {outs.get('project_type') or '-'}

### Agent Insight
{(define_state.get('insight_md') or '-').strip()}
"""
            st.markdown(short_md)

        # ---------- TAB 2 : TABLES ----------
        with tab2:
            _render_tables_from_outputs(outs)

        # ---------- TAB 3 : JSON (TRIMMED) ----------
        with tab3:
            def _safe_display_state(state: dict) -> dict:
                s = copy.deepcopy(state or {})
                for k in ["perception_trace", "decision_trace"]:
                    s.pop(k, None)
                return s

            st.json(_safe_display_state(define_state))

st.divider()

# ---------- Finalize + Downloads ----------
st.subheader("Finalize & Downloads")

final_state = memory.load_define_final(active_pid) if active_pid else None
draft_state = memory.load_define_draft(active_pid) if active_pid else None

c1, c2, c3 = st.columns([1, 1, 1])

with c1:
    # Finalize: only if there is a draft AND no final yet (efficient, no double-finalize)
    if draft_state and not final_state:
        if st.button("✅ Finalize (Lock Final)", key="define_finalize_from_downloads_btn"):
            final_result = finalize_define_agent(
                project_id=active_pid,
                define_state=draft_state,
                user_feedback=None,
            )
            # update session view immediately
            st.session_state["define_final"] = final_result
            st.session_state["define_draft"] = None
            st.success("Finalized. Final is locked and stored.")
            st.rerun()
    elif final_state:
        st.button("✅ Finalized", key="define_finalized_badge_btn", disabled=True)
    else:
        st.button("✅ Finalize (Lock Final)", key="define_finalize_from_downloads_btn_disabled", disabled=True)

with c2:
    # Generate Word ONLY from FINAL (no LLM, no state changes)
    if final_state:
        if st.button("🛠️ Generate/Refresh Final Word File", key="define_generate_final_word_btn"):
            out_path = reporting.export_define_to_word(final_state, path=f"{active_pid}_DEFINE_REPORT.docx")
            st.session_state["define_last_docx_path"] = out_path
            st.success("Word file generated/refreshed.")
    else:
        st.button("🛠️ Generate/Refresh Final Word File", key="define_generate_final_word_btn_disabled", disabled=True)

with c3:
    # Download ONLY if file exists
    docx_path = st.session_state.get("define_last_docx_path") or f"{active_pid}_DEFINE_REPORT.docx"
    if final_state:
        try:
            with open(docx_path, "rb") as f:
                doc_bytes = f.read()
            st.download_button(
                label="⬇️ Download Final Word Report",
                data=doc_bytes,
                file_name=f"{active_pid}_DEFINE_REPORT.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="define_download_final_word_btn",
            )
        except FileNotFoundError:
            st.button("⬇️ Download Final Word Report", key="define_download_final_word_btn_disabled", disabled=True)
    else:
        st.button("⬇️ Download Final Word Report", key="define_download_final_word_btn_disabled2", disabled=True)


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

# Status display
col_r, col_c, col_adv = st.columns(3)

with col_r:
    if reviewer_action == "approve":
        st.success("✅ Reviewer: Approved")
    elif reviewer_action == "reject":
        st.error("❌ Reviewer: Rejected")
    else:
        st.warning("⏳ Reviewer: Pending")

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

# Approval actions (hanya tampil kalau ada final dan role sesuai)
if final_exists:
    role = auth.get_current_role()

    if role in ("reviewer", "champion"):
        st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
        note = st.text_area(
            "Catatan (opsional)",
            key=f"gov_note_{role}_define",
            height=80,
        )
        col_app, col_rej = st.columns(2)
        with col_app:
            if st.button("✅ Approve", key=f"gov_approve_{role}_define"):
                memory.save_approval(active_pid, "define", role, "approve", note)
                audit.log_phase_event(active_pid, "define", f"approved_by_{role}")
                st.success("Approval disimpan.")
                st.rerun()
        with col_rej:
            if st.button("❌ Reject", key=f"gov_reject_{role}_define"):
                memory.save_approval(active_pid, "define", role, "reject", note)
                audit.log_phase_event(active_pid, "define", f"rejected_by_{role}")
                st.warning("Rejection disimpan.")
                st.rerun()

    elif role == "project_leader":
        if can_advance:
            st.success("✅ Semua approval lengkap. Kamu bisa lanjut ke fase Measure.")
        else:
            st.info("Menunggu approval dari Reviewer dan Champion.")
else:
    st.info("Finalize DEFINE dulu sebelum bisa di-approve.")


st.divider()

# ---------- Audit ----------
st.subheader("🧾 Audit Log — DEFINE Phase")
events = audit.get_recent_events(project_id=active_pid, phase="Define", limit=30)
if events:
    st.dataframe(events, width="stretch")
else:
    st.write("No audit events found.")

