import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import streamlit as st
from app.utils import auth, memory, audit, reporting
from app.agents.analyze_agent import run_analyze_agent, finalize_analyze_agent

st.set_page_config(page_title="ANALYZE", layout="wide")
auth.require_login()
auth.render_user_badge()

# ── Sidebar ──────────────────────────────────────────────
st.sidebar.title("🎯 DMAIC Copilot")
st.sidebar.caption("Agentic AI DMAIC Companion")
st.sidebar.divider()

active_pid = st.session_state.get("active_project_id")
if active_pid:
    st.sidebar.success(f"Active: **{active_pid}**")
else:
    st.sidebar.warning("Belum ada project aktif.")
    st.sidebar.caption("Kembali ke **Main** untuk memilih project.")

# ── Gate: harus ada active project ───────────────────────
if not active_pid:
    st.title("🔍 ANALYZE Phase")
    st.info("Belum ada project aktif. Kembali ke halaman **Main** untuk memilih project.")
    st.stop()

# ── Gate: Measure harus sudah FINAL ──────────────────────
measure_final = memory.load_measure_final(active_pid)
if not measure_final:
    st.title("🔍 ANALYZE Phase")
    st.warning("MEASURE belum di-finalize untuk project ini. Selesaikan fase Measure terlebih dahulu.")
    st.stop()

# ── Load upstream context ─────────────────────────────────
define_final = memory.load_define_final(active_pid)

# ── Auto-load analyze state ───────────────────────────────
loaded_pid = st.session_state.get("_loaded_pid")
if active_pid and loaded_pid == active_pid:
    if not st.session_state.get("analyze_draft") and not st.session_state.get("analyze_final"):
        draft = memory.load_analyze_draft(active_pid)
        if draft:
            st.session_state["analyze_draft"] = {
                "status": "draft",
                "analyze_state": draft,
                "summary": draft.get("summary_md", ""),
                "message": "Auto-loaded from disk",
            }
        final = memory.load_analyze_final(active_pid)
        if final:
            st.session_state["analyze_final"] = {
                "status": "finalized",
                "analyze_state": final,
                "summary": final.get("summary_md", ""),
                "message": "Auto-loaded from disk",
            }

def _get_current():
    if st.session_state.get("analyze_draft"):
        return "draft", st.session_state["analyze_draft"]
    if st.session_state.get("analyze_final"):
        return "final", st.session_state["analyze_final"]
    return "none", None

# ── Header ────────────────────────────────────────────────
logo_path = "app/assets/logo2.jpg"
h_title, h_logo = st.columns([5, 1])
with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)
with h_title:
    st.markdown("# DMAIC Copilot — ANALYZE Phase")
    st.caption("### Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC")
st.divider()

# ── Project Info Bar ──────────────────────────────────────
c1, c2 = st.columns([3, 1])
with c1:
    st.caption(f"Active Project: `{active_pid}`")
with c2:
    is_locked = bool(memory.load_analyze_final(active_pid))
    st.markdown(
        f"<span style='padding:6px 12px;border-radius:20px;"
        f"background:{'#0f5132' if is_locked else '#1f2937'};"
        f"color:{'#d1e7dd' if is_locked else '#e5e7eb'};font-weight:600'>"
        f"{'FINAL' if is_locked else 'DRAFT'}</span>",
        unsafe_allow_html=True,
    )

st.divider()

# ── Upstream Context (read-only) ──────────────────────────
with st.expander("📋 Upstream Context (Define + Measure)", expanded=False):
    def_outs = (define_final or {}).get("outputs") or {}
    def_ins  = (define_final or {}).get("inputs") or {}
    mea_outs = (measure_final or {}).get("outputs") or {}

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**From DEFINE:**")
        st.markdown(f"- Problem: {def_outs.get('problem_statement') or def_ins.get('problem_text') or '-'}")
        st.markdown(f"- Goal: {def_outs.get('goal_statement') or def_ins.get('goal_text') or '-'}")
        st.markdown(f"- Y Variable: {def_outs.get('y_variable') or '-'}")
    with col2:
        st.markdown("**From MEASURE:**")
        st.markdown(f"- Y Confirmed: {mea_outs.get('y_variable_confirmed') or '-'}")
        perf = (mea_outs.get('process_performance_summary') or {}).get('summary') or {}
        st.markdown(f"- Baseline Mean: {perf.get('mean') or '-'}")
        st.markdown(f"- Baseline Std: {perf.get('std') or '-'}")
        gate_m = (measure_final or {}).get("gate") or {}
        st.markdown(f"- Measure Gate: {gate_m.get('status') or '-'}")

# ── Two-column layout ─────────────────────────────────────
left_col, right_col = st.columns([1, 1], gap="large")

with left_col:
    st.subheader("Revision Actions")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🧹 Discard Draft", key="analyze_discard_btn"):
            memory.delete_analyze_draft(active_pid)
            st.session_state["analyze_draft"] = None
            st.rerun()
    st.caption("ℹ️ Discard menghapus draft. Final tetap tersimpan.")
    st.divider()

    # ── Input Form ────────────────────────────────────────
    st.subheader("ANALYZE Inputs")

    # Seed dari upstream
    def_ins  = (define_final or {}).get("inputs") or {}
    mea_outs = (measure_final or {}).get("outputs") or {}

    st.session_state.setdefault("analyze_industry",     def_ins.get("industry", "Manufacturing"))
    st.session_state.setdefault("analyze_process_area", def_ins.get("process_area", "Production"))
    st.session_state.setdefault("analyze_pain_theme",   def_ins.get("pain_theme", "Quality"))
    st.session_state.setdefault("analyze_mode",         "standard")

    is_locked = bool(memory.load_analyze_final(active_pid))

    with st.form("analyze_input_form"):
        col1, col2, col3 = st.columns(3)
        industry = col1.selectbox(
            "Industry",
            ["Manufacturing", "FMCG", "Services", "Oil & Gas", "Other"],
            key="analyze_industry",
            disabled=is_locked,
        )
        process_area = col2.selectbox(
            "Process Area",
            ["Production", "Quality", "Supply Chain", "Finance", "Other"],
            key="analyze_process_area",
            disabled=is_locked,
        )
        pain_theme = col3.selectbox(
            "Pain Theme",
            ["Quality", "Cost", "Delivery", "Safety", "Other"],
            key="analyze_pain_theme",
            disabled=is_locked,
        )

        project_mode = st.radio(
            "Analysis Mode",
            ["standard", "quick"],
            horizontal=True,
            key="analyze_mode",
            help="Standard: full analysis. Quick: concise but complete.",
            disabled=is_locked,
        )

        observations = st.text_area(
            "Field observations / additional context (optional)",
            height=100,
            key="analyze_observations",
            placeholder="Apa yang kamu lihat di lapangan? Ada pola tertentu? Data anomali?",
            disabled=is_locked,
        )

        additional_data = st.text_area(
            "Additional data or findings from Measure (optional)",
            height=80,
            key="analyze_additional_data",
            placeholder="Misalnya: shift tertentu lebih tinggi defect rate-nya, mesin X sering breakdown, dll.",
            disabled=is_locked,
        )

        feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            height=80,
            key="analyze_feedback",
            disabled=is_locked,
        )

        submitted = st.form_submit_button(
            "⚡ Generate / Update Draft" if not is_locked else "🔒 Locked",
            type="primary",
            disabled=is_locked,
        )

    if submitted:
        user_inputs = {
            "industry":        industry,
            "process_area":    process_area,
            "pain_theme":      pain_theme,
            "project_mode":    project_mode,
            "observations":    observations,
            "additional_data": additional_data,
        }
        user_feedback = {"text": feedback_text} if feedback_text.strip() else None

        with st.spinner("Running ANALYZE agent..."):
            result = run_analyze_agent(
                project_id=active_pid,
                user_inputs=user_inputs,
                define_final=define_final,
                measure_final=measure_final,
                user_feedback=user_feedback,
            )
        st.session_state["analyze_draft"] = result
        st.session_state["analyze_final"] = None
        st.rerun()

    # ── Coaching / Gate Guidance ──────────────────────────
    view, res = _get_current()
    if res and res.get("analyze_state"):
        state   = res["analyze_state"]
        gate    = state.get("gate_result") or {}
        coaching = (state.get("coaching_md") or state.get("insight_md") or "").strip()

        st.markdown("### 🧭 Agent Guidance")
        st.markdown(f"**Gate Status:** `{gate.get('status', '-')}`")
        if coaching:
            st.markdown(coaching)
        st.divider()

        # ── Finalize ──────────────────────────────────────
        if st.session_state.get("analyze_draft"):
            st.subheader("Finalize")
            gate_status = gate.get("status")
            if st.button(
                "✅ Finalize Draft (Lock Final)",
                key="analyze_finalize_btn",
                disabled=(gate_status == "FAILED"),
            ):
                draft_state = st.session_state["analyze_draft"]["analyze_state"]
                final_result = finalize_analyze_agent(
                    project_id=active_pid,
                    analyze_state=draft_state,
                )
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason"))
                else:
                    st.session_state["analyze_final"] = final_result
                    st.session_state["analyze_draft"] = None
                    st.rerun()
            if gate_status == "FAILED":
                st.caption("Finalize disabled: gate FAILED. Fix gaps dulu.")

with right_col:
    st.subheader("Report View")
    view, res = _get_current()

    if not res or not res.get("analyze_state"):
        st.info("Belum ada draft. Generate draft untuk memulai.")
    else:
        state   = res["analyze_state"]
        outputs = state.get("outputs") or {}

        tab1, tab2, tab3, tab4 = st.tabs([
            "📄 Summary",
            "🦴 Fishbone & Causes",
            "📊 Pareto & PFEA",
            "🧾 JSON",
        ])

        with tab1:
            smd = state.get("summary_md") or ""
            if smd.strip():
                st.markdown(smd)
            else:
                st.info("Summary belum tersedia.")

            # Root cause summary
            rca_summary = outputs.get("root_cause_summary") or ""
            if rca_summary:
                st.markdown("### Root Cause Summary")
                st.markdown(rca_summary)

            # Hypotheses
            hyp = outputs.get("hypotheses_tested") or []
            if hyp:
                st.markdown("### Hypotheses Tested")
                for h in hyp:
                    result_icon = {"confirmed": "✅", "rejected": "❌", "inconclusive": "⚠️"}.get(
                        h.get("result", ""), "❓"
                    )
                    st.markdown(f"{result_icon} **{h.get('hypothesis','')}**")
                    st.caption(f"Result: {h.get('result','')} — {h.get('evidence','')}")

        with tab2:
            # Fishbone
            fishbone = outputs.get("fishbone") or {}
            if fishbone:
                st.markdown("### Fishbone Diagram (6M)")
                import pandas as pd
                rows = []
                for cat, causes in fishbone.items():
                    if isinstance(causes, list):
                        for c in causes:
                            rows.append({"Category": cat, "Potential Cause": c})
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.info("Fishbone belum tersedia.")

            # Potential causes
            pot_causes = outputs.get("potential_causes") or []
            if pot_causes:
                st.markdown("### Potential Causes")
                import pandas as pd
                st.dataframe(pd.DataFrame(pot_causes), use_container_width=True)

            # Verified root causes
            root_causes = outputs.get("root_causes") or []
            if root_causes:
                st.markdown("### Root Causes")
                import pandas as pd
                st.dataframe(pd.DataFrame(root_causes), use_container_width=True)

        with tab3:
            # Pareto
            pareto = outputs.get("pareto_analysis") or {}
            if pareto:
                st.markdown("### Pareto Analysis")
                st.markdown(f"**Method:** {pareto.get('method', '-')}")
                st.markdown(f"**80/20 Insight:** {pareto.get('80_20_insight', '-')}")
                vital = pareto.get("vital_few") or []
                if vital:
                    st.markdown("**Vital Few:**")
                    for v in vital:
                        st.markdown(f"- {v}")
            else:
                st.info("Pareto belum tersedia.")

            # PFEA
            pfea = outputs.get("pfea") or []
            if pfea:
                st.markdown("### PFEA")
                import pandas as pd
                st.dataframe(pd.DataFrame(pfea), use_container_width=True)
            else:
                st.info("PFEA belum tersedia.")

            # Data collection plan
            dcp = outputs.get("data_collection_plan") or {}
            if dcp:
                st.markdown("### Data Collection Plan")
                for k, v in dcp.items():
                    st.markdown(f"- **{k}:** {v}")

        with tab4:
            import copy
            s = copy.deepcopy(state)
            for k in ["perception_trace", "decision_trace"]:
                s.pop(k, None)
            st.json(s)

# ── Governance Panel ──────────────────────────────────────
st.divider()
st.subheader("🏛️ Governance — Stage Gate Approval")

final_exists = memory.load_analyze_final(active_pid)
appr = memory.get_phase_approval_status(active_pid, "analyze")
rev_action   = (appr.get("reviewer")  or {}).get("action")
champ_action = (appr.get("champion")  or {}).get("action")
can_advance  = appr.get("can_advance", False)

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
    if can_advance: st.success("🚀 Gate: OPEN — Lanjut ke Improve")
    else:           st.info("🔒 Gate: LOCKED")

if final_exists:
    role = auth.get_current_role()
    if role in ("reviewer", "champion"):
        st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
        note = st.text_area("Catatan (opsional)", key="gov_note_analyze")
        col_app, col_rej = st.columns(2)
        with col_app:
            if st.button("✅ Approve", key="gov_approve_analyze"):
                memory.save_approval(active_pid, "analyze", role, "approve", note)
                audit.log_phase_event(active_pid, "analyze", f"approved_by_{role}")
                st.success("Approved.")
                st.rerun()
        with col_rej:
            if st.button("❌ Reject", key="gov_reject_analyze"):
                memory.save_approval(active_pid, "analyze", role, "reject", note)
                audit.log_phase_event(active_pid, "analyze", f"rejected_by_{role}")
                st.warning("Rejected.")
                st.rerun()
    elif role == "project_leader":
        if can_advance:
            st.success("✅ Semua approval lengkap. Lanjut ke fase Improve.")
        else:
            st.info("Menunggu approval dari Reviewer dan Champion.")
else:
    st.info("Finalize ANALYZE dulu sebelum bisa di-approve.")

st.divider()
st.subheader("🧾 Audit Log — ANALYZE Phase")
events = audit.get_recent_events(project_id=active_pid, phase="analyze", limit=20)
if events:
    st.dataframe(events, use_container_width=True)
else:
    st.write("No audit events found.")