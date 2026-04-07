"""
memory.py — Cloud-safe persistence layer using SQLite.

Single file (dmaic_memory.db) at repo root.
All public function signatures are backward-compatible with the
old filesystem version — agents and UI require zero changes.
"""

from __future__ import annotations

import json
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

def _conn() -> sqlite3.Connection:
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
