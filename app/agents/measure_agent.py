# app/agents/measure_agent.py
from __future__ import annotations
import pandas as pd
import numpy as np
import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from app.agents.classification_agent import get_gate_mode

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


def llm(prompt: str, model: str = "gpt-4.1-mini") -> str:
    try:
        return llm_engine.complete(prompt, model=model)
    except TypeError:
        return llm_engine.complete(prompt)


def extract_json_from_llm(raw_text: str):
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


def _summarize_define_context(define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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


def _extract_ctq_final_from_define(define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract CTQ snapshot from DEFINE FINAL in a schema-agnostic way.
    Robust terhadap berbagai format CTQ output.
    """
    if not isinstance(define_final, dict):
        return {"available": False, "ctq_items": []}

    outs = (define_final.get("outputs") or {})
    ctq = (
        outs.get("ctq_list")
        or outs.get("ctq")
        or outs.get("ctq_tree")
        or outs.get("ctq_final")
    )

    ctq_items = []
    if isinstance(ctq, list):
        for x in ctq:
            if isinstance(x, dict):
                ctq_items.append(x)
            else:
                ctq_items.append({"ctq": str(x)})
    elif isinstance(ctq, dict):
        ctq_items.append(ctq)
    elif isinstance(ctq, str) and ctq.strip():
        ctq_items.append({"ctq": ctq.strip()})

    return {
        "available": bool(ctq_items),
        "ctq_items": ctq_items,
        "raw": ctq if isinstance(ctq, (dict, list, str)) else None,
    }


def _parse_goal_target(define_context_or_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(define_context_or_final, dict):
        return {"available": False}

    d = define_context_or_final
    outs   = d.get("outputs") or {}
    inputs = d.get("inputs")  or {}
    goal = (
        outs.get("goal_statement")
        or outs.get("project_goal")
        or inputs.get("goal_text")
        or d.get("goal_statement")
        or ""
    )
    goal = str(goal).strip()
    if not goal:
        return {"available": False}

    # Pattern 1: percentage target
    m = re.search(r'(?:to|<=|≤|hingga|menjadi|kurang\s+dari)\s*(\d+(?:\.\d+)?)\s*%', goal, re.I)
    if m:
        return {"available": True, "value": float(m.group(1)), "direction": "<=",
                "unit": "%", "source_text": goal[:120]}

    m = re.search(r'(\d+(?:\.\d+)?)\s*%', goal)
    if m:
        return {"available": True, "value": float(m.group(1)), "direction": "<=",
                "unit": "%", "source_text": goal[:120]}

    # Pattern 2: minimal/minimum/at least
    m = re.search(
        r'(?:minimal|minimum|at\s+least|paling\s+sedikit|≥|>=|mencapai|melampaui|memenuhi)\s*'
        r'(\d+(?:[.,]\d+)?)',
        goal, re.I
    )
    if m:
        val = float(m.group(1).replace(",", "."))
        return {"available": True, "value": val, "direction": ">=", "source_text": goal[:120]}

    # Pattern 3: maksimal/maximum/at most
    m = re.search(
        r'(?:maksimal|maximum|at\s+most|paling\s+banyak|≤|<=|reduce\s+to|turun\s+ke|di\s+bawah)\s*'
        r'(\d+(?:[.,]\d+)?)',
        goal, re.I
    )
    if m:
        val = float(m.group(1).replace(",", "."))
        return {"available": True, "value": val, "direction": "<=", "source_text": goal[:120]}

    # Pattern 4: unit-based numeric
    m = re.search(
        r'(\d+(?:[.,]\d+)?)\s*'
        r'(?:ton|kg|unit|pcs|reactor|batch|liter|m3|hour|jam|shift|hari|day|ppm|mg|g\b)',
        goal, re.I
    )
    if m:
        val = float(m.group(1).replace(",", "."))
        direction = ">="
        if re.search(r'(?:kurang|bawah|below|reduce|turun|maks|max)', goal, re.I):
            direction = "<="
        return {"available": True, "value": val, "direction": direction, "source_text": goal[:120]}

    # Pattern 5: standalone target keyword
    m = re.search(r'(?:target|goal|sebesar|setidaknya|sebanyak)\s+(\d+(?:[.,]\d+)?)', goal, re.I)
    if m:
        val = float(m.group(1).replace(",", "."))
        return {"available": True, "value": val, "direction": ">=", "source_text": goal[:120]}

    return {"available": False, "source_text": goal[:120]}


def _profile_dataframe(df: Optional[pd.DataFrame], date_col: Optional[str] = None) -> Dict[str, Any]:
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
        except Exception:
            profile["date_col"] = date_col
    return profile


def _dq_checks(df: Optional[pd.DataFrame], y_col: Optional[str] = None, date_col: Optional[str] = None) -> Dict[str, Any]:
    if df is None:
        return {"available": False}

    out: Dict[str, Any] = {"available": True}

    miss_rate = (df.isna().mean() * 100.0).round(2)
    out["completeness"] = {
        "missing_rate_pct_by_col": miss_rate.to_dict(),
        "columns_with_missing_over_5pct": [c for c, v in miss_rate.items() if v > 5.0],
    }

    consistency_issues = []
    if y_col and y_col in df.columns:
        if pd.api.types.is_numeric_dtype(df[y_col]):
            neg = int((df[y_col] < 0).sum(skipna=True))
            if neg > 0:
                consistency_issues.append({"field": y_col, "issue": "negative_values", "count": neg})
        else:
            consistency_issues.append({"field": y_col, "issue": "y_not_numeric_dtype", "dtype": str(df[y_col].dtype)})

    dup = int(df.duplicated().sum())
    if dup > 0:
        consistency_issues.append({"field": "*", "issue": "duplicate_rows", "count": dup})

    out["consistency"] = {"issues": consistency_issues}

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
    segment_cols: Optional[list] = None,
    date_col: Optional[str] = None,
) -> Dict[str, Any]:
    if df is None or not y_col or y_col not in df.columns:
        return {"available": False, "reason": "baseline_df missing or y column not found", "y_col": y_col or ""}

    y_num = pd.to_numeric(df[y_col], errors="coerce") if not pd.api.types.is_numeric_dtype(df[y_col]) else df[y_col]
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

    seg_out = {}
    for col in (segment_cols or []):
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

    # Chart data untuk time series
    chart_data = None
    if date_col and date_col in df.columns:
        try:
            tmp = df[[date_col, y_col]].copy()
            tmp["_y_num_"] = y_num
            tmp = tmp.dropna(subset=["_y_num_"])

            parsed_dates = pd.to_datetime(tmp[date_col], errors="coerce")
            if parsed_dates.notna().sum() >= len(tmp) * 0.5:
                tmp[date_col] = parsed_dates
                tmp = tmp.dropna(subset=[date_col]).sort_values(date_col)
                chart_data = [
                    {"date": str(row[date_col].date()), "value": round(float(row["_y_num_"]), 4)}
                    for _, row in tmp.iterrows()
                ]
            else:
                chart_data = [
                    {"date": str(row[date_col]), "value": round(float(row["_y_num_"]), 4)}
                    for _, row in tmp.iterrows()
                ]
        except Exception:
            chart_data = None

    return {"summary": desc, "segments_top10_by_mean": seg_out, "chart_data": chart_data}


def _generate_performance_chart(
    baseline_results: Dict,
    target_info: Dict,
    y_label: str = "Value",
) -> Optional[str]:
    """
    Generate bar chart performa proses dengan reference line target.
    Return: base64 PNG string, atau None jika gagal.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import io, base64

        chart_data = (baseline_results or {}).get("chart_data") or []
        if not chart_data:
            return None

        chart_df = pd.DataFrame(chart_data)

        # Fix: date_labels selalu didefinisikan sebelum dipakai
        parsed = pd.to_datetime(chart_df["date"], errors="coerce")
        if parsed.notna().sum() >= len(chart_df) * 0.5:
            chart_df["date"] = parsed
            chart_df = chart_df.dropna(subset=["date"]).sort_values("date")
            date_labels = chart_df["date"].dt.strftime("%Y-%m-%d")
        else:
            date_labels = chart_df["date"].astype(str)

        if chart_df.empty:
            return None

        target_val = target_info.get("value") if (target_info or {}).get("available") else None
        t_dir = (target_info or {}).get("direction", ">=")

        fig, ax = plt.subplots(figsize=(10, 4.5))
        fig.patch.set_facecolor("#f4f8fb")
        ax.set_facecolor("#ffffff")

        x_pos = np.arange(len(chart_df))
        bar_colors = []
        for val in chart_df["value"]:
            if target_val is not None:
                on_target = (val >= target_val) if t_dir == ">=" else (val <= target_val)
                bar_colors.append("#22c55e" if on_target else "#3b82f6")
            else:
                bar_colors.append("#3b82f6")

        bars = ax.bar(x_pos, chart_df["value"], color=bar_colors,
                      width=0.6, edgecolor="white", linewidth=0.5)

        y_max = chart_df["value"].max() or 1
        for bar, val in zip(bars, chart_df["value"]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.01 * y_max,
                f"{val:.2f}", ha="center", va="bottom",
                fontsize=7.5, color="#0f172a"
            )

        if target_val is not None:
            ax.axhline(y=target_val, color="#ef4444", linewidth=1.8,
                       linestyle="--", zorder=5)
            ax.text(
                len(chart_df) - 0.4, target_val,
                f" Target = {target_val}",
                color="#ef4444", fontsize=8, va="bottom", ha="right"
            )

        ax.set_xticks(x_pos)
        ax.set_xticklabels(date_labels, rotation=30, ha="right", fontsize=8)
        ax.tick_params(axis="y", labelsize=8)
        ax.set_xlabel("Period", fontsize=9, color="#475569")
        ax.set_ylabel(y_label, fontsize=9, color="#475569")
        ax.set_title(f"Process Performance — {y_label}",
                     fontsize=11, fontweight="bold", color="#0f172a", pad=10)

        legend_handles = []
        if target_val is not None:
            legend_handles += [
                mpatches.Patch(color="#22c55e", label="On target"),
                mpatches.Patch(color="#3b82f6", label="Below/above target"),
                plt.Line2D([0], [0], color="#ef4444", linewidth=1.8,
                           linestyle="--", label=f"Target = {target_val}"),
            ]
        if legend_handles:
            ax.legend(handles=legend_handles, fontsize=8, loc="upper right",
                      framealpha=0.8, edgecolor="#e2e8f0")

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#e2e8f0")
        ax.spines["bottom"].set_color("#e2e8f0")
        ax.yaxis.grid(True, color="#f1f5f9", linewidth=0.8)
        ax.set_axisbelow(True)

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    except Exception:
        return None


def _propose_output_measurements_from_ctq(ctq_list: list, define_context: dict) -> list:
    """
    Structured proposal: dari CTQ list → kandidat output measurements.
    Robust terhadap berbagai format CTQ output dari Define.
    """
    if not ctq_list:
        return []

    goal    = str(define_context.get("goal_statement") or "").lower()
    problem = str(define_context.get("problem_statement") or "").lower()
    candidates = []
    seen = set()  # set of strings only, never dicts

    for ctq in ctq_list:
        if not isinstance(ctq, dict):
            ctq = {"ctq": str(ctq)}

        # Robust field extraction — handle berbagai schema Define output
        name = (
            str(ctq.get("name") or "")
            or str(ctq.get("ctq") or "")
            or str(ctq.get("label") or "")
        ).strip()

        # metric bisa nested dict — ambil string-nya saja
        metric_raw = ctq.get("metric") or ctq.get("kpi") or ""
        if isinstance(metric_raw, dict):
            metric = str(metric_raw.get("name") or metric_raw.get("value") or name).strip()
        else:
            metric = str(metric_raw).strip()

        unit_raw = ctq.get("unit") or ctq.get("target_unit") or ""
        unit = str(unit_raw).strip() if not isinstance(unit_raw, dict) else ""

        if not name:
            continue

        m_name = metric or name
        key = m_name.lower()

        if key not in seen:
            seen.add(key)
            candidates.append({
                "ctq_source":       name,
                "measurement_name": m_name,
                "unit":             unit,
                "representation":   "direct",
                "rationale":        f"Pengukuran langsung dari CTQ '{name}'. Paling representatif jika data tersedia.",
                "data_requirement": f"Data historis {m_name} dari sistem operasional.",
                "selected":         False,
            })

        # Trend variant
        m_lower = m_name.lower()
        if "%" in unit or any(k in m_lower for k in ["rate", "ratio", "pct", "percent", "tingkat", "overdue"]):
            t_name = f"Trend {m_name} per periode"
            t_key = t_name.lower()
            if t_key not in seen:
                seen.add(t_key)
                candidates.append({
                    "ctq_source":       name,
                    "measurement_name": t_name,
                    "unit":             unit,
                    "representation":   "trend",
                    "rationale":        f"Trend periodik {m_name} untuk melihat pola dan stabilitas proses.",
                    "data_requirement": "Data time-series minimal 8 periode.",
                    "selected":         False,
                })

        # Volume variant
        if any(k in goal + problem for k in ["ton", "unit", "pcs", "output", "produksi", "volume", "batch"]):
            v_name = f"Volume {name}"
            v_key = v_name.lower()
            if v_key not in seen:
                seen.add(v_key)
                candidates.append({
                    "ctq_source":       name,
                    "measurement_name": v_name,
                    "unit":             "unit/ton/pcs",
                    "representation":   "volume",
                    "rationale":        "Ukuran absolut output — relevan jika goal menyebut target volume.",
                    "data_requirement": "Data produksi/output per batch/shift/hari.",
                    "selected":         False,
                })

    return candidates

def _propose_operational_definitions_llm(
    selected_measurements: list,
    define_context: dict,
    project_path: str = "standard",
) -> list:
    """
    Agent propose operational definitions untuk setiap measurement yang dipilih.
    User review dan revise di data_editor — ini adalah proposal, bukan keputusan final.
    """
    if not selected_measurements:
        return []

    prompt = f"""
You are a Lean Six Sigma Master Black Belt helping a project leader define operational definitions.

Project context:
- Problem: "{define_context.get('problem_statement', '')}"
- Goal: "{define_context.get('goal_statement', '')}"
- CTQ: {json.dumps(define_context.get('ctq', ''), ensure_ascii=False)}

For each selected output measurement below, propose a complete operational definition.
Be specific, practical, and grounded in the project context.

Selected measurements:
{json.dumps([{"name": m.get("measurement_name"), "unit": m.get("unit"), "ctq_source": m.get("ctq_source"), "representation": m.get("representation")} for m in selected_measurements], indent=2, ensure_ascii=False)}

Return JSON array only, one object per measurement, same order as input:
[
  {{
    "metric_name": "exact name from input",
    "what_measured": "what exactly is being measured and how it relates to the CTQ",
    "instrument_system": "source system / tool / instrument used to collect data",
    "method_formula": "exact formula or calculation method with numerator/denominator",
    "unit": "unit of measurement",
    "frequency": "how often data is collected (e.g. Monthly, Weekly, Per transaction)",
    "decision_criteria": "what value/threshold indicates good vs bad performance"
  }}
]
""".strip()

    try:
        raw = llm(prompt)
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            proposals = json.loads(m.group(0))
            if isinstance(proposals, list) and len(proposals) == len(selected_measurements):
                return proposals
    except Exception:
        pass

    # Fallback: structured template berdasarkan measurement name
    return [
        {
            "metric_name":       m.get("measurement_name", ""),
            "what_measured":     f"Measurement of {m.get('measurement_name', '')} related to CTQ '{m.get('ctq_source', '')}'",
            "instrument_system": "Source system / ERP / MES",
            "method_formula":    "Define formula here (numerator / denominator)",
            "unit":              m.get("unit", ""),
            "frequency":         "Monthly",
            "decision_criteria": "Per target in Define goal statement",
        }
        for m in selected_measurements
    ]

def _evaluate_measurement_candidates_llm(
    candidates: list,
    define_context: dict,
    project_path: str = "standard",
) -> list:
    """
    Evaluate each candidate against 4 LSS criteria using LLM.
    Cache result in caller (session state).
    """
    if not candidates:
        return candidates

    criteria_desc = """
1. VOC/VOB alignment: Direct relationship to Voice of Customer or Business
2. Process sensitivity: Reacts to changes in input and process variables
3. Predictive power: Allows forecasting degree to which requirements are met
4. CtQ/CtB specificity: Tells something specific about CtQ/CtB fulfillment
"""
    prompt = f"""
You are a Lean Six Sigma Master Black Belt evaluating output measurement candidates.

Project context:
- Problem: "{define_context.get('problem_statement', '')}"
- Goal: "{define_context.get('goal_statement', '')}"
- CTQ: {json.dumps(define_context.get('ctq', ''), ensure_ascii=False)}

Evaluate each candidate against these 4 criteria:
{criteria_desc}

Candidates:
{json.dumps([{"name": c.get("measurement_name"), "ctq_source": c.get("ctq_source"), "representation": c.get("representation")} for c in candidates], indent=2, ensure_ascii=False)}

Score each criterion 1-3 (1=weak, 2=moderate, 3=strong).
Recommendation: "recommended", "acceptable", or "not_recommended".

Return JSON array only, same order as input:
[
  {{
    "measurement_name": "...",
    "criteria": {{
      "voc_vob_alignment":   {{"score": 1, "rationale": "..."}},
      "process_sensitivity": {{"score": 1, "rationale": "..."}},
      "predictive_power":    {{"score": 1, "rationale": "..."}},
      "ctq_specificity":     {{"score": 1, "rationale": "..."}}
    }},
    "total_score": 4,
    "recommendation": "recommended",
    "summary": "1-sentence verdict"
  }}
]
""".strip()

    try:
        raw = llm(prompt)
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            evaluations = json.loads(m.group(0))
            if isinstance(evaluations, list) and len(evaluations) == len(candidates):
                for i, cand in enumerate(candidates):
                    cand["criteria_evaluation"] = evaluations[i]
                return candidates
    except Exception:
        pass

    for cand in candidates:
        cand.setdefault("criteria_evaluation", {"error": "Evaluation unavailable — review manually."})
    return candidates


def _ensure_operational_defs_schema(rows: Any) -> list:
    expected_keys = [
        "metric_name", "what_measured", "instrument_system",
        "method_formula", "unit", "frequency", "decision_criteria",
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
        for k, v in r.items():
            if k not in item:
                item[k] = v
        norm.append(item)
    return norm


def _validate_operational_defs(rows: list) -> dict:
    issues = []
    required = ["metric_name", "what_measured", "instrument_system",
                "method_formula", "unit", "frequency", "decision_criteria"]

    for i, r in enumerate(rows):
        # Skip baris kosong sepenuhnya (placeholder rows)
        all_empty = all(not str(r.get(k, "")).strip() for k in required)
        if all_empty:
            continue
        missing = [k for k in required if not str(r.get(k, "")).strip()]
        if missing:
            issues.append({
                "row": i, "type": "missing_fields", "severity": "high",
                "message": f"Row {i+1} missing: {', '.join(missing)}"
            })
        unit = str(r.get("unit", "")).strip()
        formula = str(r.get("method_formula", "")).lower()
        if unit == "%" and formula:
            if ("* 100" not in formula) and ("percent" not in formula) and ("%" not in formula):
                issues.append({
                    "row": i, "type": "unit_formula_mismatch", "severity": "medium",
                    "message": f"Row {i+1}: unit is % but formula doesn't show percent conversion."
                })
    return {"issues": issues, "issue_count": len(issues)}


def _measure_gate_evaluate(outputs: dict, user_inputs: dict, enforce_b_rules: bool = True) -> dict:
    failed = []
    risks = []
    actions = []
    conditional = []

    # A-rules (always enforced)
    y_conf = (outputs.get("y_variable_confirmed") or "").strip()
    if not y_conf:
        failed.append("A1_y_variable_not_confirmed")
        actions.append("Konfirmasi Y variable dari CTQ Define sebelum melanjutkan.")

    # B-rules
    op_defs   = outputs.get("operational_definitions") or []
    op_issues = outputs.get("operational_definitions_issues") or []
    mplan     = outputs.get("measurement_plan") or []
    prof      = outputs.get("dataset_profile") or {}
    dq        = outputs.get("data_quality_results") or {}
    perf      = outputs.get("process_performance_summary") or {}

    if not op_defs:
        if enforce_b_rules:
            failed.append("B1_operational_definitions_missing")
        else:
            conditional.append("B1_operational_definitions_missing")
        actions.append("Tambah minimal 1 baris operational definition untuk Y.")
    else:
        high_missing = [i for i in op_issues if isinstance(i, dict) and i.get("severity") == "high"]
        if high_missing and enforce_b_rules:
            failed.append("B1_operational_definitions_incomplete")
            actions.append("Lengkapi field yang kosong di operational definitions.")

    if not (isinstance(mplan, list) and len(mplan) > 0):
        if enforce_b_rules:
            conditional.append("B2_measurement_plan_missing")
        actions.append("Tambah minimal 1 item measurement plan.")

    if prof.get("available") is True:
        if prof.get("rows", 0) <= 0:
            if enforce_b_rules:
                failed.append("B3_dataset_empty")
            actions.append("Upload dataset dengan data aktual.")

        summary = perf.get("summary") if isinstance(perf, dict) else None
        if not (summary and isinstance(summary, dict) and summary.get("available") is not False):
            if enforce_b_rules:
                conditional.append("B3_baseline_descriptives_missing")
            actions.append("Pilih Y column yang sesuai untuk menghitung baseline.")

        comp = (dq.get("completeness") or {}).get("columns_with_missing_over_5pct") or []
        if comp:
            risks.append(f"Missing rate >5% pada kolom: {comp}.")
            actions.append("Tentukan aturan penanganan missing values.")

        cons_issues = (dq.get("consistency") or {}).get("issues") or []
        if cons_issues:
            risks.append("Consistency issues: duplicate rows / negative values.")
            actions.append("Bersihkan data sebelum finalize.")
    else:
        risks.append("Dataset belum diupload — baseline tidak bisa dikonfirmasi.")
        actions.append("Upload minimal 1 extract data untuk baseline.")

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
        "status": status,
        "policy": policy,
        "failed_rules": failed,
        "conditional_rules": conditional,
        "missing_evidence": failed + conditional,
        "risk_if_proceed": risks,
        "next_actions": actions[:8],
    }


def _build_measure_coaching_md_llm(
    user_inputs: Dict[str, Any],
    outputs: Dict[str, Any],
    gate: Dict[str, Any],
    define_context: Dict[str, Any],
    project_path: str = "standard",
) -> str:
    failed      = gate.get("failed_rules") or []
    conditional = gate.get("conditional_rules") or []
    actions     = gate.get("next_actions") or []
    status      = gate.get("status", "FAIL")

    prompt = f"""
You are a Lean Six Sigma Master Black Belt coaching a project leader on the Measure phase.

Gate Status: {status}
Failed rules: {json.dumps(failed)}
Conditional rules: {json.dumps(conditional)}

Context from Define:
- Problem Statement: "{define_context.get('problem_statement', '')}"
- Goal Statement: "{define_context.get('goal_statement', '')}"
- CTQ: {json.dumps(define_context.get('ctq', ''), ensure_ascii=False)}

Measure inputs from project leader:
- Y Variable selected: "{user_inputs.get('y_variable', '')}"
- Y Column in dataset: "{user_inputs.get('y_column_name', '')}"

Next actions flagged by system:
{json.dumps(actions, ensure_ascii=False, indent=2)}

Provide coaching as MBB:
1. Quote exact inputs when pointing out gaps
2. Explain WHY each gap matters for this specific project
3. Give concrete, actionable examples
4. Address FAIL items first, then CONDITIONAL
5. Professional tone, max 300 words
6. Format: markdown with ### headings and bullets
7. Start directly — no preamble
""".strip()

    try:
        raw = llm(prompt)
        if raw and len(raw.strip()) > 80:
            return raw.strip()
    except Exception:
        pass

    lines = [f"### MEASURE Gate: **{status}**"]
    for a in actions[:6]:
        lines.append(f"- {a}")
    return "\n".join(lines)


def _rule_based_measure_conclusion(outputs: Dict[str, Any], define_final: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    perf = outputs.get("process_performance_summary") or {}
    summary = perf.get("summary") if isinstance(perf, dict) else None
    target = _parse_goal_target(define_final)
    basis = []
    validated = "unknown"

    if summary and isinstance(summary, dict):
        mean = summary.get("mean")
        median = summary.get("median")
        basis.append(f"Baseline computed: mean={mean}, median={median}.")

        if target.get("available") and isinstance(mean, (int, float)):
            tv = target.get("value")
            td = target.get("direction", "<=")
            basis.append(f"Define goal target: {td} {tv}.")
            if td == "<=":
                validated = "true" if mean > tv else "false"
            else:
                validated = "true" if mean < tv else "false"
    else:
        basis.append("No baseline available.")

    return {
        "problem_validated": validated,
        "basis": basis[:6],
        "what_would_change_mind": [
            "Confirm target/spec numerically from Define/Finance/Customer.",
            "Upload dataset with sufficient window (2–4 weeks or multiple periods).",
            "Add date column for stability/timeliness checks.",
        ],
    }


def _build_measure_summary_md(measure_state: Dict[str, Any]) -> str:
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
        f"- **Project ID:** {pid}\n"
        f"- **Project Name:** {pname}\n"
        f"- **Status:** {status}\n"
        f"- **Y (confirmed):** {y}\n"
    )


# ======================================================
# 1. PERCEPTION
# ======================================================

def perception_step(project_id: str, user_inputs: Dict[str, Any], baseline_df=None) -> Dict[str, Any]:
    industry = user_inputs.get("industry")
    pain_theme = user_inputs.get("pain_theme")
    process_area = user_inputs.get("process_area")

    similar_audit_raw = audit.get_similar_audit_events(
        industry=industry, pain_theme=pain_theme,
        process_area=process_area, phase=PHASE_NAME, k=3,
    )

    retrieve_fn = getattr(memory, "retrieve_similar_measure_episodes", None)
    similar_memory_raw = []
    if callable(retrieve_fn):
        try:
            similar_memory_raw = retrieve_fn(industry, pain_theme, k=3)
        except Exception:
            similar_memory_raw = []

    def _trim_audit(events):
        return [{"phase": e.get("phase"), "status": e.get("status"),
                 "industry": (e.get("meta") or {}).get("industry")} for e in (events or [])]

    def _trim_memory(episodes):
        return [{"industry": (ep.get("meta") or {}).get("industry"),
                 "y_variable_confirmed": (ep.get("outputs") or {}).get("y_variable_confirmed")}
                for ep in (episodes or [])]

    perception = dp.build_perception_state(
        user_inputs=user_inputs,
        audit_episodes=_trim_audit(similar_audit_raw),
        memory_episodes=_trim_memory(similar_memory_raw),
        baseline_df=baseline_df,
    )
    return perception


# ======================================================
# 2. DECISION
# ======================================================

def decision_step(perception: Dict[str, Any], user_feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prompt = f"""
You are a DMAIC Black Belt AI agent in DECISION step of MEASURE phase.

Perception:
{json.dumps(perception, indent=2)[:5000]}

User feedback (if any):
{json.dumps(user_feedback, indent=2) if user_feedback else "None."}

SCOPE: Confirm Y variable, data availability, measurement plan, baseline (descriptive only).

Return JSON ONLY:
{{
  "y_variable_confirmed": "...",
  "data_sources": [{{"source_name": "...", "system": "...", "owner": "...", "fields_needed": [], "grain": "..."}}],
  "measurement_plan": [{{"what": "...", "where": "...", "how": "...", "frequency": "...", "owner": "..."}}],
  "coaching_flag": false,
  "coaching_reason": ""
}}
"""
    raw = llm(prompt)
    parsed = extract_json_from_llm(raw)
    if parsed:
        parsed.setdefault("coaching_flag", False)
        parsed.setdefault("coaching_reason", "")
        return parsed
    return {
        "y_variable_confirmed": "",
        "data_sources": [],
        "measurement_plan": [],
        "coaching_flag": False,
        "coaching_reason": "",
    }


# ======================================================
# 3. ACTION
# ======================================================

def action_step(
    project_id: str,
    user_inputs: Dict[str, Any],
    perception: Dict[str, Any],
    decisions: Dict[str, Any],
    define_final_raw: Optional[Dict[str, Any]] = None,
    user_feedback: Optional[Dict[str, Any]] = None,
    define_context: Optional[Dict[str, Any]] = None,
    baseline_profile: Optional[Dict[str, Any]] = None,
    dq_results: Optional[Dict[str, Any]] = None,
    baseline_results: Optional[Dict[str, Any]] = None,
    project_path: str = "standard",
) -> Dict[str, Any]:

    define_context = define_context or {}
    define_final_raw = define_final_raw or {}
    baseline_profile = baseline_profile or {"available": False}
    dq_results = dq_results or {"available": False}
    baseline_results = baseline_results or {"available": False}

    # Operational definitions
    op_defs_in = _ensure_operational_defs_schema(user_inputs.get("operational_definitions"))
    op_defs_validation = _validate_operational_defs(op_defs_in) if op_defs_in else {"issues": [], "issue_count": 0}
    user_inputs["operational_definitions"] = op_defs_in
    user_inputs["operational_definitions_validation"] = op_defs_validation

    # CTQ from Define
    ctq_snapshot = _extract_ctq_final_from_define(define_final_raw)

    # Chart commentary
    _stats = (baseline_results or {}).get("summary") or {}
    _mean = _stats.get("mean")
    _tinfo = (baseline_results or {}).get("target_info") or {}
    _tval = _tinfo.get("value")
    _tdir = _tinfo.get("direction", ">=")
    _y_name = user_inputs.get("y_variable") or "the selected metric"
    _chart_data = (baseline_results or {}).get("chart_data") or []
    _period_from = _chart_data[0].get("date", "?") if _chart_data else "?"
    _period_to = _chart_data[-1].get("date", "?") if _chart_data else "?"
    _dq_issues = (dq_results or {}).get("completeness", {}).get("columns_with_missing_over_5pct") or []
    _cons_issues = (dq_results or {}).get("consistency", {}).get("issues") or []
    _dq_quality = "good" if not _dq_issues and not _cons_issues else f"fair — issues: {(_dq_issues + [str(i) for i in _cons_issues])[:3]}"

    if _tval is not None and isinstance(_mean, float):
        _on_target = (_mean >= _tval) if _tdir == ">=" else (_mean <= _tval)
        _compare = f"{'meeting' if _on_target else 'NOT meeting'} target of {_tval}"
        if _on_target:
            _position = "meeting the target"
        elif _tdir == "<=":
            _position = f"above the maximum target"
        else:
            _position = f"below the minimum target"
        _conclusion = f"Baseline is {_position} of {_tval}."
    else:
        _compare = "no numeric target available"
        _conclusion = "Define target in goal statement to enable quantitative validation."

    chart_commentary = (
        f"This graphical display shows the performance of {_y_name} "
        f"based on data collected from {_period_from} to {_period_to}. "
        f"Data quality is {_dq_quality}. "
        f"The average value is {_mean if _mean is not None else 'unavailable'}"
        + (f" ({_compare})" if _tval is not None else "") + ". "
        f"Conclusion: {_conclusion}"
    )

    prompt = f"""
ACTION STEP — MEASURE PHASE ({project_path.upper()} PATH)

User Inputs: {json.dumps(user_inputs, indent=2)[:4000]}
Define context: {json.dumps(define_context, indent=2)[:2000]}
Operational definitions table: {json.dumps(op_defs_in, indent=2)[:2000]}
Validation issues: {json.dumps(op_defs_validation, indent=2)[:1000]}
Dataset profile: {json.dumps(baseline_profile, indent=2)[:1500]}
DQ results: {json.dumps(dq_results, indent=2)[:1500]}
Baseline descriptives: {json.dumps(baseline_results, indent=2)[:2000]}
User feedback: {json.dumps(user_feedback, indent=2) if user_feedback else "None."}

Rules: Stay in MEASURE scope only. No Analyze hypotheses.

Return JSON:
{{
  "y_variable_confirmed": "...",
  "operational_definitions": [],
  "operational_definitions_issues": [],
  "data_availability_map": [],
  "measurement_plan": [],
  "measurement_system_analysis": "USE_USER_INPUT",
  "data_quality_rationale": "...",
  "baseline_descriptive_metrics": {{}},
  "risks_and_assumptions": [],
  "out_of_scope_note": "..."
}}
"""
    raw = llm(prompt)
    parsed = extract_json_from_llm(raw) or {}
    # MSA dari user input, bukan LLM
    user_msa = user_inputs.get("measurement_system_analysis") or {}
    if user_msa:
        parsed["measurement_system_analysis"] = user_msa

    if isinstance(parsed, dict):
        parsed["data_quality_results"] = dq_results
        parsed["dataset_profile"] = baseline_profile
        parsed["process_performance_summary"] = baseline_results
        parsed["chart_commentary"] = chart_commentary
        parsed["ctq_final_used"] = ctq_snapshot

        # Ensure op_defs
        op_defs_out = _ensure_operational_defs_schema(parsed.get("operational_definitions")) or op_defs_in
        parsed["operational_definitions"] = op_defs_out
        parsed["operational_definitions_issues"] = (
            parsed.get("operational_definitions_issues")
            if isinstance(parsed.get("operational_definitions_issues"), list)
            else op_defs_validation.get("issues", [])
        )

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
    }

    measure_state["summary_md"] = _build_measure_summary_md(measure_state)
    return measure_state


# ======================================================
# MAIN ENTRYPOINT
# ======================================================

def run_measure_agent(
    project_id: str,
    user_inputs: Dict[str, Any],
    define_final: Optional[pd.DataFrame] = None,
    baseline_df: Optional[pd.DataFrame] = None,
    user_feedback=None,
    project_path: str | None = None,
) -> Dict[str, Any]:

    if project_path is None:
        try:
            project_path = memory.load_project_meta(project_id).get("path", "standard")
        except Exception:
            project_path = "standard"

    gate_mode = get_gate_mode(project_path)
    audit.log_phase_event(project_id, PHASE_NAME, "started", meta=user_inputs)

    perception = perception_step(project_id, user_inputs, baseline_df)
    decisions = decision_step(perception, user_feedback=user_feedback)

    define_context = _summarize_define_context(define_final)

    date_col = user_inputs.get("date_column")
    y_col = user_inputs.get("y_column_name") or (decisions or {}).get("y_variable_confirmed") or user_inputs.get("y_variable")

    profile = _profile_dataframe(baseline_df, date_col=date_col)
    dq_results = _dq_checks(baseline_df, y_col=y_col, date_col=date_col)
    segment_cols = user_inputs.get("segment_columns") or []
    baseline_results = _compute_baseline_descriptives(baseline_df, y_col=y_col, segment_cols=segment_cols, date_col=date_col)

    # Parse target dari define_final
    goal_target = _parse_goal_target(define_final)
    if not goal_target.get("available") and user_inputs.get("target_value"):
        try:
            goal_target = {
                "available": True,
                "value": float(user_inputs["target_value"]),
                "direction": user_inputs.get("target_direction", ">="),
                "source_text": "Input manual dari user",
            }
        except Exception:
            pass

    if isinstance(baseline_results, dict):
        baseline_results["target_info"] = goal_target

    _y_label = user_inputs.get("y_variable") or define_context.get("candidate_y_from_define") or "Value"
    _chart_b64 = _generate_performance_chart(baseline_results, goal_target, y_label=_y_label)
    if isinstance(baseline_results, dict):
        baseline_results["chart_b64"] = _chart_b64

    # Dynamic chart (deterministic, mesin chart bersama)
    if isinstance(baseline_results, dict):
        from app.utils import charts as _charts
        def _to_float(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        _cd = baseline_results.get("chart_data") or []
        _vals = [d.get("value") for d in _cd if isinstance(d, dict) and d.get("value") is not None]
        _ct = (user_inputs.get("chart_type") or "performance").strip().lower()
        _lsl, _usl = _to_float(user_inputs.get("lsl")), _to_float(user_inputs.get("usl"))
        _tgt = goal_target.get("value") if goal_target.get("available") else None
        if _ct in ("", "performance"):
            baseline_results["measure_chart"] = {
                "chart_type": "performance",
                "available": bool(_chart_b64),
            }
        else:
            if _ct == "auto":
                _ct = _charts.recommend_chart_type(
                    context={"lsl": _lsl, "usl": _usl}, values=_vals, phase="measure")
            baseline_results["measure_chart"] = _charts.build_chart(
                values=_vals, chart_type=_ct,
                lsl=_lsl, usl=_usl, target=_tgt, phase="measure")

    draft_state = action_step(
        project_id, user_inputs, perception, decisions,
        define_final_raw=define_final if isinstance(define_final, dict) else None,
        user_feedback=user_feedback,
        define_context=define_context,
        baseline_profile=profile,
        dq_results=dq_results,
        baseline_results=baseline_results,
        project_path=project_path,
    )

    # Gate evaluation
    gate = _measure_gate_evaluate(
        draft_state.get("outputs", {}),
        user_inputs,
        enforce_b_rules=gate_mode["enforce_b_rules"],
    )
    draft_state["gate"] = gate

    # Coaching — LLM only on FAIL/WEAK_PASS
    gate_status = gate.get("status", "")
    if gate_status in ("FAIL", "WEAK_PASS"):
        _coaching = _build_measure_coaching_md_llm(
            user_inputs=user_inputs,
            outputs=draft_state.get("outputs") or {},
            gate=gate,
            define_context=define_context,
            project_path=project_path,
        )
    else:
        _coaching = "✅ Gate PASS — measurement plan, operational definitions, and baseline are complete."

    draft_state["coaching_md"] = _coaching
    draft_state["insight_md"] = _coaching

    outs = draft_state.get("outputs") or {}
    outs["_gate"] = gate
    outs["measure_conclusion"] = _rule_based_measure_conclusion(outs, define_final if isinstance(define_final, dict) else None)
    draft_state["outputs"] = outs
    draft_state["routing"] = {"policy": gate.get("policy"), "status": gate.get("status")}
    draft_state["updated_at"] = _now_iso()
    draft_state["summary_md"] = _build_measure_summary_md(draft_state)

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
# FINALIZE
# ======================================================

def finalize_measure_agent(
    project_id: str,
    measure_state: Dict[str, Any],
    user_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Token-safe finalize. NO LLM calls."""

    gate = (measure_state or {}).get("gate") or {}
    if gate.get("status") == "FAIL":
        return {
            "status": "blocked",
            "reason": "MEASURE gate FAIL — selesaikan A-rules sebelum finalize.",
            "gate": gate,
            "measure_state": measure_state,
            "word_path": None,
        }

    final_state = copy.deepcopy(measure_state)

    reset_fn = getattr(memory, "reset_approvals", None)
    if callable(reset_fn):
        # Gunakan lowercase agar match dengan konvensi penyimpanan approval (save_approval/get_phase_approval_status)
        reset_fn(project_id, PHASE_NAME.lower())

    if isinstance(final_state, dict):
        final_state["phase"] = final_state.get("phase") or "measure"
        final_state["status"] = "final"
        final_state["schema_version"] = final_state.get("schema_version") or MEASURE_SCHEMA
        final_state.setdefault("created_at", _now_iso())
        final_state["updated_at"] = _now_iso()
        if not final_state.get("summary_md"):
            final_state["summary_md"] = _build_measure_summary_md(final_state)
        final_state.setdefault("insight_md", "")

    # Save final
    save_final_fn = getattr(memory, "save_measure_final", None)
    if callable(save_final_fn):
        try:
            save_final_fn(project_id, final_state)
        except Exception:
            pass

    # Delete draft
    delete_draft_fn = getattr(memory, "delete_measure_draft", None)
    if callable(delete_draft_fn):
        try:
            delete_draft_fn(project_id)
        except Exception:
            pass

    # Adaptation / audit
    meta = final_state.get("meta") or {}
    outputs = final_state.get("outputs") or {}
    try:
        audit.log_phase_event(project_id, PHASE_NAME, "finalized", meta={
            "gate_status": gate.get("status"),
            "y_variable_confirmed": outputs.get("y_variable_confirmed"),
        })
    except Exception:
        pass

    # Word export
    word_path = None
    export_fn = getattr(reporting, "export_measure_to_word", None)
    if callable(export_fn):
        try:
            word_path = export_fn(final_state, path=f"{project_id}_MEASURE_REPORT.docx")
        except Exception:
            pass

    return {
        "status": "finalized",
        "measure_state": final_state,
        "critique": [],
        "summary": final_state.get("summary_md") or _build_measure_summary_md(final_state),
        "word_path": word_path,
        "message": "Measure finalized (locked).",
    }
