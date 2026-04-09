# ============================
# MAIN.py — DMAIC Copilot Dashboard
# ============================
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
from app.utils import memory, audit, auth
from app.utils.ui_helpers import render_sidebar_header

st.set_page_config(page_title="DMAIC Copilot", layout="wide")
# Logo/title di atas navigation links
logo_path = "app/assets/logo3.jpg"
if os.path.exists(logo_path):
    render_sidebar_header()

render_sidebar_header()

# ── Login Gate ──────────────────────────────────────────
auth.require_login()
auth.render_user_badge()

# ── Header ──────────────────────────────────────────────
logo_path = "app/assets/logo2.jpg"
h_title, h_logo = st.columns([6, 1])
with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=200)
with h_title:
    st.markdown("# 🎯 DMAIC Copilot")
    st.caption("Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC")

st.divider()

# ── Active Project & Role Info ───────────────────────────
role  = auth.get_current_role()
user  = auth.get_current_user()
label = auth.ROLES.get(role, {}).get("label", role)
active_pid = st.session_state.get("active_project_id")

top_l, top_r = st.columns([3, 2])
with top_l:
    if active_pid:
        st.success(f"📂 Active Project: **{active_pid}**")
    else:
        st.info("Belum ada project aktif. Pilih dari tabel di bawah.")
with top_r:
    st.caption(f"Role: **{label}** | User: `{user}`")

# ── Buat Project Baru (hanya project_leader) ─────────────
if auth.can("input"):
    with st.expander("➕ Buat Project Baru", expanded=False):
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            custom_pid = st.text_input(
                "Project ID (kosongkan = auto)",
                key="main_custom_pid",
            )
        with col2:
            project_name_init = st.text_input(
                "Nama Project",
                key="main_project_name_init",
            )
        with col3:
            path_choice = st.selectbox(
                "Path",
                ["Standard DMAIC", "Quick Improvement"],
                key="main_path_choice",
            )

        if st.button("▶ Mulai Project", type="primary", key="main_start_btn"):
            pid = custom_pid.strip() if custom_pid.strip() else memory.next_define_project_id()
            if memory.define_project_exists(pid):
                st.error(f"Project ID '{pid}' sudah ada.")
            else:
                st.session_state["active_project_id"] = pid
                st.session_state["active_project_path"] = (
                    "quick" if "Quick" in path_choice else "standard"
                )
                st.session_state["active_project_name"] = project_name_init.strip()
                st.success(f"✅ Project **{pid}** dibuat. Buka halaman **Define** di sidebar.")

st.divider()

# ── Project Status Table ──────────────────────────────────
st.subheader("📊 Project Overview")

search = st.text_input(
    "🔍 Cari project",
    key="main_search",
    placeholder="ketik ID atau nama project...",
)

with memory._db() as con:
    rows = con.execute(
        "SELECT DISTINCT project_id FROM phase_states "
        "WHERE phase='define' ORDER BY project_id"
    ).fetchall()
    all_pids = [r["project_id"] for r in rows]

if search.strip():
    all_pids = [p for p in all_pids if search.lower() in p.lower()]

phases = ["define", "measure", "analyze", "improve", "control"]

def _phase_icon(pid, phase):
    final = memory._load_state(pid, phase, "final")
    draft = memory._load_state(pid, phase, "draft")
    appr  = memory.get_phase_approval_status(pid, phase)
    if final and appr.get("can_advance"):
        return "✅"
    if final:
        return "🔒"
    if draft:
        return "🔄"
    return "⬜"

def _appr_icon(action):
    if action == "approve": return "✅"
    if action == "reject":  return "❌"
    return "⏳"

if not all_pids:
    st.info("Belum ada project." if not search.strip() else "Tidak ditemukan.")
else:
    # Header tabel
    h = st.columns([2, 3, 1, 1, 1, 1, 1, 1, 1, 1])
    for col, txt in zip(h, [
        "**ID**", "**Nama**",
        "**Def**", "**Mea**", "**Ana**", "**Imp**", "**Con**",
        "**Rev**", "**Champ**", "**Aksi**"
    ]):
        col.markdown(txt)
    st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

    for pid in all_pids:
        define_final = memory.load_define_final(pid)
        pname = ""
        if define_final:
            pname = (define_final.get("inputs") or {}).get("project_name", "")

        appr_def     = memory.get_phase_approval_status(pid, "define")
        rev_action   = (appr_def.get("reviewer")  or {}).get("action")
        champ_action = (appr_def.get("champion")  or {}).get("action")
        is_active    = (active_pid == pid)

        row = st.columns([2, 3, 1, 1, 1, 1, 1, 1, 1, 1])

        with row[0]:
            st.markdown(f"**🟢 {pid}**" if is_active else f"`{pid}`")
        with row[1]:
            st.caption(pname or "—")
        for i, phase in enumerate(phases):
            with row[2 + i]:
                st.markdown(
                    f"<center>{_phase_icon(pid, phase)}</center>",
                    unsafe_allow_html=True,
                )
        with row[7]:
            st.markdown(
                f"<center>{_appr_icon(rev_action)}</center>",
                unsafe_allow_html=True,
            )
        with row[8]:
            st.markdown(
                f"<center>{_appr_icon(champ_action)}</center>",
                unsafe_allow_html=True,
            )
        with row[9]:
            if st.button("Load", key=f"load_{pid}"):
                st.session_state["active_project_id"] = pid
                # Reset _loaded_pid agar pages reload dari disk
                st.session_state["_loaded_pid"] = None
                for key in [
                    "define_draft", "define_final",
                    "measure_draft", "measure_final",
                    "define_final_state",
                    "measure_baseline_df", "measure_uploaded_name",
                    "analyze_draft", "analyze_final",
                    "improve_draft", "improve_final",
                    "control_draft", "control_final",
                ]:
                    st.session_state.pop(key, None)
                st.rerun()

    st.markdown(
        "<small>✅ Approved &nbsp;|&nbsp; 🔒 Finalized &nbsp;|&nbsp; "
        "🔄 In Progress &nbsp;|&nbsp; ⬜ Not Started &nbsp;|&nbsp; "
        "⏳ Pending &nbsp;|&nbsp; ❌ Rejected</small>",
        unsafe_allow_html=True,
    )

st.divider()

# ── Recent Activity ──────────────────────────────────────
st.subheader("🧾 Recent Activity")
show_all = st.checkbox("Tampilkan semua project", key="main_show_all")
events = audit.get_recent_events(
    project_id=None if show_all else active_pid,
    limit=15,
)
if events:
    for e in events:
        ts    = (e.get("timestamp") or "")[:19].replace("T", " ")
        phase = e.get("phase", "")
        stat  = e.get("status", "")
        pid_e = e.get("project_id", "")
        st.markdown(f"`{ts}` &nbsp; **{pid_e}** &nbsp; {phase} → {stat}")
else:
    st.caption("Belum ada activity.")