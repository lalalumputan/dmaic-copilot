"""
improve_agent.py — IMPROVE Phase Agent
Architecture: Perception → Decision → Action → Gate → Finalize
LLM hanya jalan di run_improve_agent() (draft).
Finalize TIDAK memanggil LLM.
"""
from __future__ import annotations

import json
import re
import copy
import itertools
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import memory, audit, reporting, llm_engine

PHASE_NAME = "improve"
IMPROVE_SCHEMA = "IMPROVE_SCHEMA_V1"


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
# DOE HELPER (retained from old agent)
# ======================================================

def generate_doe_matrix(factors: Dict[str, List[str]], max_runs: int = 32) -> Dict[str, Any]:
    if not factors:
        return {"error": "No factors provided."}
    total = 1
    for v in factors.values():
        total *= len(v)
    if total > max_runs:
        return {"error": f"Total combinations {total} exceeds limit {max_runs}. Reduce factors/levels."}
    keys = list(factors.keys())
    runs = list(itertools.product(*[factors[k] for k in keys]))
    random.seed(42)
    random.shuffle(runs)
    return {
        "doe_matrix": [{"Run": i, **dict(zip(keys, combo))} for i, combo in enumerate(runs, 1)],
        "total_runs": total,
        "factors": keys,
    }


# ======================================================
# GATE EVALUATION (rules-based, no LLM)
# ======================================================

def evaluate_improve_gate(
    outputs: Dict[str, Any],
    user_inputs: Dict[str, Any],
) -> Dict[str, Any]:
    failed: List[str] = []
    conditional: List[str] = []

    # A1: Solutions identified
    solutions = outputs.get("potential_solutions") or []
    if not isinstance(solutions, list) or len(solutions) == 0:
        failed.append("A1_no_potential_solutions")

    # A2: Selected solution exists
    selected = outputs.get("selected_solution") or {}
    if not selected or not (selected.get("solution") or selected.get("name")):
        failed.append("A2_no_selected_solution")

    # A3: Implementation plan exists
    impl_plan = outputs.get("implementation_plan") or []
    if not isinstance(impl_plan, list) or len(impl_plan) == 0:
        failed.append("A3_no_implementation_plan")

    # A4: Pilot plan documented
    pilot = outputs.get("pilot_plan") or {}
    if not pilot or not pilot.get("scope"):
        failed.append("A4_no_pilot_plan")

    # B1: Effort-benefit matrix
    effort_benefit = outputs.get("effort_benefit_matrix") or []
    if not isinstance(effort_benefit, list) or len(effort_benefit) == 0:
        conditional.append("B1_effort_benefit_matrix_missing")

    # B2: Cost-benefit analysis
    cost_benefit = outputs.get("cost_benefit_analysis") or {}
    if not cost_benefit:
        conditional.append("B2_cost_benefit_analysis_missing")

    # B3: Should-be process documented
    should_be = (outputs.get("should_be_process") or "").strip()
    if not should_be:
        conditional.append("B3_should_be_process_missing")

    if failed:
        status = "FAILED"
        summary = "IMPROVE gate FAILED. Define solutions and implementation plan before proceeding."
    elif conditional:
        status = "CONDITIONAL"
        summary = "IMPROVE gate CONDITIONAL. Core deliverables present but some items need attention."
    else:
        status = "PASSED"
        summary = "IMPROVE gate PASSED. Solutions selected and implementation plan is ready."

    return {
        "status": status,
        "failed_rules": failed,
        "conditional_rules": conditional,
        "agent_summary": summary,
    }


def build_improve_coaching_md(
    gate_result: Dict[str, Any],
    outputs: Dict[str, Any],
) -> str:
    status = gate_result.get("status")
    failed = gate_result.get("failed_rules") or []
    conditional = gate_result.get("conditional_rules") or []

    gaps: List[str] = []
    actions: List[str] = []

    if "A1_no_potential_solutions" in failed:
        gaps.append("No potential solutions identified.")
        actions.append("Generate solution ideas addressing verified root causes from Analyze.")
    if "A2_no_selected_solution" in failed:
        gaps.append("No solution selected yet.")
        actions.append("Select the best solution based on effort-benefit and feasibility.")
    if "A3_no_implementation_plan" in failed:
        gaps.append("Implementation plan missing.")
        actions.append("Define steps, owners, timeline, and resources for implementation.")
    if "A4_no_pilot_plan" in failed:
        gaps.append("Pilot plan not documented.")
        actions.append("Define pilot scope, success criteria, duration, and rollback plan.")
    if "B1_effort_benefit_matrix_missing" in conditional:
        gaps.append("Effort-benefit matrix missing.")
        actions.append("Score each solution by effort (1-5) and benefit (1-5) to justify selection.")
    if "B2_cost_benefit_analysis_missing" in conditional:
        gaps.append("Cost-benefit analysis missing.")
        actions.append("Estimate investment cost vs expected savings/benefit.")
    if "B3_should_be_process_missing" in conditional:
        gaps.append("Should-be process not documented.")
        actions.append("Describe the improved process flow after solution implementation.")

    md = [f"### IMPROVE Gate Status: **{status}**"]
    md.append(f"- {gate_result.get('agent_summary', '')}")
    if gaps:
        md.append("\n### Gaps")
        md.extend([f"- {g}" for g in gaps])
    if actions:
        md.append("\n### Actions (ordered)")
        md.extend([f"{i+1}. {a}" for i, a in enumerate(actions)])
    if status == "FAILED":
        md.append("\n### Risk")
        md.append("- Implementing without a clear plan risks wasting resources and not fixing root causes.")

    return "\n".join(md).strip()


# ======================================================
# 1. PERCEPTION
# ======================================================

def perception_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    analyze_final: Optional[Dict[str, Any]] = None,
    measure_final: Optional[Dict[str, Any]] = None,
    define_final: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    industry     = user_inputs.get("industry")
    pain_theme   = user_inputs.get("pain_theme")
    process_area = user_inputs.get("process_area")

    similar_memory = []
    retrieve_fn = getattr(memory, "retrieve_similar_improve_episodes", None)
    if callable(retrieve_fn):
        try:
            similar_memory = retrieve_fn(industry, pain_theme, k=2)
        except Exception:
            pass

    ana_outs  = (analyze_final  or {}).get("outputs") or {}
    mea_outs  = (measure_final  or {}).get("outputs") or {}
    def_outs  = (define_final   or {}).get("outputs") or {}
    def_ins   = (define_final   or {}).get("inputs")  or {}

    return {
        "project_id":     project_id,
        "industry":       industry,
        "pain_theme":     pain_theme,
        "process_area":   process_area,
        "problem":        def_outs.get("problem_statement") or def_ins.get("problem_text") or "",
        "goal":           def_outs.get("goal_statement") or def_ins.get("goal_text") or "",
        "y_variable":     mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "",
        "root_causes":    ana_outs.get("root_causes") or [],
        "root_cause_summary": ana_outs.get("root_cause_summary") or "",
        "vital_few":      (ana_outs.get("pareto_analysis") or {}).get("vital_few") or [],
        "baseline_mean":  (mea_outs.get("process_performance_summary") or {}).get("summary", {}).get("mean"),
        "benefit_estimate": (define_final or {}).get("gate_evidence", {}).get("benefit_estimate") or "",
        "user_inputs":    user_inputs,
        "similar_memory": similar_memory,
    }


# ======================================================
# 2. DECISION
# ======================================================

def decision_step(
    perception: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt = f"""
DECISION STEP — IMPROVE PHASE
You are a Lean Six Sigma Master Black Belt.

Given root causes and context, decide the improvement strategy:
- What solution approach is most appropriate (Kaizen, DOE, process redesign, mistake-proofing)?
- What are the key constraints (budget, time, resources)?
- What is the pilot approach?

Return JSON only:
{{
  "solution_approach": "...",
  "key_constraints": ["..."],
  "pilot_approach": "...",
  "mode": "standard or quick",
  "priority_actions": ["..."]
}}

Root causes: {json.dumps(perception.get('root_causes', [])[:3], indent=2)}
Goal: {perception.get('goal')}
Y Variable: {perception.get('y_variable')}
Industry: {perception.get('industry')}
User feedback: {json.dumps(user_feedback or {}, indent=2)}
""".strip()

    raw = llm(prompt)
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
ACTION STEP — IMPROVE PHASE
You are a Lean Six Sigma Master Black Belt.
Mode: {mode} (if 'quick': concise but complete)

Generate a complete IMPROVE phase package in JSON:
{{
  "potential_solutions": [
    {{
      "solution": "...",
      "addresses_root_cause": "...",
      "type": "process/technology/behavioral/structural",
      "feasibility": "high/medium/low",
      "notes": "..."
    }}
  ],
  "effort_benefit_matrix": [
    {{
      "solution": "...",
      "effort_score": 1-5,
      "benefit_score": 1-5,
      "priority": "high/medium/low",
      "rationale": "..."
    }}
  ],
  "cause_solution_map": [
    {{
      "root_cause": "...",
      "solution": "...",
      "mechanism": "how solution addresses the cause"
    }}
  ],
  "cost_benefit_analysis": {{
    "investment_estimate": "...",
    "expected_annual_saving": "...",
    "payback_period": "...",
    "roi_estimate": "...",
    "assumptions": ["..."]
  }},
  "selected_solution": {{
    "solution": "...",
    "rationale": "...",
    "expected_impact_on_y": "..."
  }},
  "should_be_process": "Description of the improved process after solution implementation",
  "pilot_plan": {{
    "scope": "...",
    "duration": "...",
    "success_criteria": "...",
    "owner": "...",
    "rollback_plan": "...",
    "resources_needed": ["..."]
  }},
  "implementation_plan": [
    {{
      "step": 1,
      "action": "...",
      "owner": "...",
      "timeline": "...",
      "deliverable": "..."
    }}
  ],
  "risks_and_assumptions": ["..."]
}}

Context:
Problem: {perception.get('problem')}
Goal: {perception.get('goal')}
Y Variable: {perception.get('y_variable')}
Root causes: {json.dumps(perception.get('root_causes', [])[:5], indent=2)}
Root cause summary: {perception.get('root_cause_summary')}
Vital few: {perception.get('vital_few')}
Baseline mean: {perception.get('baseline_mean')}
Benefit estimate: {perception.get('benefit_estimate')}
Industry: {perception.get('industry')}

User inputs: {json.dumps(user_inputs, indent=2)}
Decisions: {json.dumps(decisions, indent=2)}
User feedback: {json.dumps(user_feedback or {}, indent=2)}
""".strip()

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw) or {"llm_raw": raw}

    # DOE matrix if user provided factors
    factor_text = user_inputs.get("doe_factors", "").strip()
    if factor_text and "=" in factor_text:
        try:
            factors = {}
            for chunk in factor_text.split(";"):
                chunk = chunk.strip()
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    factors[k.strip()] = [x.strip() for x in v.split(",") if x.strip()]
            if factors:
                parsed["doe_matrix"] = generate_doe_matrix(factors)
        except Exception as e:
            parsed["doe_matrix"] = {"error": str(e)}

    return parsed


# ======================================================
# 4. SUMMARY BUILDER
# ======================================================

def _build_improve_summary_md(improve_state: Dict[str, Any]) -> str:
    outputs = improve_state.get("outputs") or {}
    gate    = improve_state.get("gate_result") or {}
    pid     = improve_state.get("project_id", "-")

    selected  = outputs.get("selected_solution") or {}
    impl_plan = outputs.get("implementation_plan") or []
    cb        = outputs.get("cost_benefit_analysis") or {}

    md = [f"## IMPROVE — {pid}"]
    md.append(f"**Gate Status:** {gate.get('status', '-')}\n")

    if selected:
        md.append("### Selected Solution")
        md.append(f"**{selected.get('solution', '-')}**")
        md.append(f"- Rationale: {selected.get('rationale', '-')}")
        md.append(f"- Expected impact: {selected.get('expected_impact_on_y', '-')}\n")

    if cb:
        md.append("### Cost-Benefit")
        md.append(f"- Investment: {cb.get('investment_estimate', '-')}")
        md.append(f"- Expected saving: {cb.get('expected_annual_saving', '-')}")
        md.append(f"- ROI: {cb.get('roi_estimate', '-')}\n")

    if impl_plan:
        md.append("### Implementation Plan")
        for step in impl_plan[:5]:
            md.append(f"{step.get('step', '')}. {step.get('action', '')} — {step.get('owner', '')} ({step.get('timeline', '')})")

    coaching = (improve_state.get("coaching_md") or "").strip()
    if coaching:
        md.append("\n### Agent Guidance")
        md.append(coaching)

    return "\n".join(md).strip()


# ======================================================
# MAIN ENTRYPOINT — DRAFT
# ======================================================

def run_improve_agent(
    project_id: str,
    user_inputs: Dict[str, Any],
    define_final: Optional[Dict[str, Any]] = None,
    measure_final: Optional[Dict[str, Any]] = None,
    analyze_final: Optional[Dict[str, Any]] = None,
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, analyze_final, measure_final, define_final)
    decisions  = decision_step(perception, user_feedback)
    outputs    = action_step(project_id, user_inputs, perception, decisions, user_feedback)

    gate_result = evaluate_improve_gate(outputs, user_inputs)
    coaching_md = build_improve_coaching_md(gate_result, outputs)

    improve_state: Dict[str, Any] = {
        "schema_version": IMPROVE_SCHEMA,
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
        },
        "perception_trace": {
            "industry":    perception.get("industry"),
            "pain_theme":  perception.get("pain_theme"),
            "y_variable":  perception.get("y_variable"),
            "root_causes": perception.get("root_causes"),
        },
        "decision_trace": decisions,
        "user_feedback":  user_feedback or {},
    }

    improve_state["summary_md"] = _build_improve_summary_md(improve_state)

    save_fn = getattr(memory, "save_improve_draft", None)
    if callable(save_fn):
        save_fn(project_id, improve_state)

    audit.log_phase_event(project_id, PHASE_NAME, "draft_generated", meta={
        "gate_status": gate_result.get("status"),
    })

    return {
        "status":        "draft",
        "improve_state": improve_state,
        "summary":       improve_state["summary_md"],
        "message":       "Improve draft generated.",
    }


# ======================================================
# FINALIZE — NO LLM
# ======================================================

def finalize_improve_agent(
    project_id: str,
    improve_state: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    gate = (improve_state or {}).get("gate_result") or {}
    if gate.get("status") == "FAILED":
        return {
            "status":        "blocked",
            "reason":        "IMPROVE gate FAILED — fix critical items before finalizing.",
            "gate":          gate,
            "improve_state": improve_state,
        }

    final_state = copy.deepcopy(improve_state)
    final_state["status"]     = "final"
    final_state["updated_at"] = _now_iso()
    final_state.setdefault("created_at", _now_iso())
    final_state["summary_md"] = final_state.get("summary_md") or _build_improve_summary_md(final_state)

    save_fn = getattr(memory, "save_improve_final", None)
    if callable(save_fn):
        save_fn(project_id, final_state)

    delete_fn = getattr(memory, "delete_improve_draft", None)
    if callable(delete_fn):
        delete_fn(project_id)

    episode_fn = getattr(memory, "save_improve_episode", None)
    if callable(episode_fn):
        episode_fn(project_id, final_state, user_feedback)

    audit.log_phase_event(project_id, PHASE_NAME, "finalized", meta={
        "gate_status":  gate.get("status"),
        "failed_rules": gate.get("failed_rules", []),
    })

    return {
        "status":        "finalized",
        "improve_state": final_state,
        "summary":       final_state["summary_md"],
        "message":       "Improve finalized (locked).",
    }