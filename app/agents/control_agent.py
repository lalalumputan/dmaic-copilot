# app/agents/control_plan.py
import pandas as pd
import numpy as np
from datetime import datetime
from app.utils.guards import force_numeric, json_safe

def run(df: pd.DataFrame):
    if df is None or df.empty:
        return {"error": "Dataset kosong."}

    # Validasi kolom 'value'
    if "value" not in df.columns:
        return {"error": "Kolom wajib 'value' tidak ada."}

    df = force_numeric(df, ["value"])
    if df["value"].isna().any():
        return {"error": "Kolom 'value' mengandung nilai non-numeric/NaN setelah konversi."}

    mean = float(df["value"].mean())
    sigma = float(df["value"].std(ddof=1)) if len(df) > 1 else 0.0
    UCL, LCL = (mean + 3 * sigma), (mean - 3 * sigma)

    result = {
        "Chart": "I-MR",
        "UCL": round(json_safe(UCL), 3),
        "LCL": round(json_safe(LCL), 3),
        "Center": round(json_safe(mean), 3),
        "Reaction": "Hentikan proses jika melebihi UCL/LCL. Lakukan RCA dan containment.",
        "timestamp": datetime.now().isoformat(),
        "agent": "control_plan"
    }
    return result
