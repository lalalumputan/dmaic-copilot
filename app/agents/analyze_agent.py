# app/agents/analyze_agent.py
from __future__ import annotations

import json
import re
import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import memory, audit, reporting, llm_engine
from app.agents.classification_agent import get_gate_mode

PHASE_NAME   = "Analyze"
ANALYZE_SCHEMA = "v1.1"


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
    define_final: Optional[Dict],
    measure_final: Optional[Dict],
) -> Dict[str, Any]:
    def_outs = (define_final  or {}).get("outputs") or {}
    def_ins  = (define_final  or {}).get("inputs")  or {}
    mea_outs = (measure_final or {}).get("outputs") or {}
    perf_sum = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}

    # Baseline dari dataset; fallback ke baseline hasil ekstraksi gambar chart (quick path).
    cib = mea_outs.get("chart_image_baseline") or {}
    _baseline_mean = perf_sum.get("mean")
    _baseline_source = "dataset"
    if _baseline_mean is None and isinstance(cib, dict) and cib.get("average"):
        _baseline_mean = cib.get("average")          # estimasi visual dari gambar chart
        _baseline_source = "chart_image_visual_estimate"

    return {
        "problem_statement":   def_outs.get("problem_statement") or def_ins.get("problem_text") or "",
        "goal_statement":      def_outs.get("goal_statement") or def_ins.get("goal_text") or "",
        "y_variable":          mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "",
        "ctq_list":            def_outs.get("ctq_list") or [],
        "baseline_mean":       _baseline_mean,
        "baseline_std":        perf_sum.get("std"),
        "baseline_source":     _baseline_source,
        "chart_image_baseline": cib if isinstance(cib, dict) else {},
        "baseline_target":     (mea_outs.get("process_performance_summary") or {}).get("target_info") or {},
        "op_defs":             mea_outs.get("operational_definitions") or [],
        "dq_issues":           (mea_outs.get("data_quality_results") or {}).get("consistency", {}).get("issues") or [],
        "industry":            def_ins.get("industry") or "",
        "process_area":        def_ins.get("process_area") or "",
        "pain_theme":          def_ins.get("pain_theme") or "",
        "scope":               def_outs.get("project_scope") or {},
    }


# ======================================================
# LLM FUNCTIONS (all called only from run_analyze_agent)
# ======================================================

def _check_process_map_llm(
    process_steps: list,
    upstream: dict,
) -> dict:
    """Agent checks user-submitted process map for completeness and logic."""
    if not process_steps:
        return {"status": "missing", "feedback": "Process map not submitted.", "issues": []}

    prompt = f"""
You are a Lean Six Sigma Master Black Belt reviewing a process map.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"

User-submitted process steps:
{json.dumps(process_steps, indent=2, ensure_ascii=False)}

Evaluate:
1. Are all critical steps present for this type of process?
2. Are decision points and failure modes captured?
3. Where in the process is the problem most likely occurring?
4. What is missing or should be added?

Return JSON:
{{
  "status": "complete" | "needs_improvement",
  "problem_area_steps": ["step where problem likely occurs"],
  "missing_elements": ["..."],
  "improvement_suggestions": ["..."],
  "feedback": "1-2 sentence MBB feedback"
}}
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_from_llm(raw)
        if parsed:
            return parsed
    except Exception:
        pass
    return {"status": "needs_improvement", "feedback": "Could not evaluate — review manually.", "issues": []}


def _check_fishbone_llm(
    fishbone_inputs: dict,
    upstream: dict,
) -> dict:
    """Agent checks fishbone diagram for completeness, logic, and methodology."""
    if not fishbone_inputs or not any(fishbone_inputs.values()):
        return {"status": "missing", "feedback": "Fishbone not submitted.", "gaps": []}

    prompt = f"""
You are a Lean Six Sigma Master Black Belt reviewing a fishbone (Ishikawa) diagram.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Baseline Mean: {upstream.get('baseline_mean')}

Fishbone inputs (6M categories):
{json.dumps(fishbone_inputs, indent=2, ensure_ascii=False)}

Evaluate:
1. Are causes specific enough (not generic)?
2. Are there logical connections to Y?
3. Which categories are underdeveloped?
4. Are there important causes missing based on the problem context?
5. Which causes are most likely to be root causes?

Return JSON:
{{
  "status": "complete" | "needs_improvement",
  "strong_categories": ["..."],
  "weak_categories": ["..."],
  "missing_causes": ["..."],
  "high_priority_causes": ["cause that is most likely root cause"],
  "optimization_suggestions": ["..."],
  "feedback": "2-3 sentence MBB feedback"
}}
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_from_llm(raw)
        if parsed:
            return parsed
    except Exception:
        pass
    return {"status": "needs_improvement", "feedback": "Could not evaluate.", "gaps": []}

def _check_process_map_from_image_llm(
    image_base64: str,
    image_type: str,
    upstream: dict,
) -> dict:
    """
    Agent analyzes uploaded process map image using GPT-4o Vision.
    Returns structured check result same schema as _check_process_map_llm.
    """
    import base64

    prompt = f"""
You are a Lean Six Sigma Master Black Belt reviewing a process map or cross-functional (swimlane) diagram.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"

Analyze the uploaded process map image and evaluate:
1. Are all critical process steps captured?
2. Are swimlanes/functions clearly defined?
3. Are inputs/outputs identified?
4. Where is the problem area most likely occurring?
5. Are there decision points or failure modes shown?
6. Is this process map sufficient to support root cause analysis?

Return JSON only:
{{
  "status": "complete" | "needs_improvement" | "unreadable",
  "process_type": "swimlane/flowchart/value_stream/other",
  "functions_identified": ["function1", "function2"],
  "steps_identified": ["step1", "step2"],
  "problem_area_steps": ["step where problem likely occurs"],
  "missing_elements": ["what is missing"],
  "improvement_suggestions": ["suggestion"],
  "feedback": "2-3 sentence MBB assessment",
  "verified": true/false,
  "verification_note": "why verified or not"
}}
""".strip()

    try:
        import openai
        from app.utils import llm_engine

        # GANTI DENGAN:
        import streamlit as st
        api_key = st.secrets.get("OPENAI_API_KEY") or st.secrets.get("openai", {}).get("api_key", "")
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_type};base64,{image_base64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=1000,
        )
        raw = response.choices[0].message.content
        parsed = extract_json_from_llm(raw)
        if parsed:
            return parsed
    except Exception as e:
        pass

    return {
        "status": "needs_improvement",
        "verified": False,
        "feedback": "Image could not be analyzed — fill the process map table manually.",
        "verification_note": "Vision analysis failed.",
    }

def _propose_potential_causes_llm(
    fishbone_inputs: dict,
    process_steps: list,
    upstream: dict,
) -> list:
    """
    From fishbone + process map → numbered potential causes (X1, X2, ...).
    Each cause gets description and source (fishbone category or process step).
    """
    prompt = f"""
    You are a Lean Six Sigma Master Black Belt consolidating potential causes from fishbone and process map analysis.

    Project context:
    - Problem: "{upstream.get('problem_statement','')}"
    - Y Variable (Output Measurement): "{upstream.get('y_variable','')}"
    - Baseline Mean of Y: {upstream.get('baseline_mean')}
    - Target: {upstream.get('baseline_target',{}).get('direction','')} {upstream.get('baseline_target',{}).get('value','')}

    IMPORTANT: Only include causes that have a DIRECT causal path to Y = "{upstream.get('y_variable','')}".
    Exclude causes that are indirect, generic, or cannot be measured as an X variable.

    Fishbone inputs:
    {json.dumps(fishbone_inputs, indent=2, ensure_ascii=False)}

    Process map steps (problem area):
    {json.dumps(process_steps[:10], indent=2, ensure_ascii=False)}

    Consolidate into numbered potential causes (X1, X2, ...) that directly drive Y.

    Return JSON array:
    [
    {{
        "xn": "X1",
        "cause": "specific cause description",
        "description": "specific cause description (same as cause)",
        "source": "fishbone category or process step name",
        "in_process_step": "Step number and name from process map that this cause originates from, e.g. '3. Quality Check'",
        "direct_effect_on_y": "how this cause directly increases/decreases Y",
        "initial_hypothesis": "If [cause] increases/worsens then Y = [{upstream.get('y_variable','')}] will be [higher/lower] because [mechanism]"
    }}
    ]

    Generate 5-10 causes. Order by directness of effect on Y (most direct first).
    """.strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_array_from_llm(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def _fivewhy_drill_llm(
    cause: dict,
    upstream: dict,
    iterations: int = 3,
) -> dict:
    """
    Drill one potential cause using 5-Why to get to root cause.
    iterations: 1-5 (user configurable).
    """
    prompt = f"""
You are a Lean Six Sigma Master Black Belt conducting 5-Why analysis.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"

Potential cause to drill:
- {cause.get('xn','')}: {cause.get('cause','')}
- Initial hypothesis: {cause.get('initial_hypothesis','')}

Conduct {iterations} levels of "Why?" to reach the root cause.
Be specific and grounded in the project context.

Return JSON:
{{
  "xn": "{cause.get('xn','')}",
  "surface_cause": "{cause.get('cause','')}",
  "why_chain": [
    {{"level": 1, "why": "Why does [cause] happen?", "answer": "Because..."}},
    {{"level": 2, "why": "Why does [level 1 answer] happen?", "answer": "Because..."}},
    {{"level": {iterations}, "why": "...", "answer": "..."}}
  ],
  "root_cause": "The fundamental root cause after drilling",
  "root_cause_category": "Man/Machine/Method/Material/Measurement/Environment"
}}
""".strip()
    try:
        raw = llm(prompt)
        parsed = extract_json_from_llm(raw)
        if parsed:
            return parsed
    except Exception:
        pass
    return {
        "xn": cause.get("xn",""),
        "surface_cause": cause.get("cause",""),
        "why_chain": [],
        "root_cause": cause.get("cause",""),
        "root_cause_category": cause.get("source",""),
    }


def _build_verification_plan_llm(
    root_causes: list,
    upstream: dict,
) -> list:
    if not root_causes:
        return []

    # Pre-build di luar f-string untuk hindari unhashable dict-in-set error
    causes_summary = [
        {
            "xn": r.get("xn"),
            "root_cause": r.get("root_cause") or r.get("cause"),
            "direct_effect_on_y": r.get("direct_effect_on_y", ""),
        }
        for r in root_causes
    ]
    target_info = upstream.get("baseline_target") or {}

    prompt = f"""
You are a Lean Six Sigma Master Black Belt designing a verification plan.

Output Measurement (Y): "{upstream.get('y_variable','')}"
Baseline Mean of Y: {upstream.get('baseline_mean')}
Target: {target_info.get('direction','')} {target_info.get('value','')}

For each potential root cause below, design a verification plan that proves or disproves
the direct causal relationship between the X (root cause) and Y (output measurement).

Potential root causes ({len(causes_summary)} items — MUST return exactly this many):
{json.dumps(causes_summary, indent=2, ensure_ascii=False)}

CRITICAL RULES:
1. Return EXACTLY {len(causes_summary)} items — one per input cause. No more, no less.
2. Use the EXACT same "xn" values as in the input (X1, X2, etc.). Do NOT rename or renumber.
3. Copy the "root_cause" value from input directly — do NOT rephrase or change it.

Return JSON array only, same order as input:
[
{{
    "xn": "<exact xn from input>",
    "root_cause": "<exact root_cause from input>",
    "input_measurement_x": "specific X variable that represents this cause",
    "relation_to_y": "When X increases/decreases, Y increases/decreases because mechanism",
    "verification_method": "correlation analysis / stratification / hypothesis test / regression / time series comparison",
    "data_needed": "what data, period, source system",
    "expected_pattern_if_confirmed": "specific pattern in data that would confirm this X drives Y"
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


def _build_verification_analysis_llm(
    verification_results: list,
    upstream: dict,
) -> list:
    """
    Build detailed analysis per verified root cause with hypothesis format + conclusion.
    """
    if not verification_results:
        return []

    analyses = []
    y_var = upstream.get("y_variable","")
    target_info = upstream.get("baseline_target") or {}
    target_val  = target_info.get("value")
    target_dir  = target_info.get("direction", "<=")

    for r in verification_results:
        xn    = r.get("xn","")
        rc    = r.get("root_cause","")
        x_var = r.get("input_measurement_x","")
        result = r.get("verification_result","")
        impact_pct = r.get("impact_pct_of_target","")
        is_rc = r.get("is_root_cause", False)

        # Format hypothesis statement
        hypothesis = f"{rc} causes {y_var} to be {'higher' if target_dir == '<=' else 'lower'} than target"
        if target_val:
            hypothesis += f" of {target_val}"

        conclusion_word = "confirmed" if is_rc else "not confirmed"

        analysis = {
            "xn": xn,
            "root_cause": rc,
            "hypothesis": hypothesis,
            "hypothesis_statement": (
                f"{rc} gives {y_var} {'higher' if target_dir == '<=' else 'lower'} value "
                f"{'above' if target_dir == '<=' else 'below'} target"
                + (f" of {target_val}" if target_val else "")
            ),
            "graphical_display_description": (
                f"This display shows the relationship between {x_var} and {y_var}. "
                f"Data was collected as described in the verification plan. "
                f"We can see from this that {result or '[describe pattern observed]'}. "
                f"Conclusion: it is {conclusion_word}."
            ),
            "result": result,
            "is_root_cause": is_rc,
            "impact_pct_of_target": impact_pct,
            "conclusion": conclusion_word,
        }
        analyses.append(analysis)

    return analyses


def _build_rca_summary_table(
    potential_causes: list,
    verification_plan: list,
    verification_results: list,
) -> list:
    """
    Build root cause summary table: Xn, root cause, impact, comment on KPI.
    """
    # Build lookup maps
    plan_map   = {p.get("xn",""): p for p in (verification_plan  or [])}
    result_map = {r.get("xn",""): r for r in (verification_results or [])}

    summary = []
    for cause in (potential_causes or []):
        xn  = cause.get("xn","")
        rc  = cause.get("root_cause") or cause.get("cause","")
        res = result_map.get(xn, {})
        pln = plan_map.get(xn, {})

        is_rc     = res.get("is_root_cause", False)
        impact    = res.get("impact_level","")
        impact_pct = res.get("impact_pct_of_target","")
        comment   = pln.get("relation_to_y","")
        if impact_pct:
            comment += f" — estimated {impact_pct}% of gap to target"

        summary.append({
            "xn":          xn,
            "root_cause":  rc,
            "is_root_cause": "✅ Yes" if is_rc else "❌ No",
            "impact":      impact or "-",
            "comment":     comment or "-",
        })
    return summary


# ======================================================
# GATE EVALUATION (rules-based, no LLM)
# ======================================================

def _analyze_gate_evaluate(
    outputs: dict,
    user_inputs: dict,
    enforce_b_rules: bool = True,
) -> dict:
    failed      = []
    conditional = []
    actions     = []

    # A-rules (always enforced)
    pot_causes = outputs.get("potential_causes") or []
    if not pot_causes:
        failed.append("A1_no_potential_causes_identified")
        actions.append("Submit fishbone/process map dan generate potential causes (Xn list).")

    # A2 dihapus — 5-Why dilakukan offline, root cause di tabel Xn sudah final

    # A3/A4: verification plan & results — Quick path skip (advisory), Standard wajib
    vplan = outputs.get("verification_plan") or []
    if pot_causes and not vplan and enforce_b_rules:
        failed.append("A3_no_verification_plan")
        actions.append("Susun verification plan per potential root cause.")

    vresults = outputs.get("verification_results") or []
    if vplan and not vresults and enforce_b_rules:
        failed.append("A4_no_verification_results")
        actions.append("Isi verification results setelah melakukan verifikasi lapangan/data.")

    rca_table = outputs.get("root_cause_summary_table") or []
    if not rca_table:
        failed.append("A5_no_rca_summary_table")
        actions.append("Generate root cause summary table.")

    # B-rules (standard: enforced, quick: advisory)
    process_steps    = outputs.get("process_map_steps") or []
    pm_check         = outputs.get("process_map_check") or {}
    pm_verified      = pm_check.get("verified_via_upload", False)
    pm_has_image     = bool(outputs.get("process_map_image_b64", ""))
    pm_has_dot       = bool(outputs.get("pm_dot_code", ""))

    # B1: satisfied jika tabel diisi ATAU image uploaded ATAU DOT swimlane ada
    _pm_ok = process_steps or (pm_has_image and pm_verified) or pm_has_dot
    if not _pm_ok:
        if enforce_b_rules:
            conditional.append("B1_process_map_missing")
            actions.append("Buat process map di Step 2: upload foto (Opsi A) atau generate swimlane diagram (Opsi B).")

    # B2: fishbone — OK jika ada text 6M ATAU foto fishbone sudah diupload (offline mode)
    fishbone          = outputs.get("fishbone_inputs") or {}
    fb_photo_uploaded = bool(outputs.get("fb_photo_uploaded", False))
    _fb_ok = any((fishbone or {}).values()) or fb_photo_uploaded
    if not _fb_ok:
        if enforce_b_rules:
            conditional.append("B2_fishbone_not_documented")
            actions.append("Upload foto hasil fishbone analysis di Step 3 sebagai dokumentasi.")

    def _is_confirmed(val):
        """Robust check: handle bool, numpy.bool_, string 'True'/'true'/'1', int 1."""
        if val is None or val == "" or val is False:
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "yes", "1", "✅ yes", "✅")
        try:
            return bool(val)
        except Exception:
            return False

    confirmed_rc = [r for r in (vresults or []) if _is_confirmed(r.get("is_root_cause", False))]
    if not confirmed_rc:
        if enforce_b_rules:
            conditional.append("B3_no_confirmed_root_causes")
            actions.append("Minimal 1 root cause harus terkonfirmasi dari verifikasi di Step 7.")

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
        "status":           status,
        "policy":           policy,
        "failed_rules":     failed,
        "conditional_rules": conditional,
        "next_actions":     actions[:8],
    }


# ======================================================
# LLM COACHING (only on FAIL/WEAK_PASS)
# ======================================================

def _build_analyze_coaching_llm(
    gate: dict,
    outputs: dict,
    upstream: dict,
    user_inputs: dict,
    project_path: str = "standard",
) -> str:
    failed      = gate.get("failed_rules") or []
    conditional = gate.get("conditional_rules") or []
    actions     = gate.get("next_actions") or []
    status      = gate.get("status","FAIL")

    # Konteks workflow terbaru untuk coaching
    def _is_confirmed_local(val):
        if val is None or val is False or val == "": return False
        if isinstance(val, bool): return val
        if isinstance(val, str): return val.strip().lower() in ("true","yes","1","✅ yes","✅")
        try: return bool(val)
        except: return False

    _pm_dot        = bool(outputs.get("pm_dot_code",""))
    _pm_img        = bool(outputs.get("process_map_image_b64",""))
    _fb_photo      = bool(outputs.get("fb_photo_uploaded", False))
    _pot_causes    = outputs.get("potential_causes") or []
    _vplan         = outputs.get("verification_plan") or []
    _vresults      = outputs.get("verification_results") or []
    _confirmed_rc  = [r for r in _vresults if _is_confirmed_local(r.get("is_root_cause", False))]
    _high_impact   = [r for r in _vresults if str(r.get("impact_pct_of_target","") or "").strip() not in ("","0","None","-")]

    prompt = f"""
You are a Lean Six Sigma Master Black Belt coaching a project leader on the Analyze phase.

Execution path: {"Quick Improvement" if project_path == "quick" else "Standard DMAIC"}
{"Note: This is a Quick Improvement project — coaching must be concise, focus only on the truly critical/mandatory gaps, and do NOT require the heavier formal deliverables that are simplified in the Quick path (formal verification plan/results, detailed 6M fishbone, etc.)." if project_path == "quick" else ""}
IMPORTANT: write for a non-technical project leader. Use plain business language. NEVER mention internal rule codes or terms like "A-rules", "B-rules", or codes such as "A1_...".

Gate Status: {status}
Failed rules: {json.dumps(failed)}
Conditional rules: {json.dumps(conditional)}

Context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Baseline Mean: {upstream.get('baseline_mean')}
- Industry: {upstream.get('industry','')} / {upstream.get('process_area','')}

Current workflow state (IMPORTANT — use this to assess what's done vs missing):
- Process map (swimlane DOT): {"✅ Done" if _pm_dot else ("✅ Done via image upload" if _pm_img else "❌ Not yet")}
- Fishbone & 5-Why: {"✅ Documented (photo uploaded)" if _fb_photo else "⚠️ Done offline but photo not uploaded yet"}
- Potential causes / root causes (Xn): {len(_pot_causes)} cause(s) identified — FINAL (from offline 5-Why)
- Verification plan: {len(_vplan)} cause(s) planned
- Verification results filled: {len(_vresults)} cause(s)
- Root causes CONFIRMED (is_root_cause=True): {len(_confirmed_rc)} cause(s) → {[r.get("xn","") for r in _confirmed_rc]}
- High impact causes (impact filled): {len(_high_impact)} cause(s)

STRICT COACHING RULES:
1. If confirmed_rc count > 0: Do NOT ask user to confirm root causes. It is ALREADY DONE. Acknowledge it as complete.
2. Fishbone and 5-Why are done OFFLINE. Root causes in the Xn table are already final.
   Do NOT ask user to do 5-Why in the app or fill a fishbone form.
3. Process map = swimlane diagram (Step 2 Opsi B) or uploaded image (Opsi A). Do NOT ask to fill a table.
4. If B3 is in conditional rules BUT confirmed_rc > 0: this is a data sync issue — tell user to click "Generate Draft" again or re-save Step 7.
5. Focus coaching on what is genuinely missing, not on items already completed.

Next actions flagged: {json.dumps(actions)}

Provide MBB coaching in Bahasa Indonesia:
1. Acknowledge what is ALREADY done
2. Explain clearly what is still missing and WHY it matters for root cause validation
3. Give concrete next steps specific to this project
4. Address FAIL items first
5. Max 300 words, markdown format with ### headings
6. Start directly — no preamble
""".strip()
    try:
        raw = llm(prompt)
        if raw and len(raw.strip()) > 80:
            return raw.strip()
    except Exception:
        pass

    lines = [f"### ANALYZE Gate: **{status}**"]
    for a in actions[:6]:
        lines.append(f"- {a}")
    return "\n".join(lines)


def _build_rootcause_logic_md_llm(
    upstream: dict,
    outputs: dict,
    project_path: str = "standard",
) -> dict:
    """
    CRITICAL (kedua path): nilai NALAR KAUSAL tiap confirmed root cause terhadap
    target yang tidak tercapai. Bukan checklist.

    Mengembalikan {"md": <markdown>, "has_red": bool}.
    - quick   : 🔴 = catatan saja (tidak memblokir).
    - standard: 🔴 = sinyal blokir (caller men-FAIL-kan gate).
    """
    def _is_confirmed_local(val):
        if val is None or val is False or val == "":
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "yes", "1", "✅ yes", "✅")
        try:
            return bool(val)
        except Exception:
            return False

    vresults  = outputs.get("verification_results") or []
    confirmed = [r for r in vresults if _is_confirmed_local(r.get("is_root_cause", False))]
    if not confirmed:
        return {"md": "", "has_red": False}

    # Verification Plan: penjelasan user soal kaitan X→Y ada di kolom 'relation_to_y'
    _vplan_map = {
        str(p.get("xn", "")): p
        for p in (outputs.get("verification_plan") or []) if isinstance(p, dict)
    }
    _rc_list = []
    for r in confirmed:
        _rc  = r.get("root_cause") or r.get("cause") or r.get("description") or ""
        _hyp = r.get("hypothesis") or f"Target tidak tercapai karena {_rc}"
        _vp  = _vplan_map.get(str(r.get("xn", "")), {})
        _rc_list.append({
            "xn": r.get("xn", ""),
            "root_cause": _rc,
            "hypothesis": _hyp,
            # Penjelasan user (jika ada) — kaitan X ke Y & mekanismenya
            "relation_to_y": _vp.get("relation_to_y", "") or r.get("relation_to_y", ""),
            "input_measurement_x": _vp.get("input_measurement_x", ""),
            "verification_finding": r.get("verification_result", "") or r.get("finding", ""),
        })

    target_info = upstream.get("baseline_target") or {}
    prompt = f"""
You are a Lean Six Sigma Master Black Belt. Your job here is NOT to check boxes — it is to
CRITICALLY EVALUATE THE CAUSAL LOGIC linking each confirmed root cause to the UNMET TARGET.
A root cause that is "filled in" but does not logically explain why Y misses the target is WRONG
and must be fixed.

Project context:
- Problem: "{upstream.get('problem_statement','')}"
- Goal/Target: "{upstream.get('goal_statement','')}"
- Y Variable (outcome being measured): "{upstream.get('y_variable','')}"
- Baseline (current level of Y): {upstream.get('baseline_mean')}
- Target: {target_info.get('direction','')} {target_info.get('value','')}

Confirmed root causes (with the project leader's own explanation in "relation_to_y"):
{json.dumps(_rc_list, ensure_ascii=False, indent=2)}

IMPORTANT — READ "relation_to_y" and "verification_finding" FIRST. That is the project leader's OWN
explanation of HOW this X drives Y. If it already gives a clear, plausible causal mechanism to Y/target,
treat the logic as SOUND (🟢) and do NOT demand re-explanation or invent objections. Only flag 🟡/🔴 when
"relation_to_y" is empty, vague, or genuinely does NOT connect the cause to Y/target.

For EACH root cause, reason explicitly (using relation_to_y as the primary evidence):
- Does the stated relation_to_y give a CLEAR, PLAUSIBLE causal mechanism: root cause -> affects Y -> causes Y to miss target?
- Is it a true root cause (underlying driver) or just a symptom / restatement of the problem?
- Does it actually explain the GAP between baseline and target?

Give a verdict per root cause:
- 🟢 Logis & nyambung — mechanism clear and plausibly drives the target miss.
- 🟡 Lemah / kurang jelas — plausible but mechanism vague, too broad, or a symptom; needs sharpening.
- 🔴 Tidak nyambung — no logical causal path to Y/target, or contradicts the data; must be reframed.

Rules:
- Be a critical reasoner, NOT a checklister. If the logic is sound, say so plainly (do NOT invent objections).
- For every 🟡 or 🔴, give a CONCRETE fix: either PROPOSE a corrected hypothesis sentence
  ("Target tidak tercapai karena ...") OR state exactly what the user must clarify/verify.
- Do NOT add any overall blocking/closing/conclusion note — give ONLY per-root-cause verdicts and fixes.
- Output in Bahasa Indonesia, markdown. Keep technical terms (root cause, Y, baseline, target) in English.
- Be concise (max ~250 words). Start directly with the heading "### 🔎 Validasi Logika Root Cause".
""".strip()

    md = ""
    try:
        raw = llm(prompt)
        if raw and len(raw.strip()) > 60:
            md = raw.strip()
    except Exception:
        md = ""

    if not md:
        # Fallback deterministik
        lines = [
            "### 🔎 Validasi Logika Root Cause",
            "_Pastikan tiap root cause benar-benar menjelaskan MENGAPA target tidak tercapai "
            "(ada mekanisme kausal jelas ke Y), bukan sekadar gejala:_",
        ]
        for rc in _rc_list:
            lines.append(f"- **{rc['xn']}** {rc['hypothesis']}")
        md = "\n".join(lines)

    # 🔴 = root cause tidak nyambung secara logika.
    has_red = "🔴" in md
    if has_red:
        if project_path == "quick":
            md += ("\n\n> ⚠️ **Catatan:** ada root cause yang lemah/tidak nyambung secara logika. "
                   "Quick path tetap bisa lanjut, tapi disarankan memperbaiki framing-nya agar solusi tepat sasaran.")
        else:
            md += ("\n\n> ⛔ **Blokir:** ada root cause yang tidak nyambung secara logika. Perbaiki framing "
                   "hipotesis atau verifikasi ulang sebelum lanjut ke fase Improve.")

    return {"md": md, "has_red": has_red}


# ======================================================
# SUMMARY BUILDER
# ======================================================

def _build_analyze_summary_md(analyze_state: dict) -> str:
    outputs = analyze_state.get("outputs") or {}
    gate    = analyze_state.get("gate") or {}
    pid     = analyze_state.get("project_id", "-")

    rca_table    = outputs.get("root_cause_summary_table") or []
    confirmed_rc = [r for r in rca_table if "Yes" in str(r.get("is_root_cause",""))]
    vresults     = outputs.get("verification_results") or []

    md = [f"## ANALYZE — {pid}"]
    md.append(f"**Gate Status:** {gate.get('status','-')}\n")

    if confirmed_rc:
        md.append("### Confirmed Root Causes")
        for r in confirmed_rc:
            md.append(f"- **{r.get('xn','')}** {r.get('root_cause','')} — Impact: {r.get('impact','-')}")
    else:
        md.append("### Root Causes: _not yet confirmed_")

    if vresults:
        md.append(f"\n**Verification completed for {len(vresults)} cause(s).**")

    return "\n".join(md).strip()


# ======================================================
# PERCEPTION
# ======================================================

def perception_step(
    project_id: str,
    user_inputs: dict,
    define_final: Optional[dict] = None,
    measure_final: Optional[dict] = None,
) -> dict:
    upstream = _extract_upstream_context(define_final, measure_final)

    similar_memory = []
    retrieve_fn = getattr(memory, "retrieve_similar_analyze_episodes", None)
    if callable(retrieve_fn):
        try:
            similar_memory = retrieve_fn(
                upstream.get("industry"), upstream.get("pain_theme"), k=3
            )
        except Exception:
            pass

    return {
        "project_id":      project_id,
        "upstream":        upstream,
        "user_inputs":     user_inputs,
        "similar_memory":  similar_memory[:2],
    }


# ======================================================
# DECISION
# ======================================================

def decision_step(
    perception: dict,
    user_feedback: Optional[dict] = None,
) -> dict:
    upstream = perception.get("upstream") or {}
    prompt = f"""
DECISION STEP — ANALYZE PHASE
You are a Lean Six Sigma Master Black Belt.

Given the context, decide analysis strategy:
- Which 6M categories are most relevant?
- What verification approach (correlation, stratification, hypothesis test, regression)?
- What is the priority investigation order?

Return JSON only:
{{
  "priority_cause_categories": ["..."],
  "recommended_verification_approach": "...",
  "focus_process_area": "...",
  "analysis_depth": "standard" | "quick"
}}

Context:
Problem: "{upstream.get('problem_statement','')}"
Goal: "{upstream.get('goal_statement','')}"
Y: "{upstream.get('y_variable','')}"
Baseline: {upstream.get('baseline_mean')}
Industry: "{upstream.get('industry','')}"
Process area: "{upstream.get('process_area','')}"

User feedback: {json.dumps(user_feedback or {})}
""".strip()
    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    return parsed or {"priority_cause_categories": [], "analysis_depth": "standard"}


# ======================================================
# MAIN ENTRYPOINT
# ======================================================

def run_analyze_agent(
    project_id: str,
    user_inputs: dict,
    define_final: Optional[dict] = None,
    measure_final: Optional[dict] = None,
    user_feedback: Optional[dict] = None,
    project_path: Optional[str] = None,
) -> dict:

    if project_path is None:
        try:
            project_path = memory.load_project_meta(project_id).get("path","standard")
        except Exception:
            project_path = "standard"

    gate_mode = get_gate_mode(project_path)
    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, define_final, measure_final)
    decisions  = decision_step(perception, user_feedback)
    upstream   = perception.get("upstream") or {}

    # --- Build outputs from user inputs + LLM functions ---
    outputs: dict = {}

    # 1. Process map — dari tabel, image upload, ATAU DOT swimlane (Opsi B)
    process_steps  = user_inputs.get("process_map_steps") or []
    pm_image_b64   = user_inputs.get("process_map_image_b64", "")
    pm_image_type  = user_inputs.get("process_map_image_type", "image/png")
    pm_dot_code    = user_inputs.get("pm_dot_code", "") or ""
    pm_linked_dot  = user_inputs.get("pm_linked_dot_code", "") or ""
    pm_verified_via_upload = False

    outputs["process_map_steps"]      = process_steps
    outputs["pm_dot_code"]            = pm_dot_code
    outputs["pm_linked_dot_code"]     = pm_linked_dot

    if pm_image_b64:
        # Vision analysis dari uploaded image
        pm_check = _check_process_map_from_image_llm(pm_image_b64, pm_image_type, upstream)
        pm_verified_via_upload = pm_check.get("verified", False)
        if not process_steps and pm_check.get("steps_identified"):
            outputs["process_map_steps_from_image"] = pm_check.get("steps_identified", [])
    elif pm_dot_code:
        # DOT swimlane sudah ada — anggap process map complete
        pm_check = {
            "status":   "complete",
            "feedback": "Swimlane diagram sudah dibuat via agent (Opsi B). Proses tervisualisasi dalam format DOT.",
            "verified_via_dot": True,
            "problem_area_steps": [],
            "missing_elements":   [],
        }
        pm_verified_via_upload = True  # DOT = verified
    elif process_steps:
        pm_check = _check_process_map_llm(process_steps, upstream)
    else:
        pm_check = {"status": "missing", "feedback": "Process map belum dibuat. Gunakan Opsi A (upload) atau Opsi B (generate swimlane) di Step 2."}

    pm_check["verified_via_upload"] = pm_verified_via_upload
    outputs["process_map_check"]    = pm_check
    outputs["process_map_image_b64"]  = pm_image_b64
    outputs["process_map_image_type"] = pm_image_type

    # 2. Fishbone — dilakukan offline; foto sebagai bukti, teks 6M opsional
    fishbone_inputs   = user_inputs.get("fishbone_inputs") or {}
    fb_photo_uploaded = bool(user_inputs.get("fb_photo_uploaded", False))
    outputs["fishbone_inputs"]    = fishbone_inputs
    outputs["fb_photo_uploaded"]  = fb_photo_uploaded

    if any((fishbone_inputs or {}).values()):
        outputs["fishbone_check"] = _check_fishbone_llm(fishbone_inputs, upstream)
    elif fb_photo_uploaded:
        outputs["fishbone_check"] = {
            "status":   "complete",
            "feedback": "Fishbone dilakukan secara offline — foto hasil analisis telah diupload.",
            "gaps":     [],
        }
    else:
        outputs["fishbone_check"] = {
            "status":   "offline_pending",
            "feedback": "Fishbone dilakukan offline. Upload foto hasil fishbone di Step 3 sebagai dokumentasi.",
        }

    # 3. Potential causes (Xn) — from user OR re-proposed by agent
    potential_causes = user_inputs.get("potential_causes") or []
    if not potential_causes and (process_steps or any(fishbone_inputs.values())):
        potential_causes = _propose_potential_causes_llm(fishbone_inputs, process_steps, upstream)
    outputs["potential_causes"] = potential_causes

    # Load existing draft outputs to reuse expensive LLM results (avoid double-charging)
    _existing_draft_outputs: dict = {}
    try:
        _ed = memory.load_analyze_draft(project_id)
        if _ed and isinstance(_ed.get("outputs"), dict):
            _existing_draft_outputs = _ed["outputs"]
    except Exception:
        pass

    # 4. 5-Why dilakukan offline — root_cause di tabel Xn sudah final
    # Pastikan setiap cause punya field root_cause (fallback ke description/cause)
    for cause in potential_causes:
        if not cause.get("root_cause"):
            cause["root_cause"] = cause.get("description","") or cause.get("cause","")
    outputs["fivewhy_results"] = []  # tidak di-generate, disimpan kosong

    # 5. Verification plan
    vplan = user_inputs.get("verification_plan") or []
    if not vplan and potential_causes:
        vplan = _build_verification_plan_llm(potential_causes, upstream)
    outputs["verification_plan"] = vplan

    # 6. Verification results (from user input — user fills after fieldwork)
    vresults = user_inputs.get("verification_results") or []
    outputs["verification_results"] = vresults

    # 7. Detailed analysis per cause — reuse existing if vresults count matches
    if vresults:
        _exist_va = _existing_draft_outputs.get("verification_analysis") or []
        _exist_vr = _existing_draft_outputs.get("verification_results") or []
        if _exist_va and len(_exist_va) == len(vresults) and _exist_vr == vresults:
            outputs["verification_analysis"] = _exist_va
        else:
            outputs["verification_analysis"] = _build_verification_analysis_llm(vresults, upstream)
    else:
        outputs["verification_analysis"] = []

    # 8. Root cause summary table
    outputs["root_cause_summary_table"] = _build_rca_summary_table(
        potential_causes, vplan, vresults
    )

    # Gate
    gate = _analyze_gate_evaluate(
        outputs, user_inputs,
        enforce_b_rules=gate_mode["enforce_b_rules"],
    )

    # Validasi LOGIKA root cause — CRITICAL, selalu jalan bila ada confirmed RC
    # (quick & standard, termasuk saat gate PASS). Bukan checklist.
    _logic   = _build_rootcause_logic_md_llm(outputs=outputs, upstream=upstream, project_path=project_path)
    logic_md = _logic.get("md", "")

    # Standard: root cause yang tidak nyambung secara logika → BLOKIR gate.
    # Quick: tidak memblokir (cukup catatan di section validasi).
    if project_path != "quick" and _logic.get("has_red"):
        gate["failed_rules"] = (gate.get("failed_rules") or []) + ["A6_root_cause_logic_invalid"]
        gate["status"] = "FAIL"
        gate["policy"] = "BLOCK"
        gate["next_actions"] = (
            ["Perbaiki framing root cause yang tidak nyambung secara logika (lihat Validasi Logika Root Cause)."]
            + (gate.get("next_actions") or [])
        )

    # Coaching gate — LLM only on FAIL/WEAK_PASS
    gate_status = gate.get("status","")
    if gate_status in ("FAIL","WEAK_PASS"):
        coaching_md = _build_analyze_coaching_llm(gate, outputs, upstream, user_inputs, project_path=project_path)
    else:
        coaching_md = ""

    # Validasi logika selalu di atas; tanpa footer "Gate PASS" yang redundant.
    if logic_md and coaching_md:
        coaching_md = f"{logic_md}\n\n---\n\n{coaching_md}"
    elif logic_md:
        coaching_md = logic_md
    elif not coaching_md:
        coaching_md = "✅ Gate PASS — root causes identified, drilled, and verified."

    analyze_state = {
        "schema_version": ANALYZE_SCHEMA,
        "phase":          "analyze",
        "status":         "draft",
        "project_id":     project_id,
        "created_at":     _now_iso(),
        "updated_at":     _now_iso(),
        "inputs":         user_inputs,
        "meta": {
            "industry":    upstream.get("industry"),
            "pain_theme":  upstream.get("pain_theme"),
            "process_area": upstream.get("process_area"),
            "project_path": project_path,
        },
        "upstream":       upstream,
        "outputs":        outputs,
        "gate":           gate,
        "coaching_md":    coaching_md,
        "insight_md":     coaching_md,
        "routing":        {"policy": gate.get("policy"), "status": gate_status},
    }
    analyze_state["summary_md"] = _build_analyze_summary_md(analyze_state)

    save_fn = getattr(memory, "save_analyze_draft", None)
    if callable(save_fn):
        try: save_fn(project_id, analyze_state)
        except Exception: pass

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
    analyze_state: dict,
    user_feedback: Optional[dict] = None,
) -> dict:
    gate = (analyze_state or {}).get("gate") or {}
    if gate.get("status") == "FAIL":
        return {
            "status":        "blocked",
            "reason":        "Analyze belum bisa di-finalize — masih ada item wajib yang perlu diperbaiki.",
            "gate":          gate,
            "analyze_state": analyze_state,
            "word_path":     None,
        }

    final_state = copy.deepcopy(analyze_state)

    # Merge WIP user-edited data ke final state (antisipasi draft di-generate sebelum data diisi)
    try:
        _wip_final = memory.load_analyze_wip(project_id)
        _outs = final_state.setdefault("outputs", {})

        # Verification results: WIP menang jika draft kosong atau lebih sedikit confirmed RC
        _wip_vr = _wip_final.get("verification_results") or []
        _draft_vr = _outs.get("verification_results") or []
        def _count_confirmed(lst):
            return sum(1 for r in lst if str(r.get("is_root_cause","")).lower()
                       not in ("false","","none","0","❌ no"))
        if _wip_vr and _count_confirmed(_wip_vr) >= _count_confirmed(_draft_vr):
            _outs["verification_results"] = _wip_vr

        # Potential causes: WIP menang jika draft kosong
        _wip_pc = _wip_final.get("potential_causes") or []
        if _wip_pc and not _outs.get("potential_causes"):
            _outs["potential_causes"] = _wip_pc

        # Verification plan: WIP menang jika draft kosong
        _wip_vp = _wip_final.get("verification_plan") or []
        if _wip_vp and not _outs.get("verification_plan"):
            _outs["verification_plan"] = _wip_vp
    except Exception:
        pass

    reset_fn = getattr(memory, "reset_approvals", None)
    if callable(reset_fn):
        # Gunakan lowercase agar match dengan konvensi penyimpanan approval (save_approval/get_phase_approval_status)
        reset_fn(project_id, PHASE_NAME.lower())

    final_state["status"]     = "final"
    final_state["updated_at"] = _now_iso()
    final_state.setdefault("created_at", _now_iso())
    if not final_state.get("summary_md"):
        final_state["summary_md"] = _build_analyze_summary_md(final_state)

    save_fn = getattr(memory, "save_analyze_final", None)
    if callable(save_fn):
        try: save_fn(project_id, final_state)
        except Exception: pass

    delete_fn = getattr(memory, "delete_analyze_draft", None)
    if callable(delete_fn):
        try: delete_fn(project_id)
        except Exception: pass

    episode_fn = getattr(memory, "save_analyze_episode", None)
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
    export_fn = getattr(reporting, "export_analyze_to_word", None)
    if callable(export_fn):
        try:
            word_path = export_fn(final_state, path=f"{project_id}_ANALYZE_REPORT.docx")
        except Exception:
            pass

    return {
        "status":        "finalized",
        "analyze_state": final_state,
        "critique":      [],
        "summary":       final_state.get("summary_md") or _build_analyze_summary_md(final_state),
        "word_path":     word_path,
        "message":       "Analyze finalized (locked).",
    }


# ======================================================
# Process Map Mermaid Helpers (Free-form → Visualization)
# ======================================================

def generate_process_map_graphviz(description: str, upstream_context: dict = None) -> dict:
    """
    Generate Graphviz DOT swimlane diagram dari deskripsi proses free-form.
    Returns {"dot_code": str, "summary": str}
    """
    _ctx = upstream_context or {}
    _industry     = _ctx.get("industry", "")
    _process_area = _ctx.get("process_area", "")
    _ctx_line     = f"Industri: {_industry}, Area proses: {_process_area}" if _industry else ""

    prompt = f"""Kamu adalah process mapping expert dalam DMAIC Six Sigma.
{("Konteks: " + _ctx_line) if _ctx_line else ""}

User mendeskripsikan proses saat ini:
---
{description}
---

Tugas: Buat swimlane process map dalam format Graphviz DOT yang valid dan bisa dirender.

Aturan WAJIB:
1. Format: digraph swimlane {{ rankdir=LR; ... }} — flow kiri ke kanan, swimlane horizontal
2. Setiap aktor/departemen/PIC = satu subgraph cluster tersendiri
3. Jika aktor tidak disebutkan, kelompokkan berdasarkan fungsi (Customer, Internal, Finance, dsb.)
4. Node proses: shape=box, style="rounded,filled"
5. Decision: shape=diamond, style=filled, fillcolor="#fde68a", color="#f59e0b"
6. Edge label untuk kondisi: [label="Ya"] atau [label="Tidak"]
7. Maksimal 15 node total
8. Setiap node HARUS berada dalam satu cluster
9. ID node: huruf singkat tanpa spasi (A, B, C1, dsb.)
10. WAJIB: Label setiap node dengan nomor urut sesuai alur proses: "1. Nama Step", "2. Nama Step", dst. (decision node boleh tanpa nomor)
11. Gunakan warna berbeda per swimlane, contoh warna lane:
    Lane 1: fillcolor="#eff6ff" color="#93c5fd" node fillcolor="#bfdbfe"
    Lane 2: fillcolor="#f0fdf4" color="#86efac" node fillcolor="#bbf7d0"
    Lane 3: fillcolor="#fdf4ff" color="#d8b4fe" node fillcolor="#f3e8ff"
    Lane 4: fillcolor="#fff7ed" color="#fdba74" node fillcolor="#ffedd5"
    Lane 5: fillcolor="#fefce8" color="#fde047" node fillcolor="#fef9c3"

Format PERSIS seperti ini (jangan ada karakter lain sebelum/sesudah digraph):
digraph swimlane {{
    rankdir=LR;
    bgcolor="transparent";
    node [fontname="Helvetica", fontsize=11, margin="0.2,0.12"];
    edge [color="#64748b", arrowsize=0.8, fontsize=9, fontname="Helvetica"];

    subgraph cluster_Aktor1 {{
        label="Nama Aktor 1";
        style=filled; fillcolor="#eff6ff"; color="#93c5fd";
        node [fillcolor="#bfdbfe", color="#3b82f6", shape=box, style="rounded,filled"];
        A [label="1. Step 1"];
        B [label="2. Step 2"];
    }}

    subgraph cluster_Aktor2 {{
        label="Nama Aktor 2";
        style=filled; fillcolor="#f0fdf4"; color="#86efac";
        node [fillcolor="#bbf7d0", color="#16a34a", shape=box, style="rounded,filled"];
        C [label="3. Step 3"];
        D [label="Cek OK?", shape=diamond, fillcolor="#fde68a", color="#f59e0b"];
    }}

    A -> B -> C -> D;
    D -> E [label="Ya"];
    D -> F [label="Tidak"];
}}

Balas HANYA dengan:
1. DOT code dalam blok ```dot ... ```
2. Satu kalimat summary singkat setelah blok"""

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


def revise_process_map_graphviz(current_dot: str, revision_request: str) -> dict:
    """
    Revisi Graphviz DOT swimlane berdasarkan permintaan user.
    Returns {"dot_code": str}
    """
    prompt = f"""Kamu adalah process mapping expert.

Graphviz DOT swimlane saat ini:
```dot
{current_dot}
```

User meminta revisi:
---
{revision_request}
---

Tugas: Modifikasi DOT code di atas sesuai permintaan. Pertahankan struktur/node yang tidak berubah. Pastikan syntax DOT valid dan bisa dirender oleh Graphviz.

Balas HANYA dengan DOT code dalam blok ```dot ... ```. Tidak perlu penjelasan."""

    raw = llm(prompt)

    dot_code = current_dot  # fallback ke yang lama
    m = re.search(r"```dot\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        dot_code = m.group(1).strip()
    else:
        m2 = re.search(r"(digraph\s+\w+\s*\{.*\})", raw, re.DOTALL)
        if m2:
            dot_code = m2.group(1).strip()

    return {"dot_code": dot_code}


# Backward-compat aliases (lama → baru)
def generate_process_map_mermaid(description: str, upstream_context: dict = None) -> dict:
    r = generate_process_map_graphviz(description, upstream_context)
    return {"mermaid_code": r.get("dot_code", ""), "summary": r.get("summary", "")}

def revise_process_map_mermaid(current_mermaid: str, revision_request: str) -> dict:
    r = revise_process_map_graphviz(current_mermaid, revision_request)
    return {"mermaid_code": r.get("dot_code", "")}


def link_causes_to_process_map(dot_code: str, x_causes: list) -> dict:
    """
    Update Graphviz DOT swimlane dengan penanda ⚡ pada node yang terkait
    dengan potential root causes (Xn) dari fishbone/5-Why analysis.
    Returns {"dot_code": str, "linked_nodes": list}
    """
    if not dot_code or not x_causes:
        return {"dot_code": dot_code, "linked_nodes": []}

    causes_text = "\n".join([
        f"- {c.get('xn','')}: {c.get('cause','').strip()} (sumber: {c.get('source','').strip()})"
        for c in x_causes
        if c.get('cause','').strip()
    ])

    prompt = f"""Kamu adalah Six Sigma process mapping expert dengan keahlian Root Cause Analysis.

Graphviz DOT swimlane diagram proses saat ini:
```dot
{dot_code}
```

Daftar potential root causes (Xn) yang sudah diidentifikasi dari fishbone dan 5-Why:
{causes_text}

Tugas:
1. Untuk setiap cause di atas, identifikasi node proses mana yang paling berkaitan secara kausal
2. Update label node tersebut: tambahkan "\\n⚡ X1" (atau "\\n⚡ X1, X3" jika ada beberapa)
3. Ubah style node yang ditandai menjadi: fillcolor="#fef3c7", color="#f59e0b", penwidth=2.5, fontcolor="#78350f", style="filled,bold"
4. Node yang TIDAK terkait: pertahankan style aslinya PERSIS sama
5. Jangan hapus, pindahkan, atau ubah structure/edge apapun
6. Pastikan output DOT code valid dan bisa dirender Graphviz
7. Jika satu cause bisa dipetakan ke beberapa node, pilih yang PALING langsung/relevan saja

Return JSON:
{{
  "dot_code": "...(complete updated DOT code, ganti seluruh DOT yang lama)...",
  "linked_nodes": [
    {{"node_id": "A", "node_label": "Step Name", "linked_causes": ["X1"]}}
  ]
}}

Penting: Output HARUS berupa JSON valid. dot_code harus berisi SELURUH DOT code yang sudah diupdate."""

    try:
        raw = llm(prompt)
        parsed = extract_json_from_llm(raw)
        if parsed and parsed.get("dot_code"):
            # Bersihkan dot_code dari markdown fences jika ada
            _dc = parsed["dot_code"].strip()
            _dc = re.sub(r"^```dot\s*", "", _dc)
            _dc = re.sub(r"\s*```$", "", _dc)
            return {
                "dot_code": _dc.strip(),
                "linked_nodes": parsed.get("linked_nodes", [])
            }
    except Exception:
        pass

    # Fallback: coba extract DOT saja dari raw response
    try:
        m = re.search(r"```dot\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            return {"dot_code": m.group(1).strip(), "linked_nodes": []}
        m2 = re.search(r"(digraph\s+\w+\s*\{.*\})", raw, re.DOTALL)
        if m2:
            return {"dot_code": m2.group(1).strip(), "linked_nodes": []}
    except Exception:
        pass

    return {"dot_code": dot_code, "linked_nodes": []}