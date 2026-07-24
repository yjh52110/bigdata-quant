import json
import os
import time
from typing import Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSFER_LOG_FILE = os.path.join(BASE_DIR, "data", "transfer_log.json")

# Google's real, documented per-account Drive API limits (not fabricated).
DAILY_UPLOAD_LIMIT_BYTES = 750 * 1024**3
DAILY_DOWNLOAD_LIMIT_BYTES = 10 * 1024**4


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load() -> dict:
    if not os.path.exists(TRANSFER_LOG_FILE):
        return {}
    try:
        with open(TRANSFER_LOG_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(TRANSFER_LOG_FILE), exist_ok=True)
    with open(TRANSFER_LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_transfer(direction: str, num_bytes: int, account_index: str = "unknown") -> None:
    """Self-tracked transfer telemetry.

    The Google Drive API has no endpoint that exposes daily upload/download
    quota consumption -- `about().get(fields="storageQuota")` only returns
    total storage used, not the rolling 750GB/10TB daily transfer counters.
    So this is the only real signal available: every byte this codebase
    itself pushes through rclone gets logged here, tallied per UTC day. It
    will undercount transfers made outside this pipeline (e.g. a manual
    `rclone` run), which is disclosed via the API response rather than
    papered over with a fabricated progress bar.
    """
    assert direction in ("upload", "download")
    data = _load()
    day = _today_key()
    bucket = data.setdefault(day, {"upload_bytes": 0, "download_bytes": 0, "by_account": {}})
    bucket[f"{direction}_bytes"] = bucket.get(f"{direction}_bytes", 0) + num_bytes
    acc_bucket = bucket["by_account"].setdefault(account_index, {"upload_bytes": 0, "download_bytes": 0})
    acc_bucket[f"{direction}_bytes"] = acc_bucket.get(f"{direction}_bytes", 0) + num_bytes
    _save(data)


def get_today_totals() -> Dict:
    data = _load()
    day = _today_key()
    bucket = data.get(day, {"upload_bytes": 0, "download_bytes": 0, "by_account": {}})
    return {
        "date": day,
        "upload_bytes": bucket.get("upload_bytes", 0),
        "download_bytes": bucket.get("download_bytes", 0),
        "upload_limit_bytes": DAILY_UPLOAD_LIMIT_BYTES,
        "download_limit_bytes": DAILY_DOWNLOAD_LIMIT_BYTES,
        "by_account": bucket.get("by_account", {}),
        "self_tracked_disclaimer": (
            "Self-tracked from transfers this codebase initiated. The Google Drive "
            "API does not expose daily quota usage, so this undercounts any transfer "
            "made outside this pipeline."
        ),
    }
