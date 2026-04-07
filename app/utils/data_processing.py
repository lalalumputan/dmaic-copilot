# dmaic_copilot/app/utils/data_processing.py

from __future__ import annotations
import json
import pandas as pd
from typing import Any, Dict, List, Optional

from app.utils import llm_engine


# ------------------------------------------------------------
# BASELINE HANDLING
# ------------------------------------------------------------

def summarize_baseline(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    if df is None:
        return {}

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    summary = {}

    for col in numeric_cols:
        s = df[col].dropna()
        if len(s) == 0:
            continue

        summary[col] = {
            "mean": float(s.mean()),
            "std": float(s.std()),
            "min": float(s.min()),
            "max": float(s.max()),
            "count": int(s.count()),
        }
    return summary


# ------------------------------------------------------------
# VOC & CTQ EXTRACTION
# ------------------------------------------------------------

def extract_ctq_voc(problem_text: str, voc_list: List[str]) -> Dict[str, Any]:
    voc_text = "\n".join(voc_list) if voc_list else "(No VOC provided)"

    prompt = f"""
You are a Lean Six Sigma DEFINE-phase AI Agent.

Problem:
{problem_text}

VOC items:
{voc_text}

1. Extract 1–3 candidate CTQs with metric + unit.
2. Cluster VOC into 1–3 themes.

Return JSON:
{{
 "candidate_ctqs": [
     {{"name": "...", "description": "...", "metric": "...", "unit": "..." }}
 ],
 "voc_clusters": [
     {{"theme": "...", "items": ["...", "..."]}}
 ]
}}
"""
    raw = llm_engine.complete(prompt)
    try:
        parsed = json.loads(raw)
    except:
        parsed = {
            "candidate_ctqs": [],
            "voc_clusters": [],
            "raw_llm_output": raw,
        }

    return parsed


# ------------------------------------------------------------
# BUILD PERCEPTION STATE
# ------------------------------------------------------------

def build_perception_state(
    user_inputs: Dict[str, Any],
    audit_episodes: List[Dict[str, Any]],
    memory_episodes: List[Dict[str, Any]],
    baseline_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:

    ctq_voc = extract_ctq_voc(
        user_inputs.get("problem_text", ""),
        user_inputs.get("voc_list", [])
    )

    baseline_summary = summarize_baseline(baseline_df)

    return {
        "project_name": user_inputs.get("project_name"),
        "industry": user_inputs.get("industry"),
        "process_area": user_inputs.get("process_area"),
        "pain_theme": user_inputs.get("pain_theme"),
        "problem_text": user_inputs.get("problem_text"),
        "voc_list": user_inputs.get("voc_list"),
        "key_issue": user_inputs.get("key_issue"),
        "ctq_voc_info": ctq_voc,
        "baseline_summary": baseline_summary,
        "similar_audit_episodes": audit_episodes,
        "similar_memory_episodes": memory_episodes,
    }
