# app/agents/measure_agent.py
from __future__ import annotations
import pandas as pd
import numpy as np
import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.utils import (
    data_processing as dp,
    memory,
    audit,
    reporting,
    llm_engine,
)

PHASE_NAME = "Measure"
MEASURE_SCHEMA = "v1.0"


# ======================================================
# Helpers
# ======================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _summarize_define_context(define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Create a compact, schema-agnostic summary of DEFINE FINAL for MEASURE alignment.
    Deterministic. No LLM.
    """
    if not isinstance(define_final, dict):
        return {}

    def _g(*path, default=None):
        cur = define_final
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    return {
        "project_name": _g("inputs", "project_name") or _g("project_name") or "",
        "problem_statement": _g("outputs", "problem_statement") or _g("outputs", "business_problem") or _g("problem_statement") or "",
        "goal_statement": _g("outputs", "goal_statement") or _g("outputs", "project_goal") or _g("goal_statement") or "",
        "ctq": _g("outputs", "ctq") or _g("outputs", "ctq_tree") or "",
        "candidate_y_from_define": _g("outputs", "y_variable") or _g("inputs", "y_variable") or "",
        "scope": _g("outputs", "scope") or _g("inputs", "scope") or "",
        "process_area": _g("meta", "process_area") or _g("inputs", "process_area") or "",
        "stakeholders": _g("outputs", "stakeholders") or _g("inputs", "stakeholders") or "",
    }


def _profile_dataframe(df: Optional[pd.DataFrame], date_col: Optional[str] = None) -> Dict[str, Any]:
    """
    Lightweight dataset profile for Measure (no EDA charts).
    """
    if df is None:
        return {"available": False}

    profile = {
        "available": True,
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "columns": list(df.columns.astype(str)),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "duplicate_rows": int(df.duplicated().sum()),
        "missing_cells": int(df.isna().sum().sum()),
    }

    if date_col and date_col in df.columns:
        try:
            s = pd.to_datetime(df[date_col], errors="coerce")
            profile["date_col"] = date_col
            profile["date_min"] = None if s.dropna().empty else s.min().isoformat()
            profile["date_max"] = None if s.dropna().empty else s.max().isoformat()
            profile["date_nulls"] = int(s.isna().sum())
            if not s.dropna().empty:
                freshness_days = (pd.Timestamp.utcnow() - s.max()).total_seconds() / 86400.0
                profile["freshness_days"] = float(round(freshness_days, 3))
        except Exception:
            profile["date_col"] = date_col
            profile["date_parse_error"] = True

    return profile


def _dq_checks(df: Optional[pd.DataFrame], y_col: Optional[str] = None, date_col: Optional[str] = None) -> Dict[str, Any]:
    """
    Deterministic DQ checks aligned with scope:
    - completeness
    - consistency
    - timeliness
    """
    if df is None:
        return {"available": False}

    out: Dict[str, Any] = {"available": True}

    # completeness
    miss_rate = (df.isna().mean() * 100.0).round(2)
    out["completeness"] = {
        "missing_rate_pct_by_col": miss_rate.to_dict(),
        "columns_with_missing_over_5pct": [c for c, v in miss_rate.items() if v > 5.0],
    }

    # consistency (lightweight, non-domain-specific)
    consistency_issues = []
    if y_col and y_col in df.columns:
        if pd.api.types.is_numeric_dtype(df[y_col]):
            neg = int((df[y_col] < 0).sum(skipna=True))
            if neg > 0:
                consistency_issues.append({"field": y_col, "issue": "negative_values", "count": neg})
        else:
            # y not numeric is not always wrong, but flag for operational definition check
            consistency_issues.append({"field": y_col, "issue": "y_not_numeric_dtype", "dtype": str(df[y_col].dtype)})

    # duplicate rows
    dup = int(df.duplicated().sum())
    if dup > 0:
        consistency_issues.append({"field": "*", "issue": "duplicate_rows", "count": dup})

    out["consistency"] = {"issues": consistency_issues}

    # timeliness
    if date_col and date_col in df.columns:
        try:
            s = pd.to_datetime(df[date_col], errors="coerce")
            timeliness = {
                "date_col": date_col,
                "nulls": int(s.isna().sum()),
                "min": None if s.dropna().empty else s.min().isoformat(),
                "max": None if s.dropna().empty else s.max().isoformat(),
            }
            if not s.dropna().empty:
                freshness_days = (pd.Timestamp.utcnow() - s.max()).total_seconds() / 86400.0
                timeliness["freshness_days"] = float(round(freshness_days, 3))
            out["timeliness"] = timeliness
        except Exception:
            out["timeliness"] = {"date_col": date_col, "parse_error": True}
    else:
        out["timeliness"] = {"date_col": date_col or "", "note": "No date column provided/found."}

    return out


def _compute_baseline_descriptives(
    df: Optional[pd.DataFrame],
    y_col: Optional[str],
    segment_cols: Optional[list] = None
) -> Dict[str, Any]:
    """
    Descriptive-only baseline metrics for process performance.
    No hypothesis tests. No control charts.
    """
    if df is None or not y_col or y_col not in df.columns:
        return {
            "available": False,
            "reason": "baseline_df missing or y column not found",
            "y_col": y_col or "",
        }

    if not pd.api.types.is_numeric_dtype(df[y_col]):
        # Try coercion to numeric if possible
        y_num = pd.to_numeric(df[y_col], errors="coerce")
    else:
        y_num = df[y_col]

    base = y_num.dropna()
    if base.empty:
        return {"available": False, "reason": "Y column has no valid numeric values", "y_col": y_col}

    desc = {
        "available": True,
        "y_col": y_col,
        "count": int(base.shape[0]),
        "missing_y": int(y_num.isna().sum()),
        "mean": float(base.mean()),
        "median": float(base.median()),
        "std": float(base.std(ddof=1)) if base.shape[0] > 1 else 0.0,
        "min": float(base.min()),
        "max": float(base.max()),
        "p25": float(base.quantile(0.25)),
        "p75": float(base.quantile(0.75)),
    }

    # segmentation summaries (top worst by mean)
    seg_out = {}
    segment_cols = segment_cols or []
    for col in segment_cols:
        if col in df.columns:
            try:
                g = df[[col]].copy()
                g["_y_"] = y_num
                agg = (
                    g.dropna(subset=[col])
                     .groupby(col)["_y_"]
                     .agg(["count", "mean", "median", "min", "max"])
                     .sort_values("mean", ascending=False)
                     .head(10)
                )
                seg_out[col] = agg.round(4).reset_index().to_dict(orient="records")
            except Exception:
                seg_out[col] = {"error": True}

    return {"summary": desc, "segments_top10_by_mean": seg_out}

import re
from typing import Tuple

def _extract_ctq_final_from_define(define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract CTQ snapshot from DEFINE FINAL in a schema-agnostic way.
    Deterministic.
    """
    if not isinstance(define_final, dict):
        return {"available": False, "ctq_items": []}

    outs = (define_final.get("outputs") or {})
    ctq = outs.get("ctq") or outs.get("ctq_tree") or outs.get("ctq_final") or outs.get("ctq_list")

    ctq_items = []
    if isinstance(ctq, list):
        for x in ctq:
            if isinstance(x, dict):
                ctq_items.append(x)
            else:
                ctq_items.append({"ctq": str(x)})
    elif isinstance(ctq, dict):
        # ctq_tree style
        ctq_items.append(ctq)
    elif isinstance(ctq, str) and ctq.strip():
        ctq_items.append({"ctq": ctq.strip()})

    return {
        "available": bool(ctq_items),
        "ctq_items": ctq_items,
        "raw": ctq if isinstance(ctq, (dict, list, str)) else None,
    }


def _parse_goal_target(define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Best-effort parse target from DEFINE goal statement (if present).
    Not guaranteed. Safe fallback if unknown.
    """
    if not isinstance(define_final, dict):
        return {"available": False}

    outs = (define_final.get("outputs") or {})
    inputs = (define_final.get("inputs") or {})
    goal = outs.get("goal_statement") or outs.get("project_goal") or inputs.get("goal_text") or ""
    goal = str(goal)

    # Try to find percentage targets like "to 11%" or "≤ 11%"
    m = re.search(r'(\bto\b|\b<=\b|≤)\s*(\d+(\.\d+)?)\s*%+', goal, flags=re.IGNORECASE)
    if m:
        return {"available": True, "type": "percentage", "operator": "<=", "value": float(m.group(2)), "source": "define_goal_statement"}

    m2 = re.search(r'(\d+(\.\d+)?)\s*%+', goal)
    if m2:
        # ambiguous percent mention
        return {"available": True, "type": "percentage", "operator": "<=", "value": float(m2.group(1)), "source": "define_goal_statement_ambiguous"}

    return {"available": False, "source": "define_goal_statement"}


def _propose_metric_candidates(ctq_snapshot: Dict[str, Any], define_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Deterministic candidate list based on CTQ keywords + DEFINE context.
    Not fancy; goal is to force explicit mapping, then let user validate.
    """
    candidates = []
    cand_y = (define_context.get("candidate_y_from_define") or "").strip()
    if cand_y:
        candidates.append({
            "metric_name": cand_y,
            "role": "primary_candidate",
            "reason": "Candidate Y from DEFINE",
            "grain": "TBD",
            "source": "define",
        })

    # Very light keyword heuristics
    text_blob = json.dumps(ctq_snapshot.get("ctq_items", [])) + " " + json.dumps(define_context)
    t = text_blob.lower()

    def add(name, role, reason):
        candidates.append({"metric_name": name, "role": role, "reason": reason, "grain": "TBD", "source": "heuristic"})

    if "overdue" in t or "accounts receivable" in t or "a/r" in t:
        add("A/R Overdue Percentage", "primary_candidate", "Direct representation of overdue performance")
        add("Overdue A/R Value", "supporting", "Magnitude driver of overdue %")
        add("Days Sales Outstanding (DSO)", "supporting", "Collection speed proxy")
        add("Blocked Order Count", "supporting", "Downstream impact proxy")

    if "defect" in t or "complaint" in t or "quality" in t:
        add("Defect Rate", "primary_candidate", "CTQ likely quality outcome")
        add("First Pass Yield", "supporting", "Process capability proxy")
        add("Complaint Rate", "supporting", "Customer impact proxy")

    # Deduplicate by metric_name
    seen = set()
    uniq = []
    for c in candidates:
        k = str(c.get("metric_name", "")).strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq[:10]


def _score_candidate(c: Dict[str, Any]) -> float:
    """
    Simple deterministic scoring. Primary candidates from DEFINE/heuristics get higher.
    """
    role = (c.get("role") or "").lower()
    src = (c.get("source") or "").lower()
    score = 0.0
    if "primary" in role:
        score += 2.0
    if src == "define":
        score += 2.0
    if src == "heuristic":
        score += 1.0
    return score


def _select_primary_measurement(candidates: List[Dict[str, Any]], user_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Select primary measurement output (proposal) unless user has chosen/confirmed.
    """
    user_choice = (user_inputs.get("chosen_primary_metric") or "").strip()
    user_confirmed = bool(user_inputs.get("confirm_primary_metric"))

    if user_choice:
        return {
            "metric_name": user_choice,
            "basis": "user_choice",
            "confirmed_by_user": user_confirmed,
        }

    if not candidates:
        return {
            "metric_name": "",
            "basis": "no_candidates",
            "confirmed_by_user": False,
        }

    ranked = sorted(candidates, key=_score_candidate, reverse=True)
    top = ranked[0]
    return {
        "metric_name": top.get("metric_name", ""),
        "basis": "agent_proposal",
        "confirmed_by_user": False,
        "top_candidates": ranked[:5],
    }


def _ensure_primary_row_in_opdefs(op_defs: List[Dict[str, Any]], primary_metric: str) -> List[Dict[str, Any]]:
    """
    Ensure operational definitions include at least one row for primary metric.
    Deterministic insert if missing.
    """
    pm = (primary_metric or "").strip()
    if not pm:
        return op_defs

    found = any(str(r.get("metric_name", "")).strip().lower() == pm.lower() for r in op_defs)
    if found:
        return op_defs

    # Insert minimal row at top
    row = {
        "metric_name": pm,
        "what_measured": "Primary measurement output mapped from CTQ",
        "instrument_system": "TBD (e.g., ERP/MES/LIMS)",
        "method_formula": "TBD (define numerator/denominator clearly if %)",
        "unit": "TBD",
        "frequency": "TBD",
        "decision_criteria": "TBD (target/spec or directional rule)",
    }
    return [row] + op_defs


def _build_graphical_display_specs(user_inputs: Dict[str, Any], outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Provide chart specs (not rendering) based on available fields.
    Deterministic.
    """
    y_col = (user_inputs.get("y_column_name") or "").strip()
    date_col = user_inputs.get("date_column")
    segs = user_inputs.get("segment_columns") or []
    specs = []

    if y_col:
        specs.append({"chart_type": "histogram", "x": y_col, "y": None, "segment": None, "purpose": "Understand distribution & outliers (descriptive)."})
        specs.append({"chart_type": "boxplot", "x": (segs[0] if segs else None), "y": y_col, "segment": None, "purpose": "Compare baseline across segments (descriptive)."})

    if y_col and date_col:
        specs.append({"chart_type": "line_trend", "x": date_col, "y": y_col, "segment": (segs[0] if segs else None), "purpose": "Baseline trend over time (descriptive)."})
    else:
        specs.append({"chart_type": "note", "message": "Provide date/time column to enable trend charts and timeliness checks."})

    return specs


def _rule_based_measure_conclusion(outputs: Dict[str, Any], define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    MEASURE conclusion: problem validated or not (descriptive only).
    Uses baseline summary + optional target from DEFINE (best-effort).
    """
    gate = outputs.get("_gate")  # optional injection by caller
    perf = outputs.get("process_performance_summary") or {}
    summary = perf.get("summary") if isinstance(perf, dict) else None

    target = _parse_goal_target(define_final)
    basis = []
    confidence = "low"
    validated = "unknown"

    if gate and isinstance(gate, dict):
        if gate.get("status") == "PASS":
            confidence = "high"
        elif gate.get("status") == "WEAK_PASS":
            confidence = "medium"
        else:
            confidence = "low"

    if summary and isinstance(summary, dict) and summary.get("available", True):
        mean = summary.get("mean")
        median = summary.get("median")
        basis.append(f"Baseline descriptive computed (mean={mean}, median={median}).")

        if target.get("available") and isinstance(mean, (int, float)):
            op = target.get("operator")
            tv = target.get("value")
            basis.append(f"DEFINE goal target parsed: {op} {tv}% (best-effort).")
            if op == "<=":
                validated = "true" if mean > tv else "false"
                basis.append("If baseline > target, problem is present (validated); else not validated (by mean only).")
        else:
            basis.append("No reliable numeric target/spec available from DEFINE; conclusion limited to baseline magnitude & direction.")
    else:
        basis.append("No baseline descriptive available; cannot validate problem quantitatively.")

    return {
        "problem_validated": validated,     # true/false/unknown
        "confidence": confidence,           # high/medium/low
        "basis": basis[:6],
        "what_would_change_mind": [
            "Confirm target/spec numerically (from DEFINE/Finance/Customer requirement).",
            "Upload dataset with sufficient window (recommended 2–4 weeks or multiple periods).",
            "Add date column for stability/timeliness checks.",
        ],
    }


def extract_json_from_llm(raw_text: str):
    """
    Extract JSON object from LLM output.
    Supports:
      ```json { ... } ```
      ```{}
      Or direct JSON string.
    """
    fenced = r"```(?:json)?\s*(\{.*?\})\s*```"
    m = re.search(fenced, raw_text, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    try:
        return json.loads(raw_text)
    except Exception:
        return None


def llm(prompt: str, model: str = "gpt-4.1-mini") -> str:
    """
    Robust wrapper in case llm_engine.complete does not accept `model=`.
    """
    try:
        return llm_engine.complete(prompt, model=model)
    except TypeError:
        return llm_engine.complete(prompt)


def _build_measure_summary_md(measure_state: Dict[str, Any]) -> str:
    """
    Backward-compatible summary builder.
    Preferred: reporting.build_measure_summary_md(measure_state) -> str
    Legacy:    reporting.build_measure_summary_markdown(measure_state) -> str
    Fallback:  deterministic minimal summary.
    """
    fn = getattr(reporting, "build_measure_summary_md", None)
    if callable(fn):
        try:
            return fn(measure_state)
        except Exception:
            pass

    fn_legacy = getattr(reporting, "build_measure_summary_markdown", None)
    if callable(fn_legacy):
        try:
            return fn_legacy(measure_state)
        except Exception:
            pass

    pid = measure_state.get("project_id", "-")
    pname = (measure_state.get("inputs", {}) or {}).get("project_name") or "-"
    status = measure_state.get("status", "draft")
    y = ((measure_state.get("outputs", {}) or {}).get("y_variable_confirmed")
         or (measure_state.get("inputs", {}) or {}).get("y_variable") or "-")

    return (
        "## MEASURE Summary\n\n"
        f"- Project ID: **{pid}**\n"
        f"- Project Name: **{pname}**\n"
        f"- Status: **{status}**\n"
        f"- Y (confirmed): **{y}**\n"
    )

def _ensure_operational_defs_schema(rows: Any) -> list[dict]:
    """
    Normalize operational definitions to list[dict] with expected keys.
    """
    expected_keys = [
        "metric_name",
        "what_measured",
        "instrument_system",
        "method_formula",
        "unit",
        "frequency",
        "decision_criteria",
    ]

    if not rows:
        return []

    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []

    norm = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        item = {k: (r.get(k, "") if r.get(k) is not None else "") for k in expected_keys}
        # keep any extra keys if user adds later (future-proof)
        for k, v in r.items():
            if k not in item:
                item[k] = v
        norm.append(item)
    return norm


def _validate_operational_defs(rows: list[dict]) -> dict:
    """
    Deterministic validation: completeness + basic consistency checks.
    No LLM.
    """
    issues = []
    required = [
        "metric_name",
        "what_measured",
        "instrument_system",
        "method_formula",
        "unit",
        "frequency",
        "decision_criteria",
    ]

    for i, r in enumerate(rows):
        missing = [k for k in required if not str(r.get(k, "")).strip()]
        if missing:
            issues.append({
                "row": i,
                "type": "missing_fields",
                "severity": "high",
                "message": f"Row {i+1} missing fields: {', '.join(missing)}"
            })

        unit = str(r.get("unit", "")).strip()
        formula = str(r.get("method_formula", "")).lower()
        if unit == "%" and formula:
            if ("* 100" not in formula) and ("percent" not in formula) and ("%" not in formula):
                issues.append({
                    "row": i,
                    "type": "unit_formula_mismatch",
                    "severity": "medium",
                    "message": f"Row {i+1}: unit is % but formula doesn't show percent conversion."
                })

        freq = str(r.get("frequency", "")).lower().strip()
        what = str(r.get("what_measured", "")).lower()
        if freq and what:
            if ("month" in freq) and ("month" not in what):
                issues.append({
                    "row": i,
                    "type": "frequency_not_reflected",
                    "severity": "low",
                    "message": f"Row {i+1}: frequency is monthly but what_measured doesn't mention monthly."
                })

        # basic formula ambiguity check
        if formula and ("/" in formula) and ("total" in formula) and ("overdue" in formula) and ("open" not in formula):
            issues.append({
                "row": i,
                "type": "formula_ambiguity",
                "severity": "low",
                "message": f"Row {i+1}: formula may be ambiguous; ensure numerator/denominator clearly stated."
            })

    return {"issues": issues, "issue_count": len(issues)}



def _build_measure_insight_md_llm(perception: dict, decisions: dict, outputs: dict) -> str:
    """
    Draft-time only. Finalize must not call LLM.
    """
    prompt = f"""
You are a DMAIC Measure coach. Write concise insight (5-8 bullets) that:
- confirms Y variable and operational definition gaps
- highlights data availability risks and constraints
- flags top data quality issues (completeness/consistency/timeliness)
- recommends 2-3 immediate next steps to complete Measure (no Analyze)

Perception (summary):
{json.dumps(perception, indent=2)[:4000]}

Decisions:
{json.dumps(decisions, indent=2)[:2000]}

Measure outputs (package):
{json.dumps(outputs, indent=2)[:3000]}

Return markdown bullets only. No JSON. No heading.
"""
    return llm(prompt).strip() + "\n"

def _measure_gate_evaluate(outputs: dict, user_inputs: dict) -> dict:
    """
    Deterministic gate evaluator for MEASURE.
    Produces PASS / WEAK_PASS / FAIL + routing policy.
    """
    missing = []
    risks = []
    actions = []

    op_defs = outputs.get("operational_definitions") or []
    op_issues = outputs.get("operational_definitions_issues") or []
    dq = outputs.get("data_quality_results") or {}
    prof = outputs.get("dataset_profile") or {}
    perf = outputs.get("process_performance_summary") or {}
    mplan = outputs.get("measurement_plan") or []

    # --- Evidence checks ---
    # Operational definition contract
    if not op_defs:
        missing.append("Operational definitions table (at least 1 row for confirmed Y)")
        actions.append("Add 1 operational definition row for the confirmed Y (metric_name, formula, unit, frequency, decision criteria, source system).")
    else:
        # if high severity missing fields exist, treat as missing evidence
        high_missing = [i for i in op_issues if isinstance(i, dict) and i.get("type") == "missing_fields" and i.get("severity") == "high"]
        if high_missing:
            missing.append("Operational definitions incomplete (missing required fields)")
            actions.append("Complete required fields in operational definition rows (especially for primary Y).")

    # Measurement plan existence
    if not isinstance(mplan, list) or len(mplan) == 0:
        missing.append("Measurement plan (who/when/how/frequency/where)")
        actions.append("Add at least 1 measurement plan item: what, where, how, frequency, owner, evidence source.")

    # Dataset-based evidence (optional but stronger)
    if prof.get("available") is True:
        if prof.get("rows", 0) <= 0:
            missing.append("Dataset has zero rows")
            actions.append("Upload a dataset extract with actual records (non-empty).")

        # DQ availability
        if dq.get("available") is not True:
            missing.append("Data quality results (completeness/consistency/timeliness)")
            actions.append("Provide dataset + date column to evaluate completeness/duplicates/timeliness.")

        # Baseline availability
        # perf structure: {"summary": {...}, "segments_top10_by_mean": {...}} from our deterministic calc
        summary = perf.get("summary") if isinstance(perf, dict) else None
        if not summary or not isinstance(summary, dict) or summary.get("available") is False:
            missing.append("Baseline descriptive metrics for Y (mean/median/std/min/max/p25/p75)")
            actions.append("Select correct Y column in dataset (numeric) and regenerate baseline descriptives.")

        # Risk flags from DQ
        comp = (dq.get("completeness") or {}).get("columns_with_missing_over_5pct") or []
        if comp:
            risks.append(f"Missing rate >5% detected in columns: {comp}. Risk of biased baseline.")
            actions.append("Define missing-value handling rule (do not drop outliers/missing without rule).")

        cons_issues = (dq.get("consistency") or {}).get("issues") or []
        if cons_issues:
            risks.append("Consistency issues detected (e.g., duplicates/negative values/dtype mismatch).")
            actions.append("Define deduplication rule + value validity rules; rerun baseline after cleaning rules are specified.")

        if prof.get("duplicate_rows", 0) > 0:
            risks.append("Duplicate rows exist; baseline may be distorted.")
            actions.append("Specify deduplication key/grain (per invoice/per batch/per shift) and remove duplicates deterministically.")

        # Timeliness
        t = dq.get("timeliness") or {}
        if isinstance(t, dict) and t.get("note"):
            risks.append("No date column provided → cannot assess timeliness/stability; risk of snapshot bias.")
            actions.append("Provide date/time column or extract with timestamps to check freshness and stability window.")
    else:
        # no dataset: allow WEAK but not PASS
        risks.append("No dataset uploaded; gate cannot confirm baseline quantitatively.")
        actions.append("Upload at least one extract (recommended: 2–4 weeks window) to compute baseline descriptives and DQ checks.")

    # --- Decide status ---
    if missing:
        status = "FAIL"
    else:
        # no missing: still may be weak due to risks (e.g., no dataset, high missing/duplicates)
        status = "WEAK_PASS" if risks else "PASS"

    # --- Policy routing ---
    if status == "FAIL":
        policy = "BLOCK"
    elif status == "WEAK_PASS":
        policy = "PROCEED_WITH_WARNING"
    else:
        policy = "ADVANCE_AND_FREEZE"

    return {
        "status": status,
        "policy": policy,
        "missing_evidence": missing,
        "risk_if_proceed": risks,
        "next_actions": actions[:8],  # keep smallest list
    }
    # CTQ must exist and be used
    ctq_used = outputs.get("ctq_final_used") or {}
    if not (isinstance(ctq_used, dict) and ctq_used.get("available")):
        missing.append("DEFINE CTQ FINAL not found/used → MEASURE must be triggered by CTQ.")
        actions.append("Finalize CTQ in DEFINE (or ensure ctq is stored in DEFINE outputs), then reload project in MEASURE.")

    # Primary measurement must be explicitly selected
    pm = outputs.get("primary_measurement_output") or {}
    pm_name = (pm.get("metric_name") or "").strip()
    if not pm_name:
        missing.append("Primary measurement output not defined (CTQ→metric mapping incomplete).")
        actions.append("Choose the strongest metric that directly represents CTQ, then define its operational definition.")

    # Project leader validation required
    # User confirms via user_inputs.confirm_primary_metric true (and optionally chosen_primary_metric)
    if not bool(user_inputs.get("confirm_primary_metric")):
        missing.append("Project leader validation missing: confirm primary measurement output selection.")
        actions.append("Confirm the primary metric (project leader decision). If needed, override with chosen_primary_metric.")


# ======================================================
# 1. PERCEPTION
# ======================================================

def perception_step(project_id: str, user_inputs: Dict[str, Any], baseline_df=None) -> Dict[str, Any]:
    industry = user_inputs.get("industry")
    pain_theme = user_inputs.get("pain_theme")
    process_area = user_inputs.get("process_area")

    # Audit: reuse existing retrieval
    similar_audit_raw = audit.get_similar_audit_events(
        industry=industry,
        pain_theme=pain_theme,
        process_area=process_area,
        phase=PHASE_NAME,
        k=3,
    )

    # Memory: use getattr so skeleton works before memory.py is extended for Measure
    retrieve_fn = getattr(memory, "retrieve_similar_measure_episodes", None)
    similar_memory_raw = []
    if callable(retrieve_fn):
        try:
            similar_memory_raw = retrieve_fn(industry, pain_theme, k=3)
        except Exception:
            similar_memory_raw = []

    def _trim_audit(events):
        trimmed = []
        for e in events or []:
            meta = e.get("meta") or {}
            trimmed.append({
                "phase": e.get("phase"),
                "status": e.get("status"),
                "industry": meta.get("industry"),
                "pain_theme": meta.get("pain_theme"),
                "process_area": meta.get("process_area"),
                "project_type": meta.get("project_type"),
            })
        return trimmed

    def _trim_memory(episodes):
        trimmed = []
        for ep in episodes or []:
            meta = ep.get("meta") or {}
            inputs = ep.get("inputs") or {}
            outputs = ep.get("outputs") or {}
            trimmed.append({
                "industry": meta.get("industry") or inputs.get("industry"),
                "pain_theme": meta.get("pain_theme") or inputs.get("pain_theme"),
                "process_area": meta.get("process_area") or inputs.get("process_area"),
                "y_variable_confirmed": outputs.get("y_variable_confirmed"),
                "data_sources": outputs.get("data_sources"),
                "dq_summary": outputs.get("data_quality_summary"),
            })
        return trimmed

    similar_audit = _trim_audit(similar_audit_raw)
    similar_memory = _trim_memory(similar_memory_raw)

    # Reuse generic perception builder to avoid new module contracts
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

def decision_step(perception: Dict[str, Any], user_feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prompt = f"""
You are a DMAIC Black Belt AI agent.
You are now in the DECISION-MAKING step of MEASURE phase.

Perception:
{json.dumps(perception, indent=2)}

User feedback from previous draft (if any):
{json.dumps(user_feedback, indent=2) if user_feedback else "No feedback provided."}

Operational definitions provided (if any):
{json.dumps(_ensure_operational_defs_schema((perception.get("user_inputs") or {}).get("operational_definitions")), indent=2)[:1500] if isinstance(perception, dict) else "[]"}


SCOPE (do NOT go into Analyze):
- Confirm Y variable & operational definition
- Data availability & source mapping
- Data quality checks (completeness, consistency, timeliness)
- Measurement plan (what, where, frequency, owner)
- Baseline metrics (descriptive only)

Return JSON ONLY:
{{
  "y_variable_confirmed": "...",
  "y_operational_definition": {{
    "unit": "...",
    "calculation": "...",
    "specification": "...",
    "sampling_frame": "...",
    "inclusion_exclusion": "..."
  }},
  "data_sources": [
    {{
      "source_name": "...",
      "system": "...",
      "owner": "...",
      "location_or_access": "...",
      "fields_needed": ["..."],
      "grain": "row-level grain (e.g., per batch/per order/per day)",
      "history_available": "...",
      "update_frequency": "..."
    }}
  ],
  "data_quality_checks": {{
    "completeness": ["..."],
    "consistency": ["..."],
    "timeliness": ["..."],
    "notes": "..."
  }},
  "measurement_plan": [
    {{
      "what": "...",
      "where": "...",
      "how": "...",
      "frequency": "...",
      "owner": "...",
      "evidence": "data table / log / report"
    }}
  ],
  "baseline_metrics_plan": {{
    "descriptive_metrics": ["mean", "median", "std", "min", "max", "p25", "p75"],
    "segmentations": ["time", "site", "product", "shift"],
    "notes": "No inferential stats; descriptive only."
  }}
}}
"""

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    return parsed if isinstance(parsed, dict) else {"llm_raw": raw}


# ======================================================
# 3. ACTION (DRAFT PACKAGE)
# ======================================================


def action_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    perception: Dict[str, Any],
    decisions: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
    define_context: Optional[Dict[str, Any]] = None,
    baseline_profile: Optional[Dict[str, Any]] = None,
    dq_results: Optional[Dict[str, Any]] = None,
    baseline_results: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Few-shot: optional, safe to omit until memory supports it
    examples = []
    retrieve_fn = getattr(memory, "retrieve_similar_measure_episodes", None)
    if callable(retrieve_fn):
        try:
            examples = retrieve_fn(user_inputs.get("industry"), user_inputs.get("pain_theme"), k=2)
        except Exception:
            examples = []

    define_context = define_context or {}
    define_final_raw = define_final_raw or {}
    baseline_profile = baseline_profile or {"available": False}
    dq_results = dq_results or {"available": False}
    baseline_results = baseline_results or {"available": False}
        # ----- Operational definitions (table) -----
    op_defs_in = _ensure_operational_defs_schema(user_inputs.get("operational_definitions"))
    op_defs_validation = _validate_operational_defs(op_defs_in) if op_defs_in else {"issues": [], "issue_count": 0}
    user_inputs["operational_definitions"] = op_defs_in
    user_inputs["operational_definitions_validation"] = op_defs_validation

        # ----- CTQ-driven trigger from DEFINE FINAL -----

    ctq_snapshot = _extract_ctq_final_from_define(define_final_raw)
    metric_candidates = _propose_metric_candidates(ctq_snapshot, define_context or {})
    primary_measurement = _select_primary_measurement(metric_candidates, user_inputs)

    # Ensure operational definitions include row for primary measurement (proposal or chosen)
    op_defs_in = _ensure_primary_row_in_opdefs(op_defs_in, primary_measurement.get("metric_name", ""))

    # Build chart specs (data will be fed through baseline_df mapping)
    graph_specs = _build_graphical_display_specs(user_inputs, {})


        # ----- LLM prompt -----

    prompt = f"""
ACTION STEP — MEASURE PHASE

User Inputs:
{json.dumps(user_inputs, indent=2)}

Perception:
{json.dumps(perception, indent=2)[:7000]}

Decisions:
{json.dumps(decisions, indent=2)[:5000]}

Upstream DEFINE context (read-only summary):
{json.dumps(define_context, indent=2)[:2000]}

Operational definitions table provided by user (may be empty):
{json.dumps(op_defs_in, indent=2)[:2500]}

Deterministic validation issues on operational definitions:
{json.dumps(op_defs_validation, indent=2)[:1500]}

Dataset profile (deterministic):
{json.dumps(baseline_profile, indent=2)[:2500]}

Data quality results (deterministic):
{json.dumps(dq_results, indent=2)[:2500]}

Baseline descriptive process performance (deterministic):
{json.dumps(baseline_results, indent=2)[:3000]}

Few-shot examples (if any):
{json.dumps(examples, indent=2)[:3000]}

User feedback about previous draft (if any):
{json.dumps(user_feedback, indent=2) if user_feedback else "No feedback provided."}

Rules:
- Stay strictly in MEASURE scope (no Analyze hypotheses).
- Outputs must be structured and implementation-ready.
- If feedback exists, incorporate it explicitly.

Generate MEASURE PACKAGE in JSON:
{{
  "y_variable_confirmed": "...",
  "y_operational_definition": {{ }},
  "data_availability_map": [ ],
  "operational_definitions": [  ],          # table rows; if empty input, propose at least 1 row for confirmed Y
  "operational_definitions_issues": [  ],   # list of issues found/fixed
  "data_quality_plan": {{ }},
  "measurement_plan": [ ],
  "baseline_descriptive_metrics": {{ }},
  "risks_and_assumptions": [ ],
  "out_of_scope_note": "..."
}}
"""

    raw = llm(prompt)
    parsed = extract_json_from_llm(raw) or {"llm_raw": raw}

        # Hard-inject deterministic results so MEASURE is truly measurable (no hallucinated numbers)
    if isinstance(parsed, dict):
        parsed.setdefault("data_quality_results", dq_results)
        parsed.setdefault("dataset_profile", baseline_profile)
        parsed.setdefault("process_performance_summary", baseline_results)

        # Ensure operational definitions always present in outputs (table-based contract)
    if isinstance(parsed, dict):
        # prefer LLM-improved table if provided; otherwise keep user's
        op_defs_out = _ensure_operational_defs_schema(parsed.get("operational_definitions")) or op_defs_in
        parsed["operational_definitions"] = op_defs_out
        parsed["operational_definitions_issues"] = (
            parsed.get("operational_definitions_issues")
            if isinstance(parsed.get("operational_definitions_issues"), list)
            else op_defs_validation.get("issues", [])
        )

        # Backward compatibility: keep a single y_operational_definition object
        # Take the first row that matches confirmed Y (best-effort), else first row
        y_conf = (parsed.get("y_variable_confirmed") or "").strip()
        chosen_row = None
        if y_conf:
            for r in op_defs_out:
                if str(r.get("metric_name", "")).strip().lower() == y_conf.lower():
                    chosen_row = r
                    break
        if not chosen_row and op_defs_out:
            chosen_row = op_defs_out[0]

        if chosen_row:
            parsed["y_operational_definition"] = {
                "unit": chosen_row.get("unit", ""),
                "calculation": chosen_row.get("method_formula", ""),
                "specification": chosen_row.get("decision_criteria", ""),
                "sampling_frame": "",          # keep blank unless user provides later
                "inclusion_exclusion": ""
            }

    if isinstance(parsed, dict):
        parsed["ctq_final_used"] = ctq_snapshot
        parsed["ctq_to_metric_map"] = metric_candidates
        parsed["primary_measurement_output"] = primary_measurement
        parsed["graphical_displays"] = graph_specs

        # Force user validation requirement (project leader must confirm)
        parsed["user_validation_required"] = {
            "confirm_ctq_mapping": True,
            "confirm_primary_metric": True,
            "current_primary_metric": primary_measurement.get("metric_name", ""),
            "how_to_confirm": "Set user_inputs.confirm_primary_metric = true and (optional) user_inputs.chosen_primary_metric.",
        }

    # Remove any upstream DEFINE keys if mistakenly included
    if isinstance(parsed, dict):
        for k in ("schema_version", "project_id", "project_name"):
            parsed.pop(k, None)

    measure_state: Dict[str, Any] = {
        "schema_version": MEASURE_SCHEMA,
        "phase": "measure",
        "status": "draft",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "project_id": project_id,
        "inputs": user_inputs,
        "meta": {
            "industry": user_inputs.get("industry"),
            "pain_theme": user_inputs.get("pain_theme"),
            "process_area": user_inputs.get("process_area"),
        },
        "upstream": {
            "define_context": define_context,
            "define_locked": True,
        },
        "outputs": parsed,
        "insight_md": "",
        "summary_md": "",
        "perception_trace": {
            "industry": user_inputs.get("industry"),
            "pain_theme": user_inputs.get("pain_theme"),
            "process_area": user_inputs.get("process_area"),
        },
        "decision_trace": {
            "y_variable_confirmed": (decisions or {}).get("y_variable_confirmed"),
        },
    }

    # Draft-time only (LLM OK here)
    measure_state["insight_md"] = _build_measure_insight_md_llm(
        perception=perception,
        decisions=decisions,
        outputs=measure_state["outputs"],
    )

    measure_state["summary_md"] = _build_measure_summary_md(measure_state)
    return measure_state


# ======================================================
# 4. SELF-CRITIQUE (PATCH OUTPUTS ONLY)
# ======================================================

def self_critique(measure_state: Dict[str, Any]) -> Dict[str, Any]:
    prompt = f"""
You are the SELF-CRITIQUE agent for the DMAIC MEASURE phase.

IMPORTANT CONSTRAINTS:
- You MUST NOT change the outer schema of measure_state.
- You MUST NOT add/remove/rename any top-level keys of measure_state.
- You MUST ONLY propose changes inside measure_state["outputs"].
- Include ONLY fields that need to be changed or added.

Given measure_state (schema-wrapped):
{json.dumps(measure_state, indent=2)[:8000]}

Return STRICT JSON ONLY:
{{
  "issues": [{{"type":"...", "severity":"low|medium|high", "message":"..."}}],
  "outputs_patch": {{ ... }}
}}
"""
    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    if not isinstance(parsed, dict):
        return {"issues": [], "improved": measure_state}

    issues = parsed.get("issues", [])
    patch = parsed.get("outputs_patch") or {}

    improved = dict(measure_state)
    outputs = dict(improved.get("outputs") or {})
    if isinstance(patch, dict):
        outputs.update(patch)
    improved["outputs"] = outputs
    improved["updated_at"] = _now_iso()

    # Deterministic summary rebuild (no LLM)
    improved["summary_md"] = _build_measure_summary_md(improved)
    return {"issues": issues, "improved": improved}


# ======================================================
# 5. ADAPTATION (SAVE + AUDIT)
# ======================================================

def adaptation_step(project_id: str, measure_state: Dict[str, Any], user_feedback=None) -> None:
    # Memory save: safe before memory.py is extended
    save_episode_fn = getattr(memory, "save_measure_episode", None)
    if callable(save_episode_fn):
        try:
            save_episode_fn(project_id, measure_state, user_feedback)
        except Exception:
            pass

    meta = measure_state.get("meta") or {}
    outputs = measure_state.get("outputs") or {}

    try:
        audit.log_phase_event(
            project_id=project_id,
            phase=PHASE_NAME,
            status="success",
            meta={
                "industry": meta.get("industry"),
                "pain_theme": meta.get("pain_theme"),
                "process_area": meta.get("process_area"),
                "y_variable_confirmed": outputs.get("y_variable_confirmed"),
            },
        )
    except Exception:
        pass


# ======================================================
# MAIN ENTRYPOINT (DRAFT ONLY)
# ======================================================

def run_measure_agent(
    project_id: str,
    user_inputs: Dict[str, Any],
    define_final: Optional[Dict[str, Any]] = None,
    baseline_df: Optional[pd.DataFrame] = None,
    user_feedback=None,
) -> Dict[str, Any]:
    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, baseline_df)
    decisions = decision_step(perception, user_feedback=user_feedback)

    # ----- Upstream DEFINE context (read-only) -----
    define_context = _summarize_define_context(define_final)

    # ----- Deterministic data profiling / DQ / baseline descriptives -----
    date_col = user_inputs.get("date_column")  # optional (UI can provide later)
    y_col = user_inputs.get("y_column_name") or (decisions or {}).get("y_variable_confirmed") or user_inputs.get("y_variable")

    # IMPORTANT: y_col here is best-effort; if it doesn't match df column, results will be "not available"
    profile = _profile_dataframe(baseline_df, date_col=date_col)
    dq_results = _dq_checks(baseline_df, y_col=y_col, date_col=date_col)

    segment_cols = user_inputs.get("segment_columns") or ["site", "product", "shift"]
    baseline_results = _compute_baseline_descriptives(baseline_df, y_col=y_col, segment_cols=segment_cols)


    draft_state = action_step(
        project_id,
        user_inputs,
        perception,
        decisions,
        user_feedback=user_feedback,
        define_context=define_context,
        define_final_raw=define_final,
        baseline_profile=profile,
        dq_results=dq_results,
        baseline_results=baseline_results,
    )
    

        # Gate evaluation (deterministic) — decision-gated behavior
    gate = _measure_gate_evaluate(draft_state.get("outputs", {}), user_inputs)
    draft_state["gate"] = gate

    # Attach gate into outputs for conclusion (without LLM)
    outs = draft_state.get("outputs") or {}
    outs["_gate"] = gate
    outs["measure_conclusion"] = _rule_based_measure_conclusion(outs, define_final)
    draft_state["outputs"] = outs

    # Decision routing (policy-based)
    draft_state["routing"] = {"policy": gate.get("policy"), "status": gate.get("status")}


    if isinstance(draft_state, dict):
        draft_state["updated_at"] = _now_iso()
        draft_state["summary_md"] = _build_measure_summary_md(draft_state)

    # Save draft (safe before memory.py supports it)
    save_draft_fn = getattr(memory, "save_measure_draft", None)
    if callable(save_draft_fn):
        try:
            save_draft_fn(project_id, draft_state)
        except Exception:
            pass

    return {
        "status": "draft",
        "measure_state": draft_state,
        "summary": draft_state.get("summary_md", ""),
        "message": "Draft generated.",
    }


# ======================================================
# FINALIZE MEASURE PHASE (TOKEN-SAFE, NO LLM)
# ======================================================

def finalize_measure_agent(
    project_id: str,
    measure_state: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Token-safe finalize:
    - NO LLM calls
    - lock FINAL identical to accepted draft
    - save FINAL JSON
    - delete DRAFT
    - generate Word snapshot (optional, deterministic)
    """
    final_state = copy.deepcopy(measure_state)

    if isinstance(final_state, dict):
        final_state["phase"] = final_state.get("phase") or "measure"
        final_state["status"] = "final"
        final_state["schema_version"] = final_state.get("schema_version") or MEASURE_SCHEMA
        final_state.setdefault("created_at", _now_iso())
        final_state["updated_at"] = _now_iso()

        if not final_state.get("summary_md"):
            final_state["summary_md"] = _build_measure_summary_md(final_state)

        final_state.setdefault("insight_md", final_state.get("insight_md") or "")

    # Adaptation logging WITHOUT modifying final content
    try:
        adaptation_step(project_id, final_state, user_feedback)
    except Exception:
        pass

        gate = (measure_state or {}).get("gate") or {}
    if gate.get("status") == "FAIL":
        # do not finalize; return deterministic refusal
        return {
            "status": "blocked",
            "reason": "MEASURE gate is FAIL — cannot finalize or advance.",
            "gate": gate,
            "measure_state": measure_state,
            "word_path": None,
        }


    # Persist FINAL + remove DRAFT (safe before memory.py supports it)
    save_final_fn = getattr(memory, "save_measure_final", None)
    if callable(save_final_fn):
        try:
            save_final_fn(project_id, final_state)
        except Exception:
            pass

    delete_draft_fn = getattr(memory, "delete_measure_draft", None)
    if callable(delete_draft_fn):
        try:
            delete_draft_fn(project_id)
        except Exception:
            pass

    # Word snapshot (deterministic, no LLM)
    word_path = None
    export_fn = getattr(reporting, "export_measure_to_word", None)
    if callable(export_fn):
        try:
            word_path = export_fn(final_state, path=f"{project_id}_MEASURE_REPORT.docx")
        except Exception:
            word_path = None

    return {
        "status": "finalized",
        "measure_state": final_state,
        "critique": [],
        "summary": final_state.get("summary_md") or _build_measure_summary_md(final_state),
        "word_path": word_path,
        "message": "Measure phase finalized (locked) and stored. Word snapshot generated (if available).",
    }
