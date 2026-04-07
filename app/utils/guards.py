
import pandas as pd
from typing import List, Dict, Any

SENSITIVE_COLS = {"operator", "operator_name", "employee", "nik", "email"}

def validate_required_columns(df: pd.DataFrame, required: List[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Kolom wajib hilang: {missing}")

def sanitize_sensitive(df: pd.DataFrame) -> pd.DataFrame:
    drop_cols = [c for c in df.columns if c.lower() in SENSITIVE_COLS]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return df

def force_numeric(df: pd.DataFrame, numeric_cols: List[str]) -> pd.DataFrame:
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def json_safe(obj: Any):
    import numpy as np
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj

def df_to_json_records(df: pd.DataFrame) -> list:
    records = df.to_dict(orient="records")
    return [{k: json_safe(v) for k, v in row.items()} for row in records]

def rca_confidence_from_contribution(pct: float) -> str:
    if pct >= 50:
        return "High"
    if pct >= 20:
        return "Medium"
    return "Low"

# dmaic_copilot/app/utils/guards.py

def require_text(value: str, field: str):
    if not value or len(value.strip()) < 3:
        raise ValueError(f"{field} cannot be empty or too short.")
