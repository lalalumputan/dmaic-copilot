# app/agents/improve_doe.py
import itertools
from datetime import datetime

MAX_RUNS = 32  # batasi agar realistis untuk pilot (ubah sesuai kebutuhan)

def _parse_factors(text: str):
    """
    Parse input seperti: "Suhu=60,70; Kecepatan=100,120"
    """
    factors = {}
    if not text or "=" not in text:
        return factors
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        levels = [x.strip() for x in v.split(",") if x.strip()]
        # guard: harus >= 2 level agar ada efek
        if len(levels) < 2:
            raise ValueError(f"Faktor '{k.strip()}' perlu ≥ 2 level.")
        # guard: duplikat level
        if len(levels) != len(set(levels)):
            raise ValueError(f"Level faktor '{k.strip()}' mengandung duplikat.")
        factors[k.strip()] = levels
    return factors

def run_from_text(factor_text: str):
    try:
        factors = _parse_factors(factor_text)
        if not factors:
            return {"error": "Input faktor/level kosong atau tidak valid. Contoh: 'Suhu=60,70; Kecepatan=100,120'."}
    except Exception as e:
        return {"error": str(e)}
    return run(factors)

def run(factors: dict):
    if not factors:
        return {"error": "Input faktor/level tidak lengkap."}

    # total kombinasi
    levels_count = [len(v) for v in factors.values()]
    total_runs = 1
    for c in levels_count:
        total_runs *= c

    if total_runs > MAX_RUNS:
        return {"error": f"Total kombinasi {total_runs} melebihi batas {MAX_RUNS}. Kurangi jumlah faktor/level."}

    # generate kombinasi (full factorial)
    keys = list(factors.keys())
    runs = list(itertools.product(*[factors[k] for k in keys]))

    # beri nomor run dan randomisasi ringan (biar tidak selalu urut)
    import random
    random.seed(42)
    random.shuffle(runs)

    doe_records = []
    for i, combo in enumerate(runs, start=1):
        row = {"Run": i}
        for k, v in zip(keys, combo):
            row[k] = v
        doe_records.append(row)

    # ROI placeholder (bisa kamu hubungkan ke cost_analyzer)
    investment = 10_000_000
    expected_saving = 15_000_000
    roi = (expected_saving - investment) / investment

    return {
        "DOE_matrix": doe_records,
        "runs": total_runs,
        "factors": list(factors.keys()),
        "ROI_dummy": round(roi, 2),
        "timestamp": datetime.now().isoformat(),
        "agent": "improve_doe"
    }
