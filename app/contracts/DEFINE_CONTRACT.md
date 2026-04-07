0. Tujuan

Kontrak ini memastikan konsistensi fase DEFINE untuk DMAIC Copilot: UI Streamlit, agent, reporting, dan memory harus punya interface stabil dan schema yang jelas.

1. Prinsip wajib

No invented functions: jangan menambah nama fungsi baru tanpa update kontrak.

UI tidak boleh “recompute” summary dari state secara kreatif. Summary adalah artifact yang harus tersedia dari agent atau reporting.

Semua output yang dipakai UI harus deterministik dan berbasis state, bukan global variable.

2. Data Contract: define_state (schema minimum)

define_state adalah dict JSON-serializable, minimal harus punya:

2.1 Field wajib (required)

project_id: str

phase: str = "define"

status: str ∈ { "draft", "final" }

created_at: str (ISO 8601)

updated_at: str (ISO 8601)

inputs: dict

project_name: str

industry: str

process_area: str

pain_theme: str

problem_text: str

goal_text: str | ""

voc_list: list[str]

key_issue: str | ""

outputs: dict

charter: dict

sipoc: dict

ctq: list[dict] (atau list[str] jika belum distandarkan)

business_case: dict

risks_assumptions: dict | list (boleh sederhana v1)

insight_md: str (markdown, ringkas, untuk UI)

summary_md: str (markdown, 1–2 layar, untuk UI & report preview)

2.2 Field opsional (optional tapi direkomendasikan)

baseline_profile: dict (ringkasan baseline jika ada CSV)

version: str (mis. "1.0")

meta: dict (mis. model, prompt_version, token_usage)

3. Agent Contract: app.agents.define_agent

Modul ini wajib menyediakan fungsi berikut.

3.1 run_define_agent(...) -> dict

Signature (kontrak logis):

Input:

project_id: str

user_inputs: dict (sesuai inputs)

baseline_df: pandas.DataFrame | None

user_feedback: dict | None

user_accept: bool (False = draft)

Output dict wajib berisi:

status: str ∈ { "draft" }

define_state: dict (sesuai schema, status="draft")

summary: str (markdown; boleh sama dengan define_state["summary_md"])

critique: list (boleh kosong)

message: str

3.2 finalize_define_agent(...) -> dict

Input:

project_id: str

define_state: dict (draft)

user_feedback: dict | None

Output dict wajib berisi:

status: str = "finalized"

define_state: dict (schema, status="final")

summary: str (markdown)

critique: list

message: str

4. Reporting Contract: app.utils.reporting

Modul ini wajib menyediakan fungsi berikut (nama stabil).

4.1 build_define_summary_md(define_state: dict) -> str

Harus mengembalikan markdown tanpa akses file system.

Tidak boleh mengubah define_state.

Wajib aman untuk state lama: kalau field tidak ada, pakai placeholder.

Catatan: fungsi ini menggantikan pola rapuh seperti build_define_summary_markdown yang dipanggil UI 

1_DEFINE

. UI hanya boleh memanggil build_define_summary_md.

4.2 export_define_to_word(define_state: dict, out_dir: str | None = None) -> str

Output: path file .docx

Wajib include minimal: metadata, problem/goal, VOC→CTQ, SIPOC, business case, summary.

5. Memory Contract: app.utils.memory
5.1 load_latest_define_state(project_id: str) -> dict | None

Mengembalikan define_state terakhir (final kalau ada, kalau tidak draft terakhir).

Return None bila tidak ditemukan.

5.2 save_define_state(project_id: str, define_state: dict) -> str

Menyimpan JSON ke folder project.

Return: path file JSON.

6. Audit Contract (minimum event)

Setiap run/finalize harus menulis event audit (via utils.audit atau mekanisme setara):

event_type: "DEFINE_RUN" atau "DEFINE_FINALIZE"

project_id

timestamp

status

artifacts: list (mis. path JSON, path report jika ada)

7. Error policy

Jika schema tidak lengkap:

Agent wajib tetap return dengan placeholder + message yang menjelaskan field mana yang kosong.

Reporting tidak boleh crash karena missing keys.

8. Backward compatibility

Jika ada state lama:

build_define_summary_md() wajib handle:

summary tidak ada → generate dari field yang tersedia

outputs missing → tampilkan placeholder “Belum tersedia”