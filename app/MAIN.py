# ============================
# MAIN.py — DMAIC Copilot Dashboard
# ============================
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
from app.utils import memory, audit, auth
from app.utils.ui_helpers import render_sidebar_header
from app.agents.classification_agent import run_classification_agent, get_gate_mode

# Marker hidrasi per-fase. Tiap page punya markernya sendiri agar tidak saling
# menimpa (mencegah tab kosong saat menyusuri fase setelah Load dari MAIN).
PHASE_LOAD_MARKERS = [
    "_loaded_pid", "_measure_loaded_pid", "_analyze_loaded_pid",
    "_improve_loaded_pid", "_control_loaded_pid",
]

def _reset_phase_markers():
    """Reset semua marker hidrasi agar setiap page re-load penuh dari disk."""
    for _m in PHASE_LOAD_MARKERS:
        st.session_state[_m] = None

st.set_page_config(page_title="DMAIC Copilot", layout="wide")
# ── Slanted Tabs (samakan dengan styling tab di pages) ──
st.markdown("""
<style>
div[data-baseweb="tab-list"] {
    gap: 0 !important;
    background: #f4f8fb !important;
    border-bottom: 3px solid #16a34a !important;
    padding: 0 0 0 20px !important;
    align-items: flex-end !important;
    overflow: visible !important;
}
button[data-baseweb="tab"] {
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    color: #334155 !important;
    font-weight: 600 !important;
    font-style: normal !important;
    font-size: 0.85rem !important;
    letter-spacing: 0.015em !important;
    padding: 9px 30px !important;
    margin-right: -16px !important;
    position: relative !important;
    z-index: 1 !important;
    overflow: visible !important;
    transition: color 0.15s !important;
}
button[data-baseweb="tab"]::before {
    content: '' !important;
    position: absolute !important;
    top: 0 !important; right: 0 !important; bottom: 0 !important; left: 0 !important;
    background: #dde3ea !important;
    transform: skewX(-20deg) !important;
    z-index: -1 !important;
    transition: background 0.15s !important;
    border-radius: 3px 3px 0 0 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #ffffff !important;
    z-index: 3 !important;
    font-weight: 700 !important;
}
button[data-baseweb="tab"][aria-selected="true"]::before {
    background: #16a34a !important;
}
button[data-baseweb="tab"]:hover:not([aria-selected="true"]) {
    color: #0f172a !important;
    z-index: 2 !important;
}
button[data-baseweb="tab"]:hover:not([aria-selected="true"])::before {
    background: #c3cdd6 !important;
}
div[data-baseweb="tab-highlight"],
div[data-baseweb="tab-border"] { display: none !important; }
</style>
""", unsafe_allow_html=True)
# Logo/title di atas navigation links
logo_path = "app/assets/logo3.jpg"
if os.path.exists(logo_path):
    render_sidebar_header()
render_sidebar_header()

# ── Login Gate ──────────────────────────────────────────
auth.require_login()
auth.render_user_badge()

# ── Backend indicator (diagnostik DB) ───────────────────
try:
    _bs = memory.backend_status()
    if _bs.get("active") == "turso":
        st.sidebar.caption("🟢 DB: Turso (cloud, persisten)")
    else:
        st.sidebar.caption("🟡 DB: SQLite (lokal/ephemeral)")
        if _bs.get("reason"):
            st.sidebar.caption(f"↳ {_bs['reason']}")
except Exception:
    pass

# ── Header ──────────────────────────────────────────────
logo_path = "app/assets/logo3.jpg"
h_title, h_logo = st.columns([6, 1])
with h_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=200)
with h_title:
    st.markdown("# 🎯 DMAIC Copilot")
    st.caption("Agentic AI for Continuous Improvement — Lean Six Sigma DMAIC")

st.divider()

tab_dash, tab_guide = st.tabs(["🏠 Dashboard", "📖 Panduan DMAIC & Quick Improvement"])

with tab_dash:

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
            col1, col2 = st.columns([2, 2])
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

            st.markdown("**Project Classification — 5 Criteria**")
            st.caption("System will recommend Quick Improvement or Standard DMAIC based on your answers.")

            cl1, cl2, cl3 = st.columns(3)
            with cl1:
                perf_impact = st.selectbox(
                    "1. Performance impact",
                    ["low", "medium", "high"],
                    index=1,
                    key="main_perf_impact",
                    help="Impact on quality, cost, time, or production output"
                )
                proc_complexity = st.selectbox(
                    "2. Process complexity",
                    ["low", "medium", "high"],
                    index=1,
                    key="main_proc_complexity",
                    help="Complexity of the process and variation involved"
                )
            with cl2:
                num_functions = st.number_input(
                    "3. Number of functions/areas involved",
                    min_value=1, max_value=10, value=2,
                    key="main_num_functions",
                    help="How many departments or process areas are in scope"
                )
                data_available = st.checkbox(
                    "4a. Data already available",
                    value=True,
                    key="main_data_available",
                )
                requires_collection = st.checkbox(
                    "4b. New data collection required",
                    value=False,
                    key="main_requires_collection",
                )
            with cl3:
                impl_risk = st.selectbox(
                    "5. Implementation risk",
                    ["low", "medium", "high"],
                    index=1,
                    key="main_impl_risk",
                    help="Risk level and reversibility of the proposed solution"
                )

            if st.button("▶ Classify & Start Project", type="primary", key="main_start_btn"):
                pid = custom_pid.strip() if custom_pid.strip() else memory.next_define_project_id()
                if memory.define_project_exists(pid):
                    st.error(f"Project ID '{pid}' sudah ada.")
                else:
                    # Run classification
                    clf_inputs = {
                        "performance_impact":       perf_impact,
                        "process_complexity":       proc_complexity,
                        "num_functions_involved":   int(num_functions),
                        "data_available":           data_available,
                        "requires_data_collection": requires_collection,
                        "implementation_risk":      impl_risk,
                    }
                    clf_result = run_classification_agent(clf_inputs)
                    recommended_path = clf_result["recommended_path"]

                    # Save to DB
                    memory.save_project_meta(pid, {
                        "path":               recommended_path,
                        "classification":     clf_result,
                        "path_override":      False,
                        "path_confirmed_by":  None,
                        "project_name":       project_name_init.strip(),
                    })

                    # Set session state
                    st.session_state["active_project_id"]   = pid
                    st.session_state["active_project_path"] = recommended_path
                    st.session_state["active_project_name"] = project_name_init.strip()
                    st.session_state["main_last_clf"]        = clf_result
                    st.session_state["main_last_pid"]        = pid

            # Show classification result if just created
            clf = st.session_state.get("main_last_clf")
            pid_shown = st.session_state.get("main_last_pid")
            if clf and pid_shown:
                path      = clf["recommended_path"]
                score     = clf["total_score"]
                max_score = clf["max_score"]
                conf      = clf["confidence"]
                border    = clf.get("borderline", False)

                badge_color = "🟢" if path == "quick" else "🔵"
                if border:
                    st.warning(
                        f"**{badge_color} {path.upper()} PATH recommended** "
                        f"(score {score}/{max_score}, confidence: {conf} — borderline, reviewer should assess)\n\n"
                        f"{clf['overall_reasoning']}"
                    )
                elif conf == "high":
                    st.success(
                        f"**{badge_color} {path.upper()} PATH recommended** "
                        f"(score {score}/{max_score}, confidence: {conf})\n\n"
                        f"{clf['overall_reasoning']}"
                    )
                else:
                    st.info(
                        f"**{badge_color} {path.upper()} PATH recommended** "
                        f"(score {score}/{max_score}, confidence: {conf})\n\n"
                        f"{clf['overall_reasoning']}"
                    )

                with st.expander("Score breakdown", expanded=False):
                    for criterion, detail in clf["score_breakdown"].items():
                        st.markdown(
                            f"- **{criterion.replace('_',' ').title()}**: "
                            f"{detail['score']} pt — {detail['reasoning']}"
                        )
                st.success(f"✅ Project **{pid_shown}** created. Open **Define** in sidebar.")

    st.divider()

    # ── Recap: Needs Attention (role-based) ──────────────────
    _all_recap_pids = []
    try:
        with memory._db() as _con:
            _rows = _con.execute(
                "SELECT DISTINCT project_id FROM phase_states ORDER BY project_id"
            ).fetchall()
            _all_recap_pids = [r["project_id"] for r in _rows]
    except Exception:
        _all_recap_pids = []

    def _recap_pname(pid):
        """Ambil nama project dari meta atau define state."""
        try:
            name = memory.load_project_meta(pid).get("project_name", "") or ""
            if not name:
                _df = memory.load_define_final(pid) or memory.load_define_draft(pid) or {}
                name = (_df.get("inputs") or {}).get("project_name", "") or ""
            return name
        except Exception:
            return ""

    if role == "reviewer":
        _rev_items = []
        for _pid in _all_recap_pids:
            for _rphase in ["define", "measure", "analyze", "improve", "control"]:
                if memory._load_state(_pid, _rphase, "final"):
                    _appr = memory.get_phase_approval_status(_pid, _rphase)
                    if (
                        "reviewer" in _appr.get("required_approvers", [])
                        and not (_appr.get("reviewer") or {}).get("action")
                        and _appr.get("prev_approved", True)
                    ):
                        _rev_items.append({"pid": _pid, "nama": _recap_pname(_pid), "fase": _rphase.upper()})
        if _rev_items:
            st.subheader(f"📋 Perlu Approval Kamu — {len(_rev_items)} project")
            _rh = st.columns([2, 3, 1, 2, 1])
            _rh[0].markdown("**Project ID**")
            _rh[1].markdown("**Nama**")
            _rh[2].markdown("**Fase**")
            _rh[3].markdown("**Status**")
            _rh[4].markdown("**Aksi**")
            st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
            for _item in _rev_items:
                _rc = st.columns([2, 3, 1, 2, 1])
                _rc[0].markdown(f"`{_item['pid']}`")
                _rc[1].caption(_item['nama'] or "—")
                _rc[2].markdown(_item['fase'])
                _rc[3].markdown("⏳ Menunggu Reviewer")
                if _rc[4].button("Load", key=f"recap_rev_{_item['pid']}"):
                    st.session_state["active_project_id"] = _item['pid']
                    _reset_phase_markers()
                    st.rerun()
            st.divider()

    elif role == "champion":
        _champ_items = []
        for _pid in _all_recap_pids:
            for _cphase in ["control"]:
                if memory._load_state(_pid, _cphase, "final"):
                    _appr = memory.get_phase_approval_status(_pid, _cphase)
                    if (
                        "champion" in _appr.get("required_approvers", [])
                        and not (_appr.get("champion") or {}).get("action")
                        and _appr.get("prev_approved", True)
                    ):
                        _champ_items.append({
                            "pid": _pid, "nama": _recap_pname(_pid), "fase": _cphase.upper()
                        })
        if _champ_items:
            st.subheader(f"📋 Perlu Approval Kamu — {len(_champ_items)} project")
            _ch = st.columns([2, 3, 1, 2, 1])
            _ch[0].markdown("**Project ID**")
            _ch[1].markdown("**Nama**")
            _ch[2].markdown("**Fase**")
            _ch[3].markdown("**Status**")
            _ch[4].markdown("**Aksi**")
            st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
            for _item in _champ_items:
                _cc = st.columns([2, 3, 1, 2, 1])
                _cc[0].markdown(f"`{_item['pid']}`")
                _cc[1].caption(_item['nama'] or "—")
                _cc[2].markdown(_item['fase'])
                _cc[3].markdown("⏳ Menunggu Champion")
                if _cc[4].button("Load", key=f"recap_champ_{_item['pid']}_{_item['fase']}"):
                    st.session_state["active_project_id"] = _item['pid']
                    _reset_phase_markers()
                    st.rerun()
            st.divider()

    elif role == "project_leader":
        _pl_items = []
        for _pid in _all_recap_pids:
            for _pphase in ["define", "measure", "analyze", "improve", "control"]:
                if not memory._load_state(_pid, _pphase, "final"):
                    continue
                _pappr = memory.get_phase_approval_status(_pid, _pphase)
                if _pappr.get("can_advance"):
                    continue  # sudah approved, tidak perlu ditampilkan
                if not _pappr.get("prev_approved", True):
                    continue  # fase sebelumnya belum siap, belum actionable
                if _pappr.get("auto_advance"):
                    continue  # auto, tidak ada yang perlu di-approve
                _pn   = _recap_pname(_pid)
                _reqs = _pappr.get("required_approvers", [])
                # Cek rejection dulu
                _rejected_by = [r.title() for r in _reqs if (_pappr.get(r) or {}).get("action") == "reject"]
                _pending_by  = [r.title() for r in _reqs if not (_pappr.get(r) or {}).get("action")]
                if _rejected_by:
                    _pl_items.append({"pid": _pid, "nama": _pn, "fase": _pphase.upper(),
                                       "status": f"❌ Ditolak {', '.join(_rejected_by)} — perlu revisi"})
                elif _pending_by:
                    _pl_items.append({"pid": _pid, "nama": _pn, "fase": _pphase.upper(),
                                       "status": f"⏳ Menunggu {', '.join(_pending_by)}"})
        if _pl_items:
            st.subheader(f"📋 Status Approval Project Kamu — {len(_pl_items)} item")
            _ph = st.columns([2, 3, 1, 2, 1])
            _ph[0].markdown("**Project ID**")
            _ph[1].markdown("**Nama**")
            _ph[2].markdown("**Fase**")
            _ph[3].markdown("**Status**")
            _ph[4].markdown("**Aksi**")
            st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
            for _item in _pl_items:
                _pc = st.columns([2, 3, 1, 2, 1])
                _pc[0].markdown(f"`{_item['pid']}`")
                _pc[1].caption(_item['nama'] or "—")
                _pc[2].markdown(_item['fase'])
                _pc[3].markdown(_item['status'])
                if _pc[4].button("Load", key=f"recap_pl_{_item['pid']}_{_item['fase']}"):
                    st.session_state["active_project_id"] = _item['pid']
                    _reset_phase_markers()
                    st.rerun()
            st.divider()

    # ── Project Status Table ──────────────────────────────────
    st.subheader("📊 Project Overview")

    f1, f2, f3 = st.columns([2, 1, 1])
    with f1:
        search = st.text_input(
            "🔍 Cari project",
            key="main_search",
            placeholder="ketik ID atau nama project...",
        )
    with f2:
        filter_path = st.selectbox(
            "Path",
            options=["Semua", "Quick", "Standard"],
            key="main_filter_path",
        )
    with f3:
        filter_status = st.selectbox(
            "Status",
            options=["Semua", "In Progress", "Define Done", "Fully Approved"],
            key="main_filter_status",
        )

    with memory._db() as con:
        rows = con.execute(
            "SELECT DISTINCT project_id FROM phase_states "
            "WHERE phase='define' ORDER BY project_id"
        ).fetchall()
        all_pids = [r["project_id"] for r in rows]

    # Filter by search text (ID atau nama project)
    if search.strip():
        def _matches_search(pid):
            if search.lower() in pid.lower():
                return True
            # cek nama project di meta
            try:
                meta = memory.load_project_meta(pid)
                pname = (meta.get("project_name") or "").lower()
                return search.lower() in pname
            except Exception:
                return False
        all_pids = [p for p in all_pids if _matches_search(p)]

    # Filter by path
    if filter_path != "Semua":
        _path_val = "quick" if filter_path == "Quick" else "standard"
        def _matches_path(pid):
            try:
                return memory.load_project_meta(pid).get("path", "standard") == _path_val
            except Exception:
                return False
        all_pids = [p for p in all_pids if _matches_path(p)]

    # Filter by status
    if filter_status != "Semua":
        def _matches_status(pid):
            if filter_status == "In Progress":
                # ada draft di fase manapun, tapi belum semua final + approved
                has_any = any(memory._load_state(pid, ph, "draft") or memory._load_state(pid, ph, "final")
                              for ph in phases)
                all_done = all(
                    memory._load_state(pid, ph, "final") and
                    memory.get_phase_approval_status(pid, ph).get("can_advance")
                    for ph in phases
                )
                return has_any and not all_done
            elif filter_status == "Define Done":
                return bool(memory.load_define_final(pid))
            elif filter_status == "Fully Approved":
                return all(
                    memory._load_state(pid, ph, "final") and
                    memory.get_phase_approval_status(pid, ph).get("can_advance")
                    for ph in phases
                )
            return True
        all_pids = [p for p in all_pids if _matches_status(p)]

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
        # Legend
        st.markdown(
            "<small>🟢 Q = Quick &nbsp;|&nbsp; 🔵 S = Standard &nbsp;|&nbsp; ✎ = Overridden &nbsp;|&nbsp;"
            "✅ Approved &nbsp;|&nbsp; 🔒 Finalized &nbsp;|&nbsp; "
            "🔄 In Progress &nbsp;|&nbsp; ⬜ Not Started &nbsp;|&nbsp; "
            "⏳ Pending &nbsp;|&nbsp; ❌ Rejected</small>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='margin-bottom:6px'></div>", unsafe_allow_html=True)

        # Header tabel
        ICON_OPTIONS = ["Semua", "✅", "🔒", "🔄", "⬜"]

        h = st.columns([2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        h[0].markdown("**ID**")
        h[1].markdown("**Nama**")
        h[2].markdown("**Path**")

        phase_filters = {}
        for i, phase_label in enumerate(["Define", "Measure", "Analyze", "Improve", "Control"]):
            phase_filters[phases[i]] = h[3 + i].selectbox(
                phase_label,
                options=ICON_OPTIONS,
                key=f"main_filt_{phases[i]}",
                label_visibility="collapsed",
            )
            h[3 + i].markdown(f"<center><small>**{phase_label}**</small></center>", unsafe_allow_html=True)

        h[8].markdown("**Rev**")
        h[9].markdown("**Champ**")
        h[10].markdown("**Aksi**")
        st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

        # Apply phase filters
        def _icon_matches(pid, phase, filter_icon):
            if filter_icon == "Semua":
                return True
            return _phase_icon(pid, phase) == filter_icon

        all_pids = [
            p for p in all_pids
            if all(_icon_matches(p, ph, phase_filters[ph]) for ph in phases)
        ]


        for pid in all_pids:
                define_final = memory.load_define_final(pid)
                define_draft = memory.load_define_draft(pid)
                pname = ""
                if define_final:
                    pname = (define_final.get("inputs") or {}).get("project_name", "")
                if not pname and define_draft:
                    pname = (define_draft.get("inputs") or {}).get("project_name", "")
                if not pname:
                    try:
                        pname = memory.load_project_meta(pid).get("project_name", "")
                    except Exception:
                        pname = ""

                is_active  = (active_pid == pid)

                # Approval display:
                # Rev   = Define reviewer (gate awal)
                # Champ = Control champion (Champion hanya mandatory di Control)
                try:
                    _pid_meta = memory.load_project_meta(pid)
                    _pid_path = _pid_meta.get("path", "standard")
                except Exception:
                    _pid_meta = {}
                    _pid_path = "standard"

                appr_def  = memory.get_phase_approval_status(pid, "define")
                rev_action   = (appr_def.get("reviewer") or {}).get("action")
                # Champion sekarang HANYA mandatory di CONTROL — tampilkan CONTROL champion di tabel
                appr_ctrl    = memory.get_phase_approval_status(pid, "control")
                champ_action = (appr_ctrl.get("champion") or {}).get("action")

                row = st.columns([2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1])

                with row[0]:
                    st.markdown(f"**🟢 {pid}**" if is_active else f"`{pid}`")
                with row[1]:
                    st.caption(pname or "—")

                # Path column
                with row[2]:
                    proj_path = _pid_path       
                    override  = _pid_meta.get("path_override", False)
                    path_icon = "🟢 Q" if proj_path == "quick" else "🔵 S"
                    ov_mark   = " ✎" if override else ""
                    st.markdown(
                        f"<center><small>{path_icon}{ov_mark}</small></center>",
                        unsafe_allow_html=True,
                    )


                for i, phase in enumerate(phases):
                    with row[3 + i]:
                        st.markdown(
                            f"<center>{_phase_icon(pid, phase)}</center>",
                            unsafe_allow_html=True,
                        )
                with row[8]:
                    st.markdown(
                        f"<center>{_appr_icon(rev_action)}</center>",
                        unsafe_allow_html=True,
                    )
                with row[9]:
                    st.markdown(
                        f"<center>{_appr_icon(champ_action)}</center>",
                        unsafe_allow_html=True,
                    )
                with row[10]:
                    if st.button("Load", key=f"load_{pid}"):
                        st.session_state["active_project_id"] = pid
                        # Reset semua marker agar pages reload penuh dari disk
                        _reset_phase_markers()
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

        # ── Reviewer path override ──────────────────────────────
        if role == "reviewer":
            st.divider()
            st.markdown("#### 🔀 Path Override (Reviewer)")
            st.caption("Override the AI-recommended path for a specific project if organizational context warrants it.")

            override_pid = st.selectbox(
                "Select project to override",
                options=all_pids,
                key="main_override_pid_select",
            )
            if override_pid:
                cur_meta = memory.load_project_meta(override_pid)
                cur_path = cur_meta.get("path", "standard")
                clf_res  = cur_meta.get("classification") or {}
                st.caption(
                    f"Current path: **{cur_path.upper()}** | "
                    f"AI recommended: **{(clf_res.get('recommended_path') or 'N/A').upper()}** | "
                    f"Score: {clf_res.get('total_score','—')}/{clf_res.get('max_score','—')} | "
                    f"Confidence: {clf_res.get('confidence','—')}"
                )
                new_path = st.radio(
                    "Override to:",
                    ["standard", "quick"],
                    index=0 if cur_path == "standard" else 1,
                    horizontal=True,
                    key="main_override_path_radio",
                )    
                override_note = st.text_input(
                    "Reason for override (required)",
                    key="main_override_note",
                )
                if st.button("✅ Confirm Override", key="main_override_confirm_btn"):
                    if not override_note.strip():
                        st.error("Please provide a reason for the override.")
                    else:
                        updated_meta = dict(cur_meta)
                        updated_meta["path"]              = new_path
                        updated_meta["path_override"]     = (new_path != (clf_res.get("recommended_path") or "standard"))
                        updated_meta["path_confirmed_by"] = auth.get_current_user()
                        updated_meta["override_note"]     = override_note.strip()
                        memory.save_project_meta(override_pid, updated_meta)
                            # Sync session state if this is the active project
                        if st.session_state.get("active_project_id") == override_pid:
                            st.session_state["active_project_path"] = new_path
                        st.success(f"Path for **{override_pid}** updated to **{new_path.upper()}**.")
                        st.rerun()

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


    # ============================================
    # ADMIN PANEL — Reviewer only
    # ============================================
    if auth.get_current_role() == "reviewer":
        st.divider()
        with st.expander("⚙️ Admin Panel", expanded=False):
            st.caption("Hanya visible untuk Reviewer. Hapus project testing yang tidak diperlukan.")

            all_projects = memory.list_all_projects()
            if not all_projects:
                st.info("Tidak ada project di database.")
            else:
                import pandas as pd
                # Parse states jadi kolom readable
                rows = []
                for p in all_projects:
                    states_raw = p.get("states") or ""
                    has_final = lambda ph: f"{ph}:final" in states_raw
                    rows.append({
                        "Project ID": p["project_id"],
                        "Path": p.get("path", "-"),
                        "DEF": "✅" if has_final("define") else ("🔄" if f"define:draft" in states_raw else "⬜"),
                        "MEA": "✅" if has_final("measure") else ("🔄" if f"measure:draft" in states_raw else "⬜"),
                        "ANA": "✅" if has_final("analyze") else ("🔄" if f"analyze:draft" in states_raw else "⬜"),
                        "IMP": "✅" if has_final("improve") else ("🔄" if f"improve:draft" in states_raw else "⬜"),
                        "CON": "✅" if has_final("control") else ("🔄" if f"control:draft" in states_raw else "⬜"),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("**Hapus Project:**")
            project_ids = [p["project_id"] for p in all_projects]
            pid_to_delete = st.selectbox(
                "Pilih project yang akan dihapus",
                options=["— pilih —"] + project_ids,
                key="admin_delete_select",
            )
            confirm_delete = st.checkbox(
                f"Saya konfirmasi ingin menghapus `{pid_to_delete}` beserta seluruh datanya",
                key="admin_delete_confirm",
                disabled=(pid_to_delete == "— pilih —"),
            )
            if st.button("🗑️ Hapus Project", key="admin_delete_btn",
                         disabled=(pid_to_delete == "— pilih —" or not confirm_delete)):
                ok = memory.delete_project_all_data(pid_to_delete)
                if ok:
                    st.success(f"✅ Project `{pid_to_delete}` berhasil dihapus.")
                    st.rerun()
                else:
                    st.error("Gagal menghapus. Cek log.")

# ============================================
# GUIDELINE TAB — Panduan DMAIC & Quick Improvement
# ============================================
with tab_guide:
    st.subheader("\U0001F4D6 Panduan DMAIC & Quick Improvement")
    st.caption(
        "Saat project dibuat, sistem merekomendasikan salah satu dari dua jalur berdasarkan "
        "5 kriteria klasifikasi. Diagram di bawah menjelaskan tahapan & deliverable utama tiap jalur."
    )

    st.markdown("#### Kapan memakai jalur yang mana?")
    gc1, gc2 = st.columns(2)
    with gc1:
        st.markdown(
            "<div style='background:#eff6ff;border:1.5px solid #3b82f6;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#1e3a8a;margin-bottom:6px;'>\U0001F535 Standard DMAIC</div>"
            "<div style='font-size:0.85rem;color:#0f172a;'>Dampak besar · proses kompleks · lintas fungsi · "
            "butuh data baru · risiko implementasi tinggi.<br><b>Rigor penuh, semua deliverable.</b></div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with gc2:
        st.markdown(
            "<div style='background:#f0fdf4;border:1.5px solid #16a34a;border-radius:10px;padding:12px;'>"
            "<div style='font-weight:700;color:#14532d;margin-bottom:6px;'>\U0001F7E2 Quick Improvement</div>"
            "<div style='font-size:0.85rem;color:#0f172a;'>Masalah sederhana · dampak terbatas · 1–2 area · "
            "data sudah ada · risiko rendah.<br><b>Jalur ramping untuk quick win.</b></div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("")
    st.divider()

    # ---- Standard DMAIC (grid kartu) ----
    st.markdown("### \U0001F535 Standard DMAIC")
    st.caption("Rigor penuh — seluruh deliverable tiap fase wajib dan melewati stage gate.")
    st.markdown(
        "<div style='font-family:ui-sans-serif,system-ui,sans-serif;padding:4px 0;'>"
        "<div style='display:grid;grid-template-columns:repeat(5,1fr);gap:8px;'>"
        "<div style='background:#e0f2fe;border:1.5px solid #38bdf8;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#0369a1;font-size:0.85rem;margin-bottom:6px;'>1. DEFINE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Problem &amp; Goal (SMART)<br>"
        "&bull; VOC/VOB &rarr; CTQ/CTB<br>&bull; SIPOC<br>&bull; Project Charter<br>&bull; Benefit Estimate</div></div>"
        "<div style='background:#d1fae5;border:1.5px solid #34d399;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#065f46;font-size:0.85rem;margin-bottom:6px;'>2. MEASURE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Operational Definition<br>"
        "&bull; MSA (Gage R&amp;R)<br>&bull; Data Collection Plan<br>&bull; Baseline &amp; Process Perf.<br>&bull; Cp / Cpk</div></div>"
        "<div style='background:#ede9fe;border:1.5px solid #a78bfa;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#5b21b6;font-size:0.85rem;margin-bottom:6px;'>3. ANALYZE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Process Map (swimlane)<br>"
        "&bull; Fishbone 6M<br>&bull; 5-Why<br>&bull; Verification Plan &amp; Results<br>&bull; Root Cause</div></div>"
        "<div style='background:#ffedd5;border:1.5px solid #fb923c;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#9a3412;font-size:0.85rem;margin-bottom:6px;'>4. IMPROVE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Must Criteria Matrix<br>"
        "&bull; Potential &amp; Selected Solutions<br>&bull; Pilot Plan<br>&bull; Implementation Plan<br>&bull; Communication Plan</div></div>"
        "<div style='background:#fef9c3;border:1.5px solid #facc15;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#854d0e;font-size:0.85rem;margin-bottom:6px;'>5. CONTROL</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Control Plan<br>"
        "&bull; Control Chart (I-MR)<br>&bull; Monitoring Schedule<br>&bull; Handover Protocol<br>&bull; Sustain &amp; Lessons</div></div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ---- Quick Improvement (grid kartu) ----
    st.divider()
    st.markdown("### \U0001F7E2 Quick Improvement")
    st.caption("Jalur ramping — hanya deliverable inti tiap fase untuk quick win.")
    st.markdown(
        "<div style='font-family:ui-sans-serif,system-ui,sans-serif;padding:4px 0;'>"
        "<div style='display:grid;grid-template-columns:repeat(5,1fr);gap:8px;'>"
        "<div style='background:#e0f2fe;border:1.5px solid #38bdf8;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#0369a1;font-size:0.85rem;margin-bottom:6px;'>1. DEFINE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Problem &amp; Goal (SMART)</div></div>"
        "<div style='background:#d1fae5;border:1.5px solid #34d399;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#065f46;font-size:0.85rem;margin-bottom:6px;'>2. MEASURE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Baseline Chart</div></div>"
        "<div style='background:#ede9fe;border:1.5px solid #a78bfa;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#5b21b6;font-size:0.85rem;margin-bottom:6px;'>3. ANALYZE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Fishbone 3M (Man/Method/Machine)<br>&bull; Root Cause langsung</div></div>"
        "<div style='background:#ffedd5;border:1.5px solid #fb923c;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#9a3412;font-size:0.85rem;margin-bottom:6px;'>4. IMPROVE</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Selected Solutions<br>&bull; Implementation Plan<br>&bull; Communication Plan</div></div>"
        "<div style='background:#fef9c3;border:1.5px solid #facc15;border-radius:10px;padding:12px;'>"
        "<div style='font-weight:700;color:#854d0e;font-size:0.85rem;margin-bottom:6px;'>5. CONTROL</div>"
        "<div style='font-size:0.78rem;color:#0f172a;'>&bull; Control Chart<br>&bull; Monitoring Schedule</div></div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    st.divider()

    # ---- Tabel perbandingan per fase ----
    st.markdown("#### \U0001F4CB Perbandingan per Fase")
    _COMPARISON_ROWS = [
        {"Fase": "DEFINE",  "Standard DMAIC": "Problem & Goal, VOC/VOB→CTQ, SIPOC, Charter, Benefit", "Quick Improvement": "Problem & Goal saja"},
        {"Fase": "MEASURE", "Standard DMAIC": "Op. Definition, MSA, Data Collection, Baseline, Cp/Cpk", "Quick Improvement": "Baseline chart saja"},
        {"Fase": "ANALYZE", "Standard DMAIC": "Process Map, Fishbone 6M, 5-Why, Verification, Root Cause", "Quick Improvement": "Fishbone 3M + root cause langsung"},
        {"Fase": "IMPROVE", "Standard DMAIC": "Must Criteria, Solutions, Pilot, Implementation, Communication", "Quick Improvement": "Solutions, Implementation, Communication"},
        {"Fase": "CONTROL", "Standard DMAIC": "Control Plan, Control Chart, Monitoring, Handover, Sustain", "Quick Improvement": "Control chart + monitoring schedule"},
    ]
    st.table(_COMPARISON_ROWS)
