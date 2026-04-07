from __future__ import annotations
from pydoc import doc
from typing import Any, Dict, List
from docx import Document


def _get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _as_text(val: Any) -> str:
    if val is None:
        return "(not provided)"
    if isinstance(val, (list, tuple, set)):
        return ", ".join(str(v) for v in val)
    if isinstance(val, dict):
        return "; ".join(f"{k}: {v}" for k, v in val.items())
    return str(val)


def _normalize_define(define_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize BOTH schemas into one view:
    - New schema: define_state["inputs"], define_state["outputs"]
    - Old schema: fields at top-level
    Returns dict with keys: project_name, problem, goal, business_case, scope_in/out,
    ctqs, sipoc, early_measure_plan, y_variable, x_categories, risk_level, project_type.
    """
    inputs = define_state.get("inputs") or {}
    outputs = define_state.get("outputs") or {}

    project_name = (
        inputs.get("project_name")
        or define_state.get("project_name")
        or outputs.get("project_name")
        or "Unnamed Project"
    )

    problem = outputs.get("problem_statement") or define_state.get("problem_statement")
    goal = outputs.get("goal_statement") or define_state.get("goal_statement")
    business_case = outputs.get("business_case") or define_state.get("business_case")

    scope = outputs.get("project_scope") or define_state.get("project_scope") or {}
    scope_in = scope.get("in_scope") or scope.get("inScope")
    scope_out = scope.get("out_of_scope") or scope.get("outOfScope")

    ctqs = outputs.get("ctq_list") or define_state.get("ctq_list") or []
    sipoc = outputs.get("sipoc") or define_state.get("sipoc") or {}
    early = outputs.get("early_measure_plan") or define_state.get("early_measure_plan") or {}

    y_var = outputs.get("y_variable") or define_state.get("y_variable")
    x_cat = outputs.get("x_categories") or define_state.get("x_categories")

    risk_level = outputs.get("risk_level") or define_state.get("risk_level")
    project_type = outputs.get("project_type") or define_state.get("project_type")

    return {
        "project_name": project_name,
        "problem": problem,
        "goal": goal,
        "business_case": business_case,
        "scope_in": scope_in,
        "scope_out": scope_out,
        "ctqs": ctqs,
        "sipoc": sipoc,
        "early_measure_plan": early,
        "y_variable": y_var,
        "x_categories": x_cat,
        "risk_level": risk_level,
        "project_type": project_type,
    }

def _bullet_list(val: Any) -> str:
    if val in (None, "", [], {}):
        return "_not available_"
    if isinstance(val, list):
        return "\n".join([f"- {v}" for v in val]) if val else "_not available_"
    if isinstance(val, dict):
        # dict -> bullets of key: value
        lines = []
        for k, v in val.items():
            lines.append(f"- **{k}**: {_as_text(v)}")
        return "\n".join(lines) if lines else "_not available_"
    return f"- {val}"


def _sipoc_md(sipoc: Any) -> str:
    if not isinstance(sipoc, dict) or not sipoc:
        return "_not available_"
    # Support both title-case and lowercase keys
    suppliers = sipoc.get("Suppliers") or sipoc.get("suppliers") or []
    inputs = sipoc.get("Inputs") or sipoc.get("inputs") or []
    process = sipoc.get("Process") or sipoc.get("process") or []
    outputs = sipoc.get("Outputs") or sipoc.get("outputs") or []
    customers = sipoc.get("Customers") or sipoc.get("customers") or []

    return (
        "**Suppliers**\n" + _bullet_list(suppliers) + "\n\n"
        "**Inputs**\n" + _bullet_list(inputs) + "\n\n"
        "**Process**\n" + _bullet_list(process) + "\n\n"
        "**Outputs**\n" + _bullet_list(outputs) + "\n\n"
        "**Customers**\n" + _bullet_list(customers)
    )
import re

def _strip_md_heading(text: str) -> str:
    if not text:
        return ""
    # remove leading markdown heading like "### Agent Insight"
    return re.sub(r"^\s*#{1,6}\s*Agent Insight\s*\n?", "", text.strip(), flags=re.IGNORECASE)


def build_define_summary_md(define_state: Dict[str, Any]) -> str:
    n = _normalize_define(define_state)

    def _na(x):
        return x if x not in (None, "", [], {}) else "_not available_"

    md: List[str] = []
    md.append(f"## DEFINE PHASE SUMMARY — {n['project_name']}\n")

    # 1) Problem / Goal / Business Case
    md.append("### Problem Statement")
    md.append(str(_na(n["problem"])) + "\n")
    md.append("### Goal Statement")
    md.append(str(_na(n["goal"])) + "\n")
    md.append("### Business Case")
    md.append(str(_na(n["business_case"])) + "\n")

    # 2) Scope
    md.append("### Scope")
    md.append(f"- In Scope: {_as_text(n['scope_in'])}")
    md.append(f"- Out of Scope: {_as_text(n['scope_out'])}\n")

    # 3) CTQ
    md.append("### CTQs")
    ctqs = n["ctqs"]
    if isinstance(ctqs, list) and ctqs:
        for c in ctqs:
            if isinstance(c, dict):
                name = c.get("name") or c.get("ctq") or "CTQ"
                metric = c.get("metric") or ""
                unit = c.get("unit") or ""
                desc = c.get("description") or ""
                target = c.get("target") or ""
                mu = f" ({metric} {unit})".strip() if metric or unit else ""
                tail = f" — {desc}" if desc else ""
                tgt = f" | Target: {target}" if target else ""
                md.append(f"- **{name}**{mu}{tail}{tgt}".strip())
            else:
                md.append(f"- {c}")
    else:
        md.append("_not available_")
    md.append("")  # spacing

    # 4) Y Variable
    md.append("### Y Variable")
    md.append(str(_na(n["y_variable"])) + "\n")

    # 5) X Categories (6M)
    md.append("### X Categories (6M)")
    xc = n["x_categories"]
    if isinstance(xc, dict) and xc:
        for k, v in xc.items():
            md.append(f"**{k}**")
            md.append(_bullet_list(v))
            md.append("")
    else:
        md.append("_not available_\n")

    # 6) SIPOC
    md.append("### SIPOC")
    md.append(_sipoc_md(n["sipoc"]))
    md.append("")

    # 7) Early Measurement Plan
    md.append("### Early Measurement Plan")
    emp = n["early_measure_plan"] or {}
    if isinstance(emp, dict) and emp:
        md.append(f"- Measures: {_as_text(emp.get('measures'))}")
        md.append(f"- Responsible: {_as_text(emp.get('responsible'))}")
        md.append(f"- Frequency: {_as_text(emp.get('frequency'))}")
        md.append(f"- Data Source: {_as_text(emp.get('data_source') or emp.get('dataSource'))}\n")
    else:
        md.append("_not available_\n")

    # 8) Risk & Project Type
    md.append("### Risk & Project Type")
    md.append(f"- Risk Level: {_as_text(n['risk_level'])}")
    md.append(f"- Project Type: {_as_text(n['project_type'])}\n")

    # 9) Agent Insight (ALWAYS show if available)
    ins = define_state.get("insight_md") or ""
    ins = _strip_md_heading(ins)

    if ins.strip():
        md.append("### Agent Insight")
        md.append(ins)

    return "\n".join(md).strip()

# Backward-compatible alias
def build_define_summary_markdown(define_state: Dict[str, Any]) -> str:
    return build_define_summary_md(define_state)


def export_define_to_word(define_state: Dict[str, Any], path: str = "define_report.docx") -> str:
    n = _normalize_define(define_state)
    doc = Document()

    doc.add_heading(f"DEFINE PHASE REPORT — {n['project_name']}", 0)
    doc.add_heading("Problem Statement", level=1)
    doc.add_paragraph(_as_text(n["problem"]))
    doc.add_heading("Goal Statement", level=1)
    doc.add_paragraph(_as_text(n["goal"]))
    doc.add_heading("Business Case", level=1)
    doc.add_paragraph(_as_text(n["business_case"]))

    doc.add_heading("Scope", level=1)
    doc.add_paragraph(f"In Scope  : {_as_text(n['scope_in'])}")
    doc.add_paragraph(f"Out Scope : {_as_text(n['scope_out'])}")

    doc.add_heading("CTQ List", level=1)
    ctqs = n["ctqs"]
    if isinstance(ctqs, list) and ctqs:
        for c in ctqs:
            if isinstance(c, dict):
                line = f"- {c.get('name','CTQ')} ({c.get('metric','')} {c.get('unit','')})"
                doc.add_paragraph(line)
                if c.get("description"):
                    doc.add_paragraph(f"  - {c.get('description')}")
            else:
                doc.add_paragraph(f"- {c}")
    else:
        doc.add_paragraph("(no CTQs recorded)")

    doc.add_heading("Y Variable", level=1)
    doc.add_paragraph(_as_text(n["y_variable"]))

    doc.add_heading("X Categories (6M)", level=1)
    xc = n["x_categories"]
    if isinstance(xc, dict) and xc:
        for k, v in xc.items():
            doc.add_paragraph(f"{k}: {_as_text(v)}")
    else:
        doc.add_paragraph("(not provided)")

    doc.add_heading("SIPOC", level=1)
    sipoc = n["sipoc"] or {}
    doc.add_paragraph(f"Suppliers: {_as_text(sipoc.get('Suppliers') or sipoc.get('suppliers'))}")
    doc.add_paragraph(f"Inputs   : {_as_text(sipoc.get('Inputs')   or sipoc.get('inputs'))}")
    doc.add_paragraph(f"Process  : {_as_text(sipoc.get('Process')  or sipoc.get('process'))}")
    doc.add_paragraph(f"Outputs  : {_as_text(sipoc.get('Outputs')  or sipoc.get('outputs'))}")
    doc.add_paragraph(f"Customers: {_as_text(sipoc.get('Customers') or sipoc.get('customers'))}")

    doc.add_heading("Early Measurement Plan", level=1)
    emp = n["early_measure_plan"] or {}
    doc.add_paragraph(f"Measures   : {_as_text(emp.get('measures'))}")
    doc.add_paragraph(f"Responsible: {_as_text(emp.get('responsible'))}")

    doc.add_heading("Risk & Project Type", level=1)
    doc.add_paragraph(f"Risk Level   : {_as_text(n['risk_level'])}")
    doc.add_paragraph(f"Project Type : {_as_text(n['project_type'])}")
    
    # Agent Insight
    ins = define_state.get("insight_md") or ""
    ins = _strip_md_heading(ins)

    doc.add_heading("Agent Insight", level=1)
    doc.add_paragraph(ins if ins.strip() else "(not provided)")

    doc.save(path)



    return path
