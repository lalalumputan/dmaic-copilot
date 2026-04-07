
from datetime import datetime

def run(before_cost, after_cost, investment):
    try:
        saving = before_cost - after_cost
        roi = (saving - investment) / investment if investment else None
    except Exception as e:
        return {"error": f"Perhitungan gagal: {e}"}

    result = {
        "Saving": saving,
        "ROI": roi,
        "timestamp": datetime.now().isoformat(),
        "agent": "cost_analyzer"
    }
    return result
