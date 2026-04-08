"""
control_agent.py — CONTROL Phase Agent
Architecture: Perception → Decision → Action → Gate → Finalize
LLM hanya jalan di run_control_agent() (draft).
Finalize TIDAK memanggil LLM.
"""
from __future__ import annotations

import json
import re
import copy
import numpy as np
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import memory, audit, llm_engine

PHASE_NAME = "control"
CONTROL_SCHEMA = "CONTROL_SCHEMA_V1"


# ======================================================
# Helpers
# ======================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def llm(prompt: str) -> str:
    return llm_engine.complete(prompt)

def extract_json_from_llm(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m2 = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if m2:
        try:
            return json.loads(m2.group(1))
        except Exception:
            pass
    return None


# ======================================================
# CONTROL CHART HELPER (deterministic, no LLM)
# ======================================================

def compute_control_limits(values: List[float]) -> Dict[str, Any]:
    if not values or len(values) < 2:
        return {"available": False, "reason": "Insufficient data."}
    arr  = [float(v) for v in values if v is not None]
    mean = float(np.mean(arr))
    std  = float(np.std(arr, ddof=1))
    return {
        "available": True,
        "chart_type": "I-MR",
        "center":     round(mean, 4),
        "ucl":        round(mean + 3 * std, 4),
        "lcl":        round(mean - 3 * std, 4),
        "std":        round(std, 4),
        "n":          len(arr),
        "reaction_rule": "Stop process if point exceeds UCL/LCL. Initiate containment and RCA.",
    }


# ======================================================
# GATE EVALUATION (rules-based, no LLM)
# ======================================================

def evaluate_control_gate(
    outputs: Dict[str, Any],
    user_inputs: Dict[str, Any],
) -> Dict[str, Any]:
    failed: List[str] = []
    conditional: List[str] = []

    # A1: Control plan exists
    control_plan = outputs.get("control_plan") or []
    if not isinstance(control_plan, list) or len(control_plan) == 0:
        failed.append("A1_no_control_plan")

    # A2: Monitoring KPIs defined
    kpis = outputs.get("monitoring_kpis") or []
    if not isinstance(kpis, list) or len(kpis) == 0:
        failed.append("A2_no_monitoring_kpis")

    # A3: Reaction plan exists
    reaction_plan = outputs.get("reaction_plan") or []
    if not isinstance(reaction_plan, list) or len(reaction_plan) == 0:
        failed.append("A3_no_reaction_plan")

    # A4: Handover protocol documented
    handover = outputs.get("handover_protocol") or {}
    if not handover or not handover.get("owner"):
        failed.append("A4_no_handover_protocol")

    # B1: Sustain audit protocol
    sustain = outputs.get("sustain_audit_protocol") or {}
    if not sustain:
        conditional.append("B1_sustain_audit_protocol_missing")

    # B2: Benefit approval/validation
    benefit = outputs.get("benefit_approval") or {}
    if not benefit:
        conditional.append("B2_benefit_approval_missing")

    # B3: Monitoring & reaction plan documented
    monitoring_plan = (outputs.get("monitoring_reaction_plan") or "").strip()
    if not monitoring_plan:
        conditional.append("B3_monitoring_reaction_plan_missing")

    if failed:
        status  = "FAILED"
        summary = "CONTROL gate FAILED. Define control plan and monitoring KPIs before finalizing."
    elif conditional:
        status  = "CONDITIONAL"
        summary = "CONTROL gate CONDITIONAL. Core deliverables present but some items need attention."
    else:
        status  = "PASSED"
        summary = "CONTROL gate PASSED. Control system is ready for handover."

    return {
        "status":           status,
        "failed_rules":     failed,
        "conditional_rules": conditional,
        "agent_summary":    summary,
    }


def build_control_coaching_md(
    gate_result: Dict[str, Any],
    outputs: Dict[str, Any],
) -> str:
    status     = gate_result.get("status")
    failed     = gate_result.get("failed_rules") or []
    conditional = gate_result.get("conditional_rules") or []

    gaps: List[str]    = []
    actions: List[str] = []

    if "A1_no_control_plan" in failed:
        gaps.append("Control plan missing.")
        actions.append("Define what to control, how to measure, frequency, owner, and spec limit for each CTQ.")
    if "A2_no_monitoring_kpis" in failed:
        gaps.append("Monitoring KPIs not defined.")
        actions.append("Specify KPIs to track post-implementation: metric, target, frequency, owner.")
    if "A3_no_reaction_plan" in failed:
        gaps.append("Reaction plan missing.")
        actions.append("Define what to do when KPI goes out of control: who reacts, how fast, what steps.")
    if "A4_no_handover_protocol" in failed:
        gaps.append("Handover protocol not documented.")
        actions.append("Document who owns the process post-project, training needed, and escalation path.")
    if "B1_sustain_audit_protocol_missing" in conditional:
        gaps.append("Sustain audit protocol missing.")
        actions.append("Define periodic audit schedule to ensure improvements are sustained (30/60/90 day check).")
    if "B2_benefit_approval_missing" in conditional:
        gaps.append("Benefit approval not documented.")
        actions.append("Document validated financial/operational benefit with sign-off from Finance/Owner.")
    if "B3_monitoring_reaction_plan_missing" in conditional:
        gaps.append("Monitoring & reaction plan narrative missing.")
        actions.append("Write a brief narrative of the full monitoring and reaction system.")

    md = [f"### CONTROL Gate Status: **{status}**"]
    md.append(f"- {gate_result.get('agent_summary', '')}")
    if gaps:
        md.append("\n### Gaps")
        md.extend([f"- {g}" for g in gaps])
    if actions:
        md.append("\n### Actions (ordered)")
        md.extend([f"{i+1}. {a}" for i, a in enumerate(actions)])
    if status == "FAILED":
        md.append("\n### Risk")
        md.append("- Without a control plan, improvements will not be sustained and problems will recur.")

    return "\n".join(md).strip()


# ======================================================
# 1. PERCEPTION
# ======================================================

def perception_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    improve_final: Optional[Dict[str, Any]] = None,
    analyze_final: Optional[Dict[str, Any]] = None,
    measure_final: Optional[Dict[str, Any]] = None,
    define_final:  Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    industry     = user_inputs.get("industry")
    pain_theme   = user_inputs.get("pain_theme")
    process_area = user_inputs.get("process_area")

    similar_memory = []
    retrieve_fn = getattr(memory, "retrieve_similar_control_episodes", None)
    if callable(retrieve_fn):
        try:
            similar_memory = retrieve_fn(industry, pain_theme, k=2)
        except Exception:
            pass

    imp_outs = (improve_final or {}).get("outputs") or {}
    ana_outs = (analyze_final or {}).get("outputs") or {}
    mea_outs = (measure_final or {}).get("outputs") or {}
    def_outs = (define_final  or {}).get("outputs") or {}
    def_ins  = (define_final  or {}).get("inputs")  or {}

    return {
        "project_id":        project_id,
        "industry":          industry,
        "pain_theme":        pain_theme,
        "process_area":      process_area,
        "problem":           def_outs.get("problem_statement") or def_ins.get("problem_text") or "",
        "goal":              def_outs.get("goal_statement") or def_ins.get("goal_text") or "",
        "y_variable":        mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "",
        "selected_solution": (imp_outs.get("selected_solution") or {}).get("solution") or "",
        "implementation_plan": imp_outs.get("implementation_plan") or [],
        "pilot_plan":        imp_outs.get("pilot_plan") or {},
        "root_cause_summary": ana_outs.get("root_cause_summary") or "",
        "baseline_mean":     (mea_outs.get("process_performance_summary") or {}).get("summary", {}).get("mean"),
        "benefit_estimate":  (define_final or {}).get("gate_evidence", {}).get("benefit_estimate") or "",
        "ctq_list":          def_outs.get("ctq_list") or [],
        "user_inputs":       user_inputs,
        "similar_memory":    similar_memory,
    }


# ======================================================
# 2. DECISION
# ======================================================

def decision_step(
    perception: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt = f"""
DECISION STEP — CONTROL PHASE
You are a Lean Six Sigma Master Black Belt.

Given the improvement context, decide the control strategy:
- What control mechanisms are most appropriate (SPC, poka-yoke, SOPs, audits)?
- What are the critical control points?
- What is the handover approach?

Return JSON only:
{{
  "control_approach": "...",
  "critical_control_points": ["..."],
  "recommended_control_tools": ["SPC/poka-yoke/SOP/checklist/audit"],
  "handover_approach": "...",
  "sustainability_risks": ["..."]
}}

Context:
Solution: {perception.get('selected_solution')}
Y Variable: {perception.get('y_variable')}
Goal: {perception.get('goal')}
Industry: {perception.get('industry')}
User feedback: {json.dumps(user_feedback or {}, indent=2)}
""".strip()

    raw    = llm(prompt)
    parsed = extract_json_from_llm(raw)
    return parsed if parsed else {"llm_raw": raw}


# ======================================================
# 3. ACTION
# ======================================================

def action_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    perception: Dict[str, Any],
    decisions: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    mode = user_inputs.get("project_mode", "standard")

    prompt = f"""
ACTION STEP — CONTROL PHASE
You are a Lean Six Sigma Master Black Belt.
Mode: {mode} (if 'quick': concise but complete)

Generate a complete CONTROL phase package in JSON:
{{
  "control_plan": [
    {{
      "ctq": "...",
      "metric": "...",
      "spec_limit": "...",
      "control_method": "SPC/poka-yoke/SOP/checklist",
      "measurement_frequency": "...",
      "sample_size": "...",
      "owner": "...",
      "recording_system": "..."
    }}
  ],
  "monitoring_kpis": [
    {{
      "kpi": "...",
      "target": "...",
      "frequency": "...",
      "owner": "...",
      "dashboard": "..."
    }}
  ],
  "reaction_plan": [
    {{
      "trigger": "when KPI exceeds...",
      "immediate_action": "...",
      "root_cause_step": "...",
      "escalation": "...",
      "timeframe": "..."
    }}
  ],
  "monitoring_reaction_plan": "Narrative of the full monitoring and reaction system",
  "handover_protocol": {{
    "owner": "...",
    "training_required": ["..."],
    "documentation": ["SOP/work instruction/control chart"],
    "sign_off_required_from": ["..."],
    "escalation_path": "..."
  }},
  "sustain_audit_protocol": {{
    "audit_frequency": "...",
    "auditor": "...",
    "checklist_items": ["..."],
    "30_day_check": "...",
    "60_day_check": "...",
    "90_day_check": "..."
  }},
  "benefit_approval": {{
    "validated_benefit": "...",
    "benefit_type": "cost/quality/delivery/safety",
    "measurement_method": "...",
    "validated_by": "...",
    "validation_date": "TBD"
  }},
  "lessons_learned": ["..."],
  "project_closure_checklist": [
    {{"item": "...", "status": "done/pending"}}
  ],
  "risks_and_assumptions": ["..."]
}}

Context:
Problem: {perception.get('problem')}
Goal: {perception.get('goal')}
Y Variable: {perception.get('y_variable')}
Solution implemented: {perception.get('selected_solution')}
Root cause summary: {perception.get('root_cause_summary')}
Baseline mean: {perception.get('baseline_mean')}
CTQ list: {json.dumps(perception.get('ctq_list', [])[:3], indent=2)}
Industry: {perception.get('industry')}

User inputs: {json.dumps(user_inputs, indent=2)}
Decisions: {json.dumps(decisions, indent=2)}
User feedback: {json.dumps(user_feedback or {}, indent=2)}
""".strip()

    raw    = llm(prompt)
    parsed = extract_json_from_llm(raw) or {"llm_raw": raw}

    # Deterministic control limits if user provides values
    values = user_inputs.get("control_values") or []
    if isinstance(values, list) and len(values) >= 2:
        parsed["control_chart"] = compute_control_limits(values)

    return parsed


# ======================================================
# 4. SUMMARY BUILDER
# ======================================================

def _build_control_summary_md(control_state: Dict[str, Any]) -> str:
    outputs = control_state.get("outputs") or {}
    gate    = control_state.get("gate_result") or {}
    pid     = control_state.get("project_id", "-")

    control_plan = outputs.get("control_plan") or []
    kpis         = outputs.get("monitoring_kpis") or []
    handover     = outputs.get("handover_protocol") or {}
    benefit      = outputs.get("benefit_approval") or {}

    md = [f"## CONTROL — {pid}"]
    md.append(f"**Gate Status:** {gate.get('status', '-')}\n")

    if handover:
        md.append("### Handover")
        md.append(f"- Owner: {handover.get('owner', '-')}")
        md.append(f"- Escalation: {handover.get('escalation_path', '-')}\n")

    if benefit:
        md.append("### Validated Benefit")
        md.append(f"- {benefit.get('validated_benefit', '-')} ({benefit.get('benefit_type', '-')})\n")

    if kpis:
        md.append("### Monitoring KPIs")
        for k in kpis[:3]:
            md.append(f"- **{k.get('kpi','')}** — target: {k.get('target','')} | {k.get('frequency','')} | owner: {k.get('owner','')}")
        md.append("")

    if control_plan:
        md.append(f"### Control Plan ({len(control_plan)} items)")
        for c in control_plan[:3]:
            md.append(f"- {c.get('ctq','')} → {c.get('control_method','')} | {c.get('measurement_frequency','')} | {c.get('owner','')}")

    coaching = (control_state.get("coaching_md") or "").strip()
    if coaching:
        md.append("\n### Agent Guidance")
        md.append(coaching)

    return "\n".join(md).strip()


# ======================================================
# MAIN ENTRYPOINT — DRAFT
# ======================================================

def run_control_agent(
    project_id: str,
    user_inputs: Dict[str, Any],
    define_final:  Optional[Dict[str, Any]] = None,
    measure_final: Optional[Dict[str, Any]] = None,
    analyze_final: Optional[Dict[str, Any]] = None,
    improve_final: Optional[Dict[str, Any]] = None,
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(
        project_id, user_inputs,
        improve_final, analyze_final, measure_final, define_final
    )
    decisions = decision_step(perception, user_feedback)
    outputs   = action_step(project_id, user_inputs, perception, decisions, user_feedback)

    gate_result = evaluate_control_gate(outputs, user_inputs)
    coaching_md = build_control_coaching_md(gate_result, outputs)

    control_state: Dict[str, Any] = {
        "schema_version": CONTROL_SCHEMA,
        "phase":          PHASE_NAME,
        "status":         "draft",
        "project_id":     project_id,
        "created_at":     _now_iso(),
        "updated_at":     _now_iso(),
        "inputs":         dict(user_inputs),
        "outputs":        outputs,
        "gate_result":    gate_result,
        "coaching_md":    coaching_md,
        "insight_md":     coaching_md,
        "upstream": {
            "define_locked":  True,
            "measure_locked": True,
            "analyze_locked": True,
            "improve_locked": True,
        },
        "perception_trace": {
            "industry":          perception.get("industry"),
            "pain_theme":        perception.get("pain_theme"),
            "y_variable":        perception.get("y_variable"),
            "selected_solution": perception.get("selected_solution"),
        },
        "decision_trace": decisions,
        "user_feedback":  user_feedback or {},
    }

    control_state["summary_md"] = _build_control_summary_md(control_state)

    save_fn = getattr(memory, "save_control_draft", None)
    if callable(save_fn):
        save_fn(project_id, control_state)

    audit.log_phase_event(project_id, PHASE_NAME, "draft_generated", meta={
        "gate_status": gate_result.get("status"),
    })

    return {
        "status":        "draft",
        "control_state": control_state,
        "summary":       control_state["summary_md"],
        "message":       "Control draft generated.",
    }


# ======================================================
# FINALIZE — NO LLM
# ======================================================

def finalize_control_agent(
    project_id: str,
    control_state: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    gate = (control_state or {}).get("gate_result") or {}
    if gate.get("status") == "FAILED":
        return {
            "status":        "blocked",
            "reason":        "CONTROL gate FAILED — fix critical items before finalizing.",
            "gate":          gate,
            "control_state": control_state,
        }

    final_state = copy.deepcopy(control_state)
    final_state["status"]     = "final"
    final_state["updated_at"] = _now_iso()
    final_state.setdefault("created_at", _now_iso())
    final_state["summary_md"] = (
        final_state.get("summary_md") or _build_control_summary_md(final_state)
    )

    save_fn = getattr(memory, "save_control_final", None)
    if callable(save_fn):
        save_fn(project_id, final_state)

    delete_fn = getattr(memory, "delete_control_draft", None)
    if callable(delete_fn):
        delete_fn(project_id)

    episode_fn = getattr(memory, "save_control_episode", None)
    if callable(episode_fn):
        episode_fn(project_id, final_state, user_feedback)

    audit.log_phase_event(project_id, PHASE_NAME, "finalized", meta={
        "gate_status":  gate.get("status"),
        "failed_rules": gate.get("failed_rules", []),
    })

    return {
        "status":        "finalized",
        "control_state": final_state,
        "summary":       final_state["summary_md"],
        "message":       "Control finalized (locked). Project cycle complete.",
    }