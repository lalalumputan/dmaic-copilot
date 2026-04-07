# app/utils/text_rules.py
from typing import List, Dict
import re

# kamus kata kunci CTQ per tema (contoh awal, bisa kamu tambah)
CTQ_KEYWORDS: Dict[str, List[str]] = {
    "kualitas_warna": ["warna", "Δe", "delta e", "color deviation", "kecerahan"],
    "viskositas": ["viskositas", "kental", "encernya", "thixotropy"],
    "dimensi": ["dimensi", "ukuran", "diameter", "ketebalan", "toleransi"],
    "cacat_permukaan": ["scratch", "dent", "gelembung", "bubble", "bopeng"],
    "yield_reject": ["reject", "yield", "first pass", "rework", "scrap"],
    "delivery": ["lead time", "keterlambatan", "pengiriman", "delivery"],
}

def count_keyword_hits(text: str) -> Dict[str, int]:
    text_low = text.lower()
    hits = {}
    for theme, words in CTQ_KEYWORDS.items():
        hits[theme] = sum(len(re.findall(r"\b"+re.escape(w.lower())+r"\b", text_low)) for w in words)
    return hits

def suggest_ctq_from_hits(hits: Dict[str, int]) -> List[str]:
    # ambil theme dengan jumlah hit > 0 dan urutkan terbesar
    themes = [(k, v) for k, v in hits.items() if v > 0]
    themes.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in themes][:3]  # ambil 3 teratas
