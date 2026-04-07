from __future__ import annotations
import os
import json
from typing import Optional, Dict, Any, List
from openai import OpenAI

import os
print("DEBUG OPENAI KEY: ", os.getenv("OPENAI_API_KEY"))

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "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"))


def complete(prompt: str, model: str = "gpt-4o-mini") -> str:
    """
    Fungsi generik yang dipakai oleh agent (misalnya Define Agent)
    untuk memanggil LLM dengan satu prompt dan mendapatkan 1 output string.

    Saat ini membungkus _call_llm agar kompatibel dengan kode lama.
    Param 'model' disediakan untuk fleksibilitas ke depan.
    """
    # kalau mau benar-benar gunakan nama model dinamis, bisa override di sini.
    return _call_llm(prompt, role="Kamu adalah asisten AI untuk Continuous Improvement.")

# ==============
# DEFINE
# ==============
def get_llm_response(system_prompt: str, user_prompt: str, output_schema: str) -> str:
    """
    Fungsi utama untuk memanggil LLM dan meminta respons dalam format JSON terstruktur 
    berdasarkan skema yang disediakan (Structured Output / Pydantic).
    
    Args:
        system_prompt (str): Instruksi peran dan aturan.
        user_prompt (str): Data input kontekstual dan permintaan user.
        output_schema (str): JSON Schema (dari Pydantic) yang harus diikuti oleh LLM.
        
    Returns:
        str: Respons LLM dalam format JSON string yang mematuhi skema.
    """
    
    # ----------------------------------------------------------------------
    # Logic Simulasi (HARUS diganti dengan API Call nyata)
    # ----------------------------------------------------------------------
    
    print(f"--- LLM Engine Called ---")
    print(f"System Role: {system_prompt[:50]}...")
    print(f"User Context Size: {len(user_prompt)} characters")
    print(f"Required Schema: {output_schema}")
    
    # Placeholder: Simulasi output Define Charter yang valid
    # LLM harus memproses user_prompt (data analytics) dan menghasilkan JSON ini
    
    simulated_output = {
        "charter": {
            "project_title": "Reduksi Defect Rate Order Processing sebesar 15%",
            "project_goal": "Mengurangi persentase order dengan 'Defect' (kesalahan input/pengiriman) dari 3.5% menjadi 2.975% dalam 3 bulan, menghemat biaya re-work Rp 50 Juta.",
            "problem_statement": "Saat ini, rata-rata 3.5% dari Job Order bulanan mengalami Defect, melebihi target 2.0%. Akar masalahnya belum diketahui.",
            "scope_in": "Proses dimulai dari 'Order Entry' hingga 'Final Quality Check' di Gudang. Fokus pada data 6 bulan terakhir.",
            "scope_out": "Proses marketing, penjualan, dan pasca-pengiriman tidak termasuk. Perubahan pada sistem ERP inti tidak termasuk."
        },
        "sipoc": {
            "Suppliers": {"S1": "Sales Team", "S2": "ERP System"},
            "Inputs": {"I1": "Order Form", "I2": "Inventory Data"},
            "Process": {"P1": "Input Data", "P2": "Verifikasi Stok", "P3": "Pencetakan Label"},
            "Outputs": {"O1": "Completed Job Order", "O2": "Shipping Label"},
            "Customers": {"C1": "Warehouse Team", "C2": "Final Customer"}
        },
        "critical_to_quality": {
            "CTQ-1": "Defect Rate Order (%)",
            "CTQ-2": "Cycle Time Input (Menit)",
            "CTQ-3": "Order Accuracy (%)"
        },
        "llm_insight": "Wawasan AI: Berdasarkan data analitik awal (Perception), Defect Rate awal sebesar 3.5% valid untuk proyek DMAIC. Prioritas harus diberikan pada 'Order Entry' (S1 & P1) karena ini adalah sumber kesalahan utama dalam VOC. Target 15% pengurangan Defect Rate terukur dan realistis. Lanjutkan ke fase Measure dengan fokus pada CTQ-1."
    }
    
    # Mengembalikan output simulasi dalam bentuk string JSON
    return json.dumps(simulated_output)

def define_insight(voc_text: str, result: Dict) -> str:
    ctq = result.get("CTQ", [])
    charter = result.get("Charter", {})
    sipoc = result.get("SIPOC", {})

    prompt = f"""
Kamu adalah praktisi Lean Six Sigma Black Belt di manufaktur.

Berikut ini Voice of Customer (VOC):
\"\"\"{voc_text}\"\"\"

Hasil ekstraksi awal sistem:
- CTQ: {ctq}
- Charter: {charter}
- SIPOC: {sipoc}

Tugas kamu:
1. Rapikan problem statement dalam 1–2 kalimat yang terukur dan time-bound.
2. Evaluasi apakah goal sudah SMART, jika belum perbaiki.
3. Tegaskan scope (in-scope / out-of-scope) dan resiko utama.
4. Sarankan 3–5 pertanyaan klarifikasi yang perlu ditanyakan ke sponsor sebelum Define dinyatakan “clear”.

Jawab dengan bahasa Indonesia, gaya praktisi CI, to the point, jangan akademis.
"""
    return _call_llm(prompt, role="Kamu adalah konsultan Continuous Improvement.")


# ==============
# MEASURE
# ==============

def measure_insight(result: Dict) -> str:
    summary = result.get("summary", {})
    by_defect = result.get("by_defect", [])

    prompt = f"""
Kamu adalah Lean Six Sigma Black Belt di manufaktur.

Ringkasan hasil fase Measure:
- Summary: {summary}
- By Defect: {by_defect}

Tugas kamu:
1. Jelaskan kondisi baseline proses dengan bahasa yang mudah dipahami manajemen (defect rate, variasi, OEE jika ada).
2. Soroti 2–3 risiko pengambilan keputusan jika data/measure tidak valid (MSA, stabilitas, dsb).
3. Berikan 3 rekomendasi konkret untuk memperkuat fase Measure (contoh: tambah stratifikasi, cek MSA ulang, tambah sampling).
4. Sebutkan metrik utama yang wajib dipakai di fase Analyze dan Improve nanti.

Jawab dalam bahasa Indonesia, singkat, praktis, dan fokus pada keputusan bisnis/operasional.
"""
    return _call_llm(prompt)


# ==============
# ANALYZE
# ==============

def analyze_insight(result: Dict) -> str:
    pareto = result.get("pareto", [])
    rca = result.get("rca", [])

    prompt = f"""
Kamu adalah analis CI yang berpengalaman di pabrik manufaktur.

Data hasil Analyze:
- Pareto (defect/top loss): {pareto}
- Ringkasan RCA: {rca}

Tugas kamu:
1. Jelaskan 1–2 pola utama dari Pareto (siapa/mesin/shift/material yang paling mencolok jika ada).
2. Jelaskan perbedaan antara "gejala" dan "root cause" dari data di atas.
3. Berikan 3 hipotesis penyebab akar masalah yang layak diuji di fase Improve.
4. Tegaskan data tambahan apa (jika ada) yang perlu dikumpulkan sebelum eksekusi perbaikan.

Jawab dengan gaya coaching CI, bukan sekedar laporan statistik.
"""
    return _call_llm(prompt)


# ==============
# CONTROL
# ==============

def control_insight(result: Dict) -> str:
    charts = result.get("charts", {})
    rules = result.get("rules", {})
    signals = result.get("signals", [])

    prompt = f"""
Kamu adalah engineer kualitas yang fokus di SPC dan kontrol proses.

Ringkasan hasil Control:
- Charts: {charts}
- Rules/aturan: {rules}
- Sinyal out-of-control / alarm: {signals}

Tugas kamu:
1. Jelaskan apakah proses cenderung stabil atau tidak (secara kualitatif).
2. Jika ada sinyal out-of-control, sebutkan 2–3 kemungkinan penyebab di level shopfloor.
3. Sarankan reaksi standar (reaction plan) ketika sinyal ini muncul.
4. Sarankan 2–3 leading indicator yang sebaiknya dimonitor untuk menjaga sustainment.

Jawab dengan bahasa Indonesia, teknis praktis, bukan akademis.
"""
    return _call_llm(prompt)


# ==============
# IMPROVE (KAIZEN) — SUDAH ADA
# ==============

def _build_kaizen_prompt(problem: str, ideas: List[str], context: Dict) -> str:
    ctq = context.get("ctq", [])
    defect_info = context.get("defect_info", "")
    top_defect = context.get("top_defect", "")

    ctq_text = ", ".join(ctq) if ctq else "(belum eksplisit disebut)"
    ideas_text = "\n".join([f"- {i}" for i in ideas]) if ideas else "- (belum ada ide)"

    prompt = f"""
Kamu adalah seorang Lean Six Sigma Black Belt di manufaktur.

Konteks proyek:
- CTQ utama: {ctq_text}
- Informasi defect/kinerja: {defect_info}
- Defect dominan (jika ada): {top_defect}

Masalah yang akan diperbaiki:
\"\"\"{problem}\"\"\"

Daftar ide perbaikan yang sudah diusulkan:
{ideas_text}

Tugas kamu:
1. Ringkas masalah dalam 1–2 kalimat.
2. Kelompokkan ide menjadi 2 kategori: Quick Win vs Project.
3. Berikan 3–5 rekomendasi tambahan (bukan general, tapi spesifik ke konteks).
4. Jelaskan metrik apa yang sebaiknya dipantau (contoh: defect rate, OEE, lead time, dsb).

Jawab dengan bahasa Indonesia, gaya praktisi CI, singkat dan langsung ke poin.
"""
    return prompt.strip()


def kaizen_insight(problem: str, ideas: List[str], context: Dict) -> str:
    if not problem.strip():
        return "Belum ada problem statement yang jelas, jadi insight Kaizen belum bisa dibuat."

    prompt = _build_kaizen_prompt(problem, ideas, context)
    return _call_llm(prompt, role="Kamu adalah konsultan Continuous Improvement (Lean Six Sigma Black Belt).")


# ==============
# CORE LLM CALL
# ==============

def _call_llm(prompt: str, role: str = "Kamu adalah asisten AI untuk Continuous Improvement."):
    try:
        messages = [
            {"role": "system", "content": role},
            {"role": "user", "content": prompt},
        ]
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(Mode offline / error LLM) Insight perlu dibuat manual. Detail: {e}"

