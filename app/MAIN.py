
# ============================
# PAGE CONFIG & PROJECT MANAGER
# ============================
import os
import streamlit as st

st.set_page_config(page_title="DMAIC Copilot", layout="wide")

logo_path = "app/assets/logo2.jpg"
h_title, h_logo = st.columns([5, 1])

with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=300)

with h_title:
    st.markdown("# DMAIC Copilot — AI for Continuous Improvement")
    st.caption("### Agentic AI for Continuous Improvement Projects using Lean Six Sigma DMAIC Methodology")
    
st.divider()

# =====