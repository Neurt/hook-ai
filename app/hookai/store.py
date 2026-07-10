"""Session persistence — SQLite-backed store for the chat backend.

Sessions (profile + cv_text + last job list) survive container restarts, unlike
the previous in-process dict. Stdlib sqlite3 only, one file, thread-safe via a
process lock (fine for the single-node MVP; swap for Redis/Postgres at scale).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

from .profile import Profile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid        TEXT PRIMARY KEY,
    profile    TEXT,
    cv_text    TEXT NOT NULL DEFAULT '',
    jobs       TEXT NOT NULL DEFAULT '[]',
    history    TEXT NOT NULL DEFAULT '[]',
    search     TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);
"""

MAX_HISTORY = 12  # keep the last N chat turns per session


def _empty() -> dict[str, Any]:
    return {"profile": None, "cv_text": "", "jobs": [], "history": [], "search": {}}


class SessionStore:
    def __init__(self, path: str, max_sessions: int = 500):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._db.execute(_SCHEMA)
            for ddl in (  # migrate older databases in place
                "ALTER TABLE sessions ADD COLUMN history TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE sessions ADD COLUMN search TEXT NOT NULL DEFAULT '{}'",
            ):
                try:
                    self._db.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already exists
            self._db.commit()

    def load(self, sid: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT profile, cv_text, jobs, history, search FROM sessions WHERE sid = ?", (sid,)
            ).fetchone()
        if row is None:
            return _empty()
        profile_json, cv_text, jobs_json, history_json, search_json = row
        try:
            profile: Optional[Profile] = (
                Profile.from_dict(json.loads(profile_json)) if profile_json else None
            )
        except Exception:  # corrupt row shouldn't kill the session
            profile = None
        try:
            jobs = json.loads(jobs_json) or []
        except Exception:
            jobs = []
        try:
            history = json.loads(history_json) or []
        except Exception:
            history = []
        try:
            search = json.loads(search_json) or {}
        except Exception:
            search = {}
        return {"profile": profile, "cv_text": cv_text or "", "jobs": jobs,
                "history": history, "search": search}

    def save(self, sid: str, sess: dict[str, Any]) -> None:
        profile = sess.get("profile")
        profile_json = json.dumps(profile.to_dict()) if isinstance(profile, Profile) else None
        history = (sess.get("history") or [])[-MAX_HISTORY:]
        with self._lock:
            self._db.execute(
                "INSERT INTO sessions (sid, profile, cv_text, jobs, history, search, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sid) DO UPDATE SET profile=excluded.profile, "
                "cv_text=excluded.cv_text, jobs=excluded.jobs, history=excluded.history, "
                "search=excluded.search, updated_at=excluded.updated_at",
                (sid, profile_json, sess.get("cv_text", ""),
                 json.dumps(sess.get("jobs", [])), json.dumps(history),
                 json.dumps(sess.get("search", {})), time.time()),
            )
            # Evict oldest sessions beyond the cap so the DB can't grow unbounded.
            self._db.execute(
                "DELETE FROM sessions WHERE sid IN ("
                "  SELECT sid FROM sessions ORDER BY updated_at DESC "
                f"  LIMIT -1 OFFSET {int(self.max_sessions)})"
            )
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()
