# DMAIC Copilot

**Agentic AI companion for Lean Six Sigma DMAIC project execution.**

Capstone project — Magister Inovasi Regional, Universitas Padjadjaran (2025).

---

## Setup (Local)

```bash
pip install -r requirements.txt

# Copy and fill in your API key
cp mlit/secrets.toml.template .streamlit/secrets.toml
# Edit secrets.toml: OPENAI_API_KEY = "sk-..."

streamlit run app.py
```

## Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Select repo + `app.py` as entry point
4. In **Settings → Secrets**, paste:
   ```
   OPENAI_API_KEY = "sk-..."
   ```
5. Deploy

## Architecture

```
app.py                    ← Home + Project Selector
app/
  pages/
    1_DEFINE.py           ← Define phase UI
    2_MEASURE.py          ← Measure phase UI
  agents/
    define_agent.py       ← Perception → Decision → Action → Gate
    measure_agent.py      ← Perception → Decision → Action → Gate
  utils/
    memory.py             ← SQLite persistence (phase states + episodes)
    audit.py              ← SQLite audit trail
    llm_engine.py         ← OpenAI wrapper
    reporting.py          ← Markdown + Word export
    data_processing.py    ← Baseline analytics
```

## DMAIC Phase Status

| Phase   | Agent | UI Page | Gate |
|---------|-------|---------|------|
| Define  | ✅    | ✅      | ✅   |
| Measure | ✅    | ✅      | ✅   |
| Analyze | 🔄    | —       | —    |
| Improve | 🔄    | —       | —    |
| Control | 🔄    | —       | —    |
