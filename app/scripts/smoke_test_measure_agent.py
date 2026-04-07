# scripts/smoke_test_measure_agent.py
from __future__ import annotations

import json
import traceback

def operational_definitions_seed():
    """Return seed data for operational definitions."""
    return []

def main():
    try:
        from app.agents.measure_agent import run_measure_agent, finalize_measure_agent
    except Exception:
        print("FAILED: cannot import measure_agent")
        traceback.print_exc()
        return

    project_id = "proj_smoke_001"
    user_inputs = {
        "project_id": project_id,
        "project_name": "Smoke Test Project",
        "industry": "manufacturing",
        "pain_theme": "quality",
        "user_inputs": {
            "operational_definitions": operational_definitions_seed(),
        },
        "process_area": "packaging",
        "y_variable": "Defect Rate (%)",
        "y_operational_definition_notes": "Defect Rate = (defect units / total units) * 100, per shift",
    }
    
    print("== RUN DRAFT ==")
    try:
        res = run_measure_agent(project_id=project_id, user_inputs=user_inputs, baseline_df=None, user_feedback=None)
        ms = res["measure_state"]
        assert ms["status"] == "draft"
        assert ms["phase"] == "measure"
        assert isinstance(ms.get("outputs"), dict)
        print("OK: draft generated")
        print("summary_md:\n", ms.get("summary_md", "")[:400])
    except Exception:
        print("FAILED: run_measure_agent")
        traceback.print_exc()
        return

    print("\n== FINALIZE ==")
    try:
        fin = finalize_measure_agent(project_id=project_id, measure_state=ms, user_feedback={"notes": "ok"})
        fs = fin["measure_state"]
        assert fs["status"] == "final"
        assert fs["phase"] == "measure"
        print("OK: finalized")
        print("word_path:", fin.get("word_path"))
    except Exception:
        print("FAILED: finalize_measure_agent")
        traceback.print_exc()
        return

    print("\n== OUTPUTS KEYS ==")
    print(json.dumps(list((fs.get("outputs") or {}).keys()), indent=2))


if __name__ == "__main__":
    main()
