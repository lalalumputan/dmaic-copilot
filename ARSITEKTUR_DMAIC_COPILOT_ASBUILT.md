# Arsitektur Sistem Agentic AI DMAIC Copilot (As-Built)

> Dokumen ini mendeskripsikan arsitektur sistem **sebagaimana benar-benar dibangun** di
> dalam kode (*as-built*), bukan rancangan konseptual ideal. Setiap komponen yang
> disebutkan ditautkan ke modul/berkas nyata pada repositori, sehingga seluruh klaim
> arsitektur dapat ditelusuri dan dipertanggungjawabkan secara akademik maupun saat demo.
>
> Istilah metodologi Lean Six Sigma / DMAIC dipertahankan dalam bahasa aslinya
> (Define, Measure, Analyze, Improve, Control, CTQ, CTB, SIPOC, VOC, VOB, Cp/Cpk, I-MR, dst.).

---

## 1. Prinsip Desain yang Dianut Sistem

Sistem dibangun di atas prinsip-prinsip berikut, yang konsisten tercermin pada kode:

1. **Stage-gate DMAIC sebagai kontrol proses, bukan sekadar alur UI.** Tiap fase memiliki
   *Definition of Done* yang ditegakkan secara deterministik melalui fungsi `evaluate_gate`
   pada masing-masing agen (status `PASS`/`FAIL` + daftar rule yang gagal).
2. **Human-in-the-loop pada titik berisiko tinggi.** Konfirmasi pengguna diwajibkan pada
   artefak kritis (mis. charter confirmation di Define, Y-variable confirmation di Measure,
   confirmation sheet Process Owner di Control), bukan pada setiap aksi.
3. **Tool-first, model-second.** Sebagian besar perhitungan bersifat **deterministik tanpa
   LLM** (klasifikasi project, kontrol limit SPC, Cp/Cpk, chart engine, gate evaluation).
   LLM hanya dipakai untuk penalaran/koreksi naratif, dengan `temperature=0.4` agar konsisten.
4. **Traceability.** Setiap keluaran dapat ditelusuri ke input melalui artefak terstruktur
   yang disimpan di `phase_states` dan audit log di `audit_events`.

---

## 2. Peta Arsitektur Aktual (Layer → Berkas)

Sistem diorganisasi ke dalam tiga kelompok besar — **Presentation**, **Agentic
Orchestration**, dan **Foundation** — yang dipecah menjadi layer-layer fungsional berikut.

| Layer Fungsional | Status | Implementasi Nyata (berkas) |
|---|---|---|
| Experience Layer (UI) | Implemented | `app/MAIN.py`, `app/pages/1_DEFINE.py` … `5_CONTROL.py`, `app/utils/ui_helpers.py` |
| Workflow & Stage-Gate | Implemented (terdistribusi) | `evaluate_gate()` di tiap agen, cek artefak `*_final` upstream, `classification_agent.py` |
| Agent Layer | Implemented | `app/agents/define_agent.py … control_agent.py`, `classification_agent.py` |
| Prompt System | Implemented (inline) | `app/utils/llm_engine.py`, `perception_step()` di tiap agen, `app/utils/text_rules.py` |
| Guardrails | Implemented (parsial) | `app/utils/guards.py`, gate rules, sanitasi data sensitif |
| Tooling Layer | Implemented | `app/utils/charts.py`, `app/utils/charter_template.py`, `app/utils/reporting.py`, template Excel/Word |
| Knowledge & Memory | Implemented | `app/utils/memory.py` (SQLite: `phase_states`, `global_episodes`, `project_meta`) |
| Governance | Implemented (parsial) | `app/utils/auth.py` (RBAC), `app/utils/audit.py` (audit trail) |
| Infrastructure | Implemented (parsial) | Runtime Streamlit, SQLite `dmaic_memory.db`, `app/scripts/smoke_test_measure_agent.py` |

Keterangan status:
- **Implemented** — komponen berfungsi penuh dan dipakai pada alur produksi.
- **Parsial** — fungsi inti tersedia namun sebagian fitur ideal disederhanakan/ditunda
  (didokumentasikan sebagai *future work* pada §12).

---

## 3. Perumusan Masalah pada Setiap Agen DMAIC (Kontribusi Inti)

Inti kontribusi sistem: setiap agen fase tidak hanya merepresentasikan tahapan metodologi,
tetapi secara aktif menangani **titik kegagalan spesifik** yang umum terjadi pada eksekusi
proyek Lean Six Sigma. Penanganan ini **ditegakkan secara deterministik** lewat gate rules,
sehingga bukan sekadar saran LLM yang bisa diabaikan.

| Fase | Specific Issue yang Ditangani | Mekanisme Penegakan di Kode (gate rules) |
|---|---|---|
| **Define** | *Problem Structuring Issue* — masalah/tujuan terlalu luas, tidak terukur, tidak selaras bisnis | `A1_problem_not_smart_enough`, `A1_goal_not_smart_enough`, `A1_charter_not_confirmed`, `A2_sipoc_missing_*`, `A3_no_measurable_ctq_defined` |
| **Measure** | *Measurement Validity Issue* — metrik tidak representatif, validasi data lemah | `A1_y_variable_not_confirmed`, `B1_operational_definitions_missing/incomplete`, `B3_dataset_empty` |
| **Analyze** | *Root Cause Misidentification Issue* — akar masalah asumtif tanpa verifikasi | `A1_no_potential_causes_identified`, `A3_no_verification_plan`, `A4_no_verification_results`, `A5_no_rca_summary_table` |
| **Improve** | *Solution Selection Bias Issue* — pemilihan solusi tidak terstruktur/bias | `A0_no_confirmed_root_causes_from_analyze`, `A2_no_potential_solutions`, `A3_no_selected_solution`, `A4_no_implementation_plan`, `A5_no_pilot_plan`, `A6_no_communication_plan` |
| **Control** | *Sustainability Failure Issue* — tidak ada monitoring, masalah kambuh | `A1_no_monitoring_reaction_plan`, `A2_no_control_plan`, `A3_no_handover_protocol` |

Karena gate ini bersifat **deterministik dan memblokir** (fase tidak dapat difinalisasi /
diteruskan bila rule gagal), sistem memberi jaminan operasional bahwa titik kegagalan
spesifik tiap fase benar-benar tertangani — menjawab gap literatur Lean Six Sigma yang
umumnya bersifat konseptual dan tidak terkontrol secara sistem.

---

## 4. Experience Layer (User Interface)

Antarmuka mengadopsi pendekatan **workflow-driven**, bukan chatbot bebas. Setiap fase DMAIC
adalah halaman Streamlit tersendiri sehingga urutan metodologi tidak dapat dilompati.

**Komponen aktual:**
- **DMAIC Workspace** — `app/MAIN.py` (dashboard, pemilihan/pembuatan project, badge role)
  dan lima halaman fase `app/pages/1_DEFINE.py … 5_CONTROL.py`. Tiap halaman menampilkan
  panel **Input wajib** dan tab **Output/Artifacts**.
- **Artifact Viewer & Exporter** — tab Report pada tiap halaman + ekspor dokumen formal
  via `app/utils/reporting.py` (Word/struktur per fase).
- **Review Panel** — hasil evaluasi gate (missing fields, rule gagal) ditampilkan langsung
  pada halaman fase.
- **Stage-Gate Panel** — status `PASS`/`FAIL` + daftar perbaikan ditampilkan sebelum
  finalisasi fase.
- **Tab khusus per fase** — mis. Control memiliki tab Input, Report, Improved Performance,
  Monitoring & Reaction, Control Plan, dan Handover.

**Catatan as-built:** "Trace View" tersedia dalam bentuk penampilan rule/alasan gate;
panel traceability eksplisit yang berdiri sendiri belum diimplementasikan.

---

## 5. Workflow Orchestration & Stage-Gate Layer

Layer ini berfungsi sebagai *process control layer*. Pada implementasi aktual, orkestrasi
bersifat **terdistribusi** (bukan satu *state machine* monolitik):

- **Stage-Gate Manager** — fungsi `evaluate_gate()` pada setiap agen mengevaluasi kriteria
  metodologis dan mengembalikan `{"status": "PASS"/"FAIL", "rules": [...]}`. Status fase
  disimpan sebagai `draft` → `final` pada `phase_states`.
- **Penegakan urutan fase** — setiap agen membaca artefak `*_final` fase sebelumnya sebagai
  *upstream* (mis. `run_improve_agent` mensyaratkan `analyze_final` berisi confirmed root
  causes; gagal → rule `A0_no_confirmed_root_causes_from_analyze`). Dengan demikian fase
  berikutnya tidak dapat berjalan tanpa keluaran fase sebelumnya.
- **Task Router (sederhana)** — pemetaan intent ke agen dilakukan per-halaman fase; selain
  itu `classification_agent.py` menentukan jalur project (**Quick Improvement** vs
  **Standard DMAIC**) secara deterministik berdasarkan 5 kriteria (skor 0–15).
- **Human-in-the-Loop Checkpoints** — konfirmasi wajib pada artefak kritis (charter
  confirmation, Y-variable confirmation, confirmation sheet Process Owner di Control).

**Catatan as-built:** belum ada *Workflow Engine* sentral berbentuk state machine eksplisit
terpisah; disiplin urutan ditegakkan melalui kombinasi UI per-fase + cek artefak upstream +
gate. Status formal yang dipakai adalah `draft`/`final` + `PASS`/`FAIL` (belum
`Draft/In Review/Approved` bertingkat).

---

## 6. Agent Layer

Lapisan kecerdasan berbasis fase. Setiap agen berperan sebagai **reviewer & explainer** yang
menghasilkan rekomendasi terstruktur, bukan pengambil keputusan final.

**Agen yang diimplementasikan:**
- `define_agent.py` — `run_define_agent()`: problem/goal SMART, scope, SIPOC, VOC/VOB→CTQ/CTB,
  benefit estimate; gate di §3.
- `measure_agent.py` — `run_measure_agent()`: output measurement dari CTQ, operational
  definition, MSA, **process performance chart** (deterministik), `perception_step()` +
  retrieval episode.
- `analyze_agent.py` — `run_analyze_agent()`: potential causes (process map/fishbone),
  hipotesis, verifikasi, RCA summary table.
- `improve_agent.py` — `run_improve_agent()`: must-criteria, potential solutions, selected
  solution, implementation/pilot/communication plan.
- `control_agent.py` — `run_control_agent()`: monitoring & reaction plan, control plan,
  **control chart deterministik** (via `charts.py`), handover protocol (7 pertanyaan +
  confirmation sheet Process Owner).
- `classification_agent.py` — klasifikasi jalur project (rule-based, tanpa LLM).

**Pola umum tiap agen:** `perception_step()` (rakit konteks + retrieve episode mirip) →
penalaran LLM/aturan → `evaluate_gate()` → keluaran terstruktur (`outputs`) + simpan state.

**Catatan as-built:** "Reviewer Agent lintas-fase" sebagai komponen mandiri **belum**
diimplementasikan; pengecekan konsistensi antar fase dilakukan parsial melalui cek artefak
upstream pada tiap agen (lihat §12 future work).

---

## 7. Prompt System Layer

- **Core engine** — `app/utils/llm_engine.py`: `call_agentic_llm(system_prompt, user_prompt,
  response_format)` memakai `gpt-4o`, `temperature=0.4`, dan **injeksi instruksi Bahasa
  Indonesia otomatis** (`_LANGUAGE_INSTRUCTION`) dengan daftar istilah metodologi yang tidak
  diterjemahkan.
- **Context Assembly** — `perception_step()` pada tiap agen merakit konteks relevan (artefak
  upstream, episode mirip yang sudah di-*trim*) sebelum prompt dikirim.
- **Output Schema Controller** — mode `response_format="json"` dipakai untuk keluaran
  decision/action terstruktur.
- **Keyword rules** — `app/utils/text_rules.py` membantu menyarankan CTQ dari teks
  (deterministik, non-LLM).

**Catatan as-built:** prompt template berada **inline** di masing-masing agen (belum ada
*Prompt Template Repository* terpusat), dan validasi skema keluaran bersifat ringan
(mengandalkan JSON mode, belum validator skema formal).

---

## 8. Guardrails Layer

- **Methodological constraints** — ditegakkan oleh gate rules (urutan & prasyarat fase).
- **Quality constraints** — validasi SMART, kelengkapan SIPOC, keterukuran CTQ, dsb. di
  dalam `evaluate_gate`.
- **Safety & data protection** — `app/utils/guards.py`: `validate_required_columns`,
  `sanitize_sensitive` (membuang kolom sensitif seperti operator/NIK/email),
  `force_numeric`, `require_text`, `json_safe`.

**Catatan as-built:** guardrail tersebar (belum satu modul lintas-layer terpadu);
"Assumption & Uncertainty Control" dan mekanisme *requires-revision* formal belum
terformalisasi sebagai komponen tersendiri.

---

## 9. Tooling Layer (Non-Generatif)

Sekumpulan alat deterministik yang memperkuat eksekusi tanpa bergantung pada LLM:

- **Chart Engine** — `app/utils/charts.py`: mesin SPC bersama yang dipakai Measure dan
  Control. Mendukung **I-MR, Individuals, Run, Histogram (Cp/Cpk), Pareto, Box**, dengan
  rekomendasi otomatis (`recommend_chart_type`) atau pemilihan manual. Konstanta SPC standar
  (E2=2.66, D4=3.267, D3=0, d2=1.128). Menghasilkan figur untuk UI (`st.pyplot`) sekaligus
  PNG base64 untuk dokumen Word — keduanya dari `build_figure` yang sama (konsistensi).
- **Template Library** — `app/utils/charter_template.py`, template Excel data pasca-improve
  (download → isi → upload → chart otomatis), template Word per fase.
- **Output & Reporting Engine** — `app/utils/reporting.py`: mengompilasi artefak, parameter
  chart, dan dokumentasi handover menjadi laporan formal per fase.
- **Checklist** — gate rules berperan sebagai stage-gate checklist.

**Catatan as-built:** "Simulation/Evaluation Toolkit" baru berupa
`app/scripts/smoke_test_measure_agent.py` (lihat §12).

---

## 10. Knowledge & Memory Layer

Implementasi: `app/utils/memory.py` + `app/utils/audit.py`, berbasis **SQLite tunggal**
(`dmaic_memory.db`) — *cloud-safe* dan backward-compatible.

**Tabel & fungsi aktual:**
- `phase_states (project_id, phase, kind, payload, updated_at)` — **Project Memory**: draft &
  final tiap fase per project. Fungsi: kontinuitas kerja lintas sesi (database biasa, bukan
  "AI memory").
- `global_episodes (phase, industry, pain_theme, payload, created_at)` — **Episodic /
  Organizational Memory**: saat project difinalisasi, state disimpan sebagai *episode*
  (`_save_episode`). Saat project baru dimulai, agen me-*retrieve* hingga 3 episode termirip
  berdasarkan `industry` + `pain_theme` (`retrieve_similar_*_episodes`) dan menyuntikkannya
  ke `perception_step` sebagai konteks few-shot.
- `project_meta` — metadata & jalur project (Quick/Standard).
- `audit_events` (di `audit.py`) — audit trail (lihat §11).

**Pembelaan akademik (architectural readiness).** Pemisahan `phase_states` (working/project
memory) dan `global_episodes` (episodic/organizational memory) sejalan dengan arsitektur
*cognitive agent*. Mekanisme ini mengimplementasikan **few-shot learning berbasis konteks
organisasi**: sistem dirancang mengakumulasi pengetahuan lintas-project, menjawab gap nyata
bahwa pengetahuan DMAIC sering hilang ketika project leader berganti. Pada tahap development,
pool episode masih kosong sehingga dampak empiris belum terukur — ini diposisikan sebagai
**desain yang intentional untuk belajar seiring penggunaan**, bukan kekurangan aksidental.

**Catatan as-built:** "Reference Library terkurasi" dan retrieval RAG penuh belum
diimplementasikan; retrieval saat ini terbatas pada pencarian episode by `industry`+`pain_theme`.

---

## 11. Governance Layer

- **Role-Based Access Control** — `app/utils/auth.py`: tiga role —
  **project_leader** (`can_input`, `can_submit`), **reviewer** (`can_review`), dan
  **champion** (`can_review`, `can_approve`). Login gate via `auth.require_login()` di
  `MAIN.py`, mendukung multi-user per role via `secrets.toml`.
- **Audit Trail & Decision Logging** — `app/utils/audit.py`: tabel `audit_events`
  (timestamp, project_id, phase, status, meta) mencatat peristiwa penting (perpindahan/
  finalisasi fase, hasil gate).
- **Project Boundary / Data Isolation** — seluruh state ter-*scope* per `project_id` pada
  `phase_states`; `global_episodes` sengaja lintas-project (cross-project knowledge).

**Catatan as-built:** mapping role memakai peran organisasi proyek
(project_leader/reviewer/champion), **bukan** tingkat kompetensi Belt
(Yellow/Green/Black/Master Black Belt). Versioning/rollback bersifat parsial (draft vs final,
WIP persistence) dan belum ada rollback eksplisit antar-versi bernomor.

---

## 12. Infrastructure Layer

- **Runtime** — aplikasi Streamlit multi-halaman (`app/MAIN.py` + `app/pages/*`).
- **Storage** — SQLite tunggal `dmaic_memory.db` untuk project state, episode, metadata, dan
  audit; berkas keluaran (laporan Word) dihasilkan on-demand oleh `reporting.py`.
- **Observability** — `audit_events` sebagai jejak aktivitas/keputusan.
- **LLM Runtime Adapter** — `llm_engine.py` (klien OpenAI, `gpt-4o`).

**Catatan as-built / future work:**
- *Evaluation & Testing Harness* baru berupa satu smoke test
  (`app/scripts/smoke_test_measure_agent.py`); pustaka skenario manufaktur & uji regresi
  menyeluruh belum dibangun.
- *Quality Metrics Collection* (kelengkapan artefak otomatis, jumlah revisi, durasi fase)
  belum diimplementasikan sebagai modul tersendiri.

---

## 13. Ringkasan Status & Future Work

**Sudah diimplementasikan penuh dan menjawab kebutuhan inti tesis (III.2):**
- Lima phase agent dengan penanganan *specific issue* per fase yang ditegakkan deterministik.
- Chart engine SPC deterministik bersama (Measure + Control).
- Stage-gate per fase + human-in-the-loop checkpoints.
- Memory layer dua sub-lapis (project + episodic) + audit trail + RBAC.
- Output & Reporting Engine per fase.

**Disederhanakan / direncanakan (tidak menghalangi pembuktian kontribusi inti):**
1. Reviewer Agent lintas-fase sebagai komponen mandiri.
2. Workflow Engine sentral berbentuk state machine eksplisit.
3. RBAC tingkat Belt (saat ini peran proyek).
4. Evaluation/Testing Harness + Quality Metrics Collection.
5. Prompt Template Repository terpusat + validator skema keluaran formal.
6. Reference Library terkurasi + RAG penuh.

Komponen pada daftar kedua diposisikan sebagai **architectural readiness** —
arsitektur telah dirancang untuk menampung perluasan tersebut tanpa membongkar
fitur yang sudah berjalan dan teruji.
