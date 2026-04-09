from __future__ import annotations
from pydoc import doc
from typing import Any, Dict, List
from docx import Document
from docx.shared import RGBColor


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
    import re
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    inputs  = define_state.get("inputs") or {}
    outputs = define_state.get("outputs") or {}
    gate    = define_state.get("gate_result") or {}
    n       = _normalize_define(define_state)

    doc = Document()

    # ── Page margins ──────────────────────────────────────
    for section in doc.sections:
        section.top_margin    = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin   = Inches(1.2)
        section.right_margin  = Inches(1.2)

    # ── Helpers ───────────────────────────────────────────
    def heading(text, level=1):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(13 if level == 1 else 11)
        run.font.color.rgb = RGBColor(0x07, 0x59, 0x85)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement('w:pBdr')
        bot = OxmlElement('w:bottom')
        bot.set(qn('w:val'), 'single')
        bot.set(qn('w:sz'), '4')
        bot.set(qn('w:space'), '1')
        bot.set(qn('w:color'), '0759a0')
        pBdr.append(bot)
        pPr.append(pBdr)
        return p

    def body(text):
        t = str(text or "").strip()
        if not t or t in ("(not provided)", "_not available_", "-"):
            p = doc.add_paragraph("(not provided)")
            p.runs[0].font.italic = True
            p.runs[0].font.color.rgb = RGBColor(0x94, 0xa3, 0xb8)
        else:
            doc.add_paragraph(t)

    def bullet(text):
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(str(text))

    def kv(label, value):
        p = doc.add_paragraph()
        r = p.add_run(f"{label}: ")
        r.bold = True
        p.add_run(str(value or "(not provided)"))

    def clean_md(text):
        if not text: return ""
        text = re.sub(r'#{1,6}\s*', '', text)
        text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
        text = re.sub(r'\\[*#_]', '', text)
        return text.strip()

    def styled_table(headers, rows, header_color="1e3a5f"):
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        hdr_cells = t.rows[0].cells
        for i, h in enumerate(headers):
            hdr_cells[i].text = h
            run = hdr_cells[i].paragraphs[0].runs[0]
            run.bold = True
            run.font.color.rgb = RGBColor(0xff, 0xff, 0xff)
            run.font.size = Pt(10)
            tc = hdr_cells[i]._tc
            tcPr = tc.get_or_add_tcPr()
            shd = OxmlElement('w:shd')
            shd.set(qn('w:fill'), header_color)
            shd.set(qn('w:val'), 'clear')
            tcPr.append(shd)
        for row in rows:
            cells = t.add_row().cells
            for i, val in enumerate(row):
                cells[i].text = str(val or "")
                cells[i].paragraphs[0].runs[0].font.size = Pt(9) if cells[i].paragraphs[0].runs else None
        doc.add_paragraph()
        return t

    def gate_badge(status):
        colors = {
            "PASSED":      ("0f5132", "✅ GATE PASSED"),
            "CONDITIONAL": ("7c4700", "⚠️ GATE CONDITIONAL"),
            "FAILED":      ("842029", "❌ GATE FAILED"),
        }
        hex_color, label = colors.get(status, ("374151", f"GATE {status}"))
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        p = doc.add_paragraph()
        run = p.add_run(f"  {label}  ")
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(r, g, b)

    # ── COVER ─────────────────────────────────────────────
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("DEFINE PHASE REPORT")
    r.bold = True
    r.font.size = Pt(22)
    r.font.color.rgb = RGBColor(0x0f, 0x17, 0x2a)

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.add_run(n["project_name"]).font.size = Pt(14)

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    import datetime
    p3.add_run(f"Generated: {datetime.datetime.now().strftime('%d %B %Y')}").font.size = Pt(10)

    doc.add_paragraph()
    gate_badge(gate.get("status", "-"))
    doc.add_paragraph()
    # Draft watermark
    _status = define_state.get("status", "")
    if _status != "final":
        p_draft = doc.add_paragraph()
        p_draft.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_draft = p_draft.add_run("⚠️  DRAFT — NOT YET FINALIZED  ⚠️")
        r_draft.bold = True
        r_draft.font.size = Pt(12)
        r_draft.font.color.rgb = RGBColor(0x84, 0x20, 0x29)
    doc.add_paragraph()

    # ── 1. PROBLEM & GOAL ────────────────────────────────
    heading("1. Problem Statement")
    body(n["problem"])

    heading("2. Goal Statement")
    body(n["goal"])

    heading("3. Business Case")
    body(n["business_case"])

    # ── 4. SCOPE ─────────────────────────────────────────
    heading("4. Scope")
    scope_in  = n["scope_in"]
    scope_out = n["scope_out"]
    p = doc.add_paragraph()
    p.add_run("In Scope:").bold = True
    for item in (scope_in if isinstance(scope_in, list) else [scope_in] if scope_in else []):
        bullet(item)
    p2 = doc.add_paragraph()
    p2.add_run("Out of Scope:").bold = True
    for item in (scope_out if isinstance(scope_out, list) else [scope_out] if scope_out else []):
        bullet(item)

    # ── 5. VOC/VOB → CTQ/CTB ────────────────────────────
    heading("5. VOC / VOB → CTQ / CTB (Tool I)")
    voc_table = inputs.get("voc_table") or []
    if voc_table:
        rows = [
            [r.get("Type",""), r.get("Voice",""), r.get("Key Issue",""), r.get("CTQ/CTB","")]
            for r in voc_table
        ]
        styled_table(["Type", "Voice", "Key Issue", "CTQ / CTB"], rows)
    else:
        body(None)

    # ── 6. CTQ LIST ──────────────────────────────────────
    heading("6. CTQ / CTB List")
    ctqs = n["ctqs"]
    if isinstance(ctqs, list) and ctqs:
        rows = []
        for c in ctqs:
            if isinstance(c, dict):
                rows.append([
                    c.get("name",""),
                    c.get("metric",""),
                    c.get("unit",""),
                    c.get("description",""),
                ])
        if rows:
            styled_table(["CTQ Name","Metric","Unit","Description"], rows)
    else:
        body(None)

    # ── 7. Y VARIABLE ────────────────────────────────────
    heading("7. Y Variable")
    body(n["y_variable"])

    # ── 8. SIPOC ─────────────────────────────────────────
    heading("8. SIPOC")
    sipoc = n["sipoc"] or {}
    if sipoc:
        keys = ["Suppliers","Inputs","Process","Outputs","Customers"]
        sipoc_lists = []
        for k in keys:
            val = sipoc.get(k) or sipoc.get(k.lower()) or []
            sipoc_lists.append(val if isinstance(val, list) else [str(val)] if val else [])
        max_len = max([len(x) for x in sipoc_lists], default=0)
        if max_len > 0:
            rows = []
            for i in range(max_len):
                rows.append([lst[i] if i < len(lst) else "" for lst in sipoc_lists])
            styled_table(keys, rows, header_color="1e3a5f")
    else:
        body(None)

    # ── 9. BENEFIT ESTIMATE ──────────────────────────────
    heading("9. Benefit Estimate")
    benefit = outputs.get("benefit_estimate") or {}
    if benefit:
        if benefit.get("benefit_potentials"):
            kv("Benefit Potentials", benefit["benefit_potentials"])
        if benefit.get("calculation_method"):
            kv("Calculation Method", benefit["calculation_method"])

        kpi_rows_raw = benefit.get("kpi_table") or []
        if kpi_rows_raw:
            doc.add_paragraph().add_run("KPI Table:").bold = True
            kpi_headers = ["No","KPI","Period","Unit","Baseline","Target","Δ%","Comment"]
            kpi_rows = []
            for r in kpi_rows_raw:
                kpi_rows.append([
                    str(r.get("no","")), r.get("kpi",""), r.get("period",""),
                    r.get("unit",""), r.get("baseline",""), r.get("target",""),
                    r.get("delta_pct",""), r.get("comment",""),
                ])
            styled_table(kpi_headers, kpi_rows, header_color="0f5132")

        assumptions = benefit.get("assumptions") or []
        if assumptions:
            doc.add_paragraph().add_run("Assumptions:").bold = True
            for a in assumptions:
                bullet(a)

        soft = benefit.get("soft_benefits") or []
        if soft:
            doc.add_paragraph().add_run("Soft Benefits:").bold = True
            for s in soft:
                bullet(s)
    else:
        body(None)

    # ── 10. RISK & TYPE ──────────────────────────────────
    heading("10. Risk & Project Classification")
    kv("Risk Level", n["risk_level"])
    kv("Project Type", n["project_type"])

    # ── 11. GATE RESULT ──────────────────────────────────
    heading("11. DEFINE Gate Result")
    gate_badge(gate.get("status", "-"))
    failed      = gate.get("failed_rules") or []
    conditional = gate.get("conditional_rules") or []
    if failed:
        doc.add_paragraph().add_run("Critical Items (Failed):").bold = True
        for f in failed:
            bullet(f.replace("_", " "))
    if conditional:
        doc.add_paragraph().add_run("Conditional Items:").bold = True
        for c in conditional:
            bullet(c.replace("_", " "))

    # ── 12. AGENT COACHING ───────────────────────────────
    coaching_raw = define_state.get("coaching_md") or define_state.get("insight_md") or ""
    coaching_clean = clean_md(coaching_raw)
    if coaching_clean:
        heading("12. Agent Coaching Notes")
        for line in coaching_clean.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("- "):
                bullet(line[2:])
            elif line and line[0].isdigit() and ". " in line:
                bullet(line)
            else:
                doc.add_paragraph(line)

    doc.save(path)
    return path

