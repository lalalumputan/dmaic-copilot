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

client = OpenAI(api_key=_get_api_key())

# ==========================================
# 1. CORE ENGINE (AGENTIC FOUNDATION)
# ==========================================

def call_agentic_llm(system_prompt: str, user_prompt: str, response_format="text") -> str:
    """
    Fungsi dasar untuk semua Agent (Define, Measure, dst).
    Mendukung format JSON untuk 'Decision' dan 'Action'.
    """
    try:
        fmt = {"type": "json_object"} if response_format == "json" else {"type": "text"}
        
        response = client.chat.completions.create(
            model="gpt-4o", # Menggunakan model cerdas untuk reasoning
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format=fmt,
            temperature=0.4 # Rendah agar konsisten dalam analisis data
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"

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
