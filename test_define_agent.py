from app.agents.define_agent import run_define_agent

# -------------------------
# MOCK USER INPUTS
# -------------------------
user_inputs = {
    "project_name": "Reduce Packaging Defects",
    "industry": "Manufacturing",
    "process_area": "Production",
    "pain_theme": "Quality",
    "problem_text": """
        Defect pada inner packaging meningkat dari 2.5% menjadi 6% dalam 3 bulan terakhir,
        terutama pada sealing line. Hal ini menyebabkan rework dan scrap meningkat signifikan.
    """,
    "goal_text": "Reduce defect",
    "voc_list": [
        "Customer complain product leak",
        "High scrap cost",
        "Frequent rework on sealing line"
    ],
    "key_issue": "Possible heat instability in sealing jaws"
}

project_id = "proj_001"

# -------------------------
# STEP 1: Generate Draft
# -------------------------
result_draft = run_define_agent(
    project_id=project_id,
    user_inputs=user_inputs,
    baseline_df=None,
    user_feedback=None,
    user_accept=False       # <--- DRAFT MODE
)

print("\n======= DRAFT SUMMARY =======\n")
print(result_draft["summary"])

print("\n======= DRAFT DEFINE JSON =======\n")
print(result_draft["define_state"])


# -------------------------
# STEP 2: Finalize Define
# -------------------------
result_final = run_define_agent(
    project_id=project_id,
    user_inputs=user_inputs,
    baseline_df=None,
    user_feedback={"reason": "Looks good"},
    user_accept=False        # <--- FINAL MODE
)

print("\n======= FINAL SUMMARY =======\n")
print(result_final["summary"])

print("\n======= CRITIQUE (SELF REVIEW) =======\n")
print(result_final.get("critique", []))

print("\n======= STORED DEFINE JSON =======\n")
print(result_final["define_state"])
