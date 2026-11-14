"""Local SQLite history, audit log and per-session rate limiting."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DB = Path(os.environ.get("STUDIO_DB", Path.home() / ".sherlock_studio" / "history.db"))


def _c():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY, ts REAL, kind TEXT, query TEXT, summary TEXT, data TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS audit(
        id INTEGER PRIMARY KEY, ts REAL, kind TEXT, query TEXT, purpose TEXT)""")
    return c


def save_run(kind, query, summary: dict, data: list[dict]):
    with _c() as c:
        c.execute("INSERT INTO runs(ts,kind,query,summary,data) VALUES(?,?,?,?,?)",
                  (time.time(), kind, query, json.dumps(summary), json.dumps(data)))


def list_runs(kind=None, query=None, limit=100):
    sql, args = "SELECT id,ts,kind,query,summary FROM runs WHERE 1=1", []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if query:
        sql += " AND query=?"; args.append(query)
    sql += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
    with _c() as c:
        return [{"id": r[0], "ts": r[1], "kind": r[2], "query": r[3], "summary": json.loads(r[4])}
                for r in c.execute(sql, args)]


def get_run(run_id):
    with _c() as c:
        r = c.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
    return json.loads(r[0]) if r else []


def diff_runs(old: list[dict], new: list[dict]):
    o = {r["site"] for r in old if r.get("status") == "Found"}
    n = {r["site"] for r in new if r.get("status") == "Found"}
    return sorted(n - o), sorted(o - n)


def audit(kind, query, purpose):
    with _c() as c:
        c.execute("INSERT INTO audit(ts,kind,query,purpose) VALUES(?,?,?,?)",
                  (time.time(), kind, query, purpose))


def audit_log(limit=200):
    with _c() as c:
        return [{"ts": r[0], "kind": r[1], "query": r[2], "purpose": r[3]}
                for r in c.execute("SELECT ts,kind,query,purpose FROM audit ORDER BY ts DESC LIMIT ?", (limit,))]


def clear_all():
    with _c() as c:
        c.execute("DELETE FROM runs"); c.execute("DELETE FROM audit")


def rate_ok(timestamps: list[float], limit: int, window=3600) -> tuple[bool, list[float]]:
    now = time.time()
    ts = [t for t in timestamps if now - t < window]
    if len(ts) >= limit:
        return False, ts
    ts.append(now)
    return True, ts
