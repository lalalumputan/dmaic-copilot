import os
import json
import html as _html
import streamlit as st
import streamlit.components.v1 as components


def compact_dot(dot_code: str, threshold: int = 10) -> str:
    """
    No-op (dinonaktifkan): mengubah rankdir LR→TB pada swimlane membuat lane
    horizontal jadi kacau & terpotong di Word. Diagram dibiarkan apa adanya (LR).
    Disisakan sebagai pass-through agar call-site tidak perlu diubah.
    """
    return dot_code


def render_sidebar_header():
    logo_path = "app/assets/sidebar_logo.png"
    if os.path.exists(logo_path):
        st.logo(logo_path, size="large", link=None)


def render_phase_end_date(pid: str, phase: str, disabled: bool = False) -> bool:
    """
    Input 'Tanggal aktual selesai fase' — persist ke project timeline.
    Return True bila tanggal sudah terisi (dipakai untuk gating Finalize di Standard path).
    """
    import datetime as _dt
    from app.utils import memory as _mem

    _pt  = _mem.load_project_timeline(pid) or {}
    _cur = _pt.get(phase)
    _val = None
    if _cur:
        try:
            _val = _dt.date.fromisoformat(str(_cur))
        except Exception:
            _val = None

    _d = st.date_input(
        "📅 Tanggal aktual selesai fase",
        value=_val,
        key=f"actual_end_{phase}_{pid}",
        disabled=disabled,
        format="YYYY-MM-DD",
        help="Tanggal aktual fase ini diselesaikan. Akan direkap di Project Charter (Define).",
    )
    if _d is not None:
        _iso = _d.isoformat()
        if _pt.get(phase) != _iso:
            _pt[phase] = _iso
            _mem.save_project_timeline(pid, _pt)
    return bool((_mem.load_project_timeline(pid) or {}).get(phase))


def render_tab_quicknav(tab_labels, title="⚡ Navigasi Cepat"):
    """
    Panel shortcut di sidebar untuk loncat ke tab tanpa scroll ke atas.

    Operasi normal: SATU klik → langsung pindah tab + auto-scroll ke tab tsb.
    Degradasi anggun: jika tombol tab tidak terdeteksi (mis. struktur Streamlit
    berubah karena upgrade), tombol otomatis hanya men-scroll ke deretan tab —
    tidak pernah error.
    """
    labels = [str(x) for x in (tab_labels or [])]
    if not labels:
        return

    labels_json = json.dumps(labels, ensure_ascii=False)
    buttons_html = "".join(
        f'<button class="qn-btn" onclick="qnJump({i})" title="{_html.escape(lbl, quote=True)}">'
        f'{_html.escape(lbl)}</button>'
        for i, lbl in enumerate(labels)
    )
    # Tinggi iframe disesuaikan jumlah tombol (≈38px/tombol + padding)
    height = len(labels) * 40 + 16

    snippet = f"""
<!DOCTYPE html>
<html>
<head>
<style>
  html, body {{ margin: 0; padding: 0; background: transparent; }}
  .qn-wrap {{
    display: flex; flex-direction: column; gap: 6px;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .qn-btn {{
    display: block; width: 100%; text-align: left;
    padding: 7px 11px;
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px;
    color: #0f172a; font-size: 0.82rem; font-weight: 600;
    cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    transition: background 0.12s, border-color 0.12s;
  }}
  .qn-btn:hover {{ background: #e0f2fe; border-color: #38bdf8; }}
  .qn-btn:active {{ background: #bae6fd; }}
</style>
</head>
<body>
  <div class="qn-wrap">{buttons_html}</div>
<script>
  const QN_LABELS = {labels_json};

  function qnFindTabs() {{
    const doc = window.parent.document;
    let tabs = doc.querySelectorAll('button[data-baseweb="tab"]');
    if (!tabs.length) tabs = doc.querySelectorAll('button[role="tab"]');
    if (!tabs.length) tabs = doc.querySelectorAll('[role="tab"]');
    return tabs;
  }}

  function qnScrollTop() {{
    // Fallback: bawa deretan tab ke tampilan
    const doc = window.parent.document;
    const tabs = qnFindTabs();
    if (tabs && tabs.length) {{
      tabs[0].scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      return;
    }}
    const main = doc.querySelector('section.main')
              || doc.querySelector('[data-testid="stMain"]')
              || doc.querySelector('[data-testid="stAppViewContainer"]');
    if (main) {{ try {{ main.scrollTo({{ top: 0, behavior: 'smooth' }}); }} catch (e) {{}} }}
    try {{ window.parent.scrollTo({{ top: 0, behavior: 'smooth' }}); }} catch (e) {{}}
  }}

  function qnJump(i) {{
    const want = (QN_LABELS[i] || "").trim();
    const tabs = qnFindTabs();
    if (tabs && tabs.length && want) {{
      for (const t of tabs) {{
        const txt = (t.innerText || t.textContent || "").trim();
        if (txt === want || txt.indexOf(want) >= 0 || (txt && want.indexOf(txt) >= 0)) {{
          t.click();
          t.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
          return;
        }}
      }}
    }}
    // Degradasi anggun — tidak pernah error
    qnScrollTop();
  }}
</script>
</body>
</html>
""".strip()

    with st.sidebar:
        st.markdown(f"**{title}**")
        components.html(snippet, height=height, scrolling=False)
