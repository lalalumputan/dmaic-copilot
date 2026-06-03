from __future__ import annotations
import os
import json
from typing import Optional, Dict, Any, List
from openai import OpenAI

# MENGGUNAKAN API KEY ANDA YANG SUDAH ADA
# Saya membiarkan fallback ke key Anda agar tidak perlu setting ulang environment
import streamlit as st

def _get_api_key() -> str:
    # 1. Streamlit secrets (production / cloud deployment)
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    # 2. Environment variable (local dev with .env or shell export)
    key = os.getenv("OPENAI_API_KEY", "")
    if key:
        return key
    raise RuntimeError(
        "OPENAI_API_KEY not found. "
        "Set it in .streamlit/secrets.toml (cloud) or as env variable (local)."
    )

_client: Optional[OpenAI] = None

def _get_client() -> OpenAI:
    """Lazy: client dibuat saat pertama dipakai, BUKAN saat import.
    Mencegah seluruh modul gagal di-import bila API key belum ada / saat hot-reload."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=_get_api_key())
    return _client

# ==========================================
# 1. CORE ENGINE (AGENTIC FOUNDATION)
# ==========================================

_LANGUAGE_INSTRUCTION = """

---
INSTRUKSI BAHASA (WAJIB): Seluruh respons harus dalam Bahasa Indonesia.
Pertahankan istilah teknis berikut dalam bahasa aslinya (JANGAN diterjemahkan):
nama fase DMAIC (Define, Measure, Analyze, Improve, Control), CTQ, SIPOC,
VOC, VOB, 5-Why, Fishbone, Ishikawa, root cause, X variable, Y variable,
baseline, pilot, stakeholder, process map, control chart, gauge R&R,
Lean Six Sigma, Black Belt, Green Belt, Master Black Belt, A3, Kaizen,
dan istilah metodologi atau statistik lainnya yang tidak memiliki padanan Bahasa Indonesia yang baku.
---"""


def call_agentic_llm(system_prompt: str, user_prompt: str, response_format="text") -> str:
    """
    Fungsi dasar untuk semua Agent (Define, Measure, dst).
    Mendukung format JSON untuk 'Decision' dan 'Action'.
    Semua respons di-inject instruksi Bahasa Indonesia secara otomatis.
    """
    try:
        fmt = {"type": "json_object"} if response_format == "json" else {"type": "text"}
        full_system = system_prompt + _LANGUAGE_INSTRUCTION

        response = _get_client().chat.completions.create(
            model="gpt-4o", # Menggunakan model cerdas untuk reasoning
            messages=[
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_prompt}
            ],
            response_format=fmt,
            temperature=0.4 # Rendah agar konsisten dalam analisis data
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"

def evaluate_chart_image(image_bytes: bytes, mime: str, context_text: str = "") -> Dict[str, Any]:
    """
    VISION: Baca gambar chart performa (gpt-4o vision) untuk fase MEASURE.

    Fokus (lean, quick path): (1) KONFIRMASI apakah chart mendukung problem statement,
    (2) figur statistik estimasi visual: average, quartile (Q1/median/Q3), posisi vs target.
    Tidak mengarang presisi — semua angka adalah estimasi visual.

    Mengembalikan dict terstruktur (Bahasa Indonesia; istilah teknis tetap Inggris).
    Pada error → {"error": "..."}.
    """
    try:
        import base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime or 'image/png'};base64,{b64}"

        system = (
            "Kamu adalah Lean Six Sigma Master Black Belt fase MEASURE. Kamu menilai gambar "
            "chart performa proses yang diupload untuk MENGKONFIRMASI apakah problem statement "
            "Define terdukung data visual, dan memberi estimasi figur statistik."
        )
        instruction = (
            "Analisis chart pada gambar. JANGAN mengarang presisi — semua angka estimasi visual. "
            "Kembalikan JSON object PERSIS field berikut (teks Bahasa Indonesia):\n"
            "{\n"
            '  "problem_confirmed": "ya | tidak | belum jelas",\n'
            '  "confirmation_reason": "alasan singkat yang mengaitkan isi chart dengan problem statement",\n'
            '  "metric_name": "metrik/Y yang ditampilkan (tebakan terbaik dari sumbu/legenda)",\n'
            '  "unit": "satuan bila terlihat",\n'
            '  "average": "estimasi rata-rata, mis. ~12%",\n'
            '  "quartiles": {"q1": "estimasi", "median": "estimasi", "q3": "estimasi"},\n'
            '  "vs_target": "posisi average vs target bila garis target terlihat (mis. belum, ~5% di atas target 3%); jika tak ada target tulis: tidak terlihat",\n'
            '  "trend": "membaik | memburuk | stabil | fluktuatif",\n'
            '  "narrative": "1-3 kalimat interpretasi ringkas",\n'
            '  "readability_note": "estimasi visual; perlu data aktual untuk presisi"\n'
            "}\n"
            'Jika gambar tidak jelas/bukan chart: problem_confirmed="belum jelas" dan jelaskan di confirmation_reason.'
        )
        user_text = instruction
        if context_text and context_text.strip():
            user_text += f"\n\nKonteks proyek (dari Define):\n{context_text.strip()}"

        response = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system + _LANGUAGE_INSTRUCTION},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        out = json.loads(response.choices[0].message.content)
        return out if isinstance(out, dict) else {"error": "Format respons tidak valid."}
    except Exception as e:
        return {"error": f"Gagal mengevaluasi gambar: {str(e)}"}


def evaluate_image_md(image_bytes: bytes, mime: str, prompt_text: str, system: str = "") -> str:
    """
    VISION generik (gpt-4o): baca gambar + instruksi → kembalikan markdown.
    Dipakai mis. fase Control untuk menilai chart hasil implementasi yang diupload.
    """
    try:
        import base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime or 'image/png'};base64,{b64}"
        sys = system or "Kamu adalah Lean Six Sigma Master Black Belt."
        response = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": sys + _LANGUAGE_INSTRUCTION},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Gagal mengevaluasi gambar: {str(e)}"


# ==========================================
# 2. PHASE-SPECIFIC ENGINES (SCALABLE)
# ==========================================

def get_define_response(system_prompt: str, user_context: str) -> Dict[str, Any]:
    """
    Menjalankan Logic Define: Perception -> Decision -> Action.
    Mengembalikan JSON terstruktur sesuai DEFINE_CONTRACT.
    """
    raw_res = call_agentic_llm(system_prompt, user_context, response_format="json")
    try:
        return json.loads(raw_res)
    except:
        return {"error": "Failed to parse JSON", "raw": raw_res}

# Fungsi ini disiapkan untuk masa depan (Measure, Analyze, dst)
def get_phase_insight(phase_name: str, context_data: Dict) -> str:
    """Generic insight generator untuk fase manapun."""
    prompt = f"Sebagai CI Expert, berikan insight untuk fase {phase_name} berdasarkan data: {json.dumps(context_data)}"
    return call_agentic_llm("Kamu adalah Lean Six Sigma Master Black Belt.", prompt)

# ==========================================
# 3. ADAPTATION & LEARNING LOOP
# ==========================================

def generate_adaptation_critique(current_output: Dict, user_feedback: str) -> str:
    """
    Bagian dari ADAPTATION: Agent mengkritik diri sendiri 
    berdasarkan feedback user untuk perbaikan di iterasi berikutnya.
    """
    prompt = f"""
    DRAFT SAAT INI: {json.dumps(current_output)}
    FEEDBACK USER: {user_feedback}
    
    Analisis kesenjangan (Gap Analysis) antara draft dan keinginan user. 
    Berikan instruksi spesifik untuk perbaikan draft berikutnya.
    """
    return call_agentic_llm("Kamu adalah Quality Auditor yang kritis.", prompt)

# ==========================================
# 4. COMPATIBILITY LAYER (KODE LAMA)
# ==========================================
# Tetap mempertahankan fungsi lama agar tidak terjadi 'broken references' di file lain

def complete(prompt: str, role: str = "Asisten CI") -> str:
    return call_agentic_llm(role, prompt)

def get_llm_response(system_prompt: str, user_prompt: str, output_schema: str = "") -> str:
    # Fungsi ini sekarang benar-benar memanggil LLM, bukan simulasi lagi
    return call_agentic_llm(system_prompt, f"{user_prompt}\n\nSchema: {output_schema}", response_format="json")
def extract_json_from_llm(raw_response: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(raw_response)
    except:
        return None 
    
#STUB KAIZEN INSIGHT FUNCTION
def kaizen_insight(*args, **kwargs):
    """
    Backward-compatible stub.
    Kept to satisfy legacy imports from app.py.
    DO NOT use for FINAL generation.
    """
    return ""
