"""
auth.py — Role-based authentication untuk DMAIC Copilot.
Mendukung multi-user per role.
Format secrets.toml:
    [users.username]
    password = "..."
    role = "project_leader|reviewer|champion"
    display_name = "..."
"""
from __future__ import annotations
import streamlit as st
from typing import Optional

# ── Role definitions ──────────────────────────────────────
ROLES = {
    "project_leader": {
        "label": "Project Leader",
        "can_input": True,
        "can_submit": True,
        "can_review": False,
        "can_approve": False,
    },
    "reviewer": {
        "label": "Reviewer",
        "can_input": False,
        "can_submit": False,
        "can_review": True,
        "can_approve": False,
    },
    "champion": {
        "label": "Champion",
        "can_input": False,
        "can_submit": False,
        "can_review": True,
        "can_approve": True,
    },
}


def _get_users() -> dict:
    try:
        result = {}
        users_raw = st.secrets.get("users", {})
        
        for key in users_raw.keys():
            val = users_raw[key]
            # Format baru: nested dict [users.username]
            if hasattr(val, 'get') or isinstance(val, dict):
                result[key] = {
                    "password":     str(val.get("password", "")),
                    "role":         str(val.get("role", "")),
                    "display_name": str(val.get("display_name", key)),
                }
            # Format lama: [users] username = "password"
            elif isinstance(val, str):
                result[key] = {
                    "password":     val,
                    "role":         key,
                    "display_name": key.replace("_", " ").title(),
                }
        return result

    except Exception:
        return {
            "project_leader": {
                "password": "pl123",
                "role": "project_leader",
                "display_name": "Project Leader",
            },
            "reviewer": {
                "password": "rev123",
                "role": "reviewer",
                "display_name": "Reviewer",
            },
            "champion": {
                "password": "champ123",
                "role": "champion",
                "display_name": "Champion",
            },
        }


def login(username: str, password: str) -> Optional[dict]:
    """
    Coba login. Return user dict jika berhasil, None jika gagal.
    """
    users = _get_users()
    user  = users.get(username)
    if user and user.get("password") == password:
        return user
    return None


def get_current_role() -> Optional[str]:
    return st.session_state.get("auth_role")

def get_current_user() -> Optional[str]:
    return st.session_state.get("auth_user")

def get_current_display_name() -> str:
    return st.session_state.get("auth_display_name", get_current_user() or "")

def is_logged_in() -> bool:
    return get_current_role() is not None

def can(action: str) -> bool:
    role = get_current_role()
    if not role or role not in ROLES:
        return False
    return ROLES[role].get(f"can_{action}", False)


def require_login():
    if is_logged_in():
        return

    st.markdown("## 🔐 Login")
    st.caption("Masukkan username dan password sesuai akun kamu.")

    with st.form("login_form"):
        username  = st.text_input("Username")
        password  = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")

    if submitted:
        user = login(username.strip(), password.strip())
        if user:
            role = user.get("role", "")
            if role not in ROLES:
                st.error(f"Role '{role}' tidak dikenali. Hubungi administrator.")
            else:
                st.session_state["auth_role"]         = role
                st.session_state["auth_user"]         = username.strip()
                st.session_state["auth_display_name"] = user.get("display_name", username)
                st.switch_page("MAIN.py")
        else:
            st.error("Username atau password salah.")

    st.stop()


def logout():
    for key in ["auth_role", "auth_user", "auth_display_name"]:
        st.session_state.pop(key, None)


def render_user_badge():
    role         = get_current_role()
    display_name = get_current_display_name()
    if not role:
        return
    label = ROLES.get(role, {}).get("label", role)
    st.sidebar.markdown(
        f"<div style='padding:8px;border-radius:6px;background:#1f2937;"
        f"color:#e5e7eb;margin-bottom:8px'>"
        f"👤 <b>{display_name}</b><br>"
        f"<span style='color:#9ca3af;font-size:0.85em'>{label}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Logout", key="auth_logout_btn"):
        logout()
        st.switch_page("MAIN.py")