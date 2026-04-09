import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import streamlit as st
import pandas as pd
import copy
from app.utils import auth, memory, audit
from app.agents.improve_agent import run_improve_agent, finalize_improve_agent
from app.utils.ui_helpers import render_sidebar_header

st.set_page_config(page_title="IMPROVE", layout="wide")
auth.require_login()
auth.render_user_badge()

# ── Sidebar ──────────────────────────────────────────────
render_sidebar_header()
st.sidebar.divider()

active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")
else:
    st.sidebar.warning("Belum ada project aktif.")
    st.sidebar.caption("Kembali ke **Main** untuk memilih project.")

# ── Gate: harus ada active project ───────────────────────
if not active_pid:
    st.title("💡 IMPROVE Phase")
    st.info("Belum ada project aktif. Kembali ke halaman **Main**.")
    st.stop()

# ── Gate: Analyze harus sudah FINAL ──────────────────────
analyze_final = memory.load_analyze_final(active_pid)
if not analyze_final:
    st.title("💡 IMPROVE Phase")
    st.warning("ANALYZE belum di-finalize untuk project ini. Selesaikan fase Analyze terlebih dahulu.")
    st.stop()

# ── Load upstream ─────────────────────────────────────────
measure_final = memory.load_measure_final(active_pid)
define_final  = memory.load_define_final(active_pid)

# ── Auto-load improve state ───────────────────────────────
loaded_pid = st.session_state.get("_loaded_pid")
if active_pid and loaded_pid == active_pid:
    if not st.session_state.get("improve_draft") and not st.session_state.get("improve_final"):
        draft = memory.load_improve_draft(active_pid)
        if draft:
            st.session_state["improve_draft"] = {
                "status": "draft",
                "improve_state": draft,
                "summary": draft.get("summary_md", ""),
                "message": "Auto-loaded from disk",
            }
        final = memory.load_improve_final(active_pid)
        if final:
            st.session_state["improve_final"] = {
                "status": "finalized",
                "improve_state": final,
                "summary": final.get("summary_md", ""),
                "message": "Auto-loaded from disk",
            }

def _get_current():
    if st.session_state.get("improve_draft"):
        return "draft", st.session_state["improve_draft"]
    if st.session_state.get("improve_final"):
        return "final", st.session_state["improve_final"]
    return "none", None

# ── Header ────────────────────────────────────────────────
logo_path = "app/assets/logo2.jpg"
h_title, h_logo = st.columns([5, 1])
with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)
with h_title:
    st.markdown("# DMAIC Copilot — IMPROVE Phase")
    st.caption("### Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC")
st.divider()

# ── Project Info Bar ──────────────────────────────────────
c1, c2 = st.columns([3, 1])
with c1:
    st.caption(f"Active Project: `{active_pid}`")
with c2:
    is_locked = bool(memory.load_improve_final(active_pid))
    st.markdown(
        f"<span style='padding:6px 12px;border-radius:20px;"
        f"background:{'#0f5132' if is_locked else '#1f2937'};"
        f"color:{'#d1e7dd' if is_locked else '#e5e7eb'};font-weight:600'>"
        f"{'FINAL' if is_locked else 'DRAFT'}</span>",
        unsafe_allow_html=True,
    )
st.divider()

# ── Upstream Context ──────────────────────────────────────
with st.expander("📋 Upstream Context (Define + Measure + Analyze)", expanded=False):
    def_outs  = (define_final  or {}).get("outputs") or {}
    def_ins   = (define_final  or {}).get("inputs")  or {}
    mea_outs  = (measure_final or {}).get("outputs") or {}
    ana_outs  = (analyze_final or {}).get("outputs") or {}

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**DEFINE:**")
        st.markdown(f"- Problem: {def_outs.get('problem_statement') or def_ins.get('problem_text') or '-'}")
        st.markdown(f"- Goal: {def_outs.get('goal_statement') or def_ins.get('goal_text') or '-'}")
    with col2:
        st.markdown("**MEASURE:**")
        st.markdown(f"- Y: {mea_outs.get('y_variable_confirmed') or '-'}")
        perf = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}
        st.markdown(f"- Baseline mean: {perf.get('mean') or '-'}")
    with col3:
        st.markdown("**ANALYZE:**")
        rca_sum = ana_outs.get("root_cause_summary") or "-"
        st.markdown(f"- Root cause: {rca_sum[:120]}{'...' if len(rca_sum) > 120 else ''}")
        vital = (ana_outs.get("pareto_analysis") or {}).get("vital_few") or []
        if vital:
            st.markdown(f"- Vital few: {', '.join(vital[:3])}")

# ── Two-column layout ─────────────────────────────────────
left_col, right_col = st.columns([1, 1], gap="large")

with left_col:
    st.subheader("Revision Actions")
    if st.button("🧹 Discard Draft", key="improve_discard_btn"):
        memory.delete_improve_draft(active_pid)
        st.session_state["improve_draft"] = None
        st.rerun()
    st.caption("ℹ️ Discard menghapus draft. Final tetap tersimpan.")
    st.divider()

    st.subheader("IMPROVE Inputs")
    is_locked = bool(memory.load_improve_final(active_pid))

    def_ins = (define_final or {}).get("inputs") or {}
    st.session_state.setdefault("improve_industry",     def_ins.get("industry", "Manufacturing"))
    st.session_state.setdefault("improve_process_area", def_ins.get("process_area", "Production"))
    st.session_state.setdefault("improve_pain_theme",   def_ins.get("pain_theme", "Quality"))

    with st.form("improve_input_form"):
        col1, col2, col3 = st.columns(3)
        industry = col1.selectbox(
            "Industry",
            ["Manufacturing", "FMCG", "Services", "Oil & Gas", "Other"],
            key="improve_industry",
            disabled=is_locked,
        )
        process_area = col2.selectbox(
            "Process Area",
            ["Production", "Quality", "Supply Chain", "Finance", "Other"],
            key="improve_process_area",
            disabled=is_locked,
        )
        pain_theme = col3.selectbox(
            "Pain Theme",
            ["Quality", "Cost", "Delivery", "Safety", "Other"],
            key="improve_pain_theme",
            disabled=is_locked,
        )

        project_mode = st.radio(
            "Improvement Mode",
            ["standard", "quick"],
            horizontal=True,
            key="improve_mode",
            help="Standard: full analysis. Quick: concise but complete.",
            disabled=is_locked,
        )

        solution_ideas = st.text_area(
            "Solution ideas (optional — agent will also generate)",
            height=100,
            key="improve_solution_ideas",
            placeholder="Tuliskan ide solusi yang sudah kamu pikirkan...",
            disabled=is_locked,
        )

        constraints = st.text_area(
            "Constraints (budget, time, resources)",
            height=80,
            key="improve_constraints",
            placeholder="Contoh: budget max 50jt, harus selesai dalam 3 bulan, tidak bisa stop produksi",
            disabled=is_locked,
        )

        doe_factors = st.text_input(
            "DOE Factors (optional) — format: Factor1=L1,L2; Factor2=L1,L2",
            key="improve_doe_factors",
            placeholder="Suhu=60,70; Kecepatan=100,120",
            disabled=is_locked,
        )

        feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            height=80,
            key="improve_feedback",
            disabled=is_locked,
        )

        submitted = st.form_submit_button(
            "⚡ Generate / Update Draft" if not is_locked else "🔒 Locked",
            type="primary",
            disabled=is_locked,
        )

    if submitted:
        user_inputs = {
            "industry":       industry,
            "process_area":   process_area,
            "pain_theme":     pain_theme,
            "project_mode":   project_mode,
            "solution_ideas": solution_ideas,
            "constraints":    constraints,
            "doe_factors":    doe_factors,
        }
        user_feedback = {"text": feedback_text} if feedback_text.strip() else None

        with st.spinner("Running IMPROVE agent..."):
            result = run_improve_agent(
                project_id=active_pid,
                user_inputs=user_inputs,
                define_final=define_final,
                measure_final=measure_final,
                analyze_final=analyze_final,
                user_feedback=user_feedback,
            )
        st.session_state["improve_draft"] = result
        st.session_state["improve_final"] = None
        st.rerun()

    # ── Coaching ──────────────────────────────────────────
    view, res = _get_current()
    if res and res.get("improve_state"):
        state    = res["improve_state"]
        gate     = state.get("gate_result") or {}
        coaching = (state.get("coaching_md") or "").strip()

        st.markdown("### 🧭 Agent Guidance")
        st.markdown(f"**Gate Status:** `{gate.get('status', '-')}`")
        if coaching:
            st.markdown(coaching)
        st.divider()

        if st.session_state.get("improve_draft"):
            st.subheader("Finalize")
            gate_status = gate.get("status")
            if st.button(
                "✅ Finalize Draft (Lock Final)",
                key="improve_finalize_btn",
                disabled=(gate_status == "FAILED"),
            ):
                draft_state  = st.session_state["improve_draft"]["improve_state"]
                final_result = finalize_improve_agent(
                    project_id=active_pid,
                    improve_state=draft_state,
                )
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason"))
                else:
                    st.session_state["improve_final"] = final_result
                    st.session_state["improve_draft"] = None
                    st.rerun()
            if gate_status == "FAILED":
                st.caption("Finalize disabled: gate FAILED. Fix gaps dulu.")

with right_col:
    st.subheader("Report View")
    view, res = _get_current()

    if not res or not res.get("improve_state"):
        st.info("Belum ada draft. Generate draft untuk memulai.")
    else:
        state   = res["improve_state"]
        outputs = state.get("outputs") or {}

        tab1, tab2, tab3, tab4 = st.tabs([
            "📄 Summary",
            "💡 Solutions",
            "📋 Implementation",
            "🧾 JSON",
        ])

        with tab1:
            smd = state.get("summary_md") or ""
            if smd.strip():
                st.markdown(smd)

            should_be = outputs.get("should_be_process") or ""
            if should_be:
                st.markdown("### Should-Be Process")
                st.markdown(should_be)

            cb = outputs.get("cost_benefit_analysis") or {}
            if cb:
                st.markdown("### Cost-Benefit Analysis")
                for k, v in cb.items():
                    if isinstance(v, list):
                        st.markdown(f"- **{k}:** {', '.join(v)}")
                    else:
                        st.markdown(f"- **{k}:** {v}")

        with tab2:
            # Potential solutions
            solutions = outputs.get("potential_solutions") or []
            if solutions:
                st.markdown("### Potential Solutions")
                st.dataframe(pd.DataFrame(solutions), use_container_width=True)

            # Effort-benefit matrix
            eb = outputs.get("effort_benefit_matrix") or []
            if eb:
                st.markdown("### Effort-Benefit Matrix")
                st.dataframe(pd.DataFrame(eb), use_container_width=True)

            # Cause-solution map
            csm = outputs.get("cause_solution_map") or []
            if csm:
                st.markdown("### Cause-Solution Map")
                st.dataframe(pd.DataFrame(csm), use_container_width=True)

            # Selected solution
            selected = outputs.get("selected_solution") or {}
            if selected:
                st.markdown("### ✅ Selected Solution")
                st.success(f"**{selected.get('solution', '-')}**")
                st.markdown(f"- Rationale: {selected.get('rationale', '-')}")
                st.markdown(f"- Expected impact: {selected.get('expected_impact_on_y', '-')}")

            # DOE matrix
            doe = outputs.get("doe_matrix") or {}
            if doe and isinstance(doe, dict) and doe.get("doe_matrix"):
                st.markdown("### DOE Matrix")
                st.dataframe(pd.DataFrame(doe["doe_matrix"]), use_container_width=True)

        with tab3:
            # Implementation plan
            impl = outputs.get("implementation_plan") or []
            if impl:
                st.markdown("### Implementation Plan")
                st.dataframe(pd.DataFrame(impl), use_container_width=True)

            # Pilot plan
            pilot = outputs.get("pilot_plan") or {}
            if pilot:
                st.markdown("### Pilot Plan")
                for k, v in pilot.items():
                    if isinstance(v, list):
                        st.markdown(f"- **{k}:** {', '.join(v)}")
                    else:
                        st.markdown(f"- **{k}:** {v}")

            # Risks
            risks = outputs.get("risks_and_assumptions") or []
            if risks:
                st.markdown("### Risks & Assumptions")
                for r in risks:
                    st.markdown(f"- {r}")

        with tab4:
            s = copy.deepcopy(state)
            for k in ["perception_trace", "decision_trace"]:
                s.pop(k, None)
            st.json(s)

# ── Governance Panel ──────────────────────────────────────
st.divider()
st.subheader("🏛️ Governance — Stage Gate Approval")

final_exists  = memory.load_improve_final(active_pid)
appr          = memory.get_phase_approval_status(active_pid, "improve")
rev_action    = (appr.get("reviewer")  or {}).get("action")
champ_action  = (appr.get("champion")  or {}).get("action")
can_advance   = appr.get("can_advance", False)

col_r, col_c, col_adv = st.columns(3)
with col_r:
    if rev_action == "approve":   st.success("✅ Reviewer: Approved")
    elif rev_action == "reject":  st.error("❌ Reviewer: Rejected")
    else:                         st.warning("⏳ Reviewer: Pending")
with col_c:
    if champ_action == "approve":  st.success("✅ Champion: Approved")
    elif champ_action == "reject": st.error("❌ Champion: Rejected")
    else:                          st.warning("⏳ Champion: Pending")
with col_adv:
    if can_advance: st.success("🚀 Gate: OPEN — Lanjut ke Control")
    else:           st.info("🔒 Gate: LOCKED")

if final_exists:
    role = auth.get_current_role()
    if role in ("reviewer", "champion"):
        st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
        note = st.text_area("Catatan (opsional)", key="gov_note_improve")
        col_app, col_rej = st.columns(2)
        with col_app:
            if st.button("✅ Approve", key="gov_approve_improve"):
                memory.save_approval(active_pid, "improve", role, "approve", note)
                audit.log_phase_event(active_pid, "improve", f"approved_by_{role}")
                st.success("Approved.")
                st.rerun()
        with col_rej:
            if st.button("❌ Reject", key="gov_reject_improve"):
                memory.save_approval(active_pid, "improve", role, "reject", note)
                audit.log_phase_event(active_pid, "improve", f"rejected_by_{role}")
                st.warning("Rejected.")
                st.rerun()
    elif role == "project_leader":
        if can_advance:
            st.success("✅ Semua approval lengkap. Lanjut ke fase Control.")
        else:
            st.info("Menunggu approval dari Reviewer dan Champion.")
else:
    st.info("Finalize IMPROVE dulu sebelum bisa di-approve.")

st.divider()
st.subheader("🧾 Audit Log — IMPROVE Phase")
events = audit.get_recent_events(project_id=active_pid, phase="improve", limit=20)
if events:
    st.dataframe(events, use_container_width=True)
else:
    st.write("No audit events found.")