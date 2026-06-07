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


def _send_registration_request(username: str, password: str, role: str, display_name: str):
    """Kirim permintaan akun baru ke admin via email (blok TOML siap-paste)."""
    import smtplib, ssl
    from email.message import EmailMessage
    try:
        host = str(st.secrets.get("SMTP_HOST", "smtp.gmail.com"))
        port = int(st.secrets.get("SMTP_PORT", 587))
        user = st.secrets["SMTP_USER"]
        pwd  = st.secrets["SMTP_PASS"]
        to   = st.secrets["ADMIN_EMAIL"]
    except Exception:
        return False, "Konfigurasi email belum diset (SMTP_USER / SMTP_PASS / ADMIN_EMAIL)."

    toml_block = (
        f"[users.{username}]\n"
        f'password = "{password}"\n'
        f'role = "{role}"\n'
        f'display_name = "{display_name}"\n'
    )
    body = (
        "Permintaan akun baru DMAIC Copilot:\n\n"
        f"Username     : {username}\n"
        f"Role         : {role}\n"
        f"Nama tampilan: {display_name}\n\n"
        "Untuk mengaktifkan, tambahkan blok berikut ke Secrets Streamlit Cloud "
        "(di bawah blok [users...] lain), lalu Reboot app:\n\n"
        f"{toml_block}"
    )
    msg = EmailMessage()
    msg["Subject"] = f"[DMAIC Copilot] Permintaan akun: {username} ({role})"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.send_message(msg)
        return True, ""
    except Exception as e:
        return False, repr(e)


def _render_register_form():
    st.caption("Ajukan akun baru. Permintaan dikirim ke admin untuk diaktifkan.")
    with st.form("register_form"):
        role = st.selectbox(
            "Role", options=list(ROLES.keys()),
            format_func=lambda r: ROLES[r]["label"],
        )
        username     = st.text_input("Username")
        display_name = st.text_input("Nama tampilan")
        password     = st.text_input("Password", type="password")
        password2    = st.text_input("Ulangi password", type="password")
        submit = st.form_submit_button("Kirim permintaan akun", type="primary")

    if submit:
        u = username.strip()
        if not u or not password:
            st.error("Username dan password wajib diisi."); return
        if " " in u:
            st.error("Username tidak boleh mengandung spasi."); return
        if password != password2:
            st.error("Password tidak sama."); return
        ok, err = _send_registration_request(u, password, role, display_name.strip() or u)
        if ok:
            st.success("✅ Permintaan akun terkirim ke admin. Anda akan diberi akses setelah disetujui.")
        else:
            st.error(f"Gagal mengirim permintaan: {err}")


def require_login():
    if is_logged_in():
        return

    tab_login, tab_register = st.tabs(["🔐 Masuk", "📝 Daftar"])

    with tab_login:
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

    with tab_register:
        _render_register_form()

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