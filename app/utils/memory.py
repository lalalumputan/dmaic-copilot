"""
memory.py — Cloud-safe persistence layer using SQLite.

Single file (dmaic_memory.db) at repo root.
All public function signatures are backward-compatible with the
old filesystem version — agents and UI require zero changes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path("dmaic_memory.db")


# ======================================================
# DB BOOTSTRAP
# ======================================================

def _turso_cfg():
    """Ambil kredensial Turso dari st.secrets (cloud) atau env (lokal)."""
    url = tok = None
    try:
        import streamlit as st  # type: ignore
        url = st.secrets.get("TURSO_DATABASE_URL")
        tok = st.secrets.get("TURSO_AUTH_TOKEN")
    except Exception:
        pass
    url = url or os.getenv("TURSO_DATABASE_URL")
    tok = tok or os.getenv("TURSO_AUTH_TOKEN")
    return url, tok


class _TursoCursor:
    """Cursor-shim agar .fetchone()/.fetchall() kompatibel sqlite3.
    Row dari libsql-client mendukung akses index DAN nama (row["col"])."""
    def __init__(self, rs):
        self._rs = rs

    def fetchone(self):
        rows = getattr(self._rs, "rows", [])
        return rows[0] if rows else None

    def fetchall(self):
        return list(getattr(self._rs, "rows", []))


class _TursoConn:
    """Facade tipis kompatibel sqlite3.Connection di atas libsql-client
    (Turso). Hanya method yang dipakai memory.py yang diimplementasi:
    execute / executescript / commit / close."""
    def __init__(self, url: str, tok: str):
        import libsql_client  # type: ignore
        http = url.replace("libsql://", "https://")
        self._c = libsql_client.create_client_sync(url=http, auth_token=tok)

    def execute(self, sql: str, params=()):
        if params:
            rs = self._c.execute(sql, list(params))
        else:
            rs = self._c.execute(sql)
        return _TursoCursor(rs)

    def executescript(self, script: str):
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        if stmts:
            self._c.batch(stmts)
        return _TursoCursor(None)

    def commit(self):
        # libsql-client auto-commit per statement/batch — no-op
        pass

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


def _conn():
    """Turso (persisten) jika kredensial tersedia, selain itu SQLite lokal."""
    url, tok = _turso_cfg()
    if url and tok:
        try:
            return _TursoConn(url, tok)
        except Exception:
            # gagal konek Turso -> fallback ke SQLite lokal agar app tetap jalan
            pass
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def _db():
    con = _conn()
    try:
        _ensure_schema(con)
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS phase_states (
            project_id  TEXT NOT NULL,
            phase       TEXT NOT NULL,
            kind        TEXT NOT NULL,
            payload     TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (project_id, phase, kind)
        );

        CREATE TABLE IF NOT EXISTS global_episodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            phase       TEXT NOT NULL,
            industry    TEXT,
            pain_theme  TEXT,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_episodes_lookup
            ON global_episodes (phase, industry, pain_theme);
                      
        CREATE TABLE IF NOT EXISTS project_meta (
            project_id   TEXT PRIMARY KEY,
            path         TEXT NOT NULL DEFAULT 'standard',
            payload      TEXT NOT NULL DEFAULT '{}',
            updated_at   TEXT NOT NULL
        );
    """)


# ======================================================
# INTERNAL HELPERS
# ======================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_state(project_id: str, phase: str, kind: str, state: Dict[str, Any]) -> None:
    with _db() as con:
        con.execute(
            """
            INSERT INTO phase_states (project_id, phase, kind, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, phase, kind) DO UPDATE SET
                payload    = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (project_id, phase, kind, json.dumps(state, ensure_ascii=False), _now()),
        )


def _load_state(project_id: str, phase: str, kind: str) -> Optional[Dict[str, Any]]:
    with _db() as con:
        row = con.execute(
            "SELECT payload FROM phase_states WHERE project_id=? AND phase=? AND kind=?",
            (project_id, phase, kind),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except Exception:
        return None


def _delete_state(project_id: str, phase: str, kind: str) -> None:
    with _db() as con:
        con.execute(
            "DELETE FROM phase_states WHERE project_id=? AND phase=? AND kind=?",
            (project_id, phase, kind),
        )


def _save_episode(phase: str, state: Dict[str, Any]) -> None:
    inputs = state.get("inputs") or {}
    meta   = state.get("meta") or {}
    with _db() as con:
        con.execute(
            """
            INSERT INTO global_episodes (phase, industry, pain_theme, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                phase,
                inputs.get("industry") or meta.get("industry"),
                inputs.get("pain_theme") or meta.get("pain_theme"),
                json.dumps(state, ensure_ascii=False),
                _now(),
            ),
        )


def _retrieve_episodes(
    phase: str,
    industry: Optional[str],
    pain_theme: Optional[str],
    k: int = 3,
) -> List[Dict[str, Any]]:
    with _db() as con:
        rows = con.execute(
            "SELECT payload, industry, pain_theme FROM global_episodes "
            "WHERE phase=? ORDER BY id DESC LIMIT 200",
            (phase,),
        ).fetchall()

    def score(r) -> int:
        s = 0
        if industry   and r["industry"]   == industry:   s += 2
        if pain_theme and r["pain_theme"] == pain_theme: s += 1
        return s

    ranked = sorted(rows, key=score, reverse=True)
    results = []
    for r in ranked[:k]:
        try:
            results.append(json.loads(r["payload"]))
        except Exception:
            pass
    return results


# ======================================================
# PROJECT REGISTRY
# ======================================================

def list_define_projects_final() -> List[str]:
    with _db() as con:
        rows = con.execute(
            "SELECT project_id FROM phase_states "
            "WHERE phase='define' AND kind='final' ORDER BY project_id"
        ).fetchall()
    return [r["project_id"] for r in rows]


def next_define_project_id(prefix: str = "project_", width: int = 3) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    with _db() as con:
        rows = con.execute(
            "SELECT project_id FROM phase_states WHERE phase='define'"
        ).fetchall()
    nums = [int(m.group(1)) for r in rows if (m := pattern.match(r["project_id"]))]
    return f"{prefix}{(max(nums) + 1) if nums else 1:0{width}d}"


def define_project_exists(project_id: str) -> bool:
    with _db() as con:
        row = con.execute(
            "SELECT 1 FROM phase_states WHERE project_id=? AND phase='define' LIMIT 1",
            (project_id,),
        ).fetchone()
    return row is not None

def save_project_meta(project_id: str, meta: Dict[str, Any]) -> None:
    """
    Simpan project-level metadata: path + classification result.
    meta = {
        "path": "standard" | "quick",
        "classification": { ... result dari classification_agent ... },
        "path_confirmed_by": "reviewer" | None,
        "path_override": True | False,
    }
    """
    with _db() as con:
        con.execute(
            """
            INSERT INTO project_meta (project_id, path, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                path       = excluded.path,
                payload    = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                meta.get("path", "standard"),
                json.dumps(meta, ensure_ascii=False),
                _now(),
            ),
        )


def save_project_leader_name(project_id: str, display_name: str) -> None:
    """Simpan display_name project leader ke project_meta."""
    meta = load_project_meta(project_id)
    meta["project_leader_name"] = display_name
    save_project_meta(project_id, meta)


def load_project_meta(project_id: str) -> Dict[str, Any]:
    """
    Load project metadata. Returns default jika belum ada.
    """
    with _db() as con:
        row = con.execute(
            "SELECT payload FROM project_meta WHERE project_id=?",
            (project_id,),
        ).fetchone()
    if row is None:
        return {"path": "standard", "classification": None, "path_override": False}
    try:
        return json.loads(row["payload"])
    except Exception:
        return {"path": "standard", "classification": None, "path_override": False}


# ======================================================
# PROJECT TIMELINE (tanggal aktual selesai tiap fase)
# ======================================================

def save_project_timeline(project_id: str, timeline: Dict[str, Any]) -> None:
    """timeline = {"define": "YYYY-MM-DD", "measure": ..., ...} (tanggal aktual selesai fase)."""
    _upsert_state(project_id, "project", "timeline_actual", timeline or {})

def load_project_timeline(project_id: str) -> Dict[str, Any]:
    return _load_state(project_id, "project", "timeline_actual") or {}


# ======================================================
# DEFINE
# ======================================================

def save_define_draft(project_id: str, define_state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "define", "draft", define_state)

def load_define_draft(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "define", "draft")

def save_define_final(project_id: str, define_state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "define", "final", define_state)

def load_define_final(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "define", "final")

def delete_define_draft(project_id: str) -> None:
    _delete_state(project_id, "define", "draft")

def save_define_episode(project_id: str, define_state: Dict[str, Any], feedback=None, **kwargs) -> None:
    _save_episode("define", define_state)

def retrieve_similar_define_episodes(industry: Optional[str], pain_theme: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
    return _retrieve_episodes("define", industry, pain_theme, k)


# ======================================================
# MEASURE
# ======================================================

def save_measure_draft(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "measure", "draft", state)

def load_measure_draft(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "measure", "draft")

def save_measure_final(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "measure", "final", state)

def load_measure_final(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "measure", "final")

def delete_measure_draft(project_id: str) -> None:
    _delete_state(project_id, "measure", "draft")

def save_measure_wip(project_id: str, state: Dict[str, Any]) -> None:
    """Work-in-progress Measure inputs (op-defs + MSA) — durable tanpa harus Generate."""
    _upsert_state(project_id, "measure", "wip", state)

def load_measure_wip(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "measure", "wip")

def save_measure_episode(project_id: str, state: Dict[str, Any], feedback=None) -> None:
    _save_episode("measure", state)

def retrieve_similar_measure_episodes(industry: Optional[str], pain_theme: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
    return _retrieve_episodes("measure", industry, pain_theme, k)


# ======================================================
# ANALYZE
# ======================================================

def save_analyze_draft(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "analyze", "draft", state)

def load_analyze_draft(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "analyze", "draft")

def save_analyze_final(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "analyze", "final", state)

def load_analyze_final(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "analyze", "final")

def delete_analyze_draft(project_id: str) -> None:
    _delete_state(project_id, "analyze", "draft")

def save_analyze_episode(project_id: str, state: Dict[str, Any], feedback=None) -> None:
    _save_episode("analyze", state)

def retrieve_similar_analyze_episodes(industry: Optional[str], pain_theme: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
    return _retrieve_episodes("analyze", industry, pain_theme, k)


# ======================================================
# IMPROVE
# ======================================================

def save_improve_draft(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "improve", "draft", state)

def load_improve_draft(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "improve", "draft")

def save_improve_final(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "improve", "final", state)

def load_improve_final(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "improve", "final")

def save_improve_wip(project_id: str, state: Dict[str, Any]) -> None:
    """Work-in-progress Improve (mis. bukti hasil implementasi) — durable."""
    _upsert_state(project_id, "improve", "wip", state)

def load_improve_wip(project_id: str) -> Dict[str, Any]:
    return _load_state(project_id, "improve", "wip") or {}

def delete_improve_draft(project_id: str) -> None:
    _delete_state(project_id, "improve", "draft")

def save_improve_episode(project_id: str, state: Dict[str, Any], feedback=None) -> None:
    _save_episode("improve", state)

def retrieve_similar_improve_episodes(industry: Optional[str], pain_theme: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
    return _retrieve_episodes("improve", industry, pain_theme, k)


# ======================================================
# CONTROL
# ======================================================

def save_control_draft(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "control", "draft", state)

def load_control_draft(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "control", "draft")

def save_control_final(project_id: str, state: Dict[str, Any]) -> None:
    _upsert_state(project_id, "control", "final", state)

def load_control_final(project_id: str) -> Optional[Dict[str, Any]]:
    return _load_state(project_id, "control", "final")

def delete_control_draft(project_id: str) -> None:
    _delete_state(project_id, "control", "draft")

def save_control_episode(project_id: str, state: Dict[str, Any], feedback=None) -> None:
    _save_episode("control", state)

def retrieve_similar_control_episodes(industry: Optional[str], pain_theme: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
    return _retrieve_episodes("control", industry, pain_theme, k)

# ======================================================
# GOVERNANCE — APPROVAL TRACKING
# ======================================================

def save_approval(
    project_id: str,
    phase: str,
    role: str,
    action: str,          # 'approve' | 'reject'
    note: str = "",
    display_name: str = "",
) -> None:
    """Simpan approval/rejection dari reviewer atau champion."""
    with _db() as con:
        con.execute(
            """
            INSERT INTO phase_states (project_id, phase, kind, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, phase, kind) DO UPDATE SET
                payload    = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                phase,
                f"approval_{role}",
                json.dumps({
                    "role":         role,
                    "action":       action,
                    "note":         note,
                    "display_name": display_name,
                    "timestamp":    _now(),
                    "project_id":   project_id,
                    "phase":        phase,
                }, ensure_ascii=False),
                _now(),
            ),
        )


def load_approval(project_id: str, phase: str, role: str) -> Optional[Dict[str, Any]]:
    """Load approval untuk role tertentu di fase tertentu."""
    return _load_state(project_id, phase, f"approval_{role}")

# Urutan fase untuk sequential gate
_PHASES_ORDER = ["define", "measure", "analyze", "improve", "control"]


def _prev_phase_can_advance(project_id: str, phase: str) -> bool:
    """
    Sequential gate helper: True jika fase sebelumnya sudah can_advance.
    DEFINE selalu True (tidak ada fase sebelumnya).
    """
    idx = _PHASES_ORDER.index(phase)
    if idx == 0:
        return True
    return get_phase_approval_status(project_id, _PHASES_ORDER[idx - 1]).get("can_advance", False)


def get_phase_approval_status(project_id: str, phase: str) -> Dict[str, Any]:
    """
    Sequential approval governance dengan aturan baru.

    Berlaku untuk kedua path:
      DEFINE   — Reviewer saja (gate awal project, tanpa dependensi fase sebelumnya).
                 Champion TIDAK wajib di Define — Champion hanya mandatory di Control.
      CONTROL  — Reviewer + Champion (kedua path; gate penutup project, requires IMPROVE approved)

    Standard path fase tengah (measure, analyze, improve):
      Reviewer only, requires fase sebelumnya approved.

    Quick path fase tengah (measure, analyze, improve):
      Auto-advance, tapi hanya jika fase sebelumnya sudah approved.
    """
    project_path = load_project_meta(project_id).get("path", "standard")

    reviewer = load_approval(project_id, phase, "reviewer") or {}
    champion = load_approval(project_id, phase, "champion") or {}

    reviewer_approved = reviewer.get("action") == "approve"
    champion_approved = champion.get("action") == "approve"

    prev_ok = _prev_phase_can_advance(project_id, phase)

    if phase == "define":
        # Reviewer saja; Champion hanya mandatory di CONTROL. Tanpa sequential dependency.
        can_advance        = reviewer_approved
        required_approvers = ["reviewer"]
        auto_advance       = False
    elif phase == "control":
        # Reviewer + Champion wajib untuk KEDUA path (gate penutup project)
        can_advance        = prev_ok and reviewer_approved and champion_approved
        required_approvers = ["reviewer", "champion"]
        auto_advance       = False
    else:  # measure, analyze, improve
        if project_path == "quick":
            can_advance        = prev_ok   # auto, tapi tergantung fase sebelumnya
            required_approvers = []
            auto_advance       = True
        else:
            can_advance        = prev_ok and reviewer_approved
            required_approvers = ["reviewer"]
            auto_advance       = False

    return {
        "reviewer": {
            "action":       reviewer.get("action"),
            "note":         reviewer.get("note", ""),
            "timestamp":    reviewer.get("timestamp"),
            "display_name": reviewer.get("display_name", ""),
        },
        "champion": {
            "action":       champion.get("action"),
            "note":         champion.get("note", ""),
            "timestamp":    champion.get("timestamp"),
            "display_name": champion.get("display_name", ""),
        },
        "can_advance":        can_advance,
        "required_approvers": required_approvers,
        "auto_advance":       auto_advance,
        "prev_approved":      prev_ok,
    }


def reset_approvals(project_id: str, phase: str) -> None:
    """Reset semua approval di fase ini (misal kalau draft direvisi ulang)."""
    for role in ("reviewer", "champion"):
        _delete_state(project_id, phase, f"approval_{role}")


# ======================================================
# ADMIN — PROJECT CLEANUP
# ======================================================

def delete_project_all_data(project_id: str) -> bool:
    """
    Admin-only: hapus semua data project dari semua tabel.
    Tidak ada UI — hanya dipanggil dari admin_cleanup.py.
    """
    try:
        with _db() as con:
            con.execute("DELETE FROM phase_states WHERE project_id=?", (project_id,))
            con.execute("DELETE FROM project_meta WHERE project_id=?", (project_id,))
            # global_episodes tidak punya project_id — dibiarkan (cross-project knowledge)
        return True
    except Exception as e:
        print(f"[delete_project] Error: {e}")
        return False


def copy_project(source_pid: str, dest_pid: str, reset_approvals: bool = True) -> dict:
    """
    Salin semua data project dari source_pid ke dest_pid.
    Berguna untuk testing tanpa harus re-input data dari awal.

    Args:
        source_pid: Project ID sumber
        dest_pid: Project ID tujuan (harus belum ada)
        reset_approvals: Jika True, skip semua approval rows

    Returns:
        {"copied_rows": int, "skipped": list}  atau  {"error": str}
    """
    import json as _json
    try:
        with _db() as con:
            # 1. Cek dest_pid belum ada
            existing = con.execute(
                "SELECT 1 FROM phase_states WHERE project_id=? LIMIT 1", (dest_pid,)
            ).fetchone()
            if existing:
                return {"error": f"Project '{dest_pid}' sudah ada di database."}

            # 2. Ambil semua phase_states dari source
            rows = con.execute(
                "SELECT phase, kind, payload FROM phase_states WHERE project_id=?",
                (source_pid,)
            ).fetchall()
            if not rows:
                return {"error": f"Project '{source_pid}' tidak ditemukan atau tidak ada data."}

            skipped = []
            copied = 0
            for row in rows:
                phase = row["phase"]
                kind  = row["kind"]
                payload_str = row["payload"]

                # Skip approval rows jika reset_approvals=True
                if reset_approvals and "approval" in kind:
                    skipped.append(f"{phase}:{kind}")
                    continue

                # Update project_id di dalam payload JSON jika ada
                try:
                    payload = _json.loads(payload_str)
                    if isinstance(payload, dict) and payload.get("project_id"):
                        payload["project_id"] = dest_pid
                        payload_str = _json.dumps(payload, ensure_ascii=False)
                except Exception:
                    pass  # bukan JSON valid, salin apa adanya

                con.execute(
                    """INSERT OR REPLACE INTO phase_states
                       (project_id, phase, kind, payload, updated_at)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (dest_pid, phase, kind, payload_str)
                )
                copied += 1

            # 3. Salin project_meta
            meta_row = con.execute(
                "SELECT path, payload FROM project_meta WHERE project_id=?",
                (source_pid,)
            ).fetchone()
            if meta_row:
                try:
                    meta_payload = _json.loads(meta_row["payload"])
                    meta_payload["copied_from"] = source_pid
                    meta_payload_str = _json.dumps(meta_payload, ensure_ascii=False)
                except Exception:
                    meta_payload_str = meta_row["payload"]

                con.execute(
                    """INSERT OR REPLACE INTO project_meta
                       (project_id, path, payload, updated_at)
                       VALUES (?, ?, ?, datetime('now'))""",
                    (dest_pid, meta_row["path"], meta_payload_str)
                )

        return {"copied_rows": copied, "skipped": skipped}

    except Exception as e:
        return {"error": f"Copy gagal: {e}"}


def list_all_projects() -> list:
    """Admin: list semua project dengan status per fase."""
    with _db() as con:
        rows = con.execute(
            """
            SELECT ps.project_id, pm.path,
                   GROUP_CONCAT(ps.phase || ':' || ps.kind, ',') as states
            FROM phase_states ps
            LEFT JOIN project_meta pm ON ps.project_id = pm.project_id
            GROUP BY ps.project_id
            ORDER BY ps.project_id
            """
        ).fetchall()
    return [dict(r) for r in rows]

def save_analyze_wip(project_id: str, inputs: dict) -> None:
    """Save raw Analyze inputs WIP (no LLM, no gate) for persistence across reloads."""
    _upsert_state(project_id, "analyze", "wip", inputs)

def load_analyze_wip(project_id: str) -> dict:
    """Load Analyze WIP inputs."""
    return _load_state(project_id, "analyze", "wip") or {}

def save_control_wip(project_id: str, inputs: dict) -> None:
    """Save raw Control inputs WIP (no LLM, no gate) for persistence across reloads."""
    _upsert_state(project_id, "control", "wip", inputs)

def load_control_wip(project_id: str) -> dict:
    """Load Control WIP inputs."""
    return _load_state(project_id, "control", "wip") or {}