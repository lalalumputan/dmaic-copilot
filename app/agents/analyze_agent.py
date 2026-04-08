"""
analyze_agent.py — ANALYZE Phase Agent
Architecture: Perception → Decision → Action → Gate → Finalize
LLM hanya jalan di run_analyze_agent() (draft).
Finalize TIDAK memanggil LLM.
"""
from __future__ import annotations

import json
import re
import copy
import pandas as pd
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import (
    memory,
    audit,
    reporting,
    llm_engine,
)

PHASE_NAME = "analyze"
ANALYZE_SCHEMA = "ANALYZE_SCHEMA_V1"


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
# GATE EVALUATION (rules-based, no LLM)
# ======================================================

def evaluate_analyze_gate(
    outputs: Dict[str, Any],
    user_inputs: Dict[str, Any],
) -> Dict[str, Any]:
    failed: List[str] = []
    conditional: List[str] = []

    # A1: Root causes identified
    root_causes = outputs.get("root_causes") or []
    if not isinstance(root_causes, list) or len(root_causes) == 0:
        failed.append("A1_no_root_causes_identified")

    # A2: Root causes verified (not just listed)
    verified = [c for c in root_causes if isinstance(c, dict) and c.get("verified") is True]
    if len(verified) == 0 and len(root_causes) > 0:
        failed.append("A2_no_verified_root_causes")

    # A3: Pareto / prioritization exists
    pareto = outputs.get("pareto_analysis") or {}
    if not pareto or not isinstance(pareto, dict):
        failed.append("A3_no_pareto_prioritization")

    # A4: Root cause summary present
    rca_summary = (outputs.get("root_cause_summary") or "").strip()
    if not rca_summary:
        failed.append("A4_no_root_cause_summary")

    # B1: PFEA done
    pfea = outputs.get("pfea") or []
    if not isinstance(pfea, list) or len(pfea) == 0:
        conditional.append("B1_pfea_not_completed")

    # B2: Process map documented
    process_map = (outputs.get("process_map_notes") or "").strip()
    if not process_map:
        conditional.append("B2_process_map_missing")

    # B3: Data collection plan for verification
    dcp = outputs.get("data_collection_plan") or {}
    if not dcp:
        conditional.append("B3_data_collection_plan_missing")

    if failed:
        status = "FAILED"
        summary = "ANALYZE gate FAILED. Identify and verify root causes before proceeding."
    elif conditional:
        status = "CONDITIONAL"
        summary = "ANALYZE gate CONDITIONAL. Core deliverables present but some items need attention."
    else:
        status = "PASSED"
        summary = "ANALYZE gate PASSED. Root causes identified, verified, and prioritized."

    return {
        "status": status,
        "failed_rules": failed,
        "conditional_rules": conditional,
        "agent_summary": summary,
    }


def build_analyze_coaching_md(
    gate_result: Dict[str, Any],
    outputs: Dict[str, Any],
) -> str:
    status = gate_result.get("status")
    failed = gate_result.get("failed_rules") or []
    conditional = gate_result.get("conditional_rules") or []

    gaps: List[str] = []
    actions: List[str] = []

    if "A1_no_root_causes_identified" in failed:
        gaps.append("No root causes identified yet.")
        actions.append("Use fishbone/5-Why to identify potential causes from the Measure baseline.")
    if "A2_no_verified_root_causes" in failed:
        gaps.append("Root causes listed but none verified with data.")
        actions.append("Verify each root cause: collect data or run hypothesis test to confirm it drives Y.")
    if "A3_no_pareto_prioritization" in failed:
        gaps.append("No Pareto/prioritization of causes.")
        actions.append("Rank causes by contribution % or impact. Focus on vital few (80/20).")
    if "A4_no_root_cause_summary" in failed:
        gaps.append("Root cause summary missing.")
        actions.append("Write a 2-3 sentence summary: what is the confirmed root cause and how does it drive Y?")
    if "B1_pfea_not_completed" in conditional:
        gaps.append("PFEA not completed.")
        actions.append("Complete Process Failure Effect Analysis for the confirmed root cause area.")
    if "B2_process_map_missing" in conditional:
        gaps.append("Process map not documented.")
        actions.append("Document the process steps where root cause occurs (PFD/value stream).")
    if "B3_data_collection_plan_missing" in conditional:
        gaps.append("Data collection plan for verification missing.")
        actions.append("Define how you collected/will collect data to verify each root cause.")

    md = [f"### ANALYZE Gate Status: **{status}**"]
    md.append(f"- {gate_result.get('agent_summary', '')}")

    if gaps:
        md.append("\n### Gaps")
        md.extend([f"- {g}" for g in gaps])
    if actions:
        md.append("\n### Actions (ordered)")
        md.extend([f"{i+1}. {a}" for i, a in enumerate(actions)])
    if status == "FAILED":
        md.append("\n### Risk")
        md.append("- Proceeding to Improve without verified root causes leads to solutions that don't fix the problem.")

    return "\n".join(md).strip()


# ======================================================
# 1. PERCEPTION
# ======================================================

def perception_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    measure_final: Optional[Dict[str, Any]] = None,
    define_final: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    industry    = user_inputs.get("industry")
    pain_theme  = user_inputs.get("pain_theme")
    process_area = user_inputs.get("process_area")

    similar_memory = []
    retrieve_fn = getattr(memory, "retrieve_similar_analyze_episodes", None)
    if callable(retrieve_fn):
        try:
            similar_memory = retrieve_fn(industry, pain_theme, k=3)
        except Exception:
            pass

    # Extract key context from upstream phases
    measure_outs = (measure_final or {}).get("outputs") or {}
    define_outs  = (define_final  or {}).get("outputs") or {}
    define_ins   = (define_final  or {}).get("inputs")  or {}

    return {
        "project_id":     project_id,
        "industry":       industry,
        "pain_theme":     pain_theme,
        "process_area":   process_area,
        "problem":        define_outs.get("problem_statement") or define_ins.get("problem_text") or "",
        "goal":           define_outs.get("goal_statement") or define_ins.get("goal_text") or "",
        "y_variable":     measure_outs.get("y_variable_confirmed") or define_outs.get("y_variable") or "",
        "ctq_list":       define_outs.get("ctq_list") or [],
        "baseline_mean":  (measure_outs.get("process_performance_summary") or {}).get("summary", {}).get("mean"),
        "baseline_std":   (measure_outs.get("process_performance_summary") or {}).get("summary", {}).get("std"),
        "dq_issues":      (measure_outs.get("data_quality_results") or {}).get("consistency", {}).get("issues") or [],
        "user_inputs":    user_inputs,
        "similar_memory": similar_memory[:2],
    }


# ======================================================
# 2. DECISION
# ======================================================

def decision_step(
    perception: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt = f"""
DECISION STEP — ANALYZE PHASE
You are a Lean Six Sigma Master Black Belt.

Given the perception state below, decide the analysis strategy:
- Which analysis tools are most appropriate (Fishbone, 5-Why, PFEA, Pareto, Regression)?
- What are the most likely root cause categories (6M: Man, Machine, Method, Material, Measurement, Mother Nature)?
- What data/evidence is needed to verify root causes?
- What is the priority order for investigation?

Return JSON only:
{{
  "analysis_strategy": "...",
  "recommended_tools": ["..."],
  "priority_cause_categories": ["..."],
  "verification_approach": "...",
  "focus_process_area": "...",
  "mode": "standard" or "quick"
}}

Perception:
{json.dumps({k: perception.get(k) for k in ['industry','pain_theme','process_area','problem','goal','y_variable','baseline_mean']}, indent=2)}

User feedback:
{json.dumps(user_feedback or {}, indent=2)}
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
ACTION STEP — ANALYZE PHASE
You are a Lean Six Sigma Master Black Belt.

Generate a complete ANALYZE phase package.
Mode: {mode} (if 'quick': be concise but still cover all required elements)

Return JSON with this exact structure:
{{
  "process_map_notes": "Brief description of the process steps where the problem occurs",
  "fishbone": {{
    "Man":         ["cause1", "cause2"],
    "Machine":     ["cause1"],
    "Method":      ["cause1", "cause2"],
    "Material":    ["cause1"],
    "Measurement": ["cause1"],
    "Environment": ["cause1"]
  }},
  "potential_causes": [
    {{"cause": "...", "category": "6M category", "hypothesis": "If X then Y because..."}}
  ],
  "pfea": [
    {{
      "process_step": "...",
      "failure_mode": "...",
      "effect": "...",
      "severity": 1-10,
      "occurrence": 1-10,
      "detection": 1-10,
      "rpn": (severity x occurrence x detection),
      "recommended_action": "..."
    }}
  ],
  "pareto_analysis": {{
    "method": "contribution-based or frequency-based",
    "vital_few": ["top cause 1", "top cause 2"],
    "trivial_many": ["other causes"],
    "80_20_insight": "..."
  }},
  "root_causes": [
    {{
      "cause": "...",
      "category": "...",
      "evidence": "...",
      "verified": true/false,
      "verification_method": "...",
      "contribution_pct": 0-100
    }}
  ],
  "data_collection_plan": {{
    "what": "...",
    "how": "...",
    "who": "...",
    "when": "...",
    "sample_size": "..."
  }},
  "root_cause_summary": "2-3 sentence summary of confirmed root cause(s) and how they drive Y",
  "hypotheses_tested": [
    {{"hypothesis": "...", "result": "confirmed/rejected/inconclusive", "evidence": "..."}}
  ],
  "risks_and_assumptions": ["..."]
}}

Context:
Problem: {perception.get('problem')}
Goal: {perception.get('goal')}
Y Variable: {perception.get('y_variable')}
Baseline mean: {perception.get('baseline_mean')}
Industry: {perception.get('industry')}
Process Area: {perception.get('process_area')}
Pain Theme: {perception.get('pain_theme')}

User inputs:
{json.dumps(user_inputs, indent=2)}

Decisions:
{json.dumps(decisions, indent=2)}

User feedback:
{json.dumps(user_feedback or {}, indent=2)}
""".strip()

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw) or {"llm_raw": raw}

    return parsed


# ======================================================
# 4. SUMMARY BUILDER
# ======================================================

def _build_analyze_summary_md(analyze_state: Dict[str, Any]) -> str:
    outputs = analyze_state.get("outputs") or {}
    gate    = analyze_state.get("gate_result") or {}
    pid     = analyze_state.get("project_id", "-")

    root_causes = outputs.get("root_causes") or []
    verified    = [c for c in root_causes if isinstance(c, dict) and c.get("verified")]
    rca_summary = outputs.get("root_cause_summary") or "-"
    pareto      = outputs.get("pareto_analysis") or {}
    vital_few   = pareto.get("vital_few") or []

    md = [f"## ANALYZE — {pid}"]
    md.append(f"**Gate Status:** {gate.get('status', '-')}\n")
    md.append("### Root Cause Summary")
    md.append(rca_summary + "\n")

    if vital_few:
        md.append("### Vital Few Causes (Pareto)")
        md.extend([f"- {c}" for c in vital_few])
        md.append("")

    if verified:
        md.append("### Verified Root Causes")
        for c in verified:
            pct = c.get("contribution_pct", "")
            pct_str = f" ({pct}%)" if pct else ""
            md.append(f"- **{c.get('cause','')}**{pct_str} — {c.get('evidence','')}")

    coaching = (analyze_state.get("coaching_md") or "").strip()
    if coaching:
        md.append("\n### Agent Guidance")
        md.append(coaching)

    return "\n".join(md).strip()


# ======================================================
# MAIN ENTRYPOINT — DRAFT
# ======================================================

def run_analyze_agent(
    project_id: str,
    user_inputs: Dict[str, Any],
    define_final: Optional[Dict[str, Any]] = None,
    measure_final: Optional[Dict[str, Any]] = None,
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, measure_final, define_final)
    decisions  = decision_step(perception, user_feedback)
    outputs    = action_step(project_id, user_inputs, perception, decisions, user_feedback)

    gate_result  = evaluate_analyze_gate(outputs, user_inputs)
    coaching_md  = build_analyze_coaching_md(gate_result, outputs)

    analyze_state: Dict[str, Any] = {
        "schema_version": ANALYZE_SCHEMA,
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
        },
        "perception_trace": {
            "industry":     perception.get("industry"),
            "pain_theme":   perception.get("pain_theme"),
            "y_variable":   perception.get("y_variable"),
        },
        "decision_trace": decisions,
        "user_feedback":  user_feedback or {},
    }

    analyze_state["summary_md"] = _build_analyze_summary_md(analyze_state)

    save_fn = getattr(memory, "save_analyze_draft", None)
    if callable(save_fn):
        save_fn(project_id, analyze_state)

    audit.log_phase_event(project_id, PHASE_NAME, "draft_generated", meta={
        "gate_status": gate_result.get("status"),
    })

    return {
        "status":        "draft",
        "analyze_state": analyze_state,
        "summary":       analyze_state["summary_md"],
        "message":       "Analyze draft generated.",
    }


# ======================================================
# FINALIZE — NO LLM
# ======================================================

def finalize_analyze_agent(
    project_id: str,
    analyze_state: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    gate = (analyze_state or {}).get("gate_result") or {}
    if gate.get("status") == "FAILED":
        return {
            "status":        "blocked",
            "reason":        "ANALYZE gate FAILED — fix critical items before finalizing.",
            "gate":          gate,
            "analyze_state": analyze_state,
        }

    final_state = copy.deepcopy(analyze_state)
    final_state["status"]     = "final"
    final_state["updated_at"] = _now_iso()
    final_state.setdefault("created_at", _now_iso())
    final_state["summary_md"] = final_state.get("summary_md") or _build_analyze_summary_md(final_state)

    save_fn = getattr(memory, "save_analyze_final", None)
    if callable(save_fn):
        save_fn(project_id, final_state)

    delete_fn = getattr(memory, "delete_analyze_draft", None)
    if callable(delete_fn):
        delete_fn(project_id)

    # Save episode for few-shot memory
    episode_fn = getattr(memory, "save_analyze_episode", None)
    if callable(episode_fn):
        episode_fn(project_id, final_state, user_feedback)

    audit.log_phase_event(project_id, PHASE_NAME, "finalized", meta={
        "gate_status":   gate.get("status"),
        "failed_rules":  gate.get("failed_rules", []),
    })

    return {
        "status":        "finalized",
        "analyze_state": final_state,
        "summary":       final_state["summary_md"],
        "message":       "Analyze finalized (locked).",
    }