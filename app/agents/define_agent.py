from __future__ import annotations
import json
import re

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from app.utils import (
    data_processing as dp,
    memory,
    audit,
    reporting,
    llm_engine,
)

PHASE_NAME = "define"
DEFINE_SCHEMA = "DEFINE_SCHEMA_V3_STAGEGATE"

# ======================================================
# Helpers
# ======================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def llm(prompt: str) -> str:
    return llm_engine.complete(prompt)

def extract_json_from_llm(raw: str) -> Optional[Dict[str, Any]]:
    """
    Extract JSON object from LLM output safely.
    Accepts:
      - raw JSON
      - JSON inside fenced ```json ... ```
      - text with a JSON object embedded
    """
    if not raw:
        return None

    # Try fenced json
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # Try first {...}
    m2 = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if m2:
        blob = m2.group(1)
        try:
            return json.loads(blob)
        except Exception:
            pass
    return None

def _as_list_str(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, str):
        s = x.strip()
        return [s] if s else []
    if isinstance(x, list):
        out = []
        for it in x:
            if isinstance(it, str):
                s = it.strip()
                if s:
                    out.append(s)
        return out
    return []

def _smart_check(text: str) -> Dict[str, Any]:
    """
    Lightweight SMART sanity check (rules-based, not LLM).
    We focus on the critical parts for gate:
      - measurable indicator (numbers/%/unit)
      - time-bound indicator (date/quarter/month/week)
    """
    t = (text or "").strip().lower()
    if not t:
        return {"ok": False, "reasons": ["empty"]}

    measurable = bool(re.search(r"(\d+(\.\d+)?\s*(%|percent|hari|days|jam|hours|ton|kg|ppm|mio|million))", t)) \
                 or bool(re.search(r"\d+(\.\d+)?", t)) \
                 or bool(re.search(r"\b(increase|decrease|reduce|improve)\b", t))

    # Time-bound: detect month names + year, quarters, ranges, explicit dates, or time keywords
    timebound = bool(re.search(
        r"\b("
        r"q[1-4]|quarter|"
        r"bulan|month|minggu|week|tahun|year|"
        r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?|"
        r"by\s+\d{4}|"
        r"\d{4}\s*[-–—]\s*\d{4}|"
        r"\d{4}-\d{2}-\d{2}"
        r")\b", t
    ))

    reasons = []
    if not measurable:
        reasons.append("not clearly measurable")
    if not timebound:
        reasons.append("not clearly time-bound")

    ok = measurable and timebound
    return {"ok": ok, "reasons": reasons}


# ======================================================
# Stage Gate — Rule Formal (Tahap 1)
# ======================================================

_MONTHS = r"(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?)"
_YEAR = r"(19|20)\d{2}"

def has_measurable(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"\d+(\.\d+)?\s*(%|ppm|ppb|hours?|days?|cases?)", text, re.I)) \
        or bool(re.search(r"\d+(\.\d+)?", text))

def has_timebound(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(fr"{_MONTHS}\s+{_YEAR}", text, re.I)) \
        or bool(re.search(fr"{_YEAR}\s*[-–]\s*{_YEAR}", text)) \
        or bool(re.search(r"\b(by|before|after|within|from|to|until|between)\b", text, re.I)) \
        or bool(re.search(fr"\b{_YEAR}\b", text))


def evaluate_define_gate(
    outputs: Dict[str, Any],
    gate_evidence: Dict[str, Any],
    user_inputs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    return {
    "status": status,
    "failed_rules": failed,
    "conditional_rules": conditional,
    "agent_summary": agent_summary,
    "debug_smart": {
        "problem_checked": problem,
        "goal_checked": goal,
        "problem_ok": p_ok,
        "goal_ok": g_ok,
        "problem_reasons": _smart_check(problem)["reasons"],
        "goal_reasons": _smart_check(goal)["reasons"],
        "user_inputs_keys": sorted(list(user_inputs.keys())),
        "user_problem_text_present": bool(user_inputs.get("problem_text")),
        "user_goal_text_present": bool(user_inputs.get("goal_text")),
        },
    }

    """
    failed: List[str] = []
    conditional: List[str] = []

    # Prefer user inputs for SMART check (do not validate on rewritten outputs)
    # SMART gate validates ONLY original user inputs (single source of truth)
    problem = (user_inputs.get("problem_text") or user_inputs.get("problem") or "").strip()
    goal    = (user_inputs.get("goal_text") or user_inputs.get("goal") or "").strip()

    p_ok = _smart_check(problem)["ok"]
    g_ok = _smart_check(goal)["ok"]

    # DEBUG: capture exactly what gate validated (remove after fixed)
    debug_smart = {
    "problem_checked": problem,
    "goal_checked": goal,
    "problem_ok": p_ok,
    "goal_ok": g_ok,
    "problem_reasons": _smart_check(problem)["reasons"],
    "goal_reasons": _smart_check(goal)["reasons"],
    "user_inputs_keys": sorted(list(user_inputs.keys())),
    "user_problem_text_present": bool(user_inputs.get("problem_text")),
    "user_goal_text_present": bool(user_inputs.get("goal_text")),
}


    if not p_ok:
        failed.append("A1_problem_not_smart_enough")
    if not g_ok:
        failed.append("A1_goal_not_smart_enough")

    # A1: Charter + SMART framing
    charter_confirmed = bool(gate_evidence.get("charter_confirmed"))
    if not charter_confirmed:
        failed.append("A1_charter_not_confirmed")

    # A2: SIPOC focus & boundary
    sipoc = outputs.get("sipoc") or {}
    # minimal SIPOC validation: customers + outputs exist, process not empty
    sipoc_customers = sipoc.get("Customers") or sipoc.get("customers") or []
    sipoc_outputs = sipoc.get("Outputs") or sipoc.get("outputs") or []
    sipoc_process = sipoc.get("Process") or sipoc.get("process") or []
    if not (isinstance(sipoc_customers, list) and len(sipoc_customers) > 0):
        failed.append("A2_sipoc_missing_customers")
    if not (isinstance(sipoc_outputs, list) and len(sipoc_outputs) > 0):
        failed.append("A2_sipoc_missing_outputs")
    if not (isinstance(sipoc_process, list) and len(sipoc_process) > 0):
        failed.append("A2_sipoc_missing_process")

    # A3: VOC/VOB -> CTQ/CTB traceability & measurability intent
    ctq_list = outputs.get("ctq_list") or outputs.get("ctq") or []
    approved_ctq_count = 0
    if isinstance(ctq_list, list):
        for ctq in ctq_list:
            if isinstance(ctq, dict):
                name = (ctq.get("name") or "").strip()
                metric = (ctq.get("metric") or ctq.get("measure") or "").strip()
                unit = (ctq.get("unit") or "").strip()
                # minimum "measurable intent": has name and (metric or unit)
                if name and (metric or unit):
                    approved_ctq_count += 1
            elif isinstance(ctq, str):
                # if it's string only, it's weak (not measurable intent)
                pass

    if approved_ctq_count < 1:
        failed.append("A3_no_measurable_ctq_defined")

    # B1: Similar project history
    if gate_evidence.get("similar_project_exists") is True:
        note = (gate_evidence.get("similar_project_note") or "").strip()
        if not note:
            conditional.append("B1_similar_project_exists_no_review_note")

    # B2: Parallel/conflicting projects
    parallel_risk = (gate_evidence.get("parallel_projects_risk") or "").strip()
    if parallel_risk:
        conditional.append("B2_parallel_projects_risk_noted")

    # B3: High-level benefit estimation
    benefit = (gate_evidence.get("benefit_estimate") or "").strip()
    if not benefit:
        conditional.append("B3_benefit_estimate_missing")

    # Decide status
    if failed:
        status = "FAILED"
    elif conditional:
        status = "CONDITIONAL"
    else:
        status = "PASSED"

    # Short agent summary
    if status == "FAILED":
        agent_summary = "DEFINE gate FAILED. Fix critical items (A-rules) before proceeding."
    elif status == "CONDITIONAL":
        agent_summary = "DEFINE gate CONDITIONAL. You may finalize, but resolve noted conditions to reduce risk."
    else:
        agent_summary = "DEFINE gate PASSED. DEFINE deliverables are ready and consistent."

    return {
        "status": status,
        "failed_rules": failed,
        "conditional_rules": conditional,
        "agent_summary": agent_summary,
        "debug_smart": debug_smart,
    }


def build_ctq_contract(
    outputs: Dict[str, Any],
    gate_result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Return DEFINE_CTQ_CONTRACT only if gate PASSED/CONDITIONAL.
    """
    status = gate_result.get("status")
    if status not in ("PASSED", "CONDITIONAL"):
        return None

    sipoc = outputs.get("sipoc") or {}
    customers = sipoc.get("Customers") or sipoc.get("customers") or []
    customer = customers[0] if isinstance(customers, list) and customers else ""

    ctq_list = outputs.get("ctq_list") or []
    approved: List[Dict[str, Any]] = []
    if isinstance(ctq_list, list):
        for idx, ctq in enumerate(ctq_list, start=1):
            if not isinstance(ctq, dict):
                continue
            name = (ctq.get("name") or "").strip()
            metric = (ctq.get("metric") or ctq.get("measure") or "").strip()
            unit = (ctq.get("unit") or "").strip()
            desc = (ctq.get("description") or "").strip()
            if not name:
                continue
            if not (metric or unit):
                continue

            approved.append({
                "ctq_id": f"CTQ-{idx:02d}",
                "ctq_name": name,
                "description": desc,
                "customer": ctq.get("customer") or customer,
                "measurable_intent": metric if metric else name,
                "allowed_measurement_type": ctq.get("allowed_measurement_type") or "",
                "assumptions": ctq.get("assumptions") or "",
                "metric": metric,
                "unit": unit,
            })

    return {
        "status": "ACTIVE",
        "approved_ctq_list": approved,
        "note": "CTQs are approved for next phase only within this contract.",
    }


def build_define_coaching_summary_md(
    gate_result: Dict[str, Any],
    outputs: Dict[str, Any],
    gate_evidence: Dict[str, Any],
    prior_quick_wins: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Deterministic coaching summary (rules-based). No LLM.
    """
    status = gate_result.get("status")
    failed = gate_result.get("failed_rules") or []
    conditional = gate_result.get("conditional_rules") or []

    strengths: List[str] = []
    gaps: List[str] = []
    next_actions: List[str] = []
    risks: List[str] = []

    # strengths heuristics
    if (outputs.get("problem_statement") or "").strip():
        strengths.append("Problem statement is documented.")
    if (outputs.get("goal_statement") or "").strip():
        strengths.append("Goal statement is documented.")
    if outputs.get("sipoc"):
        strengths.append("SIPOC is provided.")
    if isinstance(outputs.get("ctq_list"), list) and len(outputs.get("ctq_list")) > 0:
        strengths.append("CTQ/CTB list is provided.")

    # gaps based on rules
    if "A1_charter_not_confirmed" in failed:
        gaps.append("Project charter is not confirmed (missing user confirmation).")
        next_actions.append("Confirm project charter setup (checkbox) before finalize.")

    if "A1_problem_not_smart_enough" in failed:
        gaps.append("Problem statement is not SMART enough (measurable and/or time-bound evidence is missing).")
        next_actions.append("Add/clarify the measurable metric (number + unit) and the timeframe (date/period) without changing the core problem.")

    if "A1_goal_not_smart_enough" in failed:
        gaps.append("Goal statement is not SMART enough (measurable and/or time-bound evidence is missing).")
        next_actions.append("Add/clarify numeric target + deadline (keep scope/metric consistent with the problem).")

    if "A2_sipoc_missing_customers" in failed or "A2_sipoc_missing_outputs" in failed or "A2_sipoc_missing_process" in failed:
        gaps.append("SIPOC is incomplete or inconsistent (Customers/Outputs/Process missing).")
        next_actions.append("Complete SIPOC focusing on ONE core process and correct customers/outputs.")
    if "A3_no_measurable_ctq_defined" in failed:
        gaps.append("No measurable CTQ/CTB defined (CTQ needs metric and/or unit).")
        next_actions.append("Convert VOC/VOB into measurable CTQ (name + metric + unit).")

    # conditional coaching
    if "B1_similar_project_exists_no_review_note" in conditional:
        gaps.append("Similar project exists but not reviewed/documented.")
        next_actions.append("Add note: what to reuse vs what differs from prior project.")
    if "B2_parallel_projects_risk_noted" in conditional:
        risks.append("Parallel/overlapping projects may affect scope, data, or ownership.")
    if "B3_benefit_estimate_missing" in conditional:
        gaps.append("High-level benefit estimate is missing.")
        next_actions.append("Add benefit estimate (type + order of magnitude).")

    # risks based on status
    if status == "FAILED":
        risks.append("Proceeding without fixing critical gate items will invalidate later phases.")
    elif status == "CONDITIONAL":
        risks.append("Proceeding with conditions unresolved increases rework risk.")

    # quick wins: only from prior projects if available
    quick_wins_md = ""
    if prior_quick_wins:
        lines = []
        for qw in prior_quick_wins[:3]:
            title = (qw.get("quick_win_title") or "").strip()
            rationale = (qw.get("rationale") or "").strip()
            if not title:
                continue
            if rationale:
                lines.append(f"- **{title}** — {rationale}")
            else:
                lines.append(f"- **{title}**")
        if lines:
            quick_wins_md = "\n### Quick Wins (from prior projects)\n" + "\n".join(lines)

    # Build MD
    md = []
    md.append(f"### DEFINE Gate Status: **{status}**")
    md.append(f"- {gate_result.get('agent_summary','').strip()}")

    if strengths:
        md.append("\n### Strengths")
        md.extend([f"- {s}" for s in strengths[:4]])

    if gaps:
        md.append("\n### Gaps")
        md.extend([f"- {g}" for g in gaps[:6]])

    if next_actions:
        md.append("\n### What to fix next (ordered)")
        md.extend([f"{i+1}. {a}" for i, a in enumerate(next_actions[:6])])

    if risks:
        md.append("\n### Risks if proceed")
        md.extend([f"- {r}" for r in risks[:3]])

    if quick_wins_md:
        md.append(quick_wins_md)

    return "\n".join(md).strip()


def _extract_prior_quick_wins(similar_memory_episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pull quick wins only if they exist in prior saved episodes.
    If none exist, return [] (do not invent).
    """
    out: List[Dict[str, Any]] = []
    for ep in similar_memory_episodes or []:
        # episodes may be define_state directly or wrapped
        st = ep.get("define_state") if isinstance(ep, dict) and "define_state" in ep else ep
        if not isinstance(st, dict):
            continue
        qws = st.get("quick_wins") or st.get("define_quick_wins") or []
        if isinstance(qws, list):
            for q in qws:
                if isinstance(q, dict) and q.get("quick_win_title"):
                    out.append(q)
    return out


# ======================================================
# 1. PERCEPTION
# ======================================================

def perception_step(project_id: str, user_inputs: Dict[str, Any], baseline_df=None) -> Dict[str, Any]:
    industry = user_inputs.get("industry")
    pain_theme = user_inputs.get("pain_theme")
    process_area = user_inputs.get("process_area")

    similar_audit = audit.get_similar_audit_events(
        industry=industry,
        pain_theme=pain_theme,
        process_area=process_area,
        phase=PHASE_NAME,
        k=3,
    )

    similar_memory = memory.retrieve_similar_define_episodes(
        industry,
        pain_theme,
        k=3,
    )

    perception = dp.build_perception_state(
        user_inputs=user_inputs,
        audit_episodes=similar_audit,
        memory_episodes=similar_memory,
        baseline_df=baseline_df,
    )
    return perception


# ======================================================
# 2. DECISION
# ======================================================

def decision_step(perception: Dict[str, Any], user_feedback=None) -> Dict[str, Any]:
    prompt = f"""
DECISION STEP — DEFINE PHASE

You are a Lean Six Sigma Master Black Belt.
Given the perception state, produce decisions to structure DEFINE work.

Return JSON with keys:
- focus_ctq_strategy
- y_variable_candidate
- scope_boundary
- sipoc_focus
- risks
- notes_for_action

Perception (trimmed):
{json.dumps({k: perception.get(k) for k in ['industry','pain_theme','process_area','key_issue'] if k in perception}, indent=2)}

User feedback (optional):
{json.dumps(user_feedback or {}, indent=2)}
""".strip()

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    return parsed if parsed else {"llm_raw": raw}


# ======================================================
# 3. ACTION (build canonical define_state)
# ======================================================

def action_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    perception: Dict[str, Any],
    decisions: Dict[str, Any],
    user_feedback: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    similar_memory = memory.retrieve_similar_define_episodes(
        user_inputs.get("industry"),
        user_inputs.get("pain_theme"),
        k=2,
    )

    prompt = f"""
ACTION STEP — DEFINE PHASE

you are a Lean Six Sigma Master Black Belt.
Create DEFINE outputs that answer:
- Why is this project important? (business case with context and impact from user input on problem statement)
- What is the problem? (from user input on problem statement, compose as SMART: Specific, Measureable, Agreed to, Realistic, Time bound statement
- What is the goal? (mirror problem but with target state, also SMART)
- What is the scope?
- SIPOC (high level, in Process use verbs)
- VOC/VOB analysis: dari voice of customer/business, ekstrak Key Issue, lalu translate ke CTQ (measurable customer requirement) atau CTB (measurable business target)
- CTQ/CTB harus MEASURABLE — harus punya nama, metric, unit, dan target arah (reduce/increase)
- Y Variable adalah CTQ atau CTB yang paling langsung merepresentasikan problem dan goal — SATU variable utama, spesifik, measurable
- Y Variable harus konsisten dengan goal statement (kalau goal bilang "reduce X dari A% ke B%", maka Y = X dengan unit %)
- Jangan buat Y Variable yang generik seperti "total supply chain cost" kalau problem spesifik tentang "% shipments with extra charges"
- Risk level & project type

Return JSON with this structure:
{{
  "business_case": "...",  
  "problem_statement": "...",
  "goal_statement": "...",
  "project_scope": {{
     "in_scope": [...],
     "out_of_scope": [...]
  }},
  "sipoc": {{
     "Suppliers": [...],
     "Inputs": [...],
     "Process": [...],
     "Outputs": [...],
     "Customers": [...]
  }},
  "ctq_list": [
     {{"name":"...", "metric":"...", "unit":"...", "description":"...", "customer":"..."}}
  ],
    \"y_variable\": \"...\",
    \"risk_level\": \"...\",
    \"project_type\": \"...\",
    \"benefit_estimate\": {{
        \"benefit_potentials\": \"...\",
        \"calculation_method\": \"...\",
        \"kpi_table\": [
        {{
            \"no\": 1,
            \"kpi\": \"...\",
            \"period\": \"...\",
            \"unit\": \"...\",
            \"baseline\": \"...\",
            \"target\": \"...\",
            \"delta_pct\": \"...\",
            \"comment\": \"...\"
        }}
        ],
        \"assumptions\": [\"...\"],
        \"soft_benefits\": [\"...\"]
    }}
    }}
  
User inputs:
{json.dumps(user_inputs, indent=2)}

Decisions:
{json.dumps(decisions, indent=2)}

Few-shot examples (if any):
{json.dumps(similar_memory[:2], indent=2)}
""".strip()

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    if not parsed:
        parsed = {"llm_raw": raw}

    # --- Build canonical schema ---
    gate_evidence = {
        "charter_confirmed": bool(user_inputs.get("charter_confirmed")),
        "similar_project_exists": bool(user_inputs.get("similar_project_exists")) if user_inputs.get("similar_project_exists") is not None else None,
        "similar_project_note": (user_inputs.get("similar_project_note") or "").strip(),
        "parallel_projects_risk": (user_inputs.get("parallel_projects_risk") or "").strip(),
        "benefit_estimate": (user_inputs.get("benefit_estimate") or "").strip(),
        "documentation_agreed": bool(user_inputs.get("documentation_agreed")) if user_inputs.get("documentation_agreed") is not None else None,
    }

    outputs = parsed if isinstance(parsed, dict) else {}

    gate_result = evaluate_define_gate(outputs=outputs, gate_evidence=gate_evidence, user_inputs=user_inputs)
    ctq_contract = build_ctq_contract(outputs=outputs, gate_result=gate_result)

    prior_quick_wins = _extract_prior_quick_wins(similar_memory)
    coaching_md = build_define_coaching_summary_md(
        gate_result=gate_result,
        outputs=outputs,
        gate_evidence=gate_evidence,
        prior_quick_wins=prior_quick_wins if prior_quick_wins else None,
    )

    define_state: Dict[str, Any] = {
        "project_id": project_id,
        "phase": PHASE_NAME,
        "status": "draft",
        "schema_version": DEFINE_SCHEMA,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "inputs": dict(user_inputs),
        "outputs": outputs,
        "gate_evidence": gate_evidence,
        "gate_result": gate_result,
        "ctq_contract": ctq_contract,
        "insight_md": coaching_md,
                "coaching_md": coaching_md,
        "quick_wins": prior_quick_wins if prior_quick_wins else [],

        # traces for debug (UI should trim/hide in JSON tab)
        "perception_trace": {
            "industry": perception.get("industry"),
            "pain_theme": perception.get("pain_theme"),
            "process_area": perception.get("process_area"),
        },
        "decision_trace": decisions,
        "user_feedback": user_feedback or {},
    }

    # summary md should be deterministic from state
    define_state["summary_md"] = _build_define_summary_md(define_state)

    return define_state


# ======================================================
# 4. SUMMARY BUILDER (fallback-safe)
# ======================================================

def _build_define_summary_md(define_state: Dict[str, Any]) -> str:
    """
    Prefer reporting.build_define_summary_md, fallback to legacy,
    else build minimal summary from define_state.
    """
    fn = getattr(reporting, "build_define_summary_md", None)
    if callable(fn):
        try:
            out = fn(define_state)
            if isinstance(out, str) and out.strip():
                return out
        except Exception:
            pass

    fn_legacy = getattr(reporting, "build_define_summary_markdown", None)
    if callable(fn_legacy):
        try:
            out = fn_legacy(define_state)
            if isinstance(out, str) and out.strip():
                return out
        except Exception:
            pass

    # Minimal fallback
    outputs = define_state.get("outputs") or {}
    gate = define_state.get("gate_result") or {}
    coaching_md = (define_state.get("coaching_md") or define_state.get("insight_md") or "").strip()


    md = []
    md.append(f"## DEFINE — {define_state.get('project_id','')}")
    md.append(f"**Gate Status:** {gate.get('status','-')}")
    md.append("")
    md.append("### Problem")
    md.append(outputs.get("problem_statement") or "-")
    md.append("\n### Goal")
    md.append(outputs.get("goal_statement") or "-")
    return "\n".join(md).strip()


# ======================================================
# RUN / FINALIZE (NO LLM ON FINALIZE)
# ======================================================

def run_define_agent(
    project_id: str,
    user_inputs: Dict[str, Any],
    baseline_df=None,
    user_feedback=None,
):
    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, baseline_df)
    decisions = decision_step(perception, user_feedback=user_feedback)
    draft_state = action_step(project_id, user_inputs, perception, decisions, user_feedback=user_feedback)

    # Refresh timestamp
    if isinstance(draft_state, dict):
        draft_state["updated_at"] = _now_iso()

    # Persist draft
    if hasattr(memory, "save_define_draft"):
        memory.save_define_draft(project_id, draft_state)

    summary = draft_state.get("summary_md") or _build_define_summary_md(draft_state)

    audit.log_phase_event(project_id, PHASE_NAME, "draft_generated", meta={
        "gate_status": (draft_state.get("gate_result") or {}).get("status"),
    })

    return {
        "status": "draft",
        "define_state": draft_state,
        "summary": summary,
        "message": "Draft generated.",
    }


def finalize_define_agent(
    project_id: str,
    define_state: Dict[str, Any],
    user_feedback: Dict[str, Any] | None = None,
):
    """
    Finalize DEFINE from an accepted draft.
    MUST NOT call LLM / must not regenerate.
    """
    final_state = dict(define_state) if isinstance(define_state, dict) else {}
    # Reset approvals kalau ini adalah re-finalize setelah reject
    reset_fn = getattr(memory, "reset_approvals", None)
    if callable(reset_fn):
        reset_fn(project_id, PHASE_NAME)
    final_state["project_id"] = project_id
    final_state["phase"] = PHASE_NAME
    final_state["status"] = "final"
    final_state["schema_version"] = final_state.get("schema_version") or DEFINE_SCHEMA
    final_state.setdefault("created_at", _now_iso())
    final_state["updated_at"] = _now_iso()

    # Lock: keep gate_result as-is (do not recompute)
    # Ensure summary exists
    final_state["summary_md"] = _build_define_summary_md(final_state)
    # Persist final and remove draft
    if hasattr(memory, "save_define_final"):
        memory.save_define_final(project_id, final_state)
    if hasattr(memory, "delete_define_draft"):
        memory.delete_define_draft(project_id)

    audit.log_phase_event(project_id, PHASE_NAME, "finalized", meta={
        "gate_status": (final_state.get("gate_result") or {}).get("status"),
        "failed_rules": (final_state.get("gate_result") or {}).get("failed_rules", []),
        "conditional_rules": (final_state.get("gate_result") or {}).get("conditional_rules", []),
    })

    return {
        "status": "finalized",
        "define_state": final_state,
        "critique": [],
        "summary": final_state["summary_md"],
        "message": "Define finalized (locked).",
    }
