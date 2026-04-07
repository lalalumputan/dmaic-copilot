# app/agents/analyze_rootcause.py
import pandas as pd
from datetime import datetime
from app.utils.guards import (
    validate_required_columns, sanitize_sensitive,
    force_numeric, json_safe, df_to_json_records, rca_confidence_from_contribution
)



REQUIRED = ["defect_type", "qty"]
NUMERIC = ["qty"]

def run(df: pd.DataFrame):
    # 1) Validasi & sanitasi
    if df is None or df.empty:
        return {"error": "Dataset kosong."}
    try:
        validate_required_columns(df, REQUIRED)
    except Exception as e:
        return {"error": str(e)}

    df = sanitize_sensitive(df)
    df = force_numeric(df, NUMERIC)
    if df["qty"].isna().any():
        return {"error": "Kolom 'qty' mengandung nilai non-numeric/NaN setelah konversi."}

    # 2) Pareto
    pareto = (df.groupby("defect_type", as_index=False)["qty"].sum()
                .sort_values("qty", ascending=False))
    total = float(pareto["qty"].sum())
    pareto["cum_%"] = (pareto["qty"].cumsum() / total * 100.0) if total > 0 else 0.0

    # 3) RCA candidate + confidence berdasar kontribusi
    rca = []
    for _, row in pareto.iterrows():
        contrib_pct = float(row["qty"]) / total * 100.0 if total > 0 else 0.0
        rca.append({
            "Cause": f"Penyebab terkait '{row['defect_type']}'",
            "Contribution_%": round(contrib_pct, 2),
            "Confidence": rca_confidence_from_contribution(contrib_pct)
        })

    # 4) Output JSON-aman
    result = {
        "pareto": df_to_json_records(pareto),
        "rca": [{k: json_safe(v) for k, v in r.items()} for r in rca],
        "timestamp": datetime.now().isoformat(),
        "agent": "analyze_rootcause"
    }
    return result
