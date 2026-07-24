import json
import os
import time

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mcp_invocations.jsonl")


def log_invocation(client: str, action: str, status: str, duration_ms: float, detail: str = "") -> dict:
    """Appends one real MCP tool-call record. Called from mcp_server.py on every
    tool invocation so the admin dashboard's MCP audit page has real data instead
    of the four hardcoded fake log lines it used to ship with."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    entry = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "client": client,
        "action": action,
        "status": status,
        "duration_ms": round(duration_ms, 1),
        "detail": detail[:300],
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_recent_logs(limit: int = 100) -> list:
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        lines = f.readlines()[-limit:]
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.reverse()
    return entries
