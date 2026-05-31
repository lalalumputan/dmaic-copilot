# app/agents/improve_agent.py
from __future__ import annotations

import json
import re
import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import memory, audit, reporting, llm_engine
from app.agents.classification_agent import get_gate_mode

PHASE_NAME     = "Improve"
IMPROVE_SCHEMA = "v1.1"


# ======================================================
# Helpers
# ======================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def llm(prompt: str) -> str:
    try:
        return llm_engine.complete(prompt)
    except Exception:
        return ""

def extract_json_from_llm(raw: str):
    if not raw:
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except Exception: pass
    m2 = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if m2:
        try: return json.loads(m2.group(1))
        except Exception: pass
    return None

def extract_json_array_from_llm(raw: str):
    if not raw:
        return None
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except Exception: pass
    return None


def _extract_upstream_context(
    define_final:  Optional[Dict],
    measure_final: Optional[Dict],
    analyze_final: Optional[Dict],
) -> Dict[str, Any]:
    def_outs  = (define_final  or {}).get("outputs") or {}
    def_ins   = (define_final  or {}).get("inputs")  or {}
    mea_outs  = (measure_final or {}).get("outputs") or {}
    ana_outs  = (analyze_final or {}).get("outputs") or {}
    perf_sum  = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}
    target    = (mea_outs.get("process_performance_summary") or {}).get("target_info") or {}
    rca_table = ana_outs.get("root_cause_summary_table") or []

    confirmed_rc = [
        r for r in rca_table
        if "Yes" in str(r.get("is_root_cause", ""))
    ]

    return {
        "problem_statement": def_outs.get("problem_statement") or def_ins.get("problem_text") or "",
        "goal_statement":    def_outs.get("goal_statement")    or def_ins.get("goal_text")    or "",
        "y_variable":        mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "",
        "ctq_list":          def_outs.get("ctq_list") or [],
        "baseline_mean":     perf_sum.get("mean"),
        "baseline_target":   target,
        "confirmed_root_causes": confirmed_rc,
        "rca_summary_table":     rca_table,
        "industry":     def_ins.get("industry") or "",
        "process_area": def_ins.get("process_area") or "",
        "pain_theme":   def_ins.get("pain_theme") or "",
        "scope":        def_outs.get("project_scope") or {},
    }


# ======================================================
# LLM FUNCTIONS
# ======================================================

def _propose_must_criteria_llm(
    upstream: dict,
    user_constraints: str = "",
) -> list:
    """Agent propose must criteria — solution constraints agar tidak create new problem."""
    prompt = f"""
You are a Lean Six Sigma Master Black Belt defining solution must criteria for an IMPROVE phase.

Must criteria are constraints that ANY solution MUST fulfill to be considered.
They prevent solutions from creating new problems.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Confirmed root causes: {json.dumps([r.get('root_cause','') for r in upstream.get('confirmed_root_causes',[])], ensure_ascii=False)}
- User constraints: "{user_constraints}"

Generate 5-8 must criteria. Each should be testable (yes/no evaluable).
Categories to consider: Safety, Quality (no new defects), Cost (within budget),
Regulatory/compliance, Operability (can be sustained), Timeline.

Return JSON array:
[
  {{
    "criterion": "specific testable statement",
    "category": "Safety/Quality/Cost/Regulatory/Operability/Timeline/Other",
    "rationale": "why this matters for this specific project"
  }}
]
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_array_from_llm(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def _propose_solutions_llm(
    upstream: dict,
    must_criteria: list,
    user_ideas: str = "",
    project_path: str = "standard",
) -> list:
    """Agent propose potential solutions per confirmed root cause."""
    confirmed_rc = upstream.get("confirmed_root_causes") or []
    criteria_list = [c.get("criterion","") for c in must_criteria] if must_criteria else []
    # Pre-build di luar f-string — hindari unhashable type: dict error (Rule #10)
    rc_summary = [
        {"xn": r.get("xn",""), "root_cause": r.get("root_cause",""), "impact": r.get("impact","")}
        for r in confirmed_rc
    ]

    prompt = f"""
You are a Lean Six Sigma Master Black Belt generating improvement solutions.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Baseline Mean: {upstream.get('baseline_mean')}
- Target: {upstream.get('baseline_target',{}).get('direction','')} {upstream.get('baseline_target',{}).get('value','')}

Confirmed root causes:
{json.dumps(rc_summary, indent=2, ensure_ascii=False)}

Must criteria (all solutions must fulfill ALL of these):
{json.dumps(criteria_list, ensure_ascii=False)}

User solution ideas (incorporate if relevant):
"{user_ideas}"

Generate potential solutions — at least one per confirmed root cause.
For each solution, check all must criteria.

Return JSON array:
[
  {{
    "solution_id": "S1",
    "idea": "specific solution description",
    "addresses_root_cause": "Xn — root cause description",
    "must_criteria_fulfilled": {{"criterion 1": true/false, "criterion 2": true/false}},
    "all_must_criteria_met": true/false,
    "type": "process/technology/behavioral/structural/poka-yoke",
    "investment_needed": "high/medium/low",
    "investment_estimate": "estimated cost range",
    "comments": "implementation notes, risks"
  }}
]
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_array_from_llm(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def _select_solution_llm(
    solutions: list,
    upstream: dict,
    user_selection: str = "",
) -> dict:
    """Agent propose selected solution with justification and CTQ impact."""
    ctq_list = upstream.get("ctq_list") or []
    # Filter solutions that meet all must criteria
    eligible = [s for s in solutions if s.get("all_must_criteria_met", True)]
    pool = eligible if eligible else solutions

    _pool_json = json.dumps(
        [{"id": s.get("solution_id"), "idea": s.get("idea"),
          "addresses": s.get("addresses_root_cause"), "investment": s.get("investment_needed")}
         for s in pool],
        indent=2, ensure_ascii=False
    )
    _ctq_json = json.dumps(
        [c.get("name") or c.get("ctq", "") for c in ctq_list if isinstance(c, dict)][:5],
        ensure_ascii=False
    )

    prompt = f"""
You are a Lean Six Sigma Master Black Belt selecting the best improvement solution.

Project context:
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- CTQ/CTB: {_ctq_json}

Eligible solutions (meet all must criteria):
{_pool_json}

User preference (if any): "{user_selection}"

Select the best solution and justify. If user specified one, validate it.

Return JSON:
{{
  "selected_solution_id": "S1",
  "solution": "solution description",
  "justification": "detailed rationale for selection over others",
  "selection_basis": ["criterion 1 that drove selection", "criterion 2"],
  "ctq_ctb_impacted": [
    {{"ctq": "CTQ name", "expected_improvement": "how this solution improves this CTQ"}}
  ],
  "expected_impact_on_y": "quantitative or qualitative expected improvement on Y",
  "effort_score": 1,
  "benefit_score": 5,
  "priority": "high/medium/low"
}}
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_from_llm(raw)
        if parsed:
            return parsed
    except Exception:
        pass
    return {}


def _build_implementation_plan_llm(
    selected_solution: dict,
    upstream: dict,
    pilot_inputs: dict,
) -> dict:
    """Build pilot plan + implementation plan."""
    prompt = f"""
You are a Lean Six Sigma Master Black Belt building an implementation plan.

Selected solution: "{selected_solution.get('solution','')}"
Justification: "{selected_solution.get('justification','')}"
Expected Y impact: "{selected_solution.get('expected_impact_on_y','')}"

Project:
- Goal: "{upstream.get('goal_statement','')}"
- Process area: "{upstream.get('process_area','')}"

Pilot inputs from user:
{json.dumps(pilot_inputs, indent=2, ensure_ascii=False)}

Return JSON:
{{
  "pilot_plan": {{
    "scope": "where/what will be piloted",
    "duration": "how long",
    "success_criteria": "measurable criteria to declare pilot successful",
    "owner": "who runs the pilot",
    "rollback_plan": "what to do if pilot fails",
    "resources_needed": ["resource 1", "resource 2"]
  }},
  "implementation_plan": [
    {{
      "step": 1,
      "action": "specific action",
      "owner": "responsible person/role",
      "timeline": "week/date",
      "deliverable": "output of this step",
      "dependencies": "what must be done before"
    }}
  ],
  "should_be_process": "Description of the improved process flow after implementation",
  "risks_and_assumptions": ["risk 1", "assumption 1"]
}}
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_from_llm(raw)
        if parsed:
            return parsed
    except Exception:
        pass
    return {}


def _seed_implementation_status(selected_solutions: list, existing: list) -> list:
    """Ensure each selected solution has a status row; preserve existing status/notes."""
    selected_solutions = selected_solutions or []
    existing = existing or []
    by_sol = {str(s.get("solution","")).strip(): s for s in existing}
    seeded = []
    for r in selected_solutions:
        sol = str(r.get("solution","")).strip()
        if not sol:
            continue
        prev = by_sol.get(sol, {})
        seeded.append({
            "solution": sol,
            "status":   prev.get("status", "not started"),
            "notes":    prev.get("notes", ""),
        })
    return seeded


def _build_communication_plan_llm(
    selected_solutions,
    upstream: dict,
    user_comm_inputs: dict,
) -> list:
    """Build communication plan for the improvement.

    selected_solutions: list of selected-solution dicts (no/solution/ctq_ctb_impacted/comments)
    or a single dict (backward compat).
    """
    if isinstance(selected_solutions, dict):
        _sol_text = selected_solutions.get("solution", "")
    elif isinstance(selected_solutions, list):
        _sol_text = "; ".join(
            str(s.get("solution","")).strip()
            for s in selected_solutions if str(s.get("solution","")).strip()
        )
    else:
        _sol_text = str(selected_solutions or "")
    prompt = f"""
You are a Lean Six Sigma Master Black Belt building a communication plan.

Selected solution(s): "{_sol_text}"
Process area: "{upstream.get('process_area','')}"
Industry: "{upstream.get('industry','')}"

User-provided stakeholders/context:
{json.dumps(user_comm_inputs, indent=2, ensure_ascii=False)}

Build a communication plan covering: announcement, training, go-live, and follow-up.

Return JSON array:
[
  {{
    "who_informed": "stakeholder group or person",
    "about_what": "what needs to be communicated",
    "who_informs": "who is responsible for communicating",
    "channel": "email/meeting/memo/training",
    "due_date": "timing (e.g. Week 1, before go-live)",
    "status": "pending"
  }}
]
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_array_from_llm(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


# ======================================================
# GATE EVALUATION
# ======================================================

def _improve_gate_evaluate(
    outputs: dict,
    user_inputs: dict,
    enforce_b_rules: bool = True,
    upstream: dict = None,
) -> dict:
    failed      = []
    conditional = []
    actions     = []

    # A0: confirmed root causes dari Analyze harus ada (always enforced)
    _upstream = upstream or {}
    confirmed_rc_upstream = _upstream.get("confirmed_root_causes") or []
    if not confirmed_rc_upstream:
        failed.append("A0_no_confirmed_root_causes_from_analyze")
        actions.append(
            "Selesaikan fase Analyze terlebih dahulu — minimal 1 root cause harus terkonfirmasi "
            "di Step 7 Verification Results (is_root_cause = Yes) sebelum Improve bisa dimulai."
        )

    # Must criteria → B-rule (skippable oleh user)
    must_criteria = outputs.get("must_criteria") or []
    must_skipped  = bool(user_inputs.get("must_skipped"))
    if not must_criteria and not must_skipped and enforce_b_rules:
        conditional.append("B0_no_must_criteria")
        actions.append("Pertimbangkan menambah must criteria, atau klik Skip Must Criteria jika tidak diperlukan.")

    solutions = outputs.get("potential_solutions") or []
    if not solutions:
        failed.append("A2_no_potential_solutions")
        actions.append("Buat potential solutions — minimal satu per confirmed root cause.")

    selected_solutions = outputs.get("selected_solutions") or []
    if not selected_solutions:
        failed.append("A3_no_selected_solution")
        actions.append("Pilih minimal satu selected solution di Step 4 (Selected Solution).")

    impl = outputs.get("implementation_plan") or []
    if not impl:
        failed.append("A4_no_implementation_plan")
        actions.append("Buat implementation plan dengan steps, owner, dan timeline.")

    pilot = outputs.get("pilot_plan") or {}
    if not pilot or not pilot.get("scope"):
        failed.append("A5_no_pilot_plan")
        actions.append("Definisikan pilot plan: scope, success criteria, rollback.")

    comm = outputs.get("communication_plan") or []
    if not comm:
        failed.append("A6_no_communication_plan")
        actions.append("Buat communication plan: siapa perlu diinformasikan, tentang apa, oleh siapa, kapan.")

    # A7: implementation status harus done semua (hanya berlaku jika ada selected solutions)
    impl_status = outputs.get("implementation_status") or []
    if selected_solutions:
        if not impl_status or not all(
            str(s.get("status","")).strip().lower() == "done" for s in impl_status
        ):
            failed.append("A7_implementation_not_done")
            actions.append("Update Step 8 — semua Implementation Status harus 'done' sebelum fase di-finalize.")

    # B-rules: CTQ impact dari selected_solutions
    if selected_solutions and enforce_b_rules:
        _any_ctq = any(str(s.get("ctq_ctb_impacted","")).strip() for s in selected_solutions)
        if not _any_ctq:
            conditional.append("B1_no_ctq_impact_stated")
            actions.append("Tambahkan CTQ/CTB yang terdampak pada selected solution.")

    should_be = (outputs.get("should_be_process") or "").strip()
    if not should_be and enforce_b_rules:
        conditional.append("B2_no_should_be_process")
        actions.append("Deskripsikan should-be process setelah solusi diimplementasi.")

    if failed:
        status = "FAIL"
        policy = "BLOCK"
    elif conditional and enforce_b_rules:
        status = "WEAK_PASS"
        policy = "PROCEED_WITH_WARNING"
    else:
        status = "PASS"
        policy = "ADVANCE_AND_FREEZE"

    return {
        "status":            status,
        "policy":            policy,
        "failed_rules":      failed,
        "conditional_rules": conditional,
        "next_actions":      actions[:8],
    }


# ======================================================
# LLM COACHING
# ======================================================

def _build_improve_coaching_llm(
    gate: dict,
    outputs: dict,
    upstream: dict,
    user_inputs: dict,
) -> str:
    failed      = gate.get("failed_rules") or []
    conditional = gate.get("conditional_rules") or []
    actions     = gate.get("next_actions") or []
    status      = gate.get("status", "FAIL")

    _confirmed_count = len(upstream.get("confirmed_root_causes", []))
    _a0_blocked = "A0_no_confirmed_root_causes_from_analyze" in failed

    # ── Konten Improve yang akan dikritisi ──
    confirmed_rcs = [
        {"xn": r.get("xn",""), "root_cause": r.get("root_cause",""), "impact": r.get("impact","")}
        for r in (upstream.get("confirmed_root_causes") or [])
    ]
    must_criteria = outputs.get("must_criteria") or []
    solutions     = outputs.get("potential_solutions") or []
    selected      = outputs.get("selected_solutions") or []
    impl          = outputs.get("implementation_plan") or []
    comm          = outputs.get("communication_plan") or []
    should_be     = (outputs.get("should_be_process") or "").strip()

    # Coverage: root cause confirmed vs yang punya solusi
    _covered = {str(s.get("addresses_root_cause","")).strip() for s in solutions}
    _rc_labels = [f"{r['xn']} — {r['root_cause']}".strip(" —") for r in confirmed_rcs]
    _uncovered = [lbl for lbl in _rc_labels if lbl not in _covered]

    _impl_status_brief = (
        "Implementation status BELUM semua 'done' — sebutkan ini SATU kalimat singkat saja, jangan dijelaskan panjang."
        if "A7_implementation_not_done" in failed else
        "Implementation status sudah lengkap — tidak perlu dibahas."
    )

    prompt = f"""
You are a Lean Six Sigma Master Black Belt coaching a project leader on the Improve phase.
Tujuan coaching: meningkatkan PELUANG SUKSES implementasi dengan mengkritisi KUALITAS ISI Improve — bukan sekadar mengingatkan status administratif.

Gate Status: {status}

Context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Industry / Process: "{upstream.get('industry','')}" / "{upstream.get('process_area','')}"

Confirmed root causes ({_confirmed_count}): {json.dumps(confirmed_rcs, ensure_ascii=False)}
Root cause TANPA solusi (coverage gap): {json.dumps(_uncovered, ensure_ascii=False)}

Must criteria: {json.dumps(must_criteria, ensure_ascii=False)}
Potential solutions: {json.dumps(solutions, ensure_ascii=False)}
Selected solutions (solution + CTQ/CTB impacted): {json.dumps(selected, ensure_ascii=False)}
Implementation plan (step/action/owner/timeline): {json.dumps(impl, ensure_ascii=False)}
Communication plan: {json.dumps(comm, ensure_ascii=False)}
Should-be / future state described: {"YA" if should_be else "BELUM"}

{"CRITICAL: A0 FAILED — Analyze belum punya confirmed root cause. Suruh user kembali ke Analyze → Step 7 dan tandai is_root_cause = Yes. JANGAN bahas konten Improve lain." if _a0_blocked else ""}

Kritisi konten secara substantif (utamakan ini, bukan status administratif):
1. **Root cause coverage** — apakah SETIAP confirmed root cause punya solusi yang benar-benar mengatasinya? Tunjuk RC yang belum tercakup.
2. **Solution ↔ root cause logic** — apakah tiap selected solution secara logis menyerang akar masalah, bukan gejala? Apakah memenuhi must criteria?
3. **CTQ/CTB linkage** — apakah dampak solusi ke CTQ/CTB masuk akal dan mengarah ke perbaikan Y?
4. **Implementation plan coherence** — apakah langkah-langkah berurutan logis, punya owner & timeline jelas, dan benar-benar mewujudkan solusi (bukan langkah generik)?
5. **Future state alignment** — apakah proses target benar-benar berubah (eliminasi/otomasi/merge step), bukan hanya mengganti label? Apakah konsisten dengan solusi terpilih?
6. **Communication plan** — apakah stakeholder kunci & momen kritis (go-live, training) tercakup?

{_impl_status_brief}

Aturan output:
- Bahasa Indonesia, markdown dengan ### headings per area di atas yang BERMASALAH (skip area yang sudah baik).
- Kutip isi user (nama solusi/step) saat menunjuk masalah, beri saran konkret untuk {upstream.get('industry','')} / {upstream.get('process_area','')}.
- Maksimal 320 kata. Mulai langsung, tanpa pembuka.
""".strip()
    try:
        raw = llm(prompt)
        if raw and len(raw.strip()) > 80:
            return raw.strip()
    except Exception:
        pass
    lines = [f"### IMPROVE Gate: **{status}**"]
    for a in actions[:6]:
        lines.append(f"- {a}")
    return "\n".join(lines)


# ======================================================
# SUMMARY BUILDER
# ======================================================

def _build_improve_summary_md(improve_state: dict) -> str:
    outputs = improve_state.get("outputs") or {}
    gate    = improve_state.get("gate") or {}
    pid     = improve_state.get("project_id", "-")

    selected_list = outputs.get("selected_solutions") or []
    impl          = outputs.get("implementation_plan") or []
    impl_status   = outputs.get("implementation_status") or []

    md = [f"## IMPROVE — {pid}"]
    md.append(f"**Gate Status:** {gate.get('status','-')}\n")

    if selected_list:
        md.append("### Selected Solutions")
        for s in selected_list:
            _ctq = s.get("ctq_ctb_impacted","")
            md.append(
                f"- **{s.get('solution','-')}**"
                + (f" — CTQ/CTB: {_ctq}" if _ctq else "")
                + (f" ({s.get('comments','')})" if s.get('comments') else "")
            )

    if impl:
        md.append(f"\n**Implementation plan:** {len(impl)} steps defined.")

    if impl_status:
        _done = sum(1 for s in impl_status if str(s.get('status','')).strip().lower() == "done")
        md.append(f"\n**Implementation status:** {_done}/{len(impl_status)} solutions done.")

    return "\n".join(md).strip()


# ======================================================
# PERCEPTION
# ======================================================

def perception_step(
    project_id: str,
    user_inputs: dict,
    define_final:  Optional[dict] = None,
    measure_final: Optional[dict] = None,
    analyze_final: Optional[dict] = None,
) -> dict:
    upstream = _extract_upstream_context(define_final, measure_final, analyze_final)

    similar_memory = []
    retrieve_fn = getattr(memory, "retrieve_similar_improve_episodes", None)
    if callable(retrieve_fn):
        try:
            similar_memory = retrieve_fn(
                upstream.get("industry"), upstream.get("pain_theme"), k=2
            )
        except Exception:
            pass

    return {
        "project_id":    project_id,
        "upstream":      upstream,
        "user_inputs":   user_inputs,
        "similar_memory": similar_memory[:2],
    }


# ======================================================
# DECISION
# ======================================================

def decision_step(perception: dict, user_feedback: Optional[dict] = None) -> dict:
    upstream = perception.get("upstream") or {}
    confirmed_rc = upstream.get("confirmed_root_causes") or []

    prompt = f"""
DECISION STEP — IMPROVE PHASE
You are a Lean Six Sigma Master Black Belt.

Given confirmed root causes, decide improvement strategy:
- What solution approach? (Kaizen, process redesign, mistake-proofing, automation, training)
- Key constraints from user?
- Pilot approach?

Return JSON only:
{{
  "solution_approach": "...",
  "key_constraints": ["..."],
  "pilot_approach": "...",
  "priority_root_causes": ["top 3 to address"],
  "recommended_solution_types": ["..."]
}}

Confirmed root causes: {json.dumps([r.get('root_cause','') for r in confirmed_rc[:5]], ensure_ascii=False)}
Goal: "{upstream.get('goal_statement','')}"
Y: "{upstream.get('y_variable','')}"
Industry: "{upstream.get('industry','')}"
User feedback: {json.dumps(user_feedback or {})}
""".strip()

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    return parsed or {"solution_approach": "To be determined", "key_constraints": []}


# ======================================================
# MAIN ENTRYPOINT
# ======================================================

def run_improve_agent(
    project_id: str,
    user_inputs: dict,
    define_final:  Optional[dict] = None,
    measure_final: Optional[dict] = None,
    analyze_final: Optional[dict] = None,
    user_feedback: Optional[dict] = None,
    project_path:  Optional[str]  = None,
) -> dict:

    if project_path is None:
        try:
            project_path = memory.load_project_meta(project_id).get("path", "standard")
        except Exception:
            project_path = "standard"

    gate_mode = get_gate_mode(project_path)
    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, define_final, measure_final, analyze_final)
    decisions  = decision_step(perception, user_feedback)
    upstream   = perception.get("upstream") or {}

    outputs: dict = {}

    # Load existing draft outputs to reuse expensive LLM results (avoid double-charging)
    _existing_draft_outputs: dict = {}
    try:
        _ed = memory.load_improve_draft(project_id)
        if _ed and isinstance(_ed.get("outputs"), dict):
            _existing_draft_outputs = _ed["outputs"]
    except Exception:
        pass

    # 1. Must criteria — user-driven (boleh kosong / di-skip), TIDAK auto-generate
    must_criteria = (user_inputs.get("must_criteria")
                     or _existing_draft_outputs.get("must_criteria")
                     or [])
    outputs["must_criteria"] = must_criteria

    # 2. Potential solutions — user-driven (Step 3), TIDAK auto-generate
    solutions = (user_inputs.get("potential_solutions")
                 or _existing_draft_outputs.get("potential_solutions")
                 or [])
    outputs["potential_solutions"] = solutions

    # 3. Selected solutions (list/tabel dari Step 4) — user-driven, TIDAK auto-select
    selected_solutions = (user_inputs.get("selected_solutions")
                          or _existing_draft_outputs.get("selected_solutions")
                          or [])
    outputs["selected_solutions"] = selected_solutions

    # 4. Implementation + pilot plan — diisi user (Step 5), TIDAK auto-generate
    pilot_inputs = user_inputs.get("pilot_inputs") or {}
    outputs["pilot_plan"] = (
        user_inputs.get("pilot_plan")
        or _existing_draft_outputs.get("pilot_plan")
        or {k: v for k, v in {
            "scope":            pilot_inputs.get("scope"),
            "success_criteria": pilot_inputs.get("success_criteria"),
            "owner":            pilot_inputs.get("owner"),
            "rollback_plan":    pilot_inputs.get("rollback_plan"),
        }.items() if v}
    )
    outputs["implementation_plan"] = (
        user_inputs.get("implementation_plan")
        or _existing_draft_outputs.get("implementation_plan")
        or []
    )
    # Future State swimlane (Step 7) — ambil dari user_inputs, fallback ke analyze_wip
    _improved_dot = (
        user_inputs.get("improved_process_map_dot")
        or _existing_draft_outputs.get("improved_process_map_dot")
        or ""
    )
    _improved_summary = (
        user_inputs.get("improved_process_map_summary")
        or _existing_draft_outputs.get("improved_process_map_summary")
        or ""
    )
    if not _improved_dot:
        try:
            _wip = memory.load_analyze_wip(project_id) or {}
            _improved_dot = _wip.get("pm_dot_improved", "") or _improved_dot
            _improved_summary = _wip.get("pm_dot_improved_summary", "") or _improved_summary
        except Exception:
            pass
    outputs["improved_process_map_dot"]     = _improved_dot
    outputs["improved_process_map_summary"] = _improved_summary

    outputs["should_be_process"] = (
        user_inputs.get("should_be_process")
        or _improved_summary
        or _existing_draft_outputs.get("should_be_process")
        or ""
    )
    outputs["risks_and_assumptions"] = (
        _existing_draft_outputs.get("risks_and_assumptions")
        or []
    )

    # 5. Communication plan — user-driven (Step 6), TIDAK auto-generate di sini
    comm_plan = (user_inputs.get("communication_plan")
                 or _existing_draft_outputs.get("communication_plan")
                 or [])
    outputs["communication_plan"] = comm_plan

    # 6. Implementation status (Step 8) — seed dari selected_solutions jika kosong
    impl_status = (user_inputs.get("implementation_status")
                   or _existing_draft_outputs.get("implementation_status")
                   or [])
    impl_status = _seed_implementation_status(selected_solutions, impl_status)
    outputs["implementation_status"] = impl_status

    # Gate
    gate = _improve_gate_evaluate(
        outputs, user_inputs,
        enforce_b_rules=gate_mode["enforce_b_rules"],
        upstream=upstream,
    )

    # Coaching — LLM only on FAIL/WEAK_PASS
    gate_status = gate.get("status","")
    if gate_status in ("FAIL","WEAK_PASS"):
        coaching_md = _build_improve_coaching_llm(gate, outputs, upstream, user_inputs)
    else:
        coaching_md = "✅ Gate PASS — must criteria defined, solutions selected, implementation and communication plan complete."

    improve_state = {
        "schema_version": IMPROVE_SCHEMA,
        "phase":          "improve",
        "status":         "draft",
        "project_id":     project_id,
        "created_at":     _now_iso(),
        "updated_at":     _now_iso(),
        "inputs":         user_inputs,
        "meta": {
            "industry":     upstream.get("industry"),
            "pain_theme":   upstream.get("pain_theme"),
            "process_area": upstream.get("process_area"),
            "project_path": project_path,
        },
        "upstream":    upstream,
        "outputs":     outputs,
        "gate":        gate,
        "coaching_md": coaching_md,
        "insight_md":  coaching_md,
        "routing":     {"policy": gate.get("policy"), "status": gate_status},
    }
    improve_state["summary_md"] = _build_improve_summary_md(improve_state)

    save_fn = getattr(memory, "save_improve_draft", None)
    if callable(save_fn):
        try: save_fn(project_id, improve_state)
        except Exception: pass

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
    improve_state: dict,
    user_feedback: Optional[dict] = None,
) -> dict:
    gate = (improve_state or {}).get("gate") or {}
    if gate.get("status") == "FAIL":
        return {
            "status":        "blocked",
            "reason":        "IMPROVE gate FAIL — selesaikan A-rules sebelum finalize.",
            "gate":          gate,
            "improve_state": improve_state,
            "word_path":     None,
        }

    final_state = copy.deepcopy(improve_state)

    reset_fn = getattr(memory, "reset_approvals", None)
    if callable(reset_fn):
        # Gunakan lowercase agar match dengan konvensi penyimpanan approval (save_approval/get_phase_approval_status)
        reset_fn(project_id, PHASE_NAME.lower())

    final_state["status"]     = "final"
    final_state["updated_at"] = _now_iso()
    final_state.setdefault("created_at", _now_iso())
    if not final_state.get("summary_md"):
        final_state["summary_md"] = _build_improve_summary_md(final_state)

    save_fn = getattr(memory, "save_improve_final", None)
    if callable(save_fn):
        try: save_fn(project_id, final_state)
        except Exception: pass

    delete_fn = getattr(memory, "delete_improve_draft", None)
    if callable(delete_fn):
        try: delete_fn(project_id)
        except Exception: pass

    episode_fn = getattr(memory, "save_improve_episode", None)
    if callable(episode_fn):
        try: episode_fn(project_id, final_state, user_feedback)
        except Exception: pass

    try:
        audit.log_phase_event(project_id, PHASE_NAME, "finalized", meta={
            "gate_status": gate.get("status"),
        })
    except Exception:
        pass

    word_path = None
    export_fn = getattr(reporting, "export_improve_to_word", None)
    if callable(export_fn):
        try:
            word_path = export_fn(final_state, path=f"{project_id}_IMPROVE_REPORT.docx")
        except Exception:
            pass

    return {
        "status":        "finalized",
        "improve_state": final_state,
        "critique":      [],
        "summary":       final_state.get("summary_md") or _build_improve_summary_md(final_state),
        "word_path":     word_path,
        "message":       "Improve finalized (locked).",
    }


# ======================================================
# Improved Process Map Mermaid (Future State)
# ======================================================

def generate_improved_process_map_graphviz(
    current_dot: str,
    improvement_description: str,
    selected_solution: str = "",
) -> dict:
    """
    Generate Future State Graphviz DOT swimlane dari current state + deskripsi improvement.
    Node baru/berubah ditandai warna hijau. Returns {"dot_code": str, "summary": str}
    """
    _baseline_block = f"""Current State Process Map (Graphviz DOT):
```dot
{current_dot}
```
""" if current_dot else "Current state process map tidak tersedia — buat dari awal berdasarkan deskripsi improvement."

    # selected_solution bisa berupa string ringkas atau list dict dari Step 4
    if isinstance(selected_solution, (list, tuple)):
        _sol_block = json.dumps(list(selected_solution), ensure_ascii=False, indent=2)
    else:
        _sol_block = str(selected_solution or "tidak disebutkan")

    prompt = f"""Kamu adalah process improvement expert dalam DMAIC Six Sigma.

{_baseline_block}
Selected solutions (yang HARUS tercermin sebagai perubahan nyata pada proses):
{_sol_block}

User mendeskripsikan perubahan/improvement tambahan:
---
{improvement_description}
---

Tugas: Buat Future State swimlane diagram (Graphviz DOT) yang menggambarkan proses SETELAH improvement.

PRINSIP UTAMA — proses HARUS benar-benar berubah, bukan sekadar ganti label:
- Untuk SETIAP selected solution, wujudkan sebagai perubahan struktural pada flow:
  ELIMINASI step yang tidak perlu, GABUNG step yang redundan, OTOMASI step manual,
  TAMBAH step baru (mis. automated check), atau UBAH urutan/aktor.
- Jangan hanya menambahkan kata "Otomatis" pada label lama — ubah aktor/urutan/keberadaan step sesuai solusi.
- Jika sebuah step dieliminasi/digabung, HAPUS dari diagram dan sambungkan ulang edge-nya.

Aturan PENOMORAN (WAJIB):
- Beri nomor ulang SEMUA step secara berurutan 1, 2, 3, ... mengikuti URUTAN ALIRAN sebenarnya dari kiri ke kanan (lintas swimlane).
- JANGAN memakai nomor lama dari current state. Setelah ada step yang dihapus/digabung/ditambah, penomoran harus rapi tanpa lompatan (tidak boleh 7→8→9 yang tidak nyambung).
- Label node format: "N. Nama Step" dengan N hasil penomoran ulang.

Aturan teknis:
1. Format: digraph swimlane {{ rankdir=LR; ... }} — swimlane horizontal
2. Pertahankan konsep cluster/swimlane (aktor) dari current state, tapi step boleh pindah aktor jika solusi menuntut
3. Node BARU atau BERUBAH (akibat solusi): fillcolor="#bbf7d0", color="#16a34a" (hijau)
4. Node TIDAK BERUBAH: pertahankan warna aslinya
5. Decision: shape=diamond, fillcolor="#fde68a", color="#f59e0b"
6. Edge label untuk kondisi: [label="Ya"] atau [label="Tidak"]
7. Maksimal 15 node total

Balas HANYA dengan:
1. DOT code dalam blok ```dot ... ```
2. Satu kalimat summary singkat yang menyebut perubahan struktural utama"""

    raw = llm(prompt)

    dot_code = ""
    m = re.search(r"```dot\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        dot_code = m.group(1).strip()
    if not dot_code:
        m2 = re.search(r"(digraph\s+\w+\s*\{.*\})", raw, re.DOTALL)
        if m2:
            dot_code = m2.group(1).strip()

    summary = ""
    m3 = re.search(r"[Ss]ummary[:\s]+(.*?)$", raw, re.MULTILINE)
    if m3:
        summary = m3.group(1).strip()

    return {"dot_code": dot_code, "summary": summary}


# Backward-compat alias
def generate_improved_process_map_mermaid(current_mermaid: str, improvement_description: str, selected_solution: str = "") -> dict:
    r = generate_improved_process_map_graphviz(current_mermaid, improvement_description, selected_solution)
    return {"mermaid_code": r.get("dot_code", ""), "summary": r.get("summary", "")}