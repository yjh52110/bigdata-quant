import configparser
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RCLONE_CONFIG_PATH = os.path.expanduser("~/.config/rclone/rclone.conf")
UNION_REMOTE_NAME = "gdrive_union"
WATCHDOG_STATE_FILE = os.path.join(BASE_DIR, "data", "compaction_state.json")


def get_rclone_union_status() -> dict:
    """Reads the actual rclone.conf on disk -- no hardcoded 'BigQuery Sync 89%'
    style numbers. Reports whether the union remote exists, how many healthy
    Drive upstreams are wired into it, and what routing policy is active."""
    if not os.path.exists(RCLONE_CONFIG_PATH):
        return {"configured": False, "upstream_count": 0, "policy": None, "upstreams": []}

    config = configparser.ConfigParser()
    config.read(RCLONE_CONFIG_PATH)
    if not config.has_section(UNION_REMOTE_NAME):
        return {"configured": False, "upstream_count": 0, "policy": None, "upstreams": []}

    upstreams_raw = config.get(UNION_REMOTE_NAME, "upstreams", fallback="")
    upstreams = [u for u in upstreams_raw.split() if u]
    policy = config.get(UNION_REMOTE_NAME, "create_policy", fallback=None)
    return {
        "configured": True,
        "upstream_count": len(upstreams),
        "policy": policy,
        "upstreams": upstreams,
    }


def get_compaction_status() -> dict:
    """Reads the real state file data_compaction_watchdog.py maintains -- last
    run time and files compacted, instead of a fake 'Cryo Node Pool 12/12' card."""
    if not os.path.exists(WATCHDOG_STATE_FILE):
        return {"running": False, "last_compaction_at": None, "files_compacted_total": 0, "last_error": None}
    try:
        with open(WATCHDOG_STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"running": False, "last_compaction_at": None, "files_compacted_total": 0, "last_error": "state file unreadable"}


def get_sync_status() -> dict:
    return {
        "rclone_union": get_rclone_union_status(),
        "compaction_watchdog": get_compaction_status(),
    }
