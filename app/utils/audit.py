"""
audit.py — Cloud-safe audit log using the same SQLite DB as memory.py.
Public API is backward-compatible with the old JSON-file version.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path("dmaic_memory.db")


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
        CREATE TABLE IF NOT EXISTS audit_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            project_id  TEXT NOT NULL,
            phase       TEXT NOT NULL,
            status      TEXT NOT NULL,
            meta        TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_audit_project
            ON audit_events (project_id, phase);
        CREATE INDEX IF NOT EXISTS idx_audit_phase
            ON audit_events (phase, status);
    """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except Exception:
        d["meta"] = {}
    return d


# ======================================================
# PUBLIC API
# ======================================================

def log_phase_event(
    project_id: str,
    phase: str,
    status: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO audit_events (timestamp, project_id, phase, status, meta) "
            "VALUES (?,?,?,?,?)",
            (_now(), project_id, phase, status,
             json.dumps(meta or {}, ensure_ascii=False)),
        )


def get_recent_events(
    project_id: Optional[str] = None,
    phase: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM audit_events"
    params: List[Any] = []
    clauses: List[str] = []

    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if phase:
        clauses.append("LOWER(phase) = LOWER(?)")
        params.append(phase)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with _db() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_similar_audit_events(
    industry: Optional[str] = None,
    pain_theme: Optional[str] = None,
    process_area: Optional[str] = None,
    phase: str = "Define",
    k: int = 5,
) -> List[Dict[str, Any]]:
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM audit_events WHERE LOWER(phase)=LOWER(?) "
            "ORDER BY id DESC LIMIT 500",
            (phase,),
        ).fetchall()

    def score(r: sqlite3.Row) -> int:
        try:
            m = json.loads(r["meta"])
        except Exception:
            m = {}
        s = 0
        if industry     and m.get("industry")     == industry:     s += 3
        if pain_theme   and m.get("pain_theme")   == pain_theme:   s += 2
        if process_area and m.get("process_area") == process_area: s += 1
        if (r["status"] or "").lower() in ("final", "finalized", "success"):
            s += 1
        return s

    ranked = sorted(rows, key=score, reverse=True)
    return [_row_to_dict(r) for r in ranked[:k]]
