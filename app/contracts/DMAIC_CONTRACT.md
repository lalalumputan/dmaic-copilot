# DMAIC Copilot — Interface Contract (5 Fase)

> Kontrak ini menjaga **konsistensi interface** lintas fase DMAIC (Define, Measure,
> Analyze, Improve, Control) antara UI Streamlit, agent, reporting, dan memory.
> Tujuannya: setiap layer punya nama fungsi & schema yang stabil sehingga perubahan
> di satu sisi tidak diam-diam merusak sisi lain.
>
> Istilah metodologi Lean Six Sigma / DMAIC dipertahankan dalam bahasa Inggris
> (CTQ, CTB, VOC, VOB, SIPOC, baseline, Cp, Cpk, I-MR, gate, root cause, charter, dst.).
>
> Dokumen ini menggantikan `DEFINE_CONTRACT.md` lama yang hanya mencakup fase Define
> (dibuat saat baru Define yang dibangun).

---

## 0. Prinsip Wajib

1. **No invented functions** — jangan menambah nama fungsi publik baru tanpa memperbarui
   kontrak ini.
2. **UI tidak boleh "recompute" artefak secara kreatif.** Summary/insight adalah artefak
   yang harus tersedia dari agent atau reporting, bukan dirakit ulang ad-hoc di UI.
3. **Deterministik & berbasis state.** Semua output yang dipakai UI bersumber dari `*_state`
   yang tersimpan, bukan dari global variable.
4. **Backward compatible.** Fungsi reporting/summary wajib aman untuk state lama: bila field
   tidak ada → pakai placeholder, jangan crash.
5. **Tool-first, model-second.** Gate evaluation, klasifikasi jalur, dan chart engine bersifat
   deterministik tanpa LLM. LLM hanya untuk penalaran/koreksi naratif & coaching.

---

## 1. Pola Interface Seragam (berlaku untuk SEMUA fase)

Ganti `<phase>` ∈ { `define`, `measure`, `analyze`, `improve`, `control` }.

### 1.1 Agent — `app.agents.<phase>_agent`

```text
run_<phase>_agent(
    project_id: str,
    user_inputs: dict,
    ... ,                       # argumen spesifik fase (lihat §3)
    project_path: str | None = None,   # "standard" | "quick"; auto-load dari project_meta bila None
) -> dict
```
Output dict wajib berisi:
- `status: "draft"`
- `<phase>_state: dict`  (schema §2, `status="draft"`)
- `summary: str`         (markdown; boleh sama dengan `<phase>_state["summary_md"]`)
- `message: str`
- *(opsional)* `critique: list`

```text
finalize_<phase>_agent(
    project_id: str,
    <phase>_state: dict,        # draft yang sudah di-accept
    user_feedback: dict | None = None,
) -> dict
```
- **MUST NOT** memanggil LLM / **tidak** meregenerasi konten. Hanya menyetel
  `status="final"`, metadata, dan menyimpan state + episode.

### 1.2 Gate — deterministik, di dalam agent

```text
_<phase>_gate_evaluate(outputs: dict, user_inputs: dict, enforce_b_rules: bool = True) -> dict
# -> {"status": "PASSED" | "CONDITIONAL" | "FAILED", "failed_rules": [...], "evidence": {...}}
```
- **A-rules** selalu ditegakkan (kritis, memblokir).
- **B-rules** hanya ditegakkan bila `enforce_b_rules=True` (jalur Standard DMAIC).
  Pada jalur Quick Improvement B-rules dilewati (lihat §4).

### 1.3 Reporting — `app.utils.reporting`

```text
export_<phase>_to_word(<phase>_state: dict, out_dir: str | None = None) -> str   # path .docx
```
- Tidak mengakses file system untuk membaca state; tidak memutasi `<phase>_state`.
- `build_define_summary_md(define_state)` dan `build_measure_summary_md(measure_state)`
  tersedia untuk preview UI. Fase lain memakai `summary_md`/`insight_md` yang sudah ada
  di dalam `<phase>_state`.

### 1.4 Memory — `app.utils.memory`

Penyimpanan **generik berbasis (project_id, phase, kind)**, bukan fungsi per-fase:
```text
_upsert_state(project_id, phase, kind, state)     # kind ∈ {"draft","final"}
_load_state(project_id, phase, kind) -> dict | None
_delete_state(project_id, phase, kind)
get_phase_approval_status(project_id, phase) -> dict
load_project_meta(project_id) -> dict             # berisi "path": "standard"|"quick"
```
Episodic memory (cross-project few-shot):
```text
save_<phase>_episode(project_id, state, feedback=None)
retrieve_similar_<phase>_episodes(industry, pain_theme, k=3) -> list[dict]
```

### 1.5 Audit — `app.utils.audit`

Setiap run/finalize menulis event: `log_phase_event(project_id, phase, event, meta=...)`
dengan event minimal `started`, `draft_generated`, `finalized`.

---

## 2. Data Contract: `<phase>_state`

Dict JSON-serializable. Field umum (wajib di semua fase):

| Field | Tipe | Keterangan |
|---|---|---|
| `project_id` | str | |
| `phase` | str | `"define"`…`"control"` |
| `status` | str | `"draft"` \| `"final"` |
| `created_at` / `updated_at` | str | ISO 8601 |
| `inputs` | dict | salinan `user_inputs` |
| `outputs` | dict | artefak terstruktur fase (lihat §3) |
| `gate_result` | dict | hasil `_<phase>_gate_evaluate` |
| `summary_md` | str | markdown ringkas untuk UI & report preview |
| `insight_md` / `coaching_md` | str | naratif coaching (LLM atau fallback deterministik) |

Field opsional yang direkomendasikan: `version`, `meta` (model, prompt_version, token_usage),
`baseline_profile` (Define/Measure bila ada CSV).

---

## 3. Spesifik per Fase

| Fase | Argumen `run_` tambahan | Output utama (`outputs`) | Gate A-rules kritis (memblokir) |
|---|---|---|---|
| **Define** | `baseline_df=None` | `charter`, `sipoc`, `ctq[]`, `business_case`, `risks_assumptions` | `A1_problem_not_smart_enough`, `A1_goal_not_smart_enough`, `A1_charter_not_confirmed`, `A2_sipoc_missing_*`, `A3_no_measurable_ctq_defined` |
| **Measure** | `baseline_df=None` | `y_variable`, `operational_definitions`, `measurement_plan`, `baseline_descriptives`, `chart_params` | `A1_y_variable_not_confirmed` (A-rule). B-rules: `B1_operational_definitions_missing/incomplete`, `B2_measurement_plan_missing`, `B3_dataset_empty`, `B3_baseline_descriptives_missing` |
| **Analyze** | upstream `measure_final` | `potential_causes`, `hypotheses`, `verification` (plan+results), `rca_summary_table` | `A1_no_potential_causes_identified`, `A3_no_verification_plan`, `A4_no_verification_results`, `A5_no_rca_summary_table` |
| **Improve** | upstream `analyze_final` (confirmed root causes) | `potential_solutions`, `selected_solution`, `implementation_plan`, `pilot_plan`, `communication_plan` | `A0_no_confirmed_root_causes_from_analyze`, `A2_no_potential_solutions`, `A3_no_selected_solution`, `A4_no_implementation_plan`, `A5_no_pilot_plan`, `A6_no_communication_plan` |
| **Control** | upstream `improve_final` | `monitoring_reaction_plan`, `control_plan`, `control_chart_params`, `handover_protocol` | `A1_no_monitoring_reaction_plan`, `A2_no_control_plan`, `A3_no_handover_protocol` |

Penegakan urutan fase: tiap agent membaca artefak `*_final` fase sebelumnya sebagai
prasyarat upstream — fase berikutnya tidak bisa berjalan tanpa keluaran final fase sebelumnya.

---

## 4. Path System (Quick vs Standard)

`app.agents.classification_agent`:
```text
run_classification_agent(inputs) -> dict          # skor 0–15 atas 5 kriteria → jalur
get_gate_mode(project_path) -> {"enforce_b_rules": bool, "prompt_depth": ..., "path_label": ...}
```
- **Standard DMAIC** → `enforce_b_rules=True` (A-rules + B-rules ditegakkan).
- **Quick Improvement** → `enforce_b_rules=False` (hanya A-rules; B-rule deliverable
  seperti formal verification plan, must-criteria matrix, formal control plan/handover
  **tidak diwajibkan**).
- `project_path` dialirkan ke `run_<phase>_agent` dan ke builder coaching agar
  saran menyesuaikan kedalaman jalur.

---

## 5. Coaching Contract

Setiap fase punya builder coaching yang dipanggil saat gate `FAILED`/`CONDITIONAL`,
plus fallback deterministik:
- Define: `coaching_step(user_inputs, outputs, gate_result, project_path)` +
  fallback `build_define_coaching_summary_md(...)`.
- Analyze/Improve/Control: `_build_<phase>_coaching_llm(..., project_path="standard")`.
- **Bahasa:** seluruh coaching ditulis **Bahasa Indonesia**; istilah metodologi tetap Inggris.
- Hasil disimpan ke `<phase>_state["coaching_md"]` / `["insight_md"]` dan dirender di
  tab Input (post-form) serta tab Report.

---

## 6. Error Policy & Backward Compatibility

- Jika schema tidak lengkap: agent tetap return dengan placeholder + `message` yang
  menjelaskan field mana yang kosong (tidak boleh raise ke UI).
- Reporting/summary **tidak boleh crash** karena missing keys → pakai placeholder
  "Belum tersedia".
- State lama tanpa `summary_md` → builder summary merakit dari field yang tersedia.
