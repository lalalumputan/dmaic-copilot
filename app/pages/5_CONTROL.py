import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import streamlit as st
import pandas as pd
import copy
from app.utils import auth, memory, audit
from app.agents.control_agent import run_control_agent, finalize_control_agent

st.set_page_config(page_title="CONTROL", layout="wide")
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
    st.title("🔒 CONTROL Phase")
    st.info("Belum ada project aktif. Kembali ke halaman **Main**.")
    st.stop()

# ── Gate: Improve harus sudah FINAL ──────────────────────
improve_final = memory.load_improve_final(active_pid)
if not improve_final:
    st.title("🔒 CONTROL Phase")
    st.warning("IMPROVE belum di-finalize untuk project ini. Selesaikan fase Improve terlebih dahulu.")
    st.stop()

# ── Load upstream ─────────────────────────────────────────
analyze_final = memory.load_analyze_final(active_pid)
measure_final = memory.load_measure_final(active_pid)
define_final  = memory.load_define_final(active_pid)

# ── Auto-load control state ───────────────────────────────
loaded_pid = st.session_state.get("_loaded_pid")
if active_pid and loaded_pid == active_pid:
    if not st.session_state.get("control_draft") and not st.session_state.get("control_final"):
        draft = memory.load_control_draft(active_pid)
        if draft:
            st.session_state["control_draft"] = {
                "status": "draft",
                "control_state": draft,
                "summary": draft.get("summary_md", ""),
                "message": "Auto-loaded from disk",
            }
        final = memory.load_control_final(active_pid)
        if final:
            st.session_state["control_final"] = {
                "status": "finalized",
                "control_state": final,
                "summary": final.get("summary_md", ""),
                "message": "Auto-loaded from disk",
            }

def _get_current():
    if st.session_state.get("control_draft"):
        return "draft", st.session_state["control_draft"]
    if st.session_state.get("control_final"):
        return "final", st.session_state["control_final"]
    return "none", None

# ── Header ────────────────────────────────────────────────
logo_path = "app/assets/logo2.jpg"
h_title, h_logo = st.columns([5, 1])
with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)
with h_title:
    st.markdown("# DMAIC Copilot — CONTROL Phase")
    st.caption("### Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC")
st.divider()

# ── Project Info Bar ──────────────────────────────────────
c1, c2 = st.columns([3, 1])
with c1:
    st.caption(f"Active Project: `{active_pid}`")
with c2:
    is_locked = bool(memory.load_control_final(active_pid))
    st.markdown(
        f"<span style='padding:6px 12px;border-radius:20px;"
        f"background:{'#0f5132' if is_locked else '#1f2937'};"
        f"color:{'#d1e7dd' if is_locked else '#e5e7eb'};font-weight:600'>"
        f"{'FINAL' if is_locked else 'DRAFT'}</span>",
        unsafe_allow_html=True,
    )
st.divider()

# ── Upstream Context ──────────────────────────────────────
with st.expander("📋 Upstream Context (semua fase)", expanded=False):
    def_outs  = (define_final  or {}).get("outputs") or {}
    def_ins   = (define_final  or {}).get("inputs")  or {}
    mea_outs  = (measure_final or {}).get("outputs") or {}
    ana_outs  = (analyze_final or {}).get("outputs") or {}
    imp_outs  = (improve_final or {}).get("outputs") or {}

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**DEFINE:**")
        st.markdown(f"- Goal: {def_outs.get('goal_statement') or def_ins.get('goal_text') or '-'}")
        st.markdown(f"- Y: {def_outs.get('y_variable') or '-'}")
    with col2:
        st.markdown("**ANALYZE:**")
        rca = ana_outs.get("root_cause_summary") or "-"
        st.markdown(f"- Root cause: {rca[:100]}{'...' if len(rca) > 100 else ''}")
    with col3:
        st.markdown("**IMPROVE:**")
        sel = (imp_outs.get("selected_solution") or {}).get("solution") or "-"
        st.markdown(f"- Solution: {sel[:100]}{'...' if len(sel) > 100 else ''}")
        pilot = imp_outs.get("pilot_plan") or {}
        st.markdown(f"- Pilot scope: {pilot.get('scope') or '-'}")

# ── Two-column layout ─────────────────────────────────────
left_col, right_col = st.columns([1, 1], gap="large")

with left_col:
    st.subheader("Revision Actions")
    if st.button("🧹 Discard Draft", key="control_discard_btn"):
        memory.delete_control_draft(active_pid)
        st.session_state["control_draft"] = None
        st.rerun()
    st.caption("ℹ️ Discard menghapus draft. Final tetap tersimpan.")
    st.divider()

    st.subheader("CONTROL Inputs")
    is_locked = bool(memory.load_control_final(active_pid))

    def_ins = (define_final or {}).get("inputs") or {}
    st.session_state.setdefault("control_industry",     def_ins.get("industry", "Manufacturing"))
    st.session_state.setdefault("control_process_area", def_ins.get("process_area", "Production"))
    st.session_state.setdefault("control_pain_theme",   def_ins.get("pain_theme", "Quality"))

    with st.form("control_input_form"):
        col1, col2, col3 = st.columns(3)
        industry = col1.selectbox(
            "Industry",
            ["Manufacturing", "FMCG", "Services", "Oil & Gas", "Other"],
            key="control_industry",
            disabled=is_locked,
        )
        process_area = col2.selectbox(
            "Process Area",
            ["Production", "Quality", "Supply Chain", "Finance", "Other"],
            key="control_process_area",
            disabled=is_locked,
        )
        pain_theme = col3.selectbox(
            "Pain Theme",
            ["Quality", "Cost", "Delivery", "Safety", "Other"],
            key="control_pain_theme",
            disabled=is_locked,
        )

        project_mode = st.radio(
            "Control Mode",
            ["standard", "quick"],
            horizontal=True,
            key="control_mode",
            disabled=is_locked,
        )

        process_owner = st.text_input(
            "Process Owner (siapa yang akan menjaga improvement ini?)",
            key="control_process_owner",
            placeholder="Nama / jabatan / departemen",
            disabled=is_locked,
        )

        control_notes = st.text_area(
            "Control notes / constraints (optional)",
            height=80,
            key="control_notes",
            placeholder="Contoh: sistem sudah ada di ERP, audit dilakukan tiap bulan, dll.",
            disabled=is_locked,
        )

        # Control chart values (optional)
        control_values_raw = st.text_input(
            "Post-implementation values for control chart (optional)",
            key="control_values_raw",
            placeholder="Masukkan angka dipisah koma: 12.1, 11.8, 13.2, 12.5",
            disabled=is_locked,
        )

        feedback_text = st.text_area(
            "Feedback for next draft (optional)",
            height=80,
            key="control_feedback",
            disabled=is_locked,
        )

        submitted = st.form_submit_button(
            "⚡ Generate / Update Draft" if not is_locked else "🔒 Locked",
            type="primary",
            disabled=is_locked,
        )

    if submitted:
        # Parse control values
        control_values = []
        if control_values_raw.strip():
            try:
                control_values = [float(x.strip()) for x in control_values_raw.split(",") if x.strip()]
            except Exception:
                st.warning("Control values tidak bisa diparsing — pastikan format angka dipisah koma.")

        user_inputs = {
            "industry":       industry,
            "process_area":   process_area,
            "pain_theme":     pain_theme,
            "project_mode":   project_mode,
            "process_owner":  process_owner,
            "control_notes":  control_notes,
            "control_values": control_values,
        }
        user_feedback = {"text": feedback_text} if feedback_text.strip() else None

        with st.spinner("Running CONTROL agent..."):
            result = run_control_agent(
                project_id=active_pid,
                user_inputs=user_inputs,
                define_final=define_final,
                measure_final=measure_final,
                analyze_final=analyze_final,
                improve_final=improve_final,
                user_feedback=user_feedback,
            )
        st.session_state["control_draft"] = result
        st.session_state["control_final"] = None
        st.rerun()

    # ── Coaching ──────────────────────────────────────────
    view, res = _get_current()
    if res and res.get("control_state"):
        state    = res["control_state"]
        gate     = state.get("gate_result") or {}
        coaching = (state.get("coaching_md") or "").strip()

        st.markdown("### 🧭 Agent Guidance")
        st.markdown(f"**Gate Status:** `{gate.get('status', '-')}`")
        if coaching:
            st.markdown(coaching)
        st.divider()

        if st.session_state.get("control_draft"):
            st.subheader("Finalize")
            gate_status = gate.get("status")
            if st.button(
                "✅ Finalize & Close Project",
                key="control_finalize_btn",
                disabled=(gate_status == "FAILED"),
            ):
                draft_state  = st.session_state["control_draft"]["control_state"]
                final_result = finalize_control_agent(
                    project_id=active_pid,
                    control_state=draft_state,
                )
                if final_result.get("status") == "blocked":
                    st.error(final_result.get("reason"))
                else:
                    st.session_state["control_final"] = final_result
                    st.session_state["control_draft"] = None
                    st.balloons()
                    st.rerun()
            if gate_status == "FAILED":
                st.caption("Finalize disabled: gate FAILED. Fix gaps dulu.")

with right_col:
    st.subheader("Report View")
    view, res = _get_current()

    if not res or not res.get("control_state"):
        st.info("Belum ada draft. Generate draft untuk memulai.")
    else:
        state   = res["control_state"]
        outputs = state.get("outputs") or {}

        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📄 Summary",
            "📋 Control Plan",
            "📊 Monitoring & Reaction",
            "🏁 Closure",
            "🧾 JSON",
        ])

        with tab1:
            smd = state.get("summary_md") or ""
            if smd.strip():
                st.markdown(smd)

            # Control chart
            chart = outputs.get("control_chart") or {}
            if chart.get("available"):
                st.markdown("### Control Chart (I-MR)")
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Center", chart.get("center"))
                col_b.metric("UCL",    chart.get("ucl"))
                col_c.metric("LCL",    chart.get("lcl"))
                st.caption(f"Reaction rule: {chart.get('reaction_rule', '-')}")

        with tab2:
            control_plan = outputs.get("control_plan") or []
            if control_plan:
                st.markdown("### Control Plan")
                st.dataframe(pd.DataFrame(control_plan), use_container_width=True)
            else:
                st.info("Control plan belum tersedia.")

            # Handover
            handover = outputs.get("handover_protocol") or {}
            if handover:
                st.markdown("### Handover Protocol")
                for k, v in handover.items():
                    if isinstance(v, list):
                        st.markdown(f"- **{k}:** {', '.join(v)}")
                    else:
                        st.markdown(f"- **{k}:** {v}")

        with tab3:
            # KPIs
            kpis = outputs.get("monitoring_kpis") or []
            if kpis:
                st.markdown("### Monitoring KPIs")
                st.dataframe(pd.DataFrame(kpis), use_container_width=True)

            # Reaction plan
            reaction = outputs.get("reaction_plan") or []
            if reaction:
                st.markdown("### Reaction Plan")
                st.dataframe(pd.DataFrame(reaction), use_container_width=True)

            # Monitoring narrative
            mon_narrative = outputs.get("monitoring_reaction_plan") or ""
            if mon_narrative:
                st.markdown("### Monitoring & Reaction Narrative")
                st.markdown(mon_narrative)

            # Sustain audit
            sustain = outputs.get("sustain_audit_protocol") or {}
            if sustain:
                st.markdown("### Sustain Audit Protocol")
                for k, v in sustain.items():
                    if isinstance(v, list):
                        st.markdown(f"- **{k}:** {', '.join(v)}")
                    else:
                        st.markdown(f"- **{k}:** {v}")

        with tab4:
            # Benefit approval
            benefit = outputs.get("benefit_approval") or {}
            if benefit:
                st.markdown("### Validated Benefit")
                for k, v in benefit.items():
                    st.markdown(f"- **{k}:** {v}")

            # Lessons learned
            lessons = outputs.get("lessons_learned") or []
            if lessons:
                st.markdown("### Lessons Learned")
                for l in lessons:
                    st.markdown(f"- {l}")

            # Project closure checklist
            checklist = outputs.get("project_closure_checklist") or []
            if checklist:
                st.markdown("### Project Closure Checklist")
                st.dataframe(pd.DataFrame(checklist), use_container_width=True)

            # Project complete badge
            if memory.load_control_final(active_pid):
                st.success("🎉 Project DMAIC selesai dan ter-close.")

        with tab5:
            s = copy.deepcopy(state)
            for k in ["perception_trace", "decision_trace"]:
                s.pop(k, None)
            st.json(s)

# ── Governance Panel ──────────────────────────────────────
st.divider()
st.subheader("🏛️ Governance — Final Gate Approval")

final_exists  = memory.load_control_final(active_pid)
appr          = memory.get_phase_approval_status(active_pid, "control")
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
    if can_advance:
        st.success("🏆 Project DMAIC CLOSED — semua fase approved!")
    else:
        st.info("🔒 Gate: LOCKED")

if final_exists:
    role = auth.get_current_role()
    if role in ("reviewer", "champion"):
        st.markdown(f"**Aksi kamu sebagai {role.title()}:**")
        note = st.text_area("Catatan (opsional)", key="gov_note_control")
        col_app, col_rej = st.columns(2)
        with col_app:
            if st.button("✅ Approve & Close", key="gov_approve_control"):
                memory.save_approval(active_pid, "control", role, "approve", note)
                audit.log_phase_event(active_pid, "control", f"approved_by_{role}")
                st.success("Approved.")
                st.rerun()
        with col_rej:
            if st.button("❌ Reject", key="gov_reject_control"):
                memory.save_approval(active_pid, "control", role, "reject", note)
                audit.log_phase_event(active_pid, "control", f"rejected_by_{role}")
                st.warning("Rejected.")
                st.rerun()
    elif role == "project_leader":
        if can_advance:
            st.success("🏆 Project selesai. Semua fase approved oleh Reviewer dan Champion.")
        else:
            st.info("Menunggu final approval dari Reviewer dan Champion.")
else:
    st.info("Finalize CONTROL dulu sebelum bisa di-approve.")

st.divider()
st.subheader("🧾 Audit Log — CONTROL Phase")
events = audit.get_recent_events(project_id=active_pid, phase="control", limit=20)
if events:
    st.dataframe(events, use_container_width=True)
else:
    st.write("No audit events found.")