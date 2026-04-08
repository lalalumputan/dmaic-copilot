"""
auth.py — Role-based authentication untuk DMAIC Copilot.
Kredensial disimpan di Streamlit Secrets (bukan database).
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


def _get_credentials() -> dict:
    """
    Baca kredensial dari Streamlit Secrets.
    Format di secrets.toml:
        [users]
        project_leader = "password1"
        reviewer = "password2"
        champion = "password3"
    """
    try:
        return dict(st.secrets.get("users", {}))
    except Exception:
        # Fallback untuk local dev tanpa secrets
        return {
            "project_leader": "pl123",
            "reviewer": "rev123",
            "champion": "champ123",
        }


def login(username: str, password: str) -> Optional[str]:
    """
    Coba login. Return role jika berhasil, None jika gagal.
    """
    creds = _get_credentials()
    if username in creds and creds[username] == password:
        return username  # username = role name
    return None


def get_current_role() -> Optional[str]:
    """Return role user yang sedang login, atau None."""
    return st.session_state.get("auth_role")


def get_current_user() -> Optional[str]:
    """Return display name user yang sedang login."""
    return st.session_state.get("auth_user")


def is_logged_in() -> bool:
    return get_current_role() is not None


def can(action: str) -> bool:
    """
    Cek apakah role saat ini punya permission untuk action.
    Actions: 'input', 'submit', 'review', 'approve'
    """
    role = get_current_role()
    if not role or role not in ROLES:
        return False
    return ROLES[role].get(f"can_{action}", False)


def require_login():
    """
    Tampilkan login form jika belum login.
    Panggil di awal setiap page.
    """
    if is_logged_in():
        return

    st.markdown("## 🔐 Login")
    st.caption("Masukkan username dan password sesuai role kamu.")

    with st.form("login_form"):
        username = st.text_input("Username", placeholder="project_leader / reviewer / champion")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")

    if submitted:
        role = login(username.strip(), password.strip())
        if role:
            st.session_state["auth_role"] = role
            st.session_state["auth_user"] = username.strip()
            st.rerun()
        else:
            st.error("Username atau password salah.")

    st.stop()


def logout():
    """Hapus session login."""
    for key in ["auth_role", "auth_user"]:
        st.session_state.pop(key, None)


def render_user_badge():
    """Tampilkan info user yang sedang login di sidebar."""
    role = get_current_role()
    user = get_current_user()
    if not role:
        return
    label = ROLES.get(role, {}).get("label", role)
    st.sidebar.markdown(
        f"<div style='padding:8px;border-radius:6px;background:#1f2937;"
        f"color:#e5e7eb;margin-bottom:8px'>"
        f"👤 <b>{user}</b><br>"
        f"<span style='color:#9ca3af;font-size:0.85em'>{label}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Logout", key="auth_logout_btn"):
        logout()
        st.rerun()