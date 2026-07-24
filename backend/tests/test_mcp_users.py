import importlib

import pytest


@pytest.fixture
def users(tmp_path, monkeypatch):
    """Fresh module instance pointed at a temp file, so tests never touch the
    real user store."""
    import backend.mcp_users as m
    importlib.reload(m)
    monkeypatch.setattr(m, "USERS_FILE", str(tmp_path / "users.json"))
    m._users.clear()
    m._loaded_mtime = None
    return m


def test_create_and_list(users):
    u = users.create_user("alice")
    assert u.api_key.startswith("cq_")
    listed = users.list_users()
    assert [x["user_id"] for x in listed] == ["alice"]
    assert "…" in listed[0]["api_key_masked"], "raw key must never be listed"


def test_authorize_rejects_missing_and_unknown_keys(users):
    users.create_user("alice")
    assert users.authorize(None)[0] is None
    assert users.authorize("")[0] is None
    assert users.authorize("cq_wrong")[0] is None


def test_authorize_accepts_valid_key(users):
    u = users.create_user("alice")
    user, err = users.authorize(u.api_key)
    assert err is None and user.user_id == "alice"


def test_disabled_key_is_rejected(users):
    u = users.create_user("alice")
    users.set_disabled("alice", True)
    user, err = users.authorize(u.api_key)
    assert user is None and "disabled" in err


def test_rate_limit_enforced_per_minute(users):
    u = users.create_user("alice", daily_quota=100, rate_per_min=2)
    assert users.authorize(u.api_key)[1] is None
    assert users.authorize(u.api_key)[1] is None
    _, err = users.authorize(u.api_key)
    assert err is not None and "Rate limit" in err


def test_daily_quota_enforced(users):
    u = users.create_user("alice", daily_quota=2, rate_per_min=100)
    users.authorize(u.api_key)
    users.authorize(u.api_key)
    _, err = users.authorize(u.api_key)
    assert err is not None and "Daily quota" in err


def test_denied_calls_do_not_consume_quota(users):
    u = users.create_user("alice", daily_quota=2, rate_per_min=100)
    for _ in range(5):
        users.authorize("cq_wrong")
    assert users.authorize(u.api_key)[1] is None
    assert users.authorize(u.api_key)[1] is None
    assert users.authorize(u.api_key)[1] is not None


def test_users_added_by_another_process_become_visible(users, tmp_path):
    """The MCP server and admin API are separate processes sharing this file;
    caching after one read previously hid new users until restart."""
    import json
    import os
    import time

    users.create_user("alice")
    raw = json.load(open(users.USERS_FILE))
    raw["cq_externally_added"] = {
        "user_id": "bob", "daily_quota": 10, "rate_per_min": 10,
        "disabled": False, "usage": {},
    }
    time.sleep(0.01)
    with open(users.USERS_FILE, "w") as f:
        json.dump(raw, f)
    os.utime(users.USERS_FILE, (time.time() + 1, time.time() + 1))

    user, err = users.authorize("cq_externally_added")
    assert err is None and user.user_id == "bob"
