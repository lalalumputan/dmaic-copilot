# app/agents/control_agent.py
from __future__ import annotations

import json
import re
import copy
import numpy as np
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import memory, audit, reporting, llm_engine
from app.agents.classification_agent import get_gate_mode

PHASE_NAME     = "Control"
CONTROL_SCHEMA = "v1.1"


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


def compute_control_limits(values: List[float]) -> Dict[str, Any]:
    """Deterministic I-MR control chart limits. No LLM."""
    if not values or len(values) < 2:
        return {"available": False, "reason": "Insufficient data (min 2 points)."}
    arr  = [float(v) for v in values if v is not None]
    mean = float(np.mean(arr))
    std  = float(np.std(arr, ddof=1))
    return {
        "available":  True,
        "chart_type": "I-MR",
        "center":     round(mean, 4),
        "ucl":        round(mean + 3 * std, 4),
        "lcl":        round(mean - 3 * std, 4),
        "std":        round(std, 4),
        "n":          len(arr),
        "values":     [round(float(v), 4) for v in arr],
        "note":       "Add control chart after 1 year of monitoring data is available.",
    }


def _extract_upstream_context(
    define_final:  Optional[Dict],
    measure_final: Optional[Dict],
    analyze_final: Optional[Dict],
    improve_final: Optional[Dict],
) -> Dict[str, Any]:
    def_outs = (define_final  or {}).get("outputs") or {}
    def_ins  = (define_final  or {}).get("inputs")  or {}
    mea_outs = (measure_final or {}).get("outputs") or {}
    ana_outs = (analyze_final or {}).get("outputs") or {}
    imp_outs = (improve_final or {}).get("outputs") or {}
    perf_sum = (mea_outs.get("process_performance_summary") or {}).get("summary") or {}
    target   = (mea_outs.get("process_performance_summary") or {}).get("target_info") or {}
    rca_table = ana_outs.get("root_cause_summary_table") or []
    confirmed_rc = [r for r in rca_table if "Yes" in str(r.get("is_root_cause",""))]

    selected = imp_outs.get("selected_solution") or {}
    comm_plan = imp_outs.get("communication_plan") or []

    return {
        "problem_statement":   def_outs.get("problem_statement") or def_ins.get("problem_text") or "",
        "goal_statement":      def_outs.get("goal_statement")    or def_ins.get("goal_text")    or "",
        "y_variable":          mea_outs.get("y_variable_confirmed") or def_outs.get("y_variable") or "",
        "ctq_list":            def_outs.get("ctq_list") or [],
        "baseline_mean":       perf_sum.get("mean"),
        "baseline_target":     target,
        "confirmed_root_causes": confirmed_rc,
        "rca_summary_table":   rca_table,
        "selected_solution":   selected.get("solution",""),
        "solution_ctq_impact": selected.get("ctq_ctb_impacted") or [],
        "should_be_process":   imp_outs.get("should_be_process",""),
        "implementation_plan": imp_outs.get("implementation_plan") or [],
        "pilot_plan":          imp_outs.get("pilot_plan") or {},
        "comm_plan":           comm_plan,
        "industry":     def_ins.get("industry",""),
        "process_area": def_ins.get("process_area",""),
        "pain_theme":   def_ins.get("pain_theme",""),
    }


# ======================================================
# LLM FUNCTIONS
# ======================================================

def _build_monitoring_reaction_plan_llm(upstream: dict, user_inputs: dict) -> list:
    """
    Build structured monitoring & reaction plan per KPI.
    Each KPI: KPI, target, frequency, who_collects, who_analyzes,
              who_reports, who_acts, deviation_threshold,
              reaction_steps (3-question decision tree per confirmed root cause),
              escalation_actions (global escalation steps if solutions fail).

    Reaction plan logic (per confirmed root cause):
      Q1: Apakah Root Cause ini terjadi lagi?
          TIDAK -> lanjut ke RC berikutnya
          YA    -> Q2
      Q2: Apakah solusinya masih terpasang (in place)?
          TIDAK -> PASANG ULANG solusi (enforce). Selesai untuk RC ini.
          YA    -> Q3
      Q3: Apakah solusi masih EFEKTIF (KPI/metrik tercapai)?
          YA   -> solusi OK, bukan penyebab -> lanjut ke RC berikutnya
          TIDAK-> solusi GAGAL -> perbaiki/redesign solusi + buka RCA tambahan
    """
    confirmed_rc = upstream.get("confirmed_root_causes") or []
    _rc_payload = [
        {
            "xn": r.get("xn", ""),
            "root_cause": r.get("root_cause", ""),
            "impact": r.get("impact", ""),
            "solution": r.get("solution", "") or r.get("countermeasure", ""),
        }
        for r in confirmed_rc if isinstance(r, dict)
    ]

    prompt = f"""
Anda adalah Master Black Belt Lean Six Sigma yang menyusun monitoring & reaction plan untuk fase Control.
Tulis SEMUA output dalam Bahasa Indonesia. Istilah metodologi (KPI, CTQ, Cp/Cpk, RCA, CAPEX, baseline) tetap dalam bentuk aslinya.

Konteks proyek:
- Problem: "{upstream.get('problem_statement','')}"
- Goal: "{upstream.get('goal_statement','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Target: {upstream.get('baseline_target',{}).get('direction','')} {upstream.get('baseline_target',{}).get('value','')}
- Selected solution: "{upstream.get('selected_solution','')}"
- Process area: "{upstream.get('process_area','')}"
- CTQ list: {json.dumps([c.get('name') or c.get('ctq','') for c in (upstream.get('ctq_list') or []) if isinstance(c,dict)][:4], ensure_ascii=False)}

Confirmed root causes dan solusinya:
{json.dumps(_rc_payload, indent=2, ensure_ascii=False)}

Bangun monitoring plan. Untuk setiap KPI utama:
- Tentukan siapa mengumpulkan data, siapa menganalisis, siapa mengirim laporan, siapa mengambil tindakan
- Tentukan deviation threshold
- Buat reaction plan yang menelusuri balik ke setiap confirmed root cause memakai decision tree 3 pertanyaan di bawah.

LOGIKA REACTION PLAN (WAJIB) — untuk SETIAP confirmed root cause, buat TEPAT 3 langkah berurutan (Q1, Q2, Q3):
- Q1 (Recurrence): "Apakah Root Cause [Xn — deskripsi] terjadi lagi?"
    JIKA TIDAK -> "Lanjut ke Root Cause berikutnya."
    JIKA YA    -> "Lanjut ke Q2."
- Q2 (In place): "Apakah solusi [nama solusi] masih terpasang (in place)?"
    JIKA TIDAK -> "PASANG ULANG solusi (enforce). Selesai untuk RC ini."
    JIKA YA    -> "Lanjut ke Q3."
- Q3 (Effective): "Apakah solusi masih EFEKTIF (KPI/metrik tercapai)?"
    JIKA YA    -> "Solusi OK, bukan penyebab. Lanjut ke Root Cause berikutnya."
    JIKA TIDAK -> "Solusi GAGAL. Perbaiki/redesign solusi + buka RCA tambahan."

Jadi jika ada N confirmed root cause, reaction_steps berisi N*3 langkah (Q1,Q2,Q3 untuk RC1, lalu Q1,Q2,Q3 untuk RC2, dst).
Penomoran "step" berurutan mulai dari 1.

escalation_actions: daftar langkah eskalasi global yang dijalankan bila solusi GAGAL atau seluruh RC sudah dicek namun deviasi tetap ada. Gunakan persis:
[
  "Buka RCA baru (cari X di luar daftar)",
  "Uji Cp/Cpk (capable atau tidak?) bila perlu",
  "Validasi ulang target baseline",
  "Eskalasi ke manajemen (CAPEX/strategis) bila perlu"
]

Return JSON array — satu objek per KPI:
[
  {{
    "kpi": "nama KPI",
    "target_value": "target beserta arah",
    "measurement_frequency": "harian/mingguan/bulanan",
    "who_collects": "peran/orang",
    "who_analyzes": "peran/orang",
    "who_sends_report": "peran/orang",
    "who_takes_action": "peran/orang",
    "deviation_threshold": "kondisi pemicu spesifik",
    "control_chart_note": "Tambahkan I-MR control chart setelah 12 bulan data",
    "reaction_steps": [
      {{
        "step": 1,
        "rc_ref": "X1",
        "question": "Q1",
        "check": "Apakah Root Cause X1 — [deskripsi] terjadi lagi?",
        "if_yes": "Lanjut ke Q2.",
        "if_no": "Lanjut ke Root Cause berikutnya."
      }},
      {{
        "step": 2,
        "rc_ref": "X1",
        "question": "Q2",
        "check": "Apakah solusi [nama] masih terpasang (in place)?",
        "if_yes": "Lanjut ke Q3.",
        "if_no": "PASANG ULANG solusi (enforce). Selesai untuk RC ini."
      }},
      {{
        "step": 3,
        "rc_ref": "X1",
        "question": "Q3",
        "check": "Apakah solusi masih EFEKTIF (KPI/metrik tercapai)?",
        "if_yes": "Solusi OK, bukan penyebab. Lanjut ke Root Cause berikutnya.",
        "if_no": "Solusi GAGAL. Perbaiki/redesign solusi + buka RCA tambahan."
      }}
    ],
    "escalation_actions": [
      "Buka RCA baru (cari X di luar daftar)",
      "Uji Cp/Cpk (capable atau tidak?) bila perlu",
      "Validasi ulang target baseline",
      "Eskalasi ke manajemen (CAPEX/strategis) bila perlu"
    ]
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


def _build_handover_protocol_llm(upstream: dict, user_inputs: dict) -> dict:
    """
    Build handover protocol per spec:
    - Should-be process set up?
    - New KPIs agreed and monitoring assured?
    - Who and what needs training?
    - Open issues with responsible person
    - Sign-off required: Project Leader + Process Owner
    """
    prompt = f"""
You are a Lean Six Sigma Master Black Belt building a handover protocol.

Project context:
- Problem solved: "{upstream.get('problem_statement','')}"
- Solution implemented: "{upstream.get('selected_solution','')}"
- Should-be process: "{upstream.get('should_be_process','')}"
- Process area: "{upstream.get('process_area','')}"
- Y Variable: "{upstream.get('y_variable','')}"

User-provided handover context:
- Process owner: "{user_inputs.get('process_owner','')}"
- Open issues: "{user_inputs.get('open_issues','')}"
- Training needs: "{user_inputs.get('training_needs','')}"
- Additional comments: "{user_inputs.get('handover_comments','')}"

Build a complete handover protocol that must be signed by Project Leader AND Process Owner.

Return JSON:
{{
  "should_be_process_set_up": true/false,
  "should_be_process_status": "description of current setup status",
  "new_kpis_agreed": true/false,
  "continuous_monitoring_assured": true/false,
  "monitoring_mechanism": "how monitoring will be sustained",
  "training_plan": [
    {{
      "who": "role/person to be trained",
      "what": "what they need to be trained on",
      "by_when": "timeline",
      "trainer": "who delivers training",
      "status": "pending"
    }}
  ],
  "open_issues": [
    {{
      "issue": "description",
      "responsible": "person/role",
      "due_date": "timeline",
      "status": "open",
      "comments": ""
    }}
  ],
  "sign_off": {{
    "project_leader": {{
      "name": "",
      "role": "Project Leader",
      "signature": "",
      "date": "",
      "confirmed": false
    }},
    "process_owner": {{
      "name": "{user_inputs.get('process_owner','')}",
      "role": "Process Owner",
      "signature": "",
      "date": "",
      "confirmed": false
    }}
  }},
  "comments": "Additional handover notes",
  "handover_complete": false
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


def _build_control_plan_llm(upstream: dict, user_inputs: dict) -> list:
    """Control plan per CTQ/metric."""
    ctq_list = upstream.get("ctq_list") or []
    prompt = f"""
You are a Lean Six Sigma Master Black Belt building a control plan.

CTQ list: {json.dumps([c.get('name') or c.get('ctq','') for c in ctq_list if isinstance(c,dict)][:5], ensure_ascii=False)}
Y Variable: "{upstream.get('y_variable','')}"
Selected solution: "{upstream.get('selected_solution','')}"
Industry: "{upstream.get('industry','')}"

Build a control plan. For each CTQ/key metric:

Return JSON array:
[
  {{
    "ctq": "CTQ or metric name",
    "metric": "specific measurement",
    "spec_limit": "USL/LSL or target range",
    "control_method": "SPC/poka-yoke/SOP/checklist/audit",
    "measurement_frequency": "frequency",
    "sample_size": "n",
    "owner": "role",
    "recording_system": "where data is recorded",
    "reaction_if_ooc": "immediate action if out of control"
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


def _build_sustain_audit_llm(upstream: dict) -> dict:
    """30/60/90 day sustain audit protocol."""
    prompt = f"""
Build a sustain audit protocol for a DMAIC improvement project.

Solution: "{upstream.get('selected_solution','')}"
Process area: "{upstream.get('process_area','')}"
Y Variable: "{upstream.get('y_variable','')}"

Return JSON:
{{
  "audit_frequency": "monthly for 6 months, then quarterly",
  "auditor": "role",
  "30_day_check": "what to verify at 30 days",
  "60_day_check": "what to verify at 60 days",
  "90_day_check": "what to verify at 90 days",
  "checklist_items": ["item 1", "item 2", "item 3"],
  "escalation_if_slipping": "what to do if improvements are slipping"
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


# ======================================================
# GATE EVALUATION
# ======================================================

def _control_gate_evaluate(
    outputs: dict,
    user_inputs: dict,
    enforce_b_rules: bool = True,
) -> dict:
    failed      = []
    conditional = []
    actions     = []

    # A-rules (always enforced)
    mon_plan = outputs.get("monitoring_reaction_plan") or []
    if not mon_plan:
        failed.append("A1_no_monitoring_reaction_plan")
        actions.append("Buat monitoring & reaction plan dengan KPI, frequency, owner, dan RC tracing template.")

    # A2: control plan — Quick path skip (advisory), Standard wajib
    control_plan = outputs.get("control_plan") or []
    if not control_plan and enforce_b_rules:
        failed.append("A2_no_control_plan")
        actions.append("Buat control plan per CTQ: metric, spec limit, control method, owner.")

    # A3: handover protocol — Quick path skip (advisory), Standard wajib
    handover = outputs.get("handover_protocol") or {}
    if not handover:
        if enforce_b_rules:
            failed.append("A3_no_handover_protocol")
            actions.append("Buat handover protocol dan upload confirmation sheet yang ditandatangani Process Owner.")
    else:
        ho_confirmed = bool(handover.get("handover_confirmed")
                            or handover.get("confirmation_sheet_name"))
        if not ho_confirmed and enforce_b_rules:
            conditional.append("B1_handover_confirmation_pending")
            actions.append("Upload handover confirmation sheet yang ditandatangani Process Owner.")

    # B-rules
    sustain = outputs.get("sustain_audit_protocol") or {}
    if not sustain and enforce_b_rules:
        conditional.append("B2_sustain_audit_missing")
        actions.append("Definisikan sustain audit protocol: 30/60/90 day checks.")

    benefit = outputs.get("benefit_validation") or {}
    if not benefit and enforce_b_rules:
        conditional.append("B3_benefit_validation_missing")
        actions.append("Dokumentasikan validated benefit dengan sign-off Finance/Controller.")

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

def _build_control_coaching_llm(
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

    prompt = f"""
You are a Lean Six Sigma Master Black Belt coaching a project leader on the Control phase.

Execution path: {"Quick Improvement" if project_path == "quick" else "Standard DMAIC"}
{"Note: This is a Quick Improvement project — coaching must be concise, focus on critical gaps only (A-rules). Do NOT require a formal Control Plan per CTQ or a full Handover Protocol (B-rule items skipped in Quick path); control chart + monitoring schedule are sufficient." if project_path == "quick" else ""}

Gate Status: {status}
Failed rules: {json.dumps(failed)}
Conditional rules: {json.dumps(conditional)}

Context:
- Problem solved: "{upstream.get('problem_statement','')}"
- Solution: "{upstream.get('selected_solution','')}"
- Y Variable: "{upstream.get('y_variable','')}"
- Process area: "{upstream.get('process_area','')}"
- Process owner: "{user_inputs.get('process_owner','')}"

Next actions: {json.dumps(actions)}

Coach as MBB — explain WHY each control mechanism matters for sustainability.
Emphasize: without proper control, improvements always revert.
Max 300 words, markdown with ### headings. Start directly.
""".strip()

    try:
        raw = llm(prompt)
        if raw and len(raw.strip()) > 80:
            return raw.strip()
    except Exception:
        pass

    lines = [f"### CONTROL Gate: **{status}**"]
    for a in actions[:6]:
        lines.append(f"- {a}")
    return "\n".join(lines)


# ======================================================
# SUMMARY BUILDER
# ======================================================

def _build_control_summary_md(control_state: dict) -> str:
    outputs = control_state.get("outputs") or {}
    gate    = control_state.get("gate") or {}
    pid     = control_state.get("project_id","-")

    mon_plan    = outputs.get("monitoring_reaction_plan") or []
    handover    = outputs.get("handover_protocol") or {}
    benefit     = outputs.get("benefit_validation") or {}
    control_plan = outputs.get("control_plan") or []

    md = [f"## CONTROL — {pid}"]
    md.append(f"**Gate Status:** {gate.get('status','-')}\n")

    if handover:
        sign_off = handover.get("sign_off") or {}
        pl = (sign_off.get("project_leader") or {})
        po = (sign_off.get("process_owner") or {})
        md.append("### Handover Protocol")
        md.append(f"- Process Owner: {po.get('name','-')}")
        md.append(f"- Monitoring assured: {handover.get('continuous_monitoring_assured','-')}")
        md.append(f"- PL Sign-off: {'✅' if pl.get('confirmed') else '⏳ Pending'}")
        md.append(f"- PO Sign-off: {'✅' if po.get('confirmed') else '⏳ Pending'}\n")

    if benefit:
        md.append("### Validated Benefit")
        md.append(f"- {benefit.get('validated_benefit','-')} ({benefit.get('benefit_type','-')})\n")

    if mon_plan:
        md.append(f"### Monitoring Plan ({len(mon_plan)} KPI(s))")
        for k in mon_plan[:3]:
            md.append(f"- **{k.get('kpi','')}** | Target: {k.get('target_value','')} | {k.get('measurement_frequency','')} | Collector: {k.get('who_collects','')}")

    if control_plan:
        md.append(f"\n### Control Plan ({len(control_plan)} items)")

    return "\n".join(md).strip()


# ======================================================
# PERCEPTION
# ======================================================

def perception_step(
    project_id: str,
    user_inputs: dict,
    improve_final: Optional[dict] = None,
    analyze_final: Optional[dict] = None,
    measure_final: Optional[dict] = None,
    define_final:  Optional[dict] = None,
) -> dict:
    upstream = _extract_upstream_context(define_final, measure_final, analyze_final, improve_final)

    similar_memory = []
    retrieve_fn = getattr(memory, "retrieve_similar_control_episodes", None)
    if callable(retrieve_fn):
        try:
            similar_memory = retrieve_fn(upstream.get("industry"), upstream.get("pain_theme"), k=2)
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
    prompt = f"""
DECISION STEP — CONTROL PHASE
You are a Lean Six Sigma Master Black Belt.

Decide control strategy for this improvement.

Return JSON only:
{{
  "control_approach": "...",
  "critical_control_points": ["..."],
  "recommended_control_tools": ["SPC/poka-yoke/SOP/checklist/audit"],
  "handover_approach": "...",
  "sustainability_risks": ["..."]
}}

Solution: "{upstream.get('selected_solution','')}"
Y: "{upstream.get('y_variable','')}"
Goal: "{upstream.get('goal_statement','')}"
Industry: "{upstream.get('industry','')}"
User feedback: {json.dumps(user_feedback or {})}
""".strip()

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    return parsed or {"control_approach": "To be determined", "sustainability_risks": []}


# ======================================================
# MAIN ENTRYPOINT
# ======================================================

def run_control_agent(
    project_id: str,
    user_inputs: dict,
    define_final:  Optional[dict] = None,
    measure_final: Optional[dict] = None,
    analyze_final: Optional[dict] = None,
    improve_final: Optional[dict] = None,
    user_feedback: Optional[dict] = None,
    project_path:  Optional[str]  = None,
) -> dict:

    if project_path is None:
        try:
            project_path = memory.load_project_meta(project_id).get("path","standard")
        except Exception:
            project_path = "standard"

    gate_mode = get_gate_mode(project_path)
    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, improve_final, analyze_final, measure_final, define_final)
    decisions  = decision_step(perception, user_feedback)
    upstream   = perception.get("upstream") or {}

    outputs: dict = {}

    # Load existing draft outputs to reuse expensive LLM results (avoid double-charging)
    _existing_draft_outputs: dict = {}
    try:
        _ed = memory.load_control_draft(project_id)
        if _ed and isinstance(_ed.get("outputs"), dict):
            _existing_draft_outputs = _ed["outputs"]
    except Exception:
        pass

    # 1. Monitoring & Reaction Plan (structured with RC tracing)
    mon_plan = (user_inputs.get("monitoring_reaction_plan")
                or _existing_draft_outputs.get("monitoring_reaction_plan")
                or [])
    if not mon_plan:
        mon_plan = _build_monitoring_reaction_plan_llm(upstream, user_inputs)
    outputs["monitoring_reaction_plan"] = mon_plan

    # 2. Control Plan per CTQ
    control_plan = (user_inputs.get("control_plan")
                    or _existing_draft_outputs.get("control_plan")
                    or [])
    if not control_plan:
        control_plan = _build_control_plan_llm(upstream, user_inputs)
    outputs["control_plan"] = control_plan

    # 3. Handover Protocol
    handover = (user_inputs.get("handover_protocol")
                or _existing_draft_outputs.get("handover_protocol")
                or {})
    if not handover:
        handover = _build_handover_protocol_llm(upstream, user_inputs)
    outputs["handover_protocol"] = handover

    # 4. Sustain Audit Protocol
    sustain = (user_inputs.get("sustain_audit_protocol")
               or _existing_draft_outputs.get("sustain_audit_protocol")
               or {})
    if not sustain:
        sustain = _build_sustain_audit_llm(upstream)
    outputs["sustain_audit_protocol"] = sustain

    # 5. Benefit Validation (user fills)
    outputs["benefit_validation"] = user_inputs.get("benefit_validation") or {}

    # 6. Lessons Learned & Closure
    outputs["lessons_learned"]           = user_inputs.get("lessons_learned") or []
    outputs["project_closure_checklist"] = user_inputs.get("project_closure_checklist") or []

    # 7. Control chart (deterministic, if user provides post-implementation values)
    values = user_inputs.get("control_values") or []
    if isinstance(values, list) and len(values) >= 2:
        from app.utils import charts as _charts
        _ct = (user_inputs.get("chart_type") or "").strip().lower()
        if _ct in ("", "auto"):
            _ct = _charts.recommend_chart_type(
                context={"y_data_type": upstream.get("y_data_type", "")},
                values=values, phase="control",
            )
        outputs["control_chart"] = _charts.build_chart(
            values=values, chart_type=_ct,
            lsl=user_inputs.get("lsl"), usl=user_inputs.get("usl"),
            target=user_inputs.get("target"), phase="control",
        )
    else:
        outputs["control_chart"] = {
            "available": False,
            "note": "Control chart will be added after 12 months of post-implementation data."
        }

    # Chart improved performance yang diupload (gambar, opsional)
    _chart_img = user_inputs.get("chart_image") or {}
    outputs["control_chart_image"] = _chart_img if _chart_img.get("b64") else {}

    # Gate
    gate = _control_gate_evaluate(
        outputs, user_inputs,
        enforce_b_rules=gate_mode["enforce_b_rules"],
    )

    # Coaching — LLM only on FAIL/WEAK_PASS
    gate_status = gate.get("status","")
    if gate_status in ("FAIL","WEAK_PASS"):
        coaching_md = _build_control_coaching_llm(gate, outputs, upstream, user_inputs, project_path=project_path)
    else:
        coaching_md = "✅ Gate PASS — monitoring plan, control plan, and handover protocol complete."

    control_state = {
        "schema_version": CONTROL_SCHEMA,
        "phase":          "control",
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
    control_state["summary_md"] = _build_control_summary_md(control_state)

    save_fn = getattr(memory, "save_control_draft", None)
    if callable(save_fn):
        try: save_fn(project_id, control_state)
        except Exception: pass

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
    control_state: dict,
    user_feedback: Optional[dict] = None,
) -> dict:
    gate = (control_state or {}).get("gate") or {}
    if gate.get("status") == "FAIL":
        return {
            "status":        "blocked",
            "reason":        "CONTROL gate FAIL — selesaikan A-rules sebelum finalize.",
            "gate":          gate,
            "control_state": control_state,
            "word_path":     None,
        }

    final_state = copy.deepcopy(control_state)

    reset_fn = getattr(memory, "reset_approvals", None)
    if callable(reset_fn):
        # Gunakan lowercase agar match dengan konvensi penyimpanan approval (save_approval/get_phase_approval_status)
        reset_fn(project_id, PHASE_NAME.lower())

    final_state["status"]     = "final"
    final_state["updated_at"] = _now_iso()
    final_state.setdefault("created_at", _now_iso())
    if not final_state.get("summary_md"):
        final_state["summary_md"] = _build_control_summary_md(final_state)

    save_fn = getattr(memory, "save_control_final", None)
    if callable(save_fn):
        try: save_fn(project_id, final_state)
        except Exception: pass

    delete_fn = getattr(memory, "delete_control_draft", None)
    if callable(delete_fn):
        try: delete_fn(project_id)
        except Exception: pass

    episode_fn = getattr(memory, "save_control_episode", None)
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
    export_fn = getattr(reporting, "export_control_to_word", None)
    if callable(export_fn):
        try:
            word_path = export_fn(final_state, path=f"{project_id}_CONTROL_REPORT.docx")
        except Exception:
            pass

    return {
        "status":        "finalized",
        "control_state": final_state,
        "critique":      [],
        "summary":       final_state.get("summary_md") or _build_control_summary_md(final_state),
        "word_path":     word_path,
        "message":       "Control finalized. DMAIC project cycle complete.",
    }