"""Pull-based job queue for free external compute (Google Colab).

A free Colab runtime has no inbound networking and no stable address, so a
worker inside a notebook cannot be connected to. It polls instead: claim a
job, run it, post the result back, every request outbound.

Note there is now also an official CLI (googlecolab/google-colab-cli) that
drives Colab from outside, which would allow direct dispatch instead. It is
not used here yet because whether it works on the free tier is unverified;
this queue is the path that has actually been tested end to end.

SQLite rather than JSON because claiming a job must be atomic: two workers
polling at the same moment must not both get the same job.
"""

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "jobs.db")

# A worker that hasn't checked in for this long is treated as gone. Colab
# sessions die abruptly (tab closed, 12h cap, idle timeout), so this also
# releases whatever job it was holding.
WORKER_TIMEOUT_S = 90
JOB_STALE_S = 1800

JOB_TYPES = ("sql", "ingest_binance")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                worker_id TEXT,
                created_at REAL NOT NULL,
                claimed_at REAL,
                finished_at REAL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                label TEXT,
                runtime TEXT,
                specs TEXT,
                last_seen REAL NOT NULL,
                jobs_done INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at)")


init_db()


def submit_job(job_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job type '{job_type}' (expected one of {', '.join(JOB_TYPES)})")
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _conn() as con:
        con.execute(
            "INSERT INTO jobs (id, type, payload, status, created_at) VALUES (?,?,?,?,?)",
            (job_id, job_type, json.dumps(payload), "pending", now),
        )
    return {"id": job_id, "type": job_type, "status": "pending", "created_at": now}


def claim_job(worker_id: str) -> Optional[Dict[str, Any]]:
    """Atomically hands one pending job to a worker.

    The UPDATE ... WHERE status='pending' is the lock: SQLite serialises
    writers, so a second worker racing for the same row sees rowcount 0 and
    moves on rather than running the job twice.
    """
    now = time.time()
    with _conn() as con:
        _release_stale(con, now)
        row = con.execute(
            "SELECT id, type, payload FROM jobs WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = con.execute(
            "UPDATE jobs SET status='running', worker_id=?, claimed_at=? WHERE id=? AND status='pending'",
            (worker_id, now, row["id"]),
        )
        if cur.rowcount == 0:
            return None
        return {"id": row["id"], "type": row["type"], "payload": json.loads(row["payload"])}


def _release_stale(con: sqlite3.Connection, now: float) -> None:
    """Returns jobs stuck on dead workers to the queue."""
    con.execute(
        "UPDATE jobs SET status='pending', worker_id=NULL, claimed_at=NULL "
        "WHERE status='running' AND claimed_at IS NOT NULL AND claimed_at < ?",
        (now - JOB_STALE_S,),
    )


def report_result(job_id: str, worker_id: str, ok: bool, result: Any = None, error: str = "") -> bool:
    now = time.time()
    with _conn() as con:
        cur = con.execute(
            "UPDATE jobs SET status=?, result=?, error=?, finished_at=? WHERE id=? AND worker_id=?",
            ("done" if ok else "failed", json.dumps(result)[:200000] if ok else None, error[:2000], now, job_id, worker_id),
        )
        if cur.rowcount and ok:
            con.execute("UPDATE workers SET jobs_done = jobs_done + 1 WHERE worker_id=?", (worker_id,))
        return cur.rowcount > 0


def heartbeat(worker_id: str, label: str = "", runtime: str = "", specs: Optional[dict] = None) -> None:
    now = time.time()
    specs_json = json.dumps(specs) if specs else None
    with _conn() as con:
        con.execute(
            "INSERT INTO workers (worker_id, label, runtime, specs, last_seen) VALUES (?,?,?,?,?) "
            "ON CONFLICT(worker_id) DO UPDATE SET last_seen=excluded.last_seen, "
            "label=excluded.label, runtime=excluded.runtime, "
            # A bare claim() heartbeat carries no specs; keep the last known set.
            "specs=COALESCE(excluded.specs, workers.specs)",
            (worker_id, label, runtime, specs_json, now),
        )


def list_workers() -> List[Dict[str, Any]]:
    now = time.time()
    with _conn() as con:
        rows = con.execute("SELECT * FROM workers ORDER BY last_seen DESC").fetchall()
    return [
        {
            "worker_id": r["worker_id"],
            "label": r["label"] or r["worker_id"][:8],
            "runtime": r["runtime"] or "",
            "jobs_done": r["jobs_done"],
            "specs": json.loads(r["specs"]) if r["specs"] else {},
            "seconds_since_seen": round(now - r["last_seen"]),
            "online": (now - r["last_seen"]) < WORKER_TIMEOUT_S,
        }
        for r in rows
    ]


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "type": r["type"],
            "status": r["status"],
            "worker_id": r["worker_id"],
            "created_at": r["created_at"],
            "finished_at": r["finished_at"],
            "duration_s": round(r["finished_at"] - r["claimed_at"], 2) if r["finished_at"] and r["claimed_at"] else None,
            "error": r["error"],
            "result_preview": (r["result"] or "")[:400] if r["status"] == "done" else None,
        })
    return out


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as con:
        r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        return None
    return {
        "id": r["id"], "type": r["type"], "status": r["status"],
        "result": json.loads(r["result"]) if r["result"] else None,
        "error": r["error"], "worker_id": r["worker_id"],
    }


def queue_stats() -> Dict[str, int]:
    with _conn() as con:
        rows = con.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
    stats = {"pending": 0, "running": 0, "done": 0, "failed": 0}
    for r in rows:
        stats[r["status"]] = r["c"]
    return stats
