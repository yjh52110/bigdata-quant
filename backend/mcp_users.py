"""Per-user API keys, daily quota and rate limiting for the MCP server.

The MCP server is exposed over HTTP to real users, so every tool call has to
be attributable to someone and bounded. Users live in a JSON file rather than
a database because the expected scale is tens of users, not thousands -- if
that changes this is the one module to swap.
"""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "data", "mcp_users.json")

DEFAULT_DAILY_QUOTA = int(os.environ.get("MCP_DEFAULT_DAILY_QUOTA", "500"))
DEFAULT_RATE_PER_MIN = int(os.environ.get("MCP_DEFAULT_RATE_PER_MIN", "20"))

_lock = threading.Lock()


@dataclass
class UserUsage:
    day: str = ""
    used_today: int = 0
    minute_bucket: int = 0
    used_this_minute: int = 0


@dataclass
class User:
    user_id: str
    api_key: str
    daily_quota: int = DEFAULT_DAILY_QUOTA
    rate_per_min: int = DEFAULT_RATE_PER_MIN
    disabled: bool = False
    usage: UserUsage = field(default_factory=UserUsage)


_users: Dict[str, User] = {}
_loaded_mtime: Optional[float] = None


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load() -> None:
    """Reloads whenever the file changes on disk.

    The admin API and the MCP server run as separate processes against the
    same file, so caching after a single read would make users created by one
    process permanently invisible to the other until restart.
    """
    global _loaded_mtime
    if not os.path.exists(USERS_FILE):
        return
    try:
        mtime = os.path.getmtime(USERS_FILE)
    except OSError:
        return
    if _loaded_mtime is not None and mtime <= _loaded_mtime:
        return
    try:
        with open(USERS_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    _users.clear()
    for key, u in raw.items():
        _users[key] = User(
            user_id=u.get("user_id", key[:8]),
            api_key=key,
            daily_quota=u.get("daily_quota", DEFAULT_DAILY_QUOTA),
            rate_per_min=u.get("rate_per_min", DEFAULT_RATE_PER_MIN),
            disabled=u.get("disabled", False),
            usage=UserUsage(**u.get("usage", {})),
        )
    _loaded_mtime = mtime


def _save() -> None:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    out = {
        u.api_key: {
            "user_id": u.user_id,
            "daily_quota": u.daily_quota,
            "rate_per_min": u.rate_per_min,
            "disabled": u.disabled,
            "usage": {
                "day": u.usage.day,
                "used_today": u.usage.used_today,
                "minute_bucket": u.usage.minute_bucket,
                "used_this_minute": u.usage.used_this_minute,
            },
        }
        for u in _users.values()
    }
    global _loaded_mtime
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, USERS_FILE)
    # Record our own write so the next _load() doesn't treat it as a foreign change.
    try:
        _loaded_mtime = os.path.getmtime(USERS_FILE)
    except OSError:
        _loaded_mtime = None


def create_user(user_id: str, daily_quota: int = DEFAULT_DAILY_QUOTA, rate_per_min: int = DEFAULT_RATE_PER_MIN) -> User:
    import secrets

    with _lock:
        _load()
        key = "cq_" + secrets.token_urlsafe(32)
        user = User(user_id=user_id, api_key=key, daily_quota=daily_quota, rate_per_min=rate_per_min)
        _users[key] = user
        _save()
        return user


def list_users() -> list:
    with _lock:
        _load()
        return [
            {
                "user_id": u.user_id,
                "api_key_masked": u.api_key[:8] + "…" + u.api_key[-4:],
                "daily_quota": u.daily_quota,
                "rate_per_min": u.rate_per_min,
                "disabled": u.disabled,
                "used_today": u.usage.used_today if u.usage.day == _today() else 0,
            }
            for u in _users.values()
        ]


def set_disabled(user_id: str, disabled: bool) -> bool:
    with _lock:
        _load()
        for u in _users.values():
            if u.user_id == user_id:
                u.disabled = disabled
                _save()
                return True
        return False


def authorize(api_key: Optional[str]) -> Tuple[Optional[User], Optional[str]]:
    """Returns (user, error). Consumes one unit of quota on success, so this
    must be called exactly once per tool invocation."""
    if not api_key:
        return None, "Missing API key. Send it as the X-API-Key header."

    with _lock:
        _load()
        user = _users.get(api_key)
        if user is None:
            return None, "Invalid API key."
        if user.disabled:
            return None, "This API key has been disabled."

        now = time.time()
        today, minute = _today(), int(now // 60)

        if user.usage.day != today:
            user.usage.day = today
            user.usage.used_today = 0
        if user.usage.minute_bucket != minute:
            user.usage.minute_bucket = minute
            user.usage.used_this_minute = 0

        if user.usage.used_this_minute >= user.rate_per_min:
            return None, f"Rate limit exceeded ({user.rate_per_min}/min). Try again shortly."
        if user.usage.used_today >= user.daily_quota:
            return None, f"Daily quota exhausted ({user.daily_quota}/day). Resets at 00:00 UTC."

        user.usage.used_this_minute += 1
        user.usage.used_today += 1
        _save()
        return user, None
