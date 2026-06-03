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
from app.agents.classification_agent import get_gate_mode

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

    # Time-bound: detect month names (EN + ID), standalone year, quarters, ranges,
    # explicit dates, or time keywords. Bahasa Indonesia is fully supported so a goal
    # like "...pada akhir Desember 2018" is correctly recognized as time-bound.
    timebound = bool(re.search(
        r"\b("
        r"q[1-4]|quarter|kuartal|triwulan|semester|"
        r"bulan\w*|month\w*|mingg\w*|week\w*|tahun\w*|year\w*|"
        # Month names — English + Bahasa Indonesia
        r"jan(uari|uary)?|feb(ruari|ruary)?|mar(et|ch)?|apr(il)?|mei|may|jun(i|e)?|jul(i|y)?|"
        r"agu(stus)?|aug(ust)?|sep(tember)?|okt(ober)?|oct(ober)?|nov(ember)?|des(ember)?|dec(ember)?|"
        r"by\s+\d{4}|"
        r"\d{4}\s*[-–—]\s*\d{4}|"
        r"\d{4}-\d{2}-\d{2}|"
        r"(19|20)\d{2}"          # standalone year, e.g. "akhir 2018"
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
    enforce_b_rules: bool = True,
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
    "orphan_ctqs": [],
}

    if not p_ok:
        failed.append("A1_problem_not_smart_enough")
    if not g_ok:
        failed.append("A1_goal_not_smart_enough")
    # A1b: Goal harus mirror problem — metrik yang sama, direction berlawanan
    if p_ok and g_ok and problem and goal:
        # Extract angka dari problem dan goal
        p_numbers = set(re.findall(r'\d+(?:[.,]\d+)?', problem))
        g_numbers = set(re.findall(r'\d+(?:[.,]\d+)?', goal))

        # Extract key metric words (noun phrases around numbers)
        p_lower = problem.lower()
        g_lower = goal.lower()

        # Cek apakah ada angka yang sama atau setidaknya ada overlap metric context
        # Heuristic: goal harus menyebut setidaknya 1 kata kunci yang ada di problem
        p_keywords = set(re.findall(r'\b[a-z]{4,}\b', p_lower)) - {
            "that", "with", "from", "this", "have", "been", "were", "will",
            "their", "which", "when", "where", "what", "then", "than",
            "pada", "dari", "dengan", "untuk", "yang", "dalam", "telah",
            "adalah", "sebesar", "antara", "hingga", "selama",
        }
        g_keywords = set(re.findall(r'\b[a-z]{4,}\b', g_lower))
        keyword_overlap = p_keywords & g_keywords

        # Mirror check: minimal ada 1 shared keyword DAN (shared number OR improvement verb)
        improvement_verbs = bool(re.search(
            r'\b(reduce|increase|improve|achieve|reach|decrease|meningkat|menurun|mencapai|'
            r'memperbaiki|mengurangi|meningkatkan)\b',
            g_lower
        ))

        if not keyword_overlap and not improvement_verbs:
            conditional.append("A1b_goal_not_mirroring_problem")
        elif not keyword_overlap and improvement_verbs:
            # Goal punya improvement verb tapi tidak ada keyword overlap
            conditional.append("A1b_goal_metric_mismatch_with_problem")

    # A1: Charter — hanya enforce untuk Standard path
    charter_confirmed = bool(gate_evidence.get("charter_confirmed"))
    if not charter_confirmed and enforce_b_rules:
        failed.append("A1_charter_not_confirmed")

    # A2: SIPOC — hanya enforce untuk Standard path
    sipoc = outputs.get("sipoc") or {}
    sipoc_customers = sipoc.get("Customers") or sipoc.get("customers") or []
    sipoc_outputs   = sipoc.get("Outputs")   or sipoc.get("outputs")   or []
    sipoc_process   = sipoc.get("Process")   or sipoc.get("process")   or []
    if enforce_b_rules:
        if not (isinstance(sipoc_customers, list) and len(sipoc_customers) > 0):
            failed.append("A2_sipoc_missing_customers")
        if not (isinstance(sipoc_outputs, list) and len(sipoc_outputs) > 0):
            failed.append("A2_sipoc_missing_outputs")
        if not (isinstance(sipoc_process, list) and len(sipoc_process) > 0):
            failed.append("A2_sipoc_missing_process")

    # A3: CTQ measurability — hanya enforce untuk Standard path
    ctq_list = outputs.get("ctq_list") or outputs.get("ctq") or []
    approved_ctq_count = 0
    if isinstance(ctq_list, list):
        for ctq in ctq_list:
            if isinstance(ctq, dict):
                name   = (ctq.get("name")   or "").strip()
                metric = (ctq.get("metric") or ctq.get("measure") or "").strip()
                unit   = (ctq.get("unit")   or "").strip()
                if name and (metric or unit):
                    approved_ctq_count += 1
    if approved_ctq_count < 1 and enforce_b_rules:
        failed.append("A3_no_measurable_ctq_defined")

    # A4: VOC/VOB → CTQ traceability (standard path only)
    if enforce_b_rules:
        voc_table = user_inputs.get("voc_table") or []
        if isinstance(voc_table, list) and len(voc_table) > 0:
            # Kumpulkan CTQ references dari VOC table
            voc_ctq_refs = set()
            for row in voc_table:
                if isinstance(row, dict):
                    ref = (row.get("CTQ/CTB") or row.get("ctq") or "").strip().lower()
                    if ref:
                        # Split by comma untuk handle multiple CTQ per row
                        for part in re.split(r'[,;/]', ref):
                            part = part.strip()
                            if part:
                                voc_ctq_refs.add(part)

            # Cek setiap CTQ apakah ter-trace ke minimal 1 VOC entry
            orphan_ctqs = []
            if isinstance(ctq_list, list):
                for c in ctq_list:
                    if not isinstance(c, dict):
                        continue
                    ctq_name = (c.get("name") or "").strip().lower()
                    if not ctq_name:
                        continue
                    # Fuzzy match: cek apakah ada VOC ref yang mengandung CTQ name atau vice versa
                    matched = any(
                        ctq_name in ref or ref in ctq_name or
                        any(word in ref for word in ctq_name.split() if len(word) > 3)
                        for ref in voc_ctq_refs
                    )
                    if not matched:
                        orphan_ctqs.append(c.get("name", ctq_name))

            if orphan_ctqs:
                conditional.append("A4_ctq_not_traceable_to_voc")
                # Store orphan names for coaching
                debug_smart["orphan_ctqs"] = orphan_ctqs
        elif isinstance(ctq_list, list) and len(ctq_list) > 0:
            # CTQ ada tapi VOC table kosong
            conditional.append("A4_voc_missing_but_ctq_exists")

    # B1: Similar project history
    if gate_evidence.get("similar_project_exists") is True:
        note = (gate_evidence.get("similar_project_note") or "").strip()
        if not note:
            conditional.append("B1_similar_project_exists_no_review_note")

    # B2: Parallel/conflicting projects
    parallel_risk = (gate_evidence.get("parallel_projects_risk") or "").strip()
    if parallel_risk:
        conditional.append("B2_parallel_projects_risk_noted")

# B3: Benefit estimate — standard path requires uploaded KPI table
    if enforce_b_rules:
        benefit_upload = user_inputs.get("benefit_data_uploaded") or {}
        if isinstance(benefit_upload, dict) and benefit_upload.get("available"):
            kpi_table = benefit_upload.get("kpi_table") or []
            has_valid_kpi = any(
                str(r.get("baseline","")).strip() not in ("","nan") and
                str(r.get("target","")).strip() not in ("","nan")
                for r in kpi_table if isinstance(r, dict)
            )
            if not kpi_table:
                conditional.append("B3_benefit_kpi_table_missing")
            elif not has_valid_kpi:
                conditional.append("B3_benefit_kpi_missing_baseline_or_target")
        else:
            justification = (user_inputs.get("benefit_no_upload_reason") or "").strip()
            if not justification:
                conditional.append("B3_benefit_not_uploaded_no_justification")
            else:
                conditional.append("B3_benefit_not_uploaded_with_justification")
    else:
        # Quick path: cukup cek string benefit_estimate
        benefit = (gate_evidence.get("benefit_estimate") or
                   str(user_inputs.get("benefit_data_uploaded") or "")).strip()
        if not benefit:
            conditional.append("B3_benefit_estimate_missing")    # B3: Benefit estimate — standard path requires uploaded KPI table


    # B4: Project Charter upload (standard path only)
    if enforce_b_rules:
        charter_upload = user_inputs.get("charter_data_uploaded") or {}
        if isinstance(charter_upload, dict) and charter_upload.get("available"):
            # Cek completeness
            required_fields = ["project_leader", "champion", "process_owner"]
            missing_fields = [f for f in required_fields
                              if not str(charter_upload.get(f, "")).strip()
                              or charter_upload.get(f, "").strip() in ("TBD", "nan", "")]
            if missing_fields:
                conditional.append(f"B4_charter_incomplete_fields_missing")
            if not charter_upload.get("timeline"):
                conditional.append("B4_charter_timeline_missing")
        else:
            justification = (user_inputs.get("charter_no_upload_reason") or "").strip()
            if not justification:
                conditional.append("B4_charter_not_uploaded_no_justification")
            else:
                conditional.append("B4_charter_not_uploaded_with_justification")

    # Decide status — B-rules downgraded to advisory for quick path
    if failed:
        status = "FAILED"
    elif conditional and enforce_b_rules:
        status = "CONDITIONAL"
    else:
        status = "PASSED"

    # Short agent summary
    if status == "FAILED":
        agent_summary = "Define belum lolos gate. Perbaiki item kritis yang masih kurang sebelum melanjutkan."
    elif status == "CONDITIONAL":
        agent_summary = "DEFINE gate CONDITIONAL. Boleh finalize, tetapi selesaikan kondisi yang dicatat untuk mengurangi risiko."
    else:
        agent_summary = "DEFINE gate PASSED. Deliverable DEFINE sudah siap dan konsisten."

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
    project_path: str = "standard",
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
        strengths.append("Problem statement sudah terdokumentasi.")
    if (outputs.get("goal_statement") or "").strip():
        strengths.append("Goal statement sudah terdokumentasi.")
    if outputs.get("sipoc"):
        strengths.append("SIPOC sudah tersedia.")
    if isinstance(outputs.get("ctq_list"), list) and len(outputs.get("ctq_list")) > 0:
        strengths.append("Daftar CTQ/CTB sudah tersedia.")

    # gaps based on rules
    if "A1_charter_not_confirmed" in failed:
        gaps.append("Project charter belum dikonfirmasi (konfirmasi user belum ada).")
        next_actions.append("Konfirmasi setup project charter (centang checkbox) sebelum finalize.")

    if "A1_problem_not_smart_enough" in failed:
        gaps.append("Problem statement belum cukup SMART (bukti measurable dan/atau time-bound belum ada).")
        next_actions.append("Tambahkan/perjelas metrik yang terukur (angka + satuan) dan rentang waktu (tanggal/periode) tanpa mengubah inti masalah.")

    if "A1_goal_not_smart_enough" in failed:
        gaps.append("Goal statement belum cukup SMART (bukti measurable dan/atau time-bound belum ada).")
        next_actions.append("Tambahkan/perjelas target numerik + deadline (jaga scope/metrik tetap konsisten dengan problem).")

    if "A1b_goal_not_mirroring_problem" in conditional:
        gaps.append("Goal statement tidak mencerminkan problem statement — metrik berbeda atau tidak ada keterkaitan yang jelas.")
        next_actions.append(
            "Pastikan goal menggunakan metrik yang sama dengan problem. "
            "Contoh: jika problem menyebut 'output 38 ton/shift', goal harus menyebut target output per shift juga, bukan metrik lain."
        )
    if "A1b_goal_metric_mismatch_with_problem" in conditional:
        gaps.append("Goal mengandung improvement verb tapi metrik yang disebutkan tidak konsisten dengan problem.")
        next_actions.append(
            "Cek kembali: apakah angka/unit di goal sama dengan yang di problem? "
            "Goal harus menjadi 'bayangan positif' dari problem — masalah yang sama, kondisi target yang berbeda."
        )
        
    if "A4_ctq_not_traceable_to_voc" in conditional:
        orphans = gate_result.get("debug_smart", {}).get("orphan_ctqs") or []
        orphan_str = ", ".join([f"'{x}'" for x in orphans]) if orphans else "beberapa CTQ"
        gaps.append(f"CTQ tidak ter-trace ke VOC/VOB: {orphan_str} tidak ditemukan di kolom CTQ/CTB tabel VOC.")
        next_actions.append(
            f"Untuk setiap CTQ yang tidak ter-trace ({orphan_str}): "
            "tambahkan baris VOC/VOB yang menghasilkan CTQ tersebut, atau hapus CTQ jika tidak ada voice yang mendukungnya."
        )
    if "A4_voc_missing_but_ctq_exists" in conditional:
        gaps.append("CTQ sudah ada tapi tabel VOC/VOB kosong — traceability tidak bisa diverifikasi.")
        next_actions.append(
            "Isi tabel VOC/VOB dengan minimal 1 entry per CTQ. "
            "Kolom CTQ/CTB di tabel VOC harus mencantumkan nama CTQ yang dihasilkan dari voice tersebut."
        )

    # conditional coaching
    if "B1_similar_project_exists_no_review_note" in conditional:
        gaps.append("Similar project exists but not reviewed/documented.")
        next_actions.append("Add note: what to reuse vs what differs from prior project.")
    if "B2_parallel_projects_risk_noted" in conditional:
        risks.append("Parallel/overlapping projects may affect scope, data, or ownership.")
    if "B3_benefit_estimate_missing" in conditional:
        gaps.append("High-level benefit estimate is missing.")
        next_actions.append("Add benefit estimate (type + order of magnitude).")
    if "B3_benefit_kpi_table_missing" in conditional:
        gaps.append("Benefit estimate tidak memiliki KPI table.")
        next_actions.append("Tambah KPI table: kolom KPI, periode, unit, baseline, target, delta%, asumsi.")
    if "B3_benefit_kpi_missing_baseline_or_target" in conditional:
        gaps.append("KPI table ada tapi baseline atau target kosong.")
        next_actions.append("Lengkapi baseline (kondisi saat ini) dan target (kondisi yang ingin dicapai) untuk setiap KPI.")
    if "B3_benefit_not_uploaded_no_justification" in conditional:
        gaps.append("Benefit estimate template belum diupload dan tidak ada justifikasi.")
        next_actions.append("Download template benefit estimate, isi KPI table (baseline + target), lalu upload kembali. Atau berikan justifikasi mengapa tidak bisa diupload sekarang.")
    if "B3_benefit_kpi_table_missing" in conditional:
        gaps.append("File benefit estimate diupload tapi KPI table kosong.")
        next_actions.append("Isi minimal 1 baris KPI table dengan baseline dan target yang terukur.")
    if "B3_benefit_kpi_missing_baseline_or_target" in conditional:
        gaps.append("KPI table ada tapi baseline atau target kosong.")
        next_actions.append("Lengkapi kolom Baseline (BSL) dan Target untuk setiap KPI.")
    if "B4_charter_not_uploaded_no_justification" in conditional:
        gaps.append("Project charter template belum diupload dan tidak ada justifikasi.")
        next_actions.append("Download template charter, isi semua roles dan timeline, lalu upload kembali. Atau berikan justifikasi.")
    if "B4_charter_incomplete_fields_missing" in conditional:
        gaps.append("Charter diupload tapi ada role penting yang kosong (Project Leader/Champion/Process Owner).")
        next_actions.append("Lengkapi semua role di sheet Project Charter dan upload ulang.")
    if "B4_charter_timeline_missing" in conditional:
        gaps.append("Charter diupload tapi timeline tidak diisi.")
        next_actions.append("Isi kolom Goal (End Date) untuk setiap milestone di sheet Project Charter.")
    # risks based on status
    if status == "FAILED":
        risks.append("Lanjut tanpa memperbaiki item gate kritis akan membuat fase berikutnya tidak valid.")
    elif status == "CONDITIONAL":
        risks.append("Lanjut dengan kondisi yang belum diselesaikan meningkatkan risiko rework.")

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
            quick_wins_md = "\n### Quick Wins (dari project sebelumnya)\n" + "\n".join(lines)

    # Build MD
    md = []
    md.append(f"### DEFINE Gate Status: **{status}**")
    if project_path == "quick":
        md.append("> ⚡ **Quick Improvement** — coaching difokuskan pada problem & goal statement saja. Item rigor Standard (charter, VOC/CTQ traceability, SIPOC, benefit estimate) tidak diwajibkan.")
    md.append(f"- {gate_result.get('agent_summary','').strip()}")

    if strengths:
        md.append("\n### Kekuatan")
        md.extend([f"- {s}" for s in strengths[:4]])

    if gaps:
        md.append("\n### Kekurangan")
        md.extend([f"- {g}" for g in gaps[:6]])

    if next_actions:
        md.append("\n### Yang perlu diperbaiki (berurutan)")
        md.extend([f"{i+1}. {a}" for i, a in enumerate(next_actions[:6])])

    if risks:
        md.append("\n### Risiko jika dilanjutkan")
        md.extend([f"- {r}" for r in risks[:3]])

    if quick_wins_md:
        md.append(quick_wins_md)

    return "\n".join(md).strip()

# ======================================================
# COACHING STEP — LLM-driven contextual coaching
# ======================================================

def coaching_step(
    user_inputs: Dict[str, Any],
    outputs: Dict[str, Any],
    gate_result: Dict[str, Any],
    project_path: str = "standard",
) -> str:
    """
    LLM-driven contextual coaching. Called from run_define_agent()
    only when gate status is FAILED or CONDITIONAL.
    Falls back to deterministic coaching if LLM fails or returns empty.
    """
    failed      = gate_result.get("failed_rules") or []
    conditional = gate_result.get("conditional_rules") or []
    status      = gate_result.get("status", "UNKNOWN")

    problem_text = (user_inputs.get("problem_text") or "").strip()
    goal_text    = (user_inputs.get("goal_text") or "").strip()

    # VOC: prefer structured table, fallback to flattened list
    # Build VOC analysis untuk coaching
    voc_table = user_inputs.get("voc_table") or []
    voc_analysis_lines = []
    if isinstance(voc_table, list) and voc_table:
        for row in voc_table:
            if not isinstance(row, dict):
                continue
            voice = (row.get("Voice") or "").strip()
            key_issue = (row.get("Key Issue") or "").strip()
            ctq_ref = (row.get("CTQ/CTB") or "").strip()
            voc_type = (row.get("Type") or "VOC").strip()
            if voice:
                voc_analysis_lines.append(
                    f"- [{voc_type}] \"{voice}\" → Issue: \"{key_issue}\" → CTQ/CTB: \"{ctq_ref}\""
                )
        voc_summary = "\n".join(voc_analysis_lines[:8]) if voc_analysis_lines else "Not provided"
    else:
        voc_summary = "Not provided"

    problem_statement = (outputs.get("problem_statement") or "").strip()[:300]
    goal_statement    = (outputs.get("goal_statement") or "").strip()[:300]
    ctq_list          = outputs.get("ctq_list") or []
    ctq_summary       = ", ".join(
        [c.get("name", "") for c in ctq_list if isinstance(c, dict) and c.get("name")][:5]
    ) or "None identified"

    
    prompt = f"""
You are a Lean Six Sigma Master Black Belt reviewing the Define phase of a manufacturing process improvement project.

Execution path: {"Quick Improvement" if project_path == "quick" else "Standard DMAIC"}
Gate Status: {status}
Failed rules: {json.dumps(failed)}
Conditional rules: {json.dumps(conditional)}
{"Note: This is a Quick Improvement project — coaching should be concise, focus only on the truly critical/mandatory gaps, and avoid requiring the heavier formal deliverables that are simplified in the Quick path." if project_path == "quick" else ""}
IMPORTANT: write for a non-technical project leader. Use plain business language. NEVER mention internal rule codes or terms like "A-rules", "B-rules", or codes such as "A1_...".

Project leader's original inputs:
- Problem (raw): "{problem_text}"
- Goal (raw): "{goal_text}"
- VOC / VOB entries:
{voc_summary}

System-generated outputs:
- Problem Statement: "{problem_statement}"
- Goal Statement: "{goal_statement}"
- CTQs identified ({len(ctq_list)}): {ctq_summary}

Provide PRACTICAL coaching as an MBB to the project leader. Hard rules:

0. Write the ENTIRE coaching in Bahasa Indonesia. Keep Lean Six Sigma / DMAIC terminology in English
   (e.g. CTQ, CTB, VOC, VOB, SIPOC, baseline, Cp, Cpk, gate, root cause, charter). Do NOT translate these terms.

1. SCOPE LOCK — coach ONLY on items that literally appear in "Failed rules" or "Conditional rules" above.
   - DO NOT invent new requirements. If something is NOT in those two lists, do NOT raise it.
   - In particular: do NOT demand a financial/monetary target in the goal, do NOT demand mirror
     validation, and do NOT demand VOC/CTQ changes UNLESS the corresponding rule
     (A1b_* for mirror, A4_* for VOC/CTQ) is actually present in the lists.
   - The goal is to get the project leader UNBLOCKED with the fewest essential fixes — not to pursue perfection.
   - A Define goal only needs to be SMART (measurable target + deadline) AND mirror the problem.
     NEVER ask HOW the goal will be achieved (no audit process, training, solutions, action steps) —
     that is the Improve phase, NOT a Define deliverable. Do NOT lengthen the goal with implementation detail.
   - If the user's goal already matches a previously suggested version and is SMART + mirrors the problem,
     declare it sufficient. Do NOT fabricate new objections to keep it failing.

2. Structure the output into at most two sections, and OMIT a section entirely if it would be empty:

   ### 🔴 Wajib diperbaiki (memblokir gate)
   - One bullet per rule in "Failed rules" ONLY (these are the A-rules that block finalize).
   - For each: quote the user's exact problem/goal text, say specifically what is missing, and give
     ONE concrete corrected example tied to that specific rule. Keep it minimal and copy-paste ready.

   ### 🟡 Saran opsional (tidak memblokir gate)
   - One short bullet per rule in "Conditional rules" ONLY. Frame as optional refinement, clearly
     state it does NOT block finalize. No long lectures.

3. If "Failed rules" is EMPTY: do NOT say the phase failed. Open with a brief line that the gate is not
   blocked and the leader may finalize, then list only the optional suggestions (if any).

4. Mirror validation (A1b_*) and VOC/CTQ consistency (A4_*) belong under the OPTIONAL section when flagged,
   never as blockers.

5. Tone: supportive MBB, not an error list. Be concise and actionable.
6. Format: markdown with ### headings and bullet points. Maximum 220 words. Start directly — no preamble.
""".strip()

    try:
        raw = llm(prompt)
        if raw and len(raw.strip()) > 80:
            return raw.strip()
    except Exception:
        pass

    # Fallback to deterministic coaching if LLM fails
    return build_define_coaching_summary_md(
        gate_result=gate_result,
        outputs=outputs,
        gate_evidence={},
        prior_quick_wins=None,
        project_path=project_path,
    )

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
    project_path: str = "standard",
) -> Dict[str, Any]:
    
    gate_mode = get_gate_mode(project_path)
    path_instruction = (
            "This is a QUICK IMPROVEMENT project. Keep outputs concise and focused on essentials. "
            "SIPOC can be high-level (3-5 items per element). CTQ: 1-2 items maximum. "
            "Benefit estimate: order of magnitude only, no detailed KPI table required."
            if project_path == "quick"
            else
            "This is a STANDARD DMAIC project. Provide complete and rigorous outputs. "
            "Full SIPOC, 2-4 CTQs with complete metrics, detailed benefit estimate with KPI table."
        )


    similar_memory = memory.retrieve_similar_define_episodes(
        user_inputs.get("industry"),
        user_inputs.get("pain_theme"),
        k=2,
    )

    if project_path == "quick":
        prompt = f"""
ACTION STEP — QUICK IMPROVEMENT PROJECT (A3 thinking)

You are a Lean Six Sigma Master Black Belt facilitating a Quick Improvement project.
This is NOT a full DMAIC — it follows A3 thinking: concise, action-oriented, one page.

Generate ONLY these outputs — nothing more:

1. business_case: 2-3 sentences max. Why does this matter to the business?
2. problem_statement: SMART, 2-3 sentences. Quote specific numbers from user input.
3. goal_statement: Mirror problem with target state + deadline. Max 2 sentences.
4. project_scope: In scope = 1-3 bullet points (process only). Out of scope = 1-2 items.
5. key_stakeholders: List of roles involved (not SIPOC — just who is affected/responsible).
6. ctq_list: EXACTLY 1 item — the single most direct measurable indicator of success.
   Format: [{{"name":"...", "metric":"...", "unit":"...", "description":"..."}}]
7. y_variable: Same as the 1 CTQ name above.
8. x_categories: Top 3 probable root causes (A3 "Analyze" quadrant — lightweight).
   Format: {{"Man": "...", "Method": "...", "Machine/Material": "..."}}
   Use only the most likely 3M categories based on the problem. Do not fabricate all 6M.
9. estimated_benefit: 1 sentence, order of magnitude only. No KPI table.
10. risk_level: "low" or "medium" only for quick projects.
11. project_type: "Quick Improvement"

CRITICAL RULES for y_variable and goal_statement:
- y_variable MUST be derived directly from the user's goal_statement text.
- Do NOT infer y_variable from pain_theme, industry, or process_area.
- If goal_statement contains a measurable target (e.g. "output minimal 43 ton per shift"),
  y_variable = that exact metric (e.g. "Output per shift (ton)").
- If goal_statement is empty or vague, use the problem_statement metric.
- NEVER default to generic labels like "cost per ton" unless explicitly stated in user input.

Return JSON:
{{
  "business_case": "...",
  "problem_statement": "...",
  "goal_statement": "...",
  "project_scope": {{"in_scope": [...], "out_of_scope": [...]}},
  "key_stakeholders": [...],
  "ctq_list": [{{"name":"...", "metric":"...", "unit":"...", "description":"..."}}],
  "y_variable": "...",
  "x_categories": {{"Man": "...", "Method": "...", "Machine/Material": "..."}},
  "estimated_benefit": "...",
  "risk_level": "...",
  "project_type": "Quick Improvement"
}}

User inputs:
{json.dumps(user_inputs, indent=2)}

Decisions:
{json.dumps(decisions, indent=2)}
""".strip()

    else:
        prompt = f"""
ACTION STEP — DEFINE PHASE (STANDARD DMAIC)

You are a Lean Six Sigma Master Black Belt.
This is a Standard DMAIC project — provide complete and rigorous outputs.

"CRITICAL: project_charter must include all roles and timeline milestones. "
    "Derive roles from user inputs if provided. Use 'TBD' if not mentioned. "
    "Timeline must be derived from goal deadline — work backward from control_end. "
    "Never leave timeline fields empty.",

Create DEFINE outputs that answer:
- Why is this project important? (business case with context and impact)
- What is the problem? (SMART: Specific, Measurable, Agreed, Realistic, Time-bound)
- What is the goal? (mirror problem with target state, also SMART)
- What is the scope?
- SIPOC (high level, use verbs in Process column)
- VOC/VOB → CTQ/CTB: extract Key Issue, translate to measurable CTQ or CTB
- CTQ/CTB must be MEASURABLE — name, metric, unit, direction (reduce/increase)
- Y Variable: single most direct CTQ/CTB representing the problem. Must match goal statement metric.
- Risk level and project type
- Detailed benefit estimate with KPI table

CRITICAL RULES for y_variable:
- y_variable MUST come from the CTQ chain: VOC/VOB → CTQ → y_variable.
- y_variable = the metric field of the most representative CTQ item.
- Do NOT infer from pain_theme or industry.

Return JSON:
{{
  "business_case": "...",
  "problem_statement": "...",
  "goal_statement": "...",
  "project_scope": {{"in_scope": [...], "out_of_scope": [...]}},
  "sipoc": {{
    "Suppliers": [...], "Inputs": [...], "Process": [...],
    "Outputs": [...], "Customers": [...]
  }},
  "ctq_list": [
    {{"name":"...", "metric":"...", "unit":"...", "description":"...", "customer":"..."}}
  ],
  "y_variable": "...",
  "risk_level": "...",
  "project_type": "...",
  "project_charter": {{
    "project_leader": "...",
    "champion": "...",
    "mentor_coach": "...",
    "process_owner": "...",
    "controller": "...",
    "team_members": ["..."],
    "timeline": {{
      "kickoff": "MMM YYYY",
      "define_end": "MMM YYYY",
      "measure_end": "MMM YYYY",
      "analyze_end": "MMM YYYY",
      "improve_end": "MMM YYYY",
      "implement_end": "MMM YYYY",
      "control_end": "MMM YYYY"
    }}
  }},
  "benefit_estimate": {{
    "benefit_potentials": "...",
    "calculation_method": "...",
    "kpi_table": [{{
      "no": 1, "kpi": "...", "period": "...", "unit": "...",
      "baseline": "...", "target": "...", "delta_pct": "...", "comment": "..."
    }}],
    "assumptions": ["..."],
    "soft_benefits": ["..."]
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
    # Charter dianggap "confirmed" bila ada salah satu sinyal nyata dari UI:
    #   (1) flag eksplisit charter_confirmed (state lama),
    #   (2) checkbox persetujuan/sign-off (documentation_agreed), atau
    #   (3) charter ter-upload & berhasil di-extract (charter_data_uploaded.available).
    _charter_uploaded_ok = (
        isinstance(user_inputs.get("charter_data_uploaded"), dict)
        and bool(user_inputs.get("charter_data_uploaded", {}).get("available"))
    )
    gate_evidence = {
        "charter_confirmed": bool(
            user_inputs.get("charter_confirmed")
            or user_inputs.get("documentation_agreed")
            or _charter_uploaded_ok
        ),
        "similar_project_exists": bool(user_inputs.get("similar_project_exists")) if user_inputs.get("similar_project_exists") is not None else None,
        "similar_project_note": (user_inputs.get("similar_project_note") or "").strip(),
        "parallel_projects_risk": (user_inputs.get("parallel_projects_risk") or "").strip(),
        "benefit_estimate": (user_inputs.get("benefit_estimate") or "").strip(),
        "documentation_agreed": bool(user_inputs.get("documentation_agreed")) if user_inputs.get("documentation_agreed") is not None else None,
    }

    outputs = parsed if isinstance(parsed, dict) else {}

    gate_result = evaluate_define_gate(
        outputs=outputs,
        gate_evidence=gate_evidence,
        user_inputs=user_inputs,
        enforce_b_rules=gate_mode["enforce_b_rules"],
    )
    ctq_contract = build_ctq_contract(outputs=outputs, gate_result=gate_result)

    prior_quick_wins = _extract_prior_quick_wins(similar_memory)
    coaching_md = build_define_coaching_summary_md(
        gate_result=gate_result,
        outputs=outputs,
        gate_evidence=gate_evidence,
        prior_quick_wins=prior_quick_wins if prior_quick_wins else None,
        project_path=project_path,
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
        "project_path": project_path,
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
    project_path: str | None = None,
):
    # Load project_path from DB if not provided
    if project_path is None:
        try:
            project_path = memory.load_project_meta(project_id).get("path", "standard")
        except Exception:
            project_path = "standard"

    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception  = perception_step(project_id, user_inputs, baseline_df)
    decisions   = decision_step(perception, user_feedback=user_feedback)
    draft_state = action_step(
        project_id, user_inputs, perception, decisions,
        user_feedback=user_feedback,
        project_path=project_path,
    )

    # ── Contextual LLM coaching — triggered only when gate needs explanation ──
    gate_status = (draft_state.get("gate_result") or {}).get("status", "")
    if gate_status in ("FAILED", "CONDITIONAL"):
        llm_coaching = coaching_step(
            user_inputs=user_inputs,
            outputs=draft_state.get("outputs") or {},
            gate_result=draft_state.get("gate_result") or {},
            project_path=project_path,
        )
        draft_state["coaching_md"] = llm_coaching
        draft_state["insight_md"]  = llm_coaching
    # PASSED: deterministic coaching from action_step() is retained as-is
    # ─────────────────────────────────────────────────────────────────────────

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
