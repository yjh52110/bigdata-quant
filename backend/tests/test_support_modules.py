import importlib
import json

import pytest


# --------------------------------------------------------------------------
# transfer_log
# --------------------------------------------------------------------------
@pytest.fixture
def tlog(tmp_path, monkeypatch):
    import backend.transfer_log as t
    importlib.reload(t)
    monkeypatch.setattr(t, "TRANSFER_LOG_FILE", str(tmp_path / "transfer.json"))
    return t


def test_totals_start_at_zero(tlog):
    totals = tlog.get_today_totals()
    assert totals["upload_bytes"] == 0 and totals["download_bytes"] == 0


def test_transfers_accumulate_per_direction(tlog):
    tlog.record_transfer("upload", 100, "acc1")
    tlog.record_transfer("upload", 50, "acc1")
    tlog.record_transfer("download", 20, "acc2")
    totals = tlog.get_today_totals()
    assert totals["upload_bytes"] == 150
    assert totals["download_bytes"] == 20
    assert totals["by_account"]["acc1"]["upload_bytes"] == 150


def test_limits_match_googles_documented_values(tlog):
    totals = tlog.get_today_totals()
    assert totals["upload_limit_bytes"] == 750 * 1024 ** 3
    assert totals["download_limit_bytes"] == 10 * 1024 ** 4


def test_self_tracking_limitation_is_disclosed(tlog):
    # The Drive API can't report real quota use, so the response must say so
    # rather than implying the number is authoritative.
    assert "undercount" in tlog.get_today_totals()["self_tracked_disclaimer"]


def test_invalid_direction_rejected(tlog):
    with pytest.raises(AssertionError):
        tlog.record_transfer("sideways", 10)


# --------------------------------------------------------------------------
# mcp_logs
# --------------------------------------------------------------------------
@pytest.fixture
def mlog(tmp_path, monkeypatch):
    import backend.mcp_logs as m
    importlib.reload(m)
    monkeypatch.setattr(m, "LOG_FILE", str(tmp_path / "inv.jsonl"))
    return m


def test_no_logs_returns_empty(mlog):
    assert mlog.read_recent_logs() == []


def test_logs_return_newest_first(mlog):
    mlog.log_invocation("alice", "a", "200 OK", 1.0)
    mlog.log_invocation("bob", "b", "200 OK", 2.0)
    logs = mlog.read_recent_logs()
    assert [x["client"] for x in logs] == ["bob", "alice"]


def test_limit_is_respected(mlog):
    for i in range(10):
        mlog.log_invocation("u", f"a{i}", "200 OK", 1.0)
    assert len(mlog.read_recent_logs(limit=3)) == 3


def test_long_detail_is_truncated(mlog):
    mlog.log_invocation("u", "a", "200 OK", 1.0, detail="x" * 5000)
    assert len(mlog.read_recent_logs()[0]["detail"]) <= 300


def test_corrupt_line_does_not_break_reading(mlog):
    mlog.log_invocation("alice", "a", "200 OK", 1.0)
    with open(mlog.LOG_FILE, "a") as f:
        f.write("this is not json\n")
    mlog.log_invocation("bob", "b", "200 OK", 1.0)
    assert len(mlog.read_recent_logs()) == 2


# --------------------------------------------------------------------------
# sync_status
# --------------------------------------------------------------------------
@pytest.fixture
def sync(tmp_path, monkeypatch):
    import backend.sync_status as s
    importlib.reload(s)
    monkeypatch.setattr(s, "RCLONE_CONFIG_PATH", str(tmp_path / "rclone.conf"))
    monkeypatch.setattr(s, "WATCHDOG_STATE_FILE", str(tmp_path / "state.json"))
    return s


def test_reports_unconfigured_rather_than_faking_status(sync):
    status = sync.get_sync_status()
    assert status["rclone_union"]["configured"] is False
    assert status["compaction_watchdog"]["running"] is False


def test_reads_real_union_policy_from_rclone_conf(sync):
    with open(sync.RCLONE_CONFIG_PATH, "w") as f:
        f.write("[gdrive_union]\ntype = union\nupstreams = a:/ b:/\ncreate_policy = epmfs\n")
    union = sync.get_rclone_union_status()
    assert union["configured"] is True
    assert union["upstream_count"] == 2
    assert union["policy"] == "epmfs"


def test_reads_real_watchdog_state(sync):
    with open(sync.WATCHDOG_STATE_FILE, "w") as f:
        json.dump({"running": True, "files_compacted_total": 7}, f)
    assert sync.get_compaction_status()["files_compacted_total"] == 7


def test_unreadable_state_file_degrades_gracefully(sync):
    with open(sync.WATCHDOG_STATE_FILE, "w") as f:
        f.write("{corrupt")
    assert sync.get_compaction_status()["last_error"] is not None
