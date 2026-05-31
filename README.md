# DMAIC Copilot

**Agentic AI companion for Lean Six Sigma DMAIC project execution.**

Capstone / Tesis — Magister Inovasi Regional (Innovation Management), Universitas Padjadjaran (2025).

Aplikasi Streamlit yang memandu eksekusi proyek **Lean Six Sigma** lima fase
(Define → Measure → Analyze → Improve → Control) dengan **stage-gate deterministik**,
agent per fase, dan **dua jalur eksekusi**: *Standard DMAIC* (rigor penuh) dan
*Quick Improvement* (jalur quick-win yang disederhanakan).

> Antarmuka & coaching dalam **Bahasa Indonesia**; istilah metodologi
> (CTQ, CTB, VOC, VOB, SIPOC, Cp/Cpk, I-MR, baseline, gate, root cause, charter) tetap Inggris.

---

## Setup (Local)

```bash
pip install -r requirements.txt

# Buat file rahasia (di-gitignore) lalu isi API key
# .streamlit/secrets.toml:
#   OPENAI_API_KEY = "sk-..."

streamlit run app/MAIN.py
```

## Deploy (Streamlit Community Cloud)

1. Push repo ini ke GitHub
2. Buka [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Pilih repo + `app/MAIN.py` sebagai entry point
4. Di **Settings → Secrets**, tempel:
   ```
   OPENAI_API_KEY = "sk-..."
   ```
5. Deploy

---

## Dua Jalur Eksekusi (Dual-Path)

`app/agents/classification_agent.py` mengklasifikasikan proyek secara **deterministik**
(skor 0–15 atas 5 kriteria) ke salah satu jalur:

| Jalur | Untuk | Penegakan Gate |
|---|---|---|
| **Standard DMAIC** | Masalah kompleks, lintas-fungsi, butuh analisis & verifikasi formal | A-rules **dan** B-rules ditegakkan penuh |
| **Quick Improvement** | Quick-win, ruang lingkup sempit, akar masalah relatif jelas | Hanya **A-rules** (deliverable B-rule formal di-skip) |

Panduan visual kedua jalur tersedia di tab **"📖 Panduan DMAIC & Quick Improvement"**
pada halaman utama (`MAIN.py`).

---

## Architecture

```
app/
  MAIN.py                   ← Dashboard, login/RBAC, pemilihan/pembuatan project, tab Panduan
  pages/
    1_DEFINE.py             ← Define phase UI
    2_MEASURE.py            ← Measure phase UI
    3_ANALYZE.py            ← Analyze phase UI
    4_IMPROVE.py            ← Improve phase UI
    5_CONTROL.py            ← Control phase UI
  agents/
    define_agent.py         ← Perception → Decision → Action → Gate + coaching
    measure_agent.py        ← idem (+ SPC chart engine, MSA)
    analyze_agent.py        ← idem (potential causes, verification, RCA)
    improve_agent.py        ← idem (solutions, selection, pilot plan)
    control_agent.py        ← idem (control plan, control chart, handover)
    classification_agent.py ← Klasifikasi jalur Quick/Standard (rule-based, non-LLM)
  utils/
    memory.py               ← SQLite persistence (phase_states + global_episodes + project_meta)
    audit.py                ← SQLite audit trail (audit_events)
    auth.py                 ← Role-Based Access Control (project_leader/reviewer/champion)
    llm_engine.py           ← OpenAI wrapper (gpt-4o, injeksi instruksi Bahasa Indonesia)
    charts.py               ← SPC chart engine bersama (I-MR, Individuals, Histogram/Cp-Cpk, Pareto, dst.)
    reporting.py            ← Markdown + Word export per fase (numbering dinamis, hide-empty)
    charter_template.py     ← Template charter/Excel/Word
    guards.py               ← Sanitasi data sensitif & validasi kolom
    text_rules.py           ← Saran CTQ berbasis keyword (non-LLM)
  contracts/
    DMAIC_CONTRACT.md       ← Interface contract 5 fase (agent/reporting/memory/gate)
```

Dokumen arsitektur lengkap (*as-built*): [`ARSITEKTUR_DMAIC_COPILOT_ASBUILT.md`](ARSITEKTUR_DMAIC_COPILOT_ASBUILT.md).

---

## DMAIC Phase Status

| Phase   | Agent | UI Page | Gate | Coaching (path-aware) |
|---------|:-----:|:-------:|:----:|:---------------------:|
| Define  | ✅ | ✅ | ✅ | ✅ |
| Measure | ✅ | ✅ | ✅ | ✅ |
| Analyze | ✅ | ✅ | ✅ | ✅ |
| Improve | ✅ | ✅ | ✅ | ✅ |
| Control | ✅ | ✅ | ✅ | ✅ |
