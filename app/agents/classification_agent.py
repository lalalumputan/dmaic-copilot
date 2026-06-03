"""
classification_agent.py — Rule-based project classification.

Determines whether a project should follow Quick Improvement or Standard DMAIC path
based on 5 criteria scored deterministically. No LLM calls.

Output: recommended_path, confidence, score_breakdown, reasoning.
"""

from __future__ import annotations
from typing import Any, Dict


# ======================================================
# SCORING CONSTANTS
# ======================================================

MAX_SCORE = 15  # 5 criteria × max 3 points each

QUICK_THRESHOLD  = 7   # score ≤ 7  → quick
STANDARD_THRESHOLD = 11 # score ≥ 11 → standard
# 8–10 → borderline (system recommends standard, note ambiguity)


# ======================================================
# SCORING RUBRIC (per criterion)
# ======================================================

def _score_performance_impact(level: str) -> tuple[int, str]:
    """
    Criterion 1: Impact on business performance (quality/cost/time/output).
    level: 'low' | 'medium' | 'high'
    """
    rubric = {
        "low":    (1, "Limited impact — single metric, localized effect."),
        "medium": (2, "Moderate impact — affects one KPI with cross-area visibility."),
        "high":   (3, "Significant impact — multiple KPIs or financial materiality."),
    }
    return rubric.get(level, (2, "Impact level not specified, defaulting to medium."))


def _score_complexity(level: str) -> tuple[int, str]:
    """
    Criterion 2: Process complexity and variation involved.
    level: 'low' | 'medium' | 'high'
    """
    rubric = {
        "low":    (1, "Simple, single-step process with low variation."),
        "medium": (2, "Multi-step process with moderate variation sources."),
        "high":   (3, "Complex process, multiple variation sources, interaction effects likely."),
    }
    return rubric.get(level, (2, "Complexity not specified, defaulting to medium."))


def _score_scope(num_functions: int) -> tuple[int, str]:
    """
    Criterion 3: Scope — number of functions or areas involved.
    num_functions: integer
    """
    if num_functions <= 1:
        return 1, "Single function/area — contained scope."
    elif num_functions <= 3:
        return 2, "2–3 functions involved — moderate cross-functional scope."
    else:
        return 3, "4+ functions involved — broad organizational scope."


def _score_data_availability(available: bool, requires_collection: bool) -> tuple[int, str]:
    """
    Criterion 4: Data availability and collection need.
    available: data already exists
    requires_collection: new data collection needed
    """
    if available and not requires_collection:
        return 1, "Data already available — no collection needed."
    elif available and requires_collection:
        return 2, "Partial data available — some collection required."
    else:
        return 3, "No data available — full data collection and MSA required."


def _score_implementation_risk(level: str) -> tuple[int, str]:
    """
    Criterion 5: Implementation risk and failure impact.
    level: 'low' | 'medium' | 'high'
    """
    rubric = {
        "low":    (1, "Low risk — reversible changes, limited downstream impact."),
        "medium": (2, "Moderate risk — partial reversibility, some downstream dependencies."),
        "high":   (3, "High risk — irreversible changes or significant failure consequences."),
    }
    return rubric.get(level, (2, "Risk level not specified, defaulting to medium."))


# ======================================================
# MAIN CLASSIFICATION FUNCTION
# ======================================================

def run_classification_agent(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rule-based classification. Called from MAIN.py when a new project is created.

    inputs = {
        "performance_impact":     "low" | "medium" | "high",
        "process_complexity":     "low" | "medium" | "high",
        "num_functions_involved": int,
        "data_available":         bool,
        "requires_data_collection": bool,
        "implementation_risk":    "low" | "medium" | "high",
    }

    Returns:
    {
        "recommended_path":  "quick" | "standard",
        "confidence":        "high" | "medium" | "low",
        "total_score":       int,
        "max_score":         int,
        "score_breakdown":   { criterion: {"score": int, "reasoning": str} },
        "overall_reasoning": str,
        "borderline":        bool,
    }
    """

    # --- Score each criterion ---
    s1, r1 = _score_performance_impact(inputs.get("performance_impact", "medium"))
    s2, r2 = _score_complexity(inputs.get("process_complexity", "medium"))
    s3, r3 = _score_scope(inputs.get("num_functions_involved", 2))
    s4, r4 = _score_data_availability(
        inputs.get("data_available", True),
        inputs.get("requires_data_collection", False),
    )
    s5, r5 = _score_implementation_risk(inputs.get("implementation_risk", "medium"))

    total = s1 + s2 + s3 + s4 + s5
    borderline = QUICK_THRESHOLD < total < STANDARD_THRESHOLD

    # --- Determine path ---
    if total <= QUICK_THRESHOLD:
        recommended_path = "quick"
        confidence = "high" if total <= 5 else "medium"
        overall_reasoning = (
            f"Total complexity score is {total}/{MAX_SCORE}. "
            "The problem is sufficiently contained for Quick Improvement execution. "
            "Compressed DMAIC depth is appropriate — focus on essentials per phase."
        )
    elif total >= STANDARD_THRESHOLD:
        recommended_path = "standard"
        confidence = "high" if total >= 13 else "medium"
        overall_reasoning = (
            f"Total complexity score is {total}/{MAX_SCORE}. "
            "The problem warrants full Standard DMAIC execution — "
            "all formal deliverables are required."
        )
    else:
        # Borderline: default to standard, flag ambiguity
        recommended_path = "standard"
        confidence = "low"
        overall_reasoning = (
            f"Total complexity score is {total}/{MAX_SCORE} — borderline zone (8–10). "
            "System recommends Standard DMAIC as the safer default. "
            "Reviewer should assess whether Quick Improvement is sufficient given organizational context."
        )

    return {
        "recommended_path": recommended_path,
        "confidence":       confidence,
        "total_score":      total,
        "max_score":        MAX_SCORE,
        "borderline":       borderline,
        "score_breakdown": {
            "performance_impact": {"score": s1, "reasoning": r1},
            "process_complexity": {"score": s2, "reasoning": r2},
            "scope":              {"score": s3, "reasoning": r3},
            "data_availability":  {"score": s4, "reasoning": r4},
            "implementation_risk":{"score": s5, "reasoning": r5},
        },
        "overall_reasoning": overall_reasoning,
    }


# ======================================================
# GATE ENFORCEMENT HELPER
# ======================================================

def get_gate_mode(project_path: str) -> Dict[str, Any]:
    """
    Returns gate enforcement config for a given path.
    Used by all run_*_agent() functions to adjust gate behavior.

    Returns:
    {
        "enforce_b_rules": bool,
        "prompt_depth":    "compressed" | "full",
        "path_label":      str,
    }
    """
    if project_path == "quick":
        return {
            "enforce_b_rules": False,
            "prompt_depth":    "compressed",
            "path_label":      "Quick Improvement",
        }
    else:
        return {
            "enforce_b_rules": True,
            "prompt_depth":    "full",
            "path_label":      "Standard DMAIC",
        }