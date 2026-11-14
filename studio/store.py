"""Local SQLite history, audit log and per-session rate limiting."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB = Path(os.environ.get("STUDIO_DB", Path.home() / ".sherlock_studio" / "history.db"))


@contextmanager
def conn():
    """Open, commit and always close (sqlite3's own context manager only commits)."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY, ts REAL, kind TEXT, query TEXT, summary TEXT, data TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY, ts REAL, kind TEXT, query TEXT, purpose TEXT)""")
        yield c
        c.commit()
    finally:
        c.close()


def scope_key(scope: dict) -> str:
    """Stable id for the settings a run used (engines, categories, NSFW, verification...)."""
    return hashlib.sha1(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:12]


def save_run(kind, query, summary: dict, data: list[dict], scope: dict | None = None):
    summary = dict(summary)
    if scope is not None:
        summary["scope"], summary["scope_key"] = scope, scope_key(scope)
    with conn() as c:
        c.execute("INSERT INTO runs(ts,kind,query,summary,data) VALUES(?,?,?,?,?)",
                  (time.time(), kind, query, json.dumps(summary), json.dumps(data)))


def list_runs(kind=None, query=None, limit=100):
    sql, args = "SELECT id,ts,kind,query,summary FROM runs WHERE 1=1", []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if query:
        sql += " AND query=?"; args.append(query)
    sql += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
    with conn() as c:
        return [{"id": r[0], "ts": r[1], "kind": r[2], "query": r[3], "summary": json.loads(r[4])}
                for r in c.execute(sql, args)]


def last_run(kind, query, scope: dict | None = None):
    """Most recent run for `query`; if scope is given, only a run that used identical settings."""
    runs = list_runs(kind, query, 50)
    if scope is None:
        return runs[0] if runs else None
    key = scope_key(scope)
    return next((r for r in runs if r["summary"].get("scope_key") == key), None)


def get_run(run_id):
    with conn() as c:
        r = c.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
    return json.loads(r[0]) if r else []


def diff_runs(old: list[dict], new: list[dict]):
    o = {r["site"] for r in old if r.get("status") == "Found"}
    n = {r["site"] for r in new if r.get("status") == "Found"}
    return sorted(n - o), sorted(o - n)


def audit(kind, query, purpose):
    with conn() as c:
        c.execute("INSERT INTO audit(ts,kind,query,purpose) VALUES(?,?,?,?)",
                  (time.time(), kind, query, purpose))


def audit_log(limit=200):
    with conn() as c:
        return [{"ts": r[0], "kind": r[1], "query": r[2], "purpose": r[3]}
                for r in c.execute("SELECT ts,kind,query,purpose FROM audit ORDER BY ts DESC LIMIT ?", (limit,))]


def clear_all():
    with conn() as c:
        c.execute("DELETE FROM runs"); c.execute("DELETE FROM audit")


def rate_ok(timestamps: list[float], limit: int, window=3600, cost: int = 1) -> tuple[bool, list[float]]:
    """Charge `cost` searches against the hourly limit. Denied requests consume nothing."""
    now = time.time()
    ts = [t for t in timestamps if now - t < window]
    if len(ts) + cost > limit:
        return False, ts
    ts.extend([now] * cost)
    return True, ts
