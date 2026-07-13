"""Tiny SQLite persistence for operator-mutable state (opt-in).

Only the state an *operator* changes and expects to survive a restart is persisted:
  * OEM partners + their granted scopes (governance), and
  * per-robot visual-navigation waypoints.

Live fleet motion is simulator-driven and intentionally ephemeral — it re-seeds on start.

Activated by setting ``ORBITAL_DB_PATH`` (e.g. ``/data/orbital.db`` on a Fly volume). When
unset, every function is a no-op and the service runs purely in-memory (local dev + tests).
Uses only the stdlib ``sqlite3`` — no new dependencies.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()


def db_path() -> str | None:
    return (os.getenv("ORBITAL_DB_PATH") or "").strip() or None


def enabled() -> bool:
    return db_path() is not None


def _connect() -> sqlite3.Connection:
    path = db_path()
    assert path is not None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    if not enabled():
        return
    with _lock, _connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS oem_partners (id TEXT PRIMARY KEY, key_hash TEXT, data TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS waypoints (robot_id TEXT PRIMARY KEY, data TEXT)")


# ── OEM partners ──────────────────────────────────────────────────────────────
def save_oem(oem_id: str, key_hash: str, data_json: str) -> None:
    if not enabled():
        return
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO oem_partners (id, key_hash, data) VALUES (?, ?, ?)",
            (oem_id, key_hash, data_json),
        )


def load_oems() -> list[tuple[str, str, str]]:
    if not enabled():
        return []
    with _lock, _connect() as conn:
        return list(conn.execute("SELECT id, key_hash, data FROM oem_partners").fetchall())


def delete_oem(oem_id: str) -> None:
    if not enabled():
        return
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM oem_partners WHERE id = ?", (oem_id,))


# ── Waypoints ─────────────────────────────────────────────────────────────────
def save_waypoints(robot_id: str, points: list[list[float]]) -> None:
    if not enabled():
        return
    with _lock, _connect() as conn:
        if points:
            conn.execute(
                "INSERT OR REPLACE INTO waypoints (robot_id, data) VALUES (?, ?)",
                (robot_id, json.dumps(points)),
            )
        else:
            conn.execute("DELETE FROM waypoints WHERE robot_id = ?", (robot_id,))


def load_waypoints() -> dict[str, list[list[float]]]:
    if not enabled():
        return {}
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT robot_id, data FROM waypoints").fetchall()
    return {rid: json.loads(data) for rid, data in rows}
