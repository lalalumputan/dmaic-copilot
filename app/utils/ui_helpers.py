import os
import streamlit as st

def render_sidebar_header():
    logo_path = "app/assets/sidebar_logo.png"
    if os.path.exists(logo_path):
        st.logo(logo_path, size="large", link=None)