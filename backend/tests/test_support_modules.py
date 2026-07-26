import importlib
import json
import os
import time

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


# --------------------------------------------------------------------------
# gemini_probe model selection
# --------------------------------------------------------------------------
def test_model_picker_prefers_newest_version_not_lexical_order():
    """A plain reverse sort picks "gemini-flash-lite-latest" over
    "gemini-3.6-flash" because 'f' > '3' -- silently selecting the weakest
    model. Selection must compare versions."""
    from backend.gemini_probe import _version_key

    names = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-flash-lite-latest",
             "gemini-2.5-flash", "gemini-3.5-flash"]
    assert max(names, key=_version_key) == "gemini-3.6-flash"


def test_model_picker_prefers_full_over_lite_at_same_version():
    from backend.gemini_probe import _version_key
    assert max(["gemini-3.5-flash-lite", "gemini-3.5-flash"], key=_version_key) == "gemini-3.5-flash"


def test_model_picker_skips_non_text_variants():
    from backend.gemini_probe import _SPECIAL_VARIANTS
    for n in ["gemini-3.1-flash-image", "gemini-3.1-flash-tts-preview",
              "gemini-2.5-computer-use-preview-10-2025"]:
        assert any(v in n for v in _SPECIAL_VARIANTS), f"{n} should be excluded"


# --------------------------------------------------------------------------
# colab_control
# --------------------------------------------------------------------------
def test_session_line_parses_real_cli_output():
    """Parsed against the CLI's actual `colab sessions` output format."""
    from backend.colab_control import _SESSION_RE
    line = "  [live] m-s-kkb-usc1b1-guv5u5jvs6wk | Hardware: CPU | Variant: DEFAULT"
    m = _SESSION_RE.search(line)
    assert m and m.group(1) == "live"
    assert m.group(2) == "m-s-kkb-usc1b1-guv5u5jvs6wk"
    assert (m.group(3), m.group(4)) == ("CPU", "DEFAULT")


def test_cert_bundle_is_pinned_for_the_cli():
    """python.org builds have no populated cert store, so the CLI's stdlib-ssl
    WebSocket layer dies with CERTIFICATE_VERIFY_FAILED unless we point it at
    certifi. Regression guard: the env must always carry a CA bundle."""
    from backend.colab_control import _env
    env = _env()
    assert env.get("SSL_CERT_FILE", "").endswith("cacert.pem")


def test_quota_is_reported_as_unavailable_never_invented(monkeypatch):
    """Google publishes no quota figure and no endpoint. The payload must say
    so explicitly so the UI can't render a made-up number."""
    import backend.colab_control as c
    monkeypatch.setattr(c, "list_sessions", lambda: {"available": False, "sessions": []})
    o = c.overview()
    assert o["quota_available"] is False
    assert o["quota_note"]
    # bool is a subclass of int, so quota_available itself must not count.
    assert not any("quota" in k and isinstance(v, (int, float)) and not isinstance(v, bool)
                   for k, v in o.items()), "no numeric quota may be present"


def test_documented_limits_include_the_multi_account_prohibition():
    """The user has ~100 accounts; the UI must surface that using them to
    widen Colab compute is against Google's terms."""
    from backend.colab_control import DOCUMENTED_LIMITS
    joined = " ".join(x["item"] + x["note"] for x in DOCUMENTED_LIMITS)
    assert "多账号" in joined and "multiple accounts" in joined


def test_uncreatable_hardware_variant_is_not_offered():
    from backend.colab_control import HARDWARE
    assert HARDWARE["gpu"] and "V100" not in HARDWARE["gpu"]
    assert set(HARDWARE) == {"cpu", "gpu", "tpu"}


def test_missing_cli_degrades_instead_of_raising(monkeypatch):
    import backend.colab_control as c
    monkeypatch.setattr(c.shutil, "which", lambda _: None)
    s = c.list_sessions()
    assert s["available"] is False and s["sessions"] == []


def test_entitlement_probe_records_verbatim_rejection(monkeypatch, tmp_path):
    """The backend's own rejection text is the only authoritative reason an
    account can't get a machine type, so it must be stored, not paraphrased."""
    import backend.colab_control as c
    monkeypatch.setattr(c, "ENTITLEMENT_CACHE", str(tmp_path / "ent.json"))
    monkeypatch.setattr(c, "HARDWARE", {"cpu": ["DEFAULT"], "gpu": ["A100"], "tpu": []})
    monkeypatch.setattr(c, "_run", lambda a, timeout=0: {
        "ok": False, "out": "", "err": "Backend rejected accelerator 'A100'. You may not have quota"}
        if "--gpu" in a else {"ok": True, "out": "Session READY.", "err": ""})
    monkeypatch.setattr(c, "probe_session", lambda n: {"ok": True, "specs": {"cpu_count": 2}})

    r = c.probe_entitlements()
    by = {a["variant"]: a for a in r["attempts"]}
    assert by["DEFAULT"]["granted"] is True
    assert by["A100"]["granted"] is False
    assert "Backend rejected accelerator 'A100'" in by["A100"]["reason"]
    assert r["granted"] == ["cpu:DEFAULT"]
    assert c.cached_entitlements()["granted"] == ["cpu:DEFAULT"]


def test_plans_carry_no_invented_burn_rate():
    """Google shows the per-hour compute-unit burn rate only in the notebook UI;
    nothing can read it, so no rate may appear in the plan table."""
    from backend.colab_control import PLANS, UNITS_EXPIRY_NOTE
    blob = " ".join(f"{p['plan']}{p['units']}{p['extra']}" for p in PLANS)
    assert "单元/小时" not in blob and "units/hour" not in blob
    assert "读不到" in UNITS_EXPIRY_NOTE
    assert any("100" in p["units"] for p in PLANS)   # Pro, from the official page
    assert any("600" in p["units"] for p in PLANS)   # Pro+


def test_drive_access_path_distinguishes_fuse_from_rest_api():
    """Measured in a live session: colab drivemount fails headless (needs the
    notebook consent popup) while the Drive REST API answers in 33.8ms with a
    plain 401. Conflating the two would wrongly suggest Colab can't reach
    Drive at all -- this project's worker uses the REST path, not FUSE."""
    from backend.colab_control import DOCUMENTED_LIMITS
    by = {d["item"]: d for d in DOCUMENTED_LIMITS}
    assert "不可用" in by["云盘 FUSE 挂载"]["value"]
    assert "可达" in by["云盘 REST API"]["value"]
    assert "mount failed" in by["云盘 FUSE 挂载"]["note"]


def test_quota_note_does_not_claim_the_figure_is_unpublished():
    """Earlier wording said Google "doesn't publish" a quota figure. The
    notebook UI does show an account-specific projection; what's true is that
    no interface exposes it. Keep the distinction so the panel stays accurate."""
    from backend.colab_control import DOCUMENTED_LIMITS
    by = {d["item"]: d for d in DOCUMENTED_LIMITS}
    assert "网页端可见" in by["配额数值"]["value"]
    assert "不公布" not in by["配额数值"]["value"]
    assert "google.colab" in by["配额数值"]["note"]


def test_orphaned_session_is_flagged_not_silently_listed(monkeypatch):
    """A session the server still runs but the CLI lost the name mapping for
    shows as [?] and cannot be stopped from the CLI, quietly burning free-tier
    allowance. It must be flagged, and must not be offered a probe button
    (the probe resolves the same missing name)."""
    import backend.colab_control as c
    monkeypatch.setattr(c, "cli_status", lambda: {"installed": True, "version": "0.6.0",
                                                 "authenticated": True, "auth_hint": None})
    monkeypatch.setattr(c, "_run", lambda a, timeout=0: {
        "ok": True, "err": "",
        "out": ("[?] m-s-kkb-use1d0-1zu6p7qmd860k | Hardware: CPU | Variant: DEFAULT\n"
                "[mine] m-s-kkb-usc1b1-aaa | Hardware: CPU | Variant: DEFAULT")})
    monkeypatch.setattr(c, "_local_session_names", lambda: {"mine"})
    monkeypatch.setattr(c, "session_status", lambda n: {"status": "IDLE", "last_execution": None})

    r = c.list_sessions()
    by = {s["name"]: s for s in r["sessions"]}
    assert by["?"]["orphan"] is True
    assert by["mine"]["orphan"] is False
    assert r["orphan_count"] == 1
    assert "网页端" in r["orphan_hint"]
    # Status is only looked up for sessions the CLI can actually address.
    assert "status" not in by["?"] and by["mine"]["status"] == "IDLE"


def test_no_orphans_means_no_warning(monkeypatch):
    import backend.colab_control as c
    monkeypatch.setattr(c, "cli_status", lambda: {"installed": True, "version": "0.6.0",
                                                 "authenticated": True, "auth_hint": None})
    monkeypatch.setattr(c, "_run", lambda a, timeout=0: {
        "ok": True, "err": "", "out": "[mine] m-x | Hardware: CPU | Variant: DEFAULT"})
    monkeypatch.setattr(c, "_local_session_names", lambda: {"mine"})
    monkeypatch.setattr(c, "session_status", lambda n: {"status": "IDLE", "last_execution": None})
    assert c.list_sessions()["orphan_hint"] is None


# --------------------------------------------------------------------------
# google_account_manager scope choice
# --------------------------------------------------------------------------
def test_drive_scope_stays_non_restricted():
    """`drive` is a restricted scope: shipping it means passing Google's CASA
    assessment before the consent screen can leave Testing mode, where refresh
    tokens die after 7 days and only ~100 hand-listed users can connect. This
    pipeline only ever reads files it wrote, so drive.file suffices."""
    from backend.google_account_manager import SCOPES
    assert SCOPES == ["https://www.googleapis.com/auth/drive.file"]
    assert "auth/drive" not in [s.rsplit("/", 1)[0] + "/drive" for s in SCOPES if s.endswith("/drive")]


# --------------------------------------------------------------------------
# kaggle_control
# --------------------------------------------------------------------------
def test_kaggle_quota_seconds_convert_to_hours(monkeypatch):
    """The CLI reports seconds; the panel shows hours. Conversion happens once
    here so the component can't disagree with the tests."""
    from backend.kaggle_control import _normalise
    q = _normalise({"timeUsed": 3600 * 9, "totalTimeAllowed": 3600 * 30, "timeReserved": 0})
    assert q["used_h"] == 9.0 and q["total_h"] == 30.0
    assert q["remaining_h"] == 21.0 and q["pct_used"] == 30.0


def test_missing_quota_field_stays_absent_not_zero():
    """A missing total must not render as 0 h, which would read as "quota
    exhausted" when the real state is "unknown"."""
    from backend.kaggle_control import _normalise
    q = _normalise({"timeUsed": 100})
    assert "total_h" not in q
    assert "remaining_h" not in q
    assert "pct_used" not in q


def test_kaggle_snake_and_camel_field_names_both_parse():
    """The CLI's JSON output and the SDK types differ in casing, so both spellings
    have to resolve or the panel silently shows nothing."""
    from backend.kaggle_control import _normalise
    a = _normalise({"timeUsed": 3600, "totalTimeAllowed": 7200})
    b = _normalise({"time_used": 3600, "total_time_allowed": 7200})
    assert a["remaining_h"] == b["remaining_h"] == 1.0


def test_kaggle_reports_unauthenticated_instead_of_a_placeholder_quota(monkeypatch):
    import backend.kaggle_control as k
    monkeypatch.setattr(k.shutil, "which", lambda _: "/usr/bin/kaggle")
    monkeypatch.setattr(k.os.path, "exists", lambda p: False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    r = k.quota()
    assert r["available"] is False
    assert r["authenticated"] is False
    assert "gpu" not in r
    assert "kaggle.json" in r["auth_hint"]


def test_kaggle_capabilities_state_the_colab_contrast():
    """The reason to add Kaggle at all is that its quota is readable and its
    kernels are externally dispatchable -- both absent in Colab. Keep that
    stated so the panel explains the architectural choice."""
    from backend.kaggle_control import CAPABILITIES
    blob = " ".join(c["item"] + c["value"] + c["note"] for c in CAPABILITIES)
    assert "kaggle quota" in blob and "kernels push" in blob
    assert "worker" in blob


def test_kaggle_panel_states_quota_is_per_kaggle_account_not_google():
    """A costly assumption: that N Google accounts yield N Kaggle quotas. The
    CLI itself says a separate Kaggle account is required, so the panel must
    say it too rather than letting the pool-of-100 idea look viable here."""
    from backend.kaggle_control import CAPABILITIES
    by = {c["item"]: c for c in CAPABILITIES}
    assert "Kaggle 账号" in by["额度挂在哪"]["value"]
    assert "不等于" in by["额度挂在哪"]["note"]
    assert "is_phone_verified" in by["加速器与账号验证"]["note"]
    assert "不用 GPU" in by["对本项目的真实价值"]["note"]


def test_free_tier_figures_carry_their_provenance():
    """Web-researched numbers must not be presentable as official ones. Each
    row states where it came from, and the one figure sources disagree on is
    labelled as such rather than silently picked."""
    from backend.kaggle_control import FREE_TIER
    assert all(f["source"] in {"measured", "official", "official-ish", "community", "conflicting"}
               for f in FREE_TIER)
    by = {f["item"]: f for f in FREE_TIER}
    # The 30h figure is a verbatim Kaggle staff quote, so it may claim official.
    assert by["GPU 每周配额"]["source"] == "official"
    assert "30hrs of guaranteed quota" in by["GPU 每周配额"]["note"]
    # Session length is the number sources contradict each other on.
    assert by["单次会话上限"]["source"] == "conflicting"
    # RAM/disk came from third parties only.
    assert by["内存"]["source"] == "community"


def test_free_tier_leads_with_the_fact_that_matters_here():
    """This pipeline is CPU-bound, so "CPU is unmetered" is the load-bearing
    fact -- not the GPU hours everyone quotes."""
    from backend.kaggle_control import FREE_TIER
    assert FREE_TIER[0]["item"] == "CPU 时长"
    assert FREE_TIER[0]["value"] == "不限额"


def test_live_quota_is_declared_authoritative_over_the_researched_table(monkeypatch):
    import backend.kaggle_control as k
    monkeypatch.setattr(k, "quota", lambda: {"available": False, "reason": "not authenticated"})
    o = k.overview()
    assert "kaggle quota" in o["free_tier_note"]
    assert "优先" in o["free_tier_note"]


# --------------------------------------------------------------------------
# kaggle_dispatch
# --------------------------------------------------------------------------
def test_internet_is_forced_on_for_every_dispatched_kernel():
    """Kaggle defaults enable_internet to False. Every job this project
    dispatches fetches from S3 / Binance / Drive, so leaving it to the caller
    means jobs that fail silently with empty output."""
    from backend.kaggle_dispatch import build_metadata
    m = build_metadata("u", "cq-aws-eth-blocks", "t", "job.py")
    assert m["enable_internet"] is True
    assert m["is_private"] is True


def test_metadata_matches_the_clis_own_permitted_values():
    """Key names and values come from the installed CLI's validation lists, not
    from docs, so a schema drift shows up here rather than as a push failure."""
    from backend.kaggle_dispatch import build_metadata, LANGUAGES, KERNEL_TYPES
    m = build_metadata("u", "cq-test-slug", "t", "job.py")
    assert m["language"] in LANGUAGES and m["kernel_type"] in KERNEL_TYPES
    assert m["id"] == "u/cq-test-slug"
    assert set(m) == {"id", "title", "code_file", "language", "kernel_type", "is_private",
                      "enable_gpu", "enable_tpu", "enable_internet", "dataset_sources",
                      "competition_sources", "kernel_sources", "model_sources"}


def test_slug_rules_match_kaggles_validation():
    """Kaggle rejects slugs under five characters and slugs carrying an owner
    or version, so catch both before spending a push."""
    from backend.kaggle_dispatch import build_metadata, DispatchError
    with pytest.raises(DispatchError, match="five|5"):
        build_metadata("u", "abcd", "t", "job.py")
    with pytest.raises(DispatchError, match="slash"):
        build_metadata("u", "owner/slug-name", "t", "job.py")
    assert build_metadata("u", "abcde", "t", "job.py")["id"] == "u/abcde"


def test_generated_job_script_is_valid_python_with_params_inlined():
    """The kernel only receives the pushed folder, so the script must be
    self-contained and must compile before it costs a push."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "aws", "chain": "eth", "table": "blocks",
                         "days": ["2024-01-15"]})
    compile(src, "job.py", "exec")
    assert '"chain": "eth"' in src
    assert "/kaggle/working" in src


def test_dispatch_refuses_without_a_token_instead_of_pushing(monkeypatch):
    import backend.kaggle_dispatch as kd
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": False, "auth_hint": "放置 kaggle.json"})
    with pytest.raises(kd.DispatchError, match="kaggle.json"):
        kd.dispatch("u", "cq-test-slug", {"kind": "aws"})


def test_dispatch_records_ref_and_version_from_push_output(monkeypatch, tmp_path):
    import backend.kaggle_dispatch as kd
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})
    monkeypatch.setattr(kd, "_run", lambda a, timeout=0: {
        "ok": True, "err": "",
        "out": "Kernel version 3 successfully pushed. Please check progress at "
               "https://www.kaggle.com/code/someuser/cq-aws-eth-blocks"})
    job = kd.dispatch("someuser", "cq-aws-eth-blocks",
                      {"kind": "aws", "chain": "eth", "table": "blocks", "days": ["2024-01-15"]})
    assert job["ref"] == "someuser/cq-aws-eth-blocks"
    assert job["version"] == 3
    assert job["status"] == "QUEUED"
    assert kd.list_jobs()[0]["ref"] == job["ref"]


def test_failed_push_raises_with_the_backends_own_message(monkeypatch, tmp_path):
    import backend.kaggle_dispatch as kd
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})
    monkeypatch.setattr(kd, "_run", lambda a, timeout=0: {
        "ok": False, "out": "", "err": "403 - You don't have permission"})
    with pytest.raises(kd.DispatchError, match="403"):
        kd.dispatch("u", "cq-test-slug", {"kind": "aws"})
    assert kd.list_jobs() == []      # a failed push must not be recorded as a job


def test_status_recognises_every_state_the_sdk_declares():
    from backend.kaggle_dispatch import _STATUS_RE, DONE_STATES
    from backend.kaggle_control import KERNEL_STATES
    for st in KERNEL_STATES:
        assert _STATUS_RE.search(f'"status": "{st}"'), st
    assert DONE_STATES == {"COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED"}
    assert "RUNNING" not in DONE_STATES and "QUEUED" not in DONE_STATES


def test_refresh_only_polls_jobs_that_are_still_running(monkeypatch, tmp_path):
    """Each poll is a CLI call, so finished jobs must not be re-checked."""
    import backend.kaggle_dispatch as kd
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    kd._save_jobs([{"ref": "u/done", "status": "COMPLETE"},
                   {"ref": "u/live", "status": "QUEUED"}])
    polled = []
    monkeypatch.setattr(kd, "status", lambda ref: (polled.append(ref),
                        {"ref": ref, "state": "RUNNING", "done": False})[1])
    jobs = kd.refresh_jobs()
    assert polled == ["u/live"]
    assert {j["ref"]: j["status"] for j in jobs} == {"u/done": "COMPLETE", "u/live": "RUNNING"}


def test_drive_access_is_rest_only_and_measured():
    """Both platforms reach Drive the same way: REST with an OAuth token. The
    FUSE rows were dropped once the question was settled, but the reason has to
    survive in the notes -- otherwise the next person reaches for drive.mount()
    again and loses an afternoon to a popup that never appears."""
    from backend.kaggle_control import DRIVE_ACCESS
    assert len(DRIVE_ACCESS) == 2
    assert all(d["method"] == "Drive REST API" for d in DRIVE_ACCESS)
    assert all(d["works"] and d["verified"] for d in DRIVE_ACCESS)

    by = {d["platform"]: d for d in DRIVE_ACCESS}
    assert "33.8ms" in by["Colab"]["note"]
    assert "mount failed" in by["Colab"]["note"]      # why FUSE is not an option
    assert "83.0ms" in by["Kaggle"]["note"]
    assert "没有挂载方案" in by["Kaggle"]["note"]


def test_drivecheck_job_probes_all_four_unknowns():
    """One push has to settle: is egress open, does the Drive API answer, can a
    secret be injected, and how fast is Google's network from Kaggle."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "drivecheck"})
    compile(src, "job.py", "exec")
    for probe in ("drive_api", "oauth_token_endpoint", "kaggle_secrets",
                  "google_colab_module", "gcs_download_MBps"):
        assert probe in src, probe
    # 401 without a token is the healthy answer and must not be treated as failure.
    assert "HTTPError" in src and "401" in src


def test_drivecheck_needs_no_parameters():
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "drivecheck"})
    assert '"kind": "drivecheck"' in src


# --------------------------------------------------------------------------
# drive_rest
# --------------------------------------------------------------------------
def test_drive_client_is_stdlib_only():
    """It must run in Colab and Kaggle with nothing to pip install, and be
    shippable verbatim inside a Kaggle push folder."""
    import ast, pathlib
    src = pathlib.Path("backend/drive_rest.py").read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"json", "mimetypes", "os", "time", "urllib", "typing"}, imported


def test_resumable_chunk_size_is_a_multiple_of_256kib():
    """Drive rejects resumable chunks that aren't a multiple of 256 KiB."""
    from backend.drive_rest import CHUNK
    assert CHUNK % (256 * 1024) == 0 and CHUNK > 0


def test_folder_query_escapes_quotes(monkeypatch):
    """An apostrophe in a folder name would otherwise break the query and could
    change which files the search matches. Patched via the fixture so the stub
    can't leak into later tests."""
    import backend.drive_rest as dr
    captured = {}

    def stub(url, **kw):
        captured["url"] = url
        return {"files": []}

    monkeypatch.setattr(dr, "_json_request", stub)
    dr.find_folder("tok", "it's a folder")
    assert "%5C%27" in captured["url"] or "\\'" in captured["url"]


def test_secret_must_carry_all_three_oauth_fields():
    from backend.drive_rest import token_from_secret, DriveError
    with pytest.raises(DriveError, match="not valid JSON"):
        token_from_secret("not json")
    with pytest.raises(DriveError, match="refresh_token"):
        token_from_secret('{"client_id": "a", "client_secret": "b"}')


def test_worker_no_longer_uses_fuse_mount():
    """drive.mount() fails headless, so the worker must not depend on it. The
    only permitted mentions are in the docstring explaining why."""
    import pathlib, re
    src = pathlib.Path("notebooks/colab_worker.py").read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", src, count=1)   # strip module docstring
    assert "drive.mount(" not in code
    assert "drive_rest" in code and "push_to_drive" in code


def test_kaggle_push_ships_the_shared_drive_client(monkeypatch, tmp_path):
    """The kernel can only import files present in the push folder, so
    drive_rest.py has to travel with it -- not be re-implemented inline."""
    import backend.kaggle_dispatch as kd
    seen = {}
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})

    def fake_run(args, timeout=0):
        folder = args[args.index("-p") + 1]
        seen["files"] = sorted(os.listdir(folder))
        return {"ok": True, "out": "Kernel version 1 successfully pushed", "err": ""}

    import os
    monkeypatch.setattr(kd, "_run", fake_run)
    kd.dispatch("u", "cq-test-slug", {"kind": "aws", "chain": "eth",
                                      "table": "blocks", "days": ["2024-01-15"]})
    assert seen["files"] == ["drive_rest.py", "job.py", "kernel-metadata.json"]


def test_dispatched_script_never_inlines_a_long_lived_credential():
    """The script is stored in the kernel, so anything pasted into it leaks with
    the kernel. Checked against credential *values*, not field names: the
    embedded drive_rest module legitimately mentions "client_secret" and
    "refresh_token" as required keys of the secret it parses."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "aws", "chain": "eth", "table": "blocks",
                         "days": ["2024-01-15"], "drive_folder": "chainquant"})
    compile(src, "job.py", "exec")
    assert "UserSecretsClient" in src              # the credential is read, not carried
    # Value prefixes, not field names: the embedded module necessarily names the
    # OAuth form fields it posts.
    assert "GOCSPX-" not in src                    # Google client-secret prefix
    assert "1//" not in src                        # Google refresh-token prefix


def test_drive_rest_is_embedded_not_shipped_as_a_sibling_file():
    """`kernels push` uploads the folder, but Kaggle only treats code_file as the
    kernel source: a sibling module is not importable there. Measured -- the job
    failed with "No module named 'drive_rest'" while the file sat in the pushed
    folder. So the module has to be materialised into the script itself."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "aws", "chain": "eth", "table": "blocks",
                         "days": ["2024-01-15"], "drive_folder": "chainquant"})
    assert "def upload_tree" in src and "def access_token" in src
    assert "_sys.modules['drive_rest']" in src
    compile(src, "job.py", "exec")


def test_download_rate_excludes_dependency_install_time():
    """The first run reported 0.3 MB/s for a transfer of seconds, because the
    rate was measured from script start and pip install dominated."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "aws", "chain": "eth", "table": "blocks",
                         "days": ["2024-01-15"]})
    assert "dl_started = time.time()" in src
    assert "download_seconds" in src
    # The clock must start after the install, not at script start.
    assert src.index('pip("awscli")') < src.index("dl_started = time.time()")


def test_unreachable_host_raises_named_error_not_a_bare_urlerror(monkeypatch):
    """A DNS failure or empty CA store must not escape as a raw URLError -- the
    worker catches DriveError, so anything else crashes the job loop."""
    import urllib.error
    import backend.drive_rest as dr
    monkeypatch.setattr(dr.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] nope")))
    with pytest.raises(dr.DriveError) as ei:
        dr.about("tok")
    assert "could not reach www.googleapis.com" in str(ei.value)
    # The certificate case is singled out because it otherwise reads as an outage.
    assert "SSL_CERT_FILE" in str(ei.value)


def test_http_status_is_returned_not_raised(monkeypatch):
    """401/403 are real answers from Google and must stay distinguishable from
    never having reached it."""
    import io, urllib.error
    import backend.drive_rest as dr

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"error":1}'))

    monkeypatch.setattr(dr.urllib.request, "urlopen", boom)
    status, _, body = dr._request("https://www.googleapis.com/x")
    assert status == 401 and b"error" in body


# --------------------------------------------------------------------------
# OAuth PKCE round-trip
# --------------------------------------------------------------------------
def test_pkce_verifier_is_carried_from_auth_url_to_callback():
    """google-auth-oauthlib enables PKCE by default, so the auth URL carries a
    code_challenge. The exchange happens in a later request with a fresh Flow,
    so the verifier must be stashed with the state nonce -- otherwise Google
    rejects it with "(invalid_grant) Missing code verifier." and the account
    silently never connects (observed against the real endpoint)."""
    import inspect
    from backend.google_account_manager import GoogleAccountManager

    sig = inspect.signature(GoogleAccountManager.handle_callback)
    assert "code_verifier" in sig.parameters

    src = inspect.getsource(GoogleAccountManager.handle_callback)
    # It must be applied to the Flow, not merely accepted and ignored.
    assert "flow.code_verifier = code_verifier" in src


def test_pending_oauth_state_stores_both_account_and_verifier():
    import inspect
    import backend.api_server as api

    create = inspect.getsource(api.create_auth_url)
    assert '"code_verifier"' in create and "flow" in create
    cb = inspect.getsource(api.oauth_callback)
    assert 'pending.get("code_verifier")' in cb
    # The nonce still has to gate the callback -- it is the only proof the
    # redirect belongs to a flow we started.
    assert "_pending_oauth.pop(state" in cb


def test_measure_script_uses_a_small_target_not_a_huge_table():
    """The measurement must not cost real quota to prove a point: eth/blocks is
    ~5 MB/day, while traces is ~2.6 GB/day."""
    import pathlib
    src = pathlib.Path("scripts/measure_drive_write.py").read_text()
    assert '"table": "blocks"' in src and '"chain": "eth"' in src
    assert "traces" not in src
    # It must request the Drive upload, or it measures only the download half.
    assert '"drive_folder"' in src


def test_credential_handling_is_confined_to_one_function():
    """The default path never touches the credential -- Kaggle Secrets carries
    it. --use-access-token deliberately does, to mint a ~1h token, and that is
    the only place allowed to: keeping it in one named function is what makes the
    exposure reviewable."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("scripts/measure_drive_write.py").read_text())
    touching = {"_decrypt", "GoogleAccountManager", "CREDENTIALS_FILE", "refresh_token"}
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "mint_access_token":
            continue
        src = ast.unparse(node)
        for name in touching:
            if name in src:
                offenders.append((getattr(node, "name", type(node).__name__), name))
    assert not offenders, f"credential handling leaked outside mint_access_token: {offenders}"


def test_minted_token_is_never_printed():
    """It is a live credential for about an hour; it belongs in the job params,
    not in a terminal or a log."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("scripts/measure_drive_write.py").read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "mint_access_token")
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            printed = ast.unparse(node)
            assert "token" not in printed or "len(token)" in printed, printed


def test_push_timeout_is_seconds_not_a_duration_string(monkeypatch, tmp_path):
    """`kernels push -t` is argparse type=int. A duration string like "15m" is
    rejected before the push happens (observed against the real CLI), so the
    value must be coerced to whole seconds."""
    import backend.kaggle_dispatch as kd
    seen = {}
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})

    def fake_run(args, timeout=0):
        seen["args"] = args
        return {"ok": True, "out": "Kernel version 1 successfully pushed", "err": ""}

    monkeypatch.setattr(kd, "_run", fake_run)
    kd.dispatch("u", "cq-test-slug", {"kind": "drivecheck"}, timeout=900)
    i = seen["args"].index("-t")
    assert seen["args"][i + 1] == "900"
    int(seen["args"][i + 1])   # must parse as an int, as argparse will


def test_quota_parses_the_clis_actual_hour_strings():
    """Real output is a list of rows with "30.00h" strings, not the SDK's
    seconds-based object. Parsing only the latter produced an empty panel
    against a live account."""
    from backend.kaggle_control import _normalise
    q = _normalise({"resource": "GPU", "used": "4.50h", "remaining": "25.50h",
                    "total": "30.00h", "refreshAt": "2026-08-01T00:00:00"})
    assert q["used_h"] == 4.5 and q["total_h"] == 30.0
    assert q["remaining_h"] == 25.5 and q["pct_used"] == 15.0
    assert q["refresh_at"] == "2026-08-01T00:00:00"


def test_quota_still_parses_the_sdk_seconds_shape():
    from backend.kaggle_control import _normalise
    q = _normalise({"timeUsed": 3600 * 9, "totalTimeAllowed": 3600 * 30})
    assert q["used_h"] == 9.0 and q["total_h"] == 30.0 and q["remaining_h"] == 21.0


def test_kernel_title_is_kept_consistent_with_the_slug(monkeypatch, tmp_path):
    """Kaggle derives the kernel's slug from the TITLE, not from metadata `id`.
    Observed: id=yjh980/cq-drivecheck with title "ChainQuant drive connectivity
    check" created yjh980/chainquant-drive-connectivity-check, after which every
    status/output call on the requested ref failed with "Permission
    'kernels.get' was denied"."""
    import json as _json
    import os
    import backend.kaggle_dispatch as kd

    seen = {}
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})

    def fake_run(args, timeout=0):
        folder = args[args.index("-p") + 1]
        with open(os.path.join(folder, "kernel-metadata.json")) as f:
            seen["meta"] = _json.load(f)
        return {"ok": True, "out": "Kernel version 1 successfully pushed", "err": ""}

    monkeypatch.setattr(kd, "_run", fake_run)
    kd.dispatch("u", "cq-drivecheck", {"kind": "drivecheck"},
                title="ChainQuant drive connectivity check")
    # The prose title would slugify elsewhere, so the slug must be used instead.
    assert seen["meta"]["title"] == "cq-drivecheck"
    assert kd._slugify(seen["meta"]["title"]) == "cq-drivecheck"


def test_ref_comes_from_the_url_kaggle_prints(monkeypatch, tmp_path):
    """Kaggle's URL is authoritative about which kernel it actually created."""
    import backend.kaggle_dispatch as kd
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})
    monkeypatch.setattr(kd, "_run", lambda a, timeout=0: {
        "ok": True, "err": "",
        "out": "Kernel version 2 successfully pushed. Please check progress at "
               "https://www.kaggle.com/code/yjh980/chainquant-drive-check"})
    job = kd.dispatch("yjh980", "cq-other-slug", {"kind": "drivecheck"})
    assert job["ref"] == "yjh980/chainquant-drive-check"


def test_panel_records_that_kaggle_internet_needs_phone_verification():
    """Measured: enable_internet:true has no effect on an unverified account --
    DNS itself fails inside the kernel. This row has to survive the account
    later becoming verified, because it is what explains an empty result to the
    next person whose account isn't."""
    from backend.kaggle_control import FREE_TIER
    by = {f["item"]: f for f in FREE_TIER}
    assert "手机验证" in by["联网前置条件"]["value"]
    assert "Get phone verified" in by["联网前置条件"]["note"]
    # The before/after contrast is the evidence; keep both figures.
    state = by["本账号当前状态"]["note"]
    assert "DNS 不通" in state and "324.5" in state


def test_no_row_claims_fuse_is_usable():
    """Measured on both platforms: unattended FUSE mounting does not work. The
    module-level comment records it; no row may contradict that."""
    import inspect
    import backend.kaggle_control as kc
    src = inspect.getsource(kc)
    assert "drive.mount()" in src and "colabtools#4182" in src
    assert not any("FUSE" in d["method"] for d in kc.DRIVE_ACCESS)


def test_kaggle_drive_reachability_records_both_sides_of_verification():
    """The same probe was run before and after phone verification: DNS failure
    and 0.0 MB/s first, then HTTP 401 in 83ms and 324.5 MB/s. Keeping both in
    the note is what makes the cause attributable to verification rather than
    to this project's code."""
    from backend.kaggle_control import DRIVE_ACCESS, FREE_TIER
    rest = next(d for d in DRIVE_ACCESS if d["platform"] == "Kaggle")
    assert rest["works"] is True and rest["verified"] is True
    assert "401" in rest["note"] and "手机验证" in rest["note"]

    by = {f["item"]: f for f in FREE_TIER}
    egress = by["到 Google 存储下行（实测）"]
    assert egress["source"] == "measured" and "324.5" in egress["value"]
    # Colab's comparable figure must stay alongside it: the two are only
    # meaningful relative to each other for a bandwidth-bound pipeline.
    assert "185.9" in egress["note"]


def test_export_script_indexes_accounts_as_a_dict(monkeypatch, tmp_path, capsys):
    """GoogleAccountManager.accounts is keyed by account_index. Iterating it as
    a list of records yields strings, which raised AttributeError on the first
    real account -- and stayed hidden while the pool was empty, because the
    loop simply never ran."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "export_drive_secret", "scripts/export_drive_secret.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"web": {"client_id": "real-id", "client_secret": "real-secret"}}))
    monkeypatch.setattr(mod, "CREDENTIALS_FILE", str(creds))

    class FakeMgr:
        accounts = {"acc-01": {"account_index": "acc-01", "refresh_token": "enc"}}

        def _decrypt(self, v):
            return "plain-refresh-token"

    monkeypatch.setattr(mod, "GoogleAccountManager", FakeMgr)
    monkeypatch.setattr(sys, "argv", ["export_drive_secret.py", "acc-01"])

    assert mod.main() == 0
    printed = json.loads(capsys.readouterr().out.strip())
    assert printed == {"client_id": "real-id", "client_secret": "real-secret",
                       "refresh_token": "plain-refresh-token"}


def test_export_script_lists_connected_accounts_on_a_bad_name(monkeypatch, tmp_path, capsys):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "export_drive_secret2", "scripts/export_drive_secret.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"web": {"client_id": "real-id", "client_secret": "s"}}))
    monkeypatch.setattr(mod, "CREDENTIALS_FILE", str(creds))

    class FakeMgr:
        accounts = {"acc-01": {}, "acc-02": {}}

    monkeypatch.setattr(mod, "GoogleAccountManager", FakeMgr)
    monkeypatch.setattr(sys, "argv", ["export_drive_secret.py", "acc-99"])
    assert mod.main() == 1
    assert "acc-01, acc-02" in capsys.readouterr().err


def test_upload_figure_carries_the_scale_caveat():
    """Measured 36.56 MB/s at 200 MB but 1.53 MB/s at 5.9 MB on the same code
    path -- a 24x spread caused purely by per-request round trips. Quoting the
    small-file number without the caveat would understate capacity by that much."""
    from backend.kaggle_control import FREE_TIER
    by = {f["item"]: f for f in FREE_TIER}
    up = by["写入云盘上行（实测）"]
    assert up["source"] == "measured" and "36.56" in up["value"]
    assert "1.53" in up["note"] and "小文件" in up["note"]


def test_uploadbench_generates_incompressible_data():
    """Compressible filler would let Drive flatter the throughput figure."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "uploadbench", "mb": 200, "drive_folder": "chainquant"})
    compile(src, "job.py", "exec")
    assert "os.urandom" in src
    # It must not spend the measurement on a download it doesn't need.
    assert "aws s3 cp" not in src.split("elif kind == \"uploadbench\"")[1].split("elif")[0]


def test_secret_path_stores_a_refresh_token_not_an_access_token():
    """The whole point of the Secrets path is permanence: an access token dies in
    an hour, so the stored blob must be the refresh-token triple that
    token_from_secret() exchanges on every run."""
    from backend.drive_rest import token_from_secret, DriveError
    # An access token alone must be rejected, loudly, rather than half-working.
    with pytest.raises(DriveError, match="client_id"):
        token_from_secret(json.dumps({"access_token": "FAKE-not-a-real-prefix"}))
    # And the accepted shape is exactly what export_drive_secret.py emits.
    import inspect
    src = inspect.getsource(token_from_secret)
    assert '"client_id", "client_secret", "refresh_token"' in src


def test_job_records_never_persist_a_credential(tmp_path, monkeypatch):
    """Params carrying a token must reach the kernel but not the job log.
    GitHub's secret scanner blocked a push over exactly this: a live
    drive_access_token sitting in backend/data/kaggle_jobs.json."""
    import backend.kaggle_dispatch as kd
    monkeypatch.setattr(kd, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(kd, "cli_status", lambda: {
        "installed": True, "authenticated": True, "auth_hint": None})
    monkeypatch.setattr(kd, "_run", lambda a, timeout=0: {
        "ok": True, "out": "Kernel version 1 successfully pushed", "err": ""})

    # Deliberately not a real token prefix: fixtures should not trip
    # secret scanners on their own.
    secret = "FAKE-TOKEN-" + "z" * 40
    kd.dispatch("u", "cq-test-slug", {"kind": "uploadbench", "mb": 200,
                                      "drive_access_token": secret})
    on_disk = (tmp_path / "jobs.json").read_text()
    assert secret not in on_disk
    assert "redacted" in on_disk
    # Non-secret params must survive, or the log stops being useful.
    assert '"mb": 200' in on_disk


def test_redaction_matches_by_substring_not_exact_key():
    """New callers will invent new names; anything that reads like a credential
    has to be caught without updating a list each time."""
    from backend.kaggle_dispatch import _redact
    out = _redact({"my_api_key": "x" * 40, "user_password": "hunter2",
                   "chain": "eth"})
    assert "x" * 40 not in str(out) and "hunter2" not in str(out)
    assert out["chain"] == "eth"


# --------------------------------------------------------------------------
# drive_store: the Drive-side storage convention
# --------------------------------------------------------------------------
def test_dataset_path_and_view_name_agree_with_duckdb_engine():
    """duckdb_engine derives a view name from the leading directory plus every
    key=value value. If drive_store disagreed, the catalog and the dashboard
    would refer to the same data by different names."""
    from backend.drive_store import dataset_path, view_name_for, L2
    p = dataset_path(L2, name="addr_flow_1d", date="2024-01")
    assert p == "chainquant/L2_features/name=addr_flow_1d/date=2024-01"
    assert view_name_for(p) == "features_addr_flow_1d_2024_01"


def test_partition_values_that_would_break_a_path_are_rejected():
    """A slash or a space in a value would either split the path or produce a
    view name that needs quoting everywhere."""
    from backend.drive_store import dataset_path, StoreError, L1
    for bad in ("a/b", "has space", "", "-leading-dash"):
        with pytest.raises(StoreError):
            dataset_path(L1, symbol=bad)
    with pytest.raises(StoreError, match="lower_snake_case"):
        dataset_path(L1, **{"Symbol": "BTCUSDT"})
    with pytest.raises(StoreError, match="layer"):
        dataset_path("L9_nope", symbol="BTCUSDT")


def test_file_planning_warns_below_the_measured_floor():
    """5.9 MB uploaded at 1.53 MB/s against 36.56 MB/s for 200 MB. Writing small
    parts is the most expensive mistake available here, so it must be called out
    rather than silently accepted."""
    from backend.drive_store import plan_files, MIN_FILE_BYTES, MAX_FILE_BYTES
    small = plan_files(6 * 1024 ** 2)
    assert small["parts"] == 1 and small["warning"]
    assert "1.5" in small["warning"] and "36" in small["warning"]

    ok = plan_files(300 * 1024 ** 2)
    assert ok["parts"] == 1 and ok["warning"] is None

    big = plan_files(4 * 1024 ** 3)
    assert big["parts"] >= 8
    assert MIN_FILE_BYTES * 0.7 <= big["part_bytes"] <= MAX_FILE_BYTES


def test_blob_placement_splits_at_the_inline_limit():
    from backend.drive_store import blob_placement, BLOB_INLINE_MAX
    assert blob_placement(1024) == "inline"
    assert blob_placement(BLOB_INLINE_MAX) == "inline"
    assert blob_placement(BLOB_INLINE_MAX + 1) == "standalone"


def test_write_options_carry_compression_and_the_sort_key():
    """zstd is 1.9x smaller than snappy for 0.07s of decompression, and the sort
    key is what makes row-group statistics tight enough to skip on."""
    from backend.drive_store import write_options
    o = write_options(sort_key="block_time")
    assert o["compression"] == "zstd"
    assert o["sort_by"] == "block_time"


def test_catalog_accumulates_across_incremental_writes(tmp_path):
    """Partitions arrive in batches. Overwriting totals would leave the catalog
    describing only the last batch, which is worse than having no catalog."""
    from backend.drive_store import Catalog, L2
    cat = Catalog(str(tmp_path / "datasets.json"))
    cat.upsert(layer=L2, partition_keys={"name": "addr_flow_1d"},
               rows=100, bytes_=1000, files=1, time_min="2024-01", time_max="2024-01")
    cat.upsert(layer=L2, partition_keys={"name": "addr_flow_1d"},
               rows=50, bytes_=500, files=1, time_min="2023-12", time_max="2024-02")
    e = cat.get("features_addr_flow_1d")
    assert e["rows"] == 150 and e["bytes"] == 1500 and e["files"] == 2
    # The span must widen in both directions, not follow the latest write.
    assert e["time_min"] == "2023-12" and e["time_max"] == "2024-02"


def test_catalog_survives_a_corrupt_file(tmp_path):
    from backend.drive_store import Catalog
    p = tmp_path / "datasets.json"
    p.write_text("{not json")
    assert Catalog(str(p)).list() == []


def test_catalog_summary_says_it_excludes_raw_chain_data():
    """The Drive total is a few hundred GB while the raw chain data is 61.31 TB on
    S3. A bare number here would be read as the whole holding."""
    import os as _os
    import tempfile
    from backend.drive_store import Catalog
    with tempfile.TemporaryDirectory() as d:
        summary = Catalog(_os.path.join(d, "c.json")).summary()
    assert "S3" in summary["note"] and summary["total_datasets"] == 0


# --------------------------------------------------------------------------
# s3_views: querying the public dataset in place
# --------------------------------------------------------------------------
def test_chain_prefix_handles_the_nested_provider_layout():
    """Five chains sit one level deeper, under v1.1/sonarx/. Assuming
    "{version}/{chain}/" would silently miss half the catalogue."""
    from backend.s3_views import glob_for
    assert "v1.0/eth/transactions" in glob_for("eth", "transactions")
    assert "v1.1/sonarx/base/traces" in glob_for("base", "traces")
    assert "v1.1/sonarx/arbitrum/logs" in glob_for("arbitrum", "logs")


def test_glob_uses_the_s3_scheme_not_https():
    """DuckDB refuses globs on generic HTTP paths: "Globs (`*`) for generic HTTP
    file is are not supported". Measured -- the https:// form fails outright."""
    from backend.s3_views import glob_for
    g = glob_for("eth", "blocks", "2024-01")
    assert g.startswith("s3://") and "https://" not in g
    assert "date=2024-01*" in g


def test_date_prefix_is_validated():
    from backend.s3_views import glob_for, S3ViewError
    for bad in ("2024/01", "last-week", "24-01", "'; DROP"):
        with pytest.raises(S3ViewError, match="date_prefix"):
            glob_for("eth", "blocks", bad)


def test_unknown_chain_or_table_is_refused_with_the_options():
    from backend.s3_views import glob_for, S3ViewError
    with pytest.raises(S3ViewError, match="unknown chain"):
        glob_for("solana", "blocks")
    with pytest.raises(S3ViewError, match="no table"):
        glob_for("btc", "traces")          # btc has blocks and transactions only


def test_every_measured_table_exists_in_the_layout():
    """The two are edited separately; a size row for a table the layout doesn't
    know would surface as a dashboard entry that cannot be queried."""
    from backend.s3_views import MEASURED, LAYOUT
    for (chain, table) in MEASURED:
        assert chain in LAYOUT, chain
        assert table in LAYOUT[chain]["tables"], (chain, table)


def test_measured_totals_match_the_recorded_measurement():
    """61.31 TB across the sampled tables, measured 2026-07-26. A silent edit to
    a size row would otherwise go unnoticed."""
    from backend.s3_views import measured_total_gb
    assert 62_000 <= measured_total_gb() <= 64_000
    assert 5_800 <= measured_total_gb("eth") <= 6_600
    assert measured_total_gb("base") > measured_total_gb("eth")   # L2 outgrew L1


def test_view_name_is_derivable_without_a_round_trip():
    """The dashboard builds the same name client-side to keep the SQL box in step
    with the picker, so the rule has to be pure."""
    from backend.s3_views import view_name
    assert view_name("eth", "blocks", "2024-01-15") == "s3_eth_blocks_2024_01_15"
    assert view_name("cronos", "decoded-events") == "s3_cronos_decoded_events"


def test_factor_job_reads_only_the_columns_it_needs():
    """token_transfers has 11 columns; the factor needs 5. Measured, one column of
    a 1400 MB file transfers 0.29% of it -- the column list is what decides this
    job's cost, so a SELECT * would quietly multiply it."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "factor", "days": ["2024-01-15"]})
    compile(src, "job.py", "exec")
    assert "SELECT date, token_address, from_address, to_address, value" in src
    for unused in ("transaction_hash", "block_hash", "last_modified"):
        assert unused not in src, unused


def test_factor_output_follows_the_storage_rules():
    """zstd and an explicit time sort, per drive_store: without the sort every row
    group's statistics span the whole period and nothing can be skipped on read."""
    from backend.kaggle_dispatch import render_script
    from backend.drive_store import COMPRESSION
    src = render_script({"kind": "factor", "days": ["2024-01-15"]})
    assert f"COMPRESSION '{COMPRESSION}'" in src
    assert "ORDER BY date, address" in src


def test_factor_narrows_the_glob_to_the_requested_range():
    """A whole-year wildcard costs about twice a single day's, and the difference
    is directory listing rather than data."""
    from backend.kaggle_dispatch import render_script
    src = render_script({"kind": "factor", "days": ["2024-01-15", "2024-01-21"]})
    assert "os.path.commonprefix" in src
    assert "WHERE date BETWEEN" in src


def test_transient_network_errors_retry_but_answers_do_not():
    """A bare SSL EOF against oauth2.googleapis.com killed a dispatch outright.
    Transport hiccups say nothing about the request and must be retried; an HTTP
    status is a real answer and retrying would return the same thing."""
    import backend.drive_rest as dr

    calls = {"n": 0}

    class Boom(dr.urllib.error.URLError):
        def __init__(self):
            super().__init__("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred")

    def flaky(req, timeout=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise Boom()
        class R:
            status = 200
            headers = {}
            def read(self): return b'{"ok": true}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()

    orig_open, orig_sleep = dr.urllib.request.urlopen, dr.time.sleep
    dr.urllib.request.urlopen = flaky
    dr.time.sleep = lambda s: None
    try:
        status, _, body = dr._request("https://oauth2.googleapis.com/token")
        assert status == 200 and calls["n"] == 3
    finally:
        dr.urllib.request.urlopen, dr.time.sleep = orig_open, orig_sleep


def test_certificate_failures_are_not_retried_and_name_the_fix():
    """Retrying a missing CA store just wastes time, and the raw message reads as
    a network outage."""
    import backend.drive_rest as dr

    calls = {"n": 0}

    def always_cert_fail(req, timeout=0):
        calls["n"] += 1
        raise dr.urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] bad")

    orig = dr.urllib.request.urlopen
    dr.urllib.request.urlopen = always_cert_fail
    try:
        with pytest.raises(dr.DriveError, match="SSL_CERT_FILE"):
            dr._request("https://oauth2.googleapis.com/token")
        assert calls["n"] == 1
    finally:
        dr.urllib.request.urlopen = orig


def test_dashboard_has_no_placeholder_strategy_data():
    """The overview carried a hardcoded leaderboard (Alpha-Omega-01 etc.) labelled
    as illustrative. Now that a real factor exists, the panel shows live readings
    instead -- and the fixture names must not creep back in."""
    import pathlib
    src = pathlib.Path("admin-dashboard/src").rglob("*.tsx")
    for f in src:
        text = f.read_text()
        for fake in ("Alpha-Omega-01", "Mean-Rev-BTC", "Arb-Flash-Bot"):
            assert fake not in text, f"{fake} still present in {f.name}"


# --------------------------------------------------------------------------
# Multi-account placement
# --------------------------------------------------------------------------
def _acc(idx, used_tb, limit_tb=5.0, connected=True):
    tb = 1024 ** 4
    return {"account_index": idx, "is_connected": connected,
            "used": int(used_tb * tb), "limit": int(limit_tb * tb),
            "free": int((limit_tb - used_tb) * tb)}


def test_placement_prefers_where_the_dataset_already_lives():
    """Moving a dataset would orphan the folder id every reader holds."""
    from backend.drive_store import choose_account
    accs = [_acc("acc-01", 0.1), _acc("acc-02", 4.0)]
    # acc-01 has far more room, but the dataset is already on acc-02.
    assert choose_account(accs, existing="acc-02") == "acc-02"
    assert choose_account(accs) == "acc-01"          # no history: most free wins


def test_placement_skips_accounts_near_their_limit():
    """Drive slows and starts refusing writes near the limit, so headroom is kept
    rather than filling an account to the brim."""
    from backend.drive_store import choose_account, ACCOUNT_HEADROOM
    assert ACCOUNT_HEADROOM < 1.0
    accs = [_acc("full", 4.8), _acc("roomy", 1.0)]
    assert choose_account(accs, need_bytes=10 * 1024 ** 3) == "roomy"


def test_placement_honours_a_domain_pin_when_it_fits():
    from backend.drive_store import choose_account, DOMAIN_PINS
    DOMAIN_PINS["news"] = "acc-02"
    try:
        accs = [_acc("acc-01", 0.1), _acc("acc-02", 2.0)]
        assert choose_account(accs, domain="news") == "acc-02"
        # A pinned account with no room must not win.
        assert choose_account([_acc("acc-01", 0.1), _acc("acc-02", 4.9)],
                              domain="news", need_bytes=10 * 1024 ** 3) == "acc-01"
    finally:
        DOMAIN_PINS.pop("news", None)


def test_placement_failure_names_the_connected_accounts():
    """"no space" without saying where you looked is unactionable."""
    from backend.drive_store import choose_account, PlacementError
    with pytest.raises(PlacementError, match="acc-01"):
        choose_account([_acc("acc-01", 4.9)], need_bytes=int(0.5 * 1024 ** 4))
    with pytest.raises(PlacementError, match="no connected"):
        choose_account([_acc("acc-01", 0.1, connected=False)])


def test_catalog_never_silently_relocates_a_dataset(tmp_path):
    from backend.drive_store import Catalog, L2
    cat = Catalog(str(tmp_path / "c.json"))
    cat.upsert(layer=L2, partition_keys={"name": "f"}, account="acc-01", bytes_=10)
    cat.upsert(layer=L2, partition_keys={"name": "f"}, account="acc-02", bytes_=10)
    assert cat.get("features_f")["account"] == "acc-01"


def test_placement_report_separates_drive_usage_from_ours():
    """Drive's figure covers the whole account; ours covers only what this
    platform wrote. Conflating them would read as a discrepancy."""
    from backend.drive_store import placement_report
    rep = placement_report([_acc("acc-01", 1.0)],
                           [{"account": "acc-01", "bytes": 5000, "partition_keys": ["domain"]},
                            {"account": None, "bytes": 1}])
    a = rep["accounts"][0]
    assert a["our_bytes"] == 5000 and a["drive_used"] > a["our_bytes"]
    assert rep["unplaced_datasets"] == 1
    assert "不跨账号拆分" in rep["note"]


# --------------------------------------------------------------------------
# Source registry
# --------------------------------------------------------------------------
def test_in_place_sources_declare_no_fetch():
    """Declaring both a locator and a fetch is exactly the confusion this mode
    exists to prevent: measured, copying the AWS dataset in would cost ~62 hours
    to end up slower and coarser than querying it where it is."""
    from backend.sources import Source, SourceError, IN_PLACE
    with pytest.raises(SourceError, match="must not define fetch"):
        Source(name="x", domain="chain", mode=IN_PLACE, locator="s3://b",
               fetch=lambda: None)
    with pytest.raises(SourceError, match="needs a locator"):
        Source(name="x", domain="chain", mode=IN_PLACE)


def test_batch_and_poll_sources_must_declare_partitioning_and_time():
    from backend.sources import Source, SourceError, BATCH
    with pytest.raises(SourceError, match="partition_keys"):
        Source(name="x", domain="news", mode=BATCH, fetch=lambda: None)
    with pytest.raises(SourceError, match="time_column"):
        Source(name="x", domain="news", mode=BATCH, fetch=lambda: None,
               partition_keys=["domain"])


def test_registry_marks_which_sources_consume_drive():
    """in_place sources must not be counted as Drive consumers -- that is the
    whole point of the mode."""
    from backend.sources import catalogue, get
    c = catalogue()
    by_name = {s["name"]: s for s in c["sources"]}
    assert by_name["aws_chain"]["stored_on_drive"] is False
    assert by_name["binance_klines"]["stored_on_drive"] is True
    assert get("aws_chain").fetch is None


def test_source_output_path_matches_the_storage_convention():
    from backend.sources import get
    p = get("addr_flow_1d").output_path(name="addr_flow_1d", date="2024-01")
    assert p == "chainquant/L2_features/name=addr_flow_1d/date=2024-01"


# --------------------------------------------------------------------------
# collections: Apify-shaped storage for collection work
# --------------------------------------------------------------------------
def test_dataset_buffers_instead_of_writing_every_batch(tmp_path):
    """A scraper emits tens of records at a time. Writing each batch out would
    produce thousands of tiny files, and 5.9 MB uploads at 1.53 MB/s against
    36.56 MB/s for 200 MB -- a 24x penalty paid again on every read."""
    from backend.collections import Dataset
    ds = Dataset("news", str(tmp_path), time_column="ts")
    for i in range(50):
        out = ds.push([{"ts": i, "title": f"t{i}"}])
        assert out["flushed"] is None          # nothing written yet
    assert ds.stats()["files"] == 0
    assert ds.stats()["buffered_records"] == 50


def test_dataset_flush_sorts_by_time_and_uses_zstd(tmp_path):
    """Unsorted output means every row group's statistics span the whole period
    and a predicate can skip nothing."""
    import polars as pl
    from backend.collections import Dataset
    ds = Dataset("news", str(tmp_path), time_column="ts")
    ds.push([{"ts": 3, "v": "c"}, {"ts": 1, "v": "a"}, {"ts": 2, "v": "b"}])
    info = ds.flush("explicit")
    got = pl.read_parquet(info["path"])
    assert got["ts"].to_list() == [1, 2, 3]
    meta = pl.read_parquet_schema(info["path"])
    assert set(meta) == {"ts", "v"}


def test_early_flush_warns_with_both_rates(tmp_path):
    """Flushing small is sometimes right -- a crawl ending, a shift boundary --
    but the cost is real and must not be silent."""
    from backend.collections import Dataset
    ds = Dataset("news", str(tmp_path), time_column="ts")
    ds.push([{"ts": 1, "v": "a"}])
    info = ds.flush("explicit")
    assert "warning" in info
    assert "1.5" in info["warning"] and "36" in info["warning"]
    assert ds.stats()["undersized_files"] == 1


def test_size_triggered_flush_is_not_warned_about(tmp_path):
    from backend.collections import Dataset
    ds = Dataset("news", str(tmp_path), time_column="ts", flush_bytes=200)
    ds.push([{"ts": i, "body": "x" * 50} for i in range(20)])
    assert ds.stats()["files"] == 1
    assert "warning" not in ds.files[0]
    assert ds.files[0]["reason"] == "size"


def test_kv_store_keeps_small_blobs_out_of_standalone_files(tmp_path):
    """Thousands of small standalone uploads is the same 24x trap; small blobs
    wait to be packed into one Parquet file with a BLOB column."""
    from backend.collections import KVStore
    kv = KVStore("shots", str(tmp_path))
    small = kv.put("page-1.html", b"<html>" + b"x" * 1000)
    big = kv.put("video.bin", b"y" * (9 * 1024 * 1024))
    assert small["placement"] == "inline" and small["packed"] is False
    assert big["placement"] == "standalone" and os.path.exists(big["path"])

    packed = kv.pack()
    assert packed["keys"] == 1
    assert kv.locate("page-1.html")["packed"] is True
    assert kv.locate("page-1.html")["pack_file"] == packed["path"]
    assert kv.stats()["pending_pack"] == 0


def test_kv_index_survives_a_restart(tmp_path):
    """drive.file cannot browse and Drive listings are slow, so a key that could
    only be found by scanning would be unusable from an MCP call."""
    from backend.collections import KVStore
    KVStore("shots", str(tmp_path)).put("a", b"1")
    assert KVStore("shots", str(tmp_path)).locate("a")["sha256"]


def test_request_queue_dedupes_and_survives_restart(tmp_path):
    from backend.collections import RequestQueue
    q = RequestQueue("crawl", str(tmp_path))
    k1, added1 = q.add("https://x.test/a")
    k2, added2 = q.add("https://x.test/a")
    assert k1 == k2 and added1 is True and added2 is False

    r = q.reserve()
    assert r["state"] == "running" and r["attempts"] == 1
    # A fresh instance must see the same position -- a 12h session cap means any
    # real crawl outlives one session.
    assert RequestQueue("crawl", str(tmp_path)).stats()["running"] == 1


def test_request_queue_retries_then_gives_up(tmp_path):
    """A transient failure should not cost the URL; a permanent one should not
    retry forever."""
    from backend.collections import RequestQueue
    q = RequestQueue("crawl", str(tmp_path), max_attempts=2)
    key, _ = q.add("https://x.test/a")
    q.reserve(); r = q.fail(key, "timeout")
    assert r["state"] == "pending"           # attempt 1 of 2
    q.reserve(); r = q.fail(key, "timeout")
    assert r["state"] == "failed"            # attempts exhausted
    assert q.stats()["failed"] == 1


def test_stale_running_requests_are_reclaimed(tmp_path):
    """Whatever was in flight when a session was killed would otherwise be stuck
    in RUNNING forever."""
    from backend.collections import RequestQueue
    q = RequestQueue("crawl", str(tmp_path))
    key, _ = q.add("https://x.test/a")
    q.reserve()
    q.requests[key]["reserved_at"] = time.time() - 7200
    assert q.reclaim_stale(older_than_s=3600) == 1
    assert q.stats()["pending"] == 1


def test_range_read_builds_an_inclusive_header(monkeypatch):
    """Drive honours Range on alt=media -- verified 2026-07-26, a 1 MiB request
    against a 200 MB file returned 206 with Content-Range 0-1048575/209715200."""
    import backend.drive_rest as dr
    seen = {}

    def fake(url, method="GET", data=None, headers=None, timeout=0):
        seen["url"], seen["headers"] = url, headers
        return 206, {}, b"x" * 16

    monkeypatch.setattr(dr, "_request", fake)
    dr.read_range("tok", "fid", 0, 15)
    assert seen["headers"]["Range"] == "bytes=0-15"
    assert "alt=media" in seen["url"]


def test_range_read_rejects_a_backwards_range():
    from backend.drive_rest import read_range, DriveError
    with pytest.raises(DriveError, match="bad range"):
        read_range("tok", "fid", 10, 5)


def test_duckdb_attach_passes_the_token_as_a_header_not_a_url_param(monkeypatch):
    """A token in the URL would land in DuckDB's query log and in any error
    message quoting the SQL."""
    import backend.drive_rest as dr
    executed = []

    class FakeCon:
        def execute(self, sql):
            executed.append(sql)

    dr.duckdb_attach(FakeCon(), "SECRET-TOKEN")
    joined = " ".join(executed)
    assert "httpfs" in joined
    assert "EXTRA_HTTP_HEADERS" in joined and "Authorization" in joined
    assert "?access_token=" not in joined


def test_media_url_shape():
    from backend.drive_rest import media_url
    assert media_url("abc") == "https://www.googleapis.com/drive/v3/files/abc?alt=media"


# --------------------------------------------------------------------------
# compaction
# --------------------------------------------------------------------------
def _write_parquet(path, rows, cols=("ts", "v")):
    import polars as pl
    data = {c: [f"{c}{i}" if c == "v" else i for i in rows] for c in cols}
    pl.DataFrame(data).write_parquet(path, compression="zstd")
    return path


def test_plan_leaves_already_large_files_alone(tmp_path):
    """Rewriting a file that is already big enough spends the upload cost again
    for no read-side gain."""
    from backend.compaction import plan
    big = _write_parquet(str(tmp_path / "big.parquet"), range(200_000))
    _write_parquet(str(tmp_path / "a.parquet"), range(10))
    _write_parquet(str(tmp_path / "b.parquet"), range(10, 20))

    p = plan(str(tmp_path), min_bytes=os.path.getsize(big))
    assert big in p["already_ok"]
    assert all(big not in g for g in p["groups"])
    assert len(p["groups"]) == 1 and len(p["groups"][0]) == 2


def test_compaction_preserves_every_row_and_the_sort(tmp_path):
    import polars as pl
    from backend.compaction import compact_group
    a = _write_parquet(str(tmp_path / "a.parquet"), [5, 1, 9])
    b = _write_parquet(str(tmp_path / "b.parquet"), [7, 2])

    r = compact_group([a, b], time_column="ts")
    assert r["rows"] == 5
    assert not os.path.exists(a) and not os.path.exists(b)
    got = pl.read_parquet(r["output"])
    assert got["ts"].to_list() == [1, 2, 5, 7, 9]     # re-sorted, not concatenated


def test_mismatched_schemas_are_refused_not_coerced(tmp_path):
    """Silently widening a schema during a merge would change what downstream
    readers see without anyone asking for it."""
    from backend.compaction import compact_group, CompactionError
    a = _write_parquet(str(tmp_path / "a.parquet"), [1, 2], cols=("ts", "v"))
    b = _write_parquet(str(tmp_path / "b.parquet"), [3, 4], cols=("ts", "v", "extra"))
    with pytest.raises(CompactionError, match="differing columns"):
        compact_group([a, b])
    # Both inputs must survive a refusal.
    assert os.path.exists(a) and os.path.exists(b)


def test_row_count_mismatch_aborts_without_deleting_inputs(tmp_path, monkeypatch):
    """The verify-before-delete step is the one thing standing between a bug and
    data loss, so it has to be exercised."""
    import polars as pl
    import backend.compaction as comp
    a = _write_parquet(str(tmp_path / "a.parquet"), [1, 2])
    b = _write_parquet(str(tmp_path / "b.parquet"), [3, 4])

    real = comp._read_meta

    def lying_meta(path):
        rows, cols = real(path)
        # Pretend the merged output lost a row.
        return (rows - 1, cols) if "compacted" in path else (rows, cols)

    monkeypatch.setattr(comp, "_read_meta", lying_meta)
    with pytest.raises(comp.CompactionError, match="row count mismatch"):
        comp.compact_group([a, b], time_column="ts")
    assert os.path.exists(a) and os.path.exists(b)
    # And no temporary file is left behind.
    assert not [f for f in os.listdir(tmp_path) if f.endswith(comp.TMP_SUFFIX)]


def test_a_single_small_file_is_reported_not_rewritten(tmp_path):
    from backend.compaction import plan
    only = _write_parquet(str(tmp_path / "a.parquet"), [1])
    p = plan(str(tmp_path))
    assert p["groups"] == [] and p["singletons"] == [only]


def test_one_bad_group_does_not_stop_the_rest(tmp_path):
    from backend.compaction import compact
    good = tmp_path / "good"; good.mkdir()
    _write_parquet(str(good / "a.parquet"), [1, 2])
    _write_parquet(str(good / "b.parquet"), [3, 4])
    _write_parquet(str(good / "c.parquet"), [5, 6], cols=("ts", "v", "extra"))

    out = compact(str(good), time_column="ts", max_bytes=10_000_000)
    # Either it merged what it could or it reported why; it must not raise.
    assert out["merged_groups"] + len(out["failures"]) >= 1


def test_dry_run_changes_nothing(tmp_path):
    from backend.compaction import compact
    a = _write_parquet(str(tmp_path / "a.parquet"), [1, 2])
    b = _write_parquet(str(tmp_path / "b.parquet"), [3, 4])
    out = compact(str(tmp_path), time_column="ts", dry_run=True)
    assert out["dry_run"] is True
    assert os.path.exists(a) and os.path.exists(b)


def test_compaction_endpoint_refuses_paths_outside_the_data_dir():
    """This is the only endpoint that deletes files, so the path must not be able
    to point anywhere else."""
    import inspect
    import backend.api_server as api
    src = inspect.getsource(api.storage_compact)
    assert "os.path.abspath" in src
    assert "startswith(root" in src
    assert "dry_run" in inspect.signature(api.CompactionRequest).parameters or \
           "dry_run" in api.CompactionRequest.model_fields
    # Defaulting to a real run would make an accidental call destructive.
    assert api.CompactionRequest.model_fields["dry_run"].default is True


# --------------------------------------------------------------------------
# pump: bulk drain and incremental watch
# --------------------------------------------------------------------------
def test_cursor_is_written_after_the_records_not_before(tmp_path):
    """Advancing first and crashing second silently skips whatever was in flight.
    A duplicate is cheap; a hole is permanent."""
    from backend.pump import pump, RateLimiter

    order = []

    def fetch(pos):
        i = pos or 0
        return ([{"i": i}], i + 1 if i < 2 else None)

    def sink(rows):
        order.append(("sink", rows[0]["i"]))

    class SpyCursorOrder:
        pass

    import backend.pump as pmod
    real_advance = pmod.Cursor.advance

    def spy_advance(self, **kw):
        order.append(("cursor", kw.get("position")))
        return real_advance(self, **kw)

    pmod.Cursor.advance = spy_advance
    try:
        pump(name="t", state_root=str(tmp_path), fetch=fetch, sink=sink,
             limiter=RateLimiter(rate_per_s=1e6))
    finally:
        pmod.Cursor.advance = real_advance

    # Every sink call must precede the cursor write that covers it.
    assert order[0][0] == "sink" and order[1][0] == "cursor"
    assert [o[0] for o in order] == ["sink", "cursor"] * (len(order) // 2)


def test_pump_resumes_from_disk_after_a_restart(tmp_path):
    """A free runtime is capped at 12 hours; any real backfill outlives one."""
    from backend.pump import pump, RateLimiter
    seen_positions = []

    def fetch(pos):
        i = pos or 0
        seen_positions.append(i)
        return ([{"i": i}], i + 1 if i < 5 else None)

    fast = lambda: RateLimiter(rate_per_s=1e6)
    r1 = pump(name="t", state_root=str(tmp_path), fetch=fetch, sink=lambda x: None,
              limiter=fast(), max_pages=3)
    assert r1["status"] == "max_pages" and r1["pages_this_run"] == 3

    r2 = pump(name="t", state_root=str(tmp_path), fetch=fetch, sink=lambda x: None,
              limiter=fast())
    assert r2["status"] == "exhausted"
    # It picked up where it left off rather than starting over.
    assert seen_positions == [0, 1, 2, 3, 4, 5]


def test_exhausted_source_is_not_re_pumped(tmp_path):
    from backend.pump import pump, RateLimiter
    calls = {"n": 0}

    def fetch(pos):
        calls["n"] += 1
        return ([{"i": 1}], None)

    args = dict(name="t", state_root=str(tmp_path), sink=lambda x: None)
    pump(fetch=fetch, limiter=RateLimiter(rate_per_s=1e6), **args)
    out = pump(fetch=fetch, limiter=RateLimiter(rate_per_s=1e6), **args)
    assert out["status"] == "already_exhausted" and calls["n"] == 1


def test_server_retry_after_outranks_the_configured_rate(tmp_path):
    """The source knows its limits; our configured rate is a guess."""
    from backend.pump import pump, RateLimiter, RateLimited
    slept = []
    lim = RateLimiter(rate_per_s=1e6, sleeper=slept.append)
    state = {"n": 0}

    def fetch(pos):
        state["n"] += 1
        if state["n"] == 1:
            raise RateLimited("slow down", retry_after=42)
        return ([{"i": 1}], None)

    out = pump(name="t", state_root=str(tmp_path), fetch=fetch,
               sink=lambda x: None, limiter=lim)
    assert out["status"] == "exhausted"
    assert 42 in slept


def test_absurd_retry_after_is_capped(tmp_path):
    """A bad header must not hang the run for a day."""
    from backend.pump import RateLimiter, MAX_BACKOFF_S
    slept = []
    lim = RateLimiter(rate_per_s=1e6, sleeper=slept.append)
    lim.penalise(86400)
    assert slept == [MAX_BACKOFF_S]


def test_fetch_failure_leaves_the_cursor_where_it_was(tmp_path):
    """A retry must re-read the failed page, not skip it."""
    from backend.pump import pump, PumpError, RateLimiter, Cursor

    def fetch(pos):
        raise ValueError("network went away")

    with pytest.raises(PumpError, match="network went away"):
        pump(name="t", state_root=str(tmp_path), fetch=fetch, sink=lambda x: None,
             limiter=RateLimiter(rate_per_s=1e6))
    assert Cursor("t", str(tmp_path)).state["position"] is None


def test_watch_stores_only_what_it_has_not_seen(tmp_path):
    """Every "since" API returns overlap at its boundary; without dedup that
    overlap is stored again on every visit."""
    from backend.pump import watch, RateLimiter
    stored = []
    batch = [{"id": 1, "ts": "2024-01-01"}, {"id": 2, "ts": "2024-01-02"}]

    args = dict(name="w", state_root=str(tmp_path), sink=stored.extend,
                id_of=lambda r: r["id"], watermark_of=lambda r: r["ts"])
    r1 = watch(fetch=lambda wm: batch, limiter=RateLimiter(rate_per_s=1e6), **args)
    assert r1["stored"] == 2 and r1["duplicates"] == 0

    r2 = watch(fetch=lambda wm: batch, limiter=RateLimiter(rate_per_s=1e6), **args)
    assert r2["stored"] == 0 and r2["duplicates"] == 2
    assert len(stored) == 2


def test_watermark_never_moves_backwards(tmp_path):
    """An out-of-order page would otherwise open a permanent gap."""
    from backend.pump import watch, RateLimiter
    args = dict(name="w", state_root=str(tmp_path), sink=lambda x: None,
                id_of=lambda r: r["id"], watermark_of=lambda r: r["ts"])
    watch(fetch=lambda wm: [{"id": 1, "ts": "2024-06-01"}],
          limiter=RateLimiter(rate_per_s=1e6), **args)
    out = watch(fetch=lambda wm: [{"id": 2, "ts": "2024-01-01"}],
                limiter=RateLimiter(rate_per_s=1e6), **args)
    assert out["watermark"] == "2024-06-01"


def test_rate_limiter_actually_limits():
    """A pump with no limiter gets the account banned."""
    from backend.pump import RateLimiter
    slept = []
    now = {"t": 0.0}
    lim = RateLimiter(rate_per_s=2, burst=1, sleeper=slept.append,
                      clock=lambda: now["t"])
    lim.acquire()          # uses the single burst token
    lim.acquire()          # must wait 1/2 s
    assert slept and abs(slept[0] - 0.5) < 1e-6


def test_poll_sources_must_pick_a_strategy():
    """Draining and watching keep different state and fail differently, so the
    choice cannot be left implicit."""
    from backend.sources import Source, SourceError, POLL, PUMP, TEXT, L1
    with pytest.raises(SourceError, match="strategy"):
        Source(name="x", domain="news", mode=POLL, shape=TEXT, layer=L1,
               partition_keys=["domain"], fetch=lambda: None, time_column="ts")
    ok = Source(name="x", domain="news", mode=POLL, shape=TEXT, layer=L1,
                partition_keys=["domain"], fetch=lambda: None, time_column="ts",
                strategy=PUMP)
    assert ok.summary()["strategy"] == "pump"


def test_strategy_is_rejected_on_non_poll_sources():
    from backend.sources import Source, SourceError, BATCH, PUMP
    with pytest.raises(SourceError, match="only applies to poll"):
        Source(name="x", domain="market", mode=BATCH, fetch=lambda: None,
               partition_keys=["domain"], time_column="ts", strategy=PUMP)


# --------------------------------------------------------------------------
# Catalog backup
# --------------------------------------------------------------------------
class _FakeDrive:
    def __init__(self):
        self.files = {}
        self.folders = {}

    def ensure_path(self, token, path):
        self.folders.setdefault(path, f"folder-{len(self.folders)}")
        return self.folders[path]

    def find_file(self, token, name, parent=None):
        key = (parent, name)
        return {"id": key} if key in self.files else None

    def upload(self, token, local_path, parent_id, name=None):
        name = name or os.path.basename(local_path)
        with open(local_path, "rb") as f:
            self.files[(parent_id, name)] = f.read()
        return {"id": (parent_id, name), "bytes": len(self.files[(parent_id, name)])}

    def download(self, token, file_id, dest_path):
        with open(dest_path, "wb") as f:
            f.write(self.files[file_id])
        return {"bytes": len(self.files[file_id])}


def test_catalog_backup_round_trip(tmp_path):
    """Which account holds a dataset exists nowhere but the catalog; losing it
    would mean searching every connected account to find out."""
    from backend.drive_store import Catalog, L2
    drive = _FakeDrive()
    cat = Catalog(str(tmp_path / "datasets.json"))
    cat.upsert(layer=L2, partition_keys={"name": "f"}, account="acc-02",
               rows=10, bytes_=100)
    cat.backup_to_drive(drive, "tok")

    # Losing the catalog means losing the directory, not one file: the shard
    # holds the same entries.
    import shutil
    shutil.rmtree(os.path.dirname(cat.path))
    fresh = Catalog(str(tmp_path / "datasets.json"))
    assert fresh.list() == []
    fresh.restore_from_drive(drive, "tok")
    assert fresh.get("features_f")["account"] == "acc-02"


def test_restore_refuses_to_clobber_a_newer_local_catalog(tmp_path):
    """Overwriting by default would turn a stale backup into data loss on a
    machine that happened to be ahead."""
    from backend.drive_store import Catalog, L2
    drive = _FakeDrive()
    cat = Catalog(str(tmp_path / "datasets.json"))
    cat.upsert(layer=L2, partition_keys={"name": "old"}, account="acc-01")
    cat.backup_to_drive(drive, "tok")
    cat.upsert(layer=L2, partition_keys={"name": "new"}, account="acc-01")

    out = cat.restore_from_drive(drive, "tok")
    assert out["restored"] is False and "overwrite" in out["reason"]
    assert cat.get("features_new") is not None      # local work survived

    forced = cat.restore_from_drive(drive, "tok", overwrite=True)
    assert forced["restored"] is True


def test_backup_replaces_rather_than_accumulates(tmp_path):
    """An old catalog is worse than none: it points confidently at datasets that
    have since moved."""
    from backend.drive_store import Catalog, L2
    drive = _FakeDrive()
    cat = Catalog(str(tmp_path / "datasets.json"))
    cat.upsert(layer=L2, partition_keys={"name": "f"}, account="acc-01")
    first = cat.backup_to_drive(drive, "tok")
    second = cat.backup_to_drive(drive, "tok")
    assert first["replaced"] is False and second["replaced"] is True
    assert len(drive.files) == 1


def test_backup_of_a_missing_catalog_is_an_error(tmp_path):
    from backend.drive_store import Catalog, StoreError
    cat = Catalog(str(tmp_path / "nope.json"))
    with pytest.raises(StoreError, match="does not exist"):
        cat.backup_to_drive(_FakeDrive(), "tok")


# --------------------------------------------------------------------------
# Concurrency across several Drive accounts
# --------------------------------------------------------------------------
def test_folder_lookup_is_deterministic_when_duplicates_exist(monkeypatch):
    """Verified against a live account: Drive permits two folders with the same
    name under one parent, so a find-then-create race really does produce them.
    Every writer must then converge on the same one, or the dataset splits in
    half and each holder sees only its own files."""
    import backend.drive_rest as dr
    monkeypatch.setattr(dr, "find_folders", lambda t, n, p=None: [
        {"id": "zzz", "name": n}, {"id": "aaa", "name": n}, {"id": "mmm", "name": n}])
    assert dr.find_folder("tok", "x") == "aaa"
    # Order returned by the API must not change the answer.
    monkeypatch.setattr(dr, "find_folders", lambda t, n, p=None: [
        {"id": "aaa", "name": n}, {"id": "zzz", "name": n}, {"id": "mmm", "name": n}])
    assert dr.find_folder("tok", "x") == "aaa"


def test_ensure_folder_rechecks_after_creating(monkeypatch):
    """The id just created may not be the one everyone else will use."""
    import backend.drive_rest as dr
    state = {"created": False}
    monkeypatch.setattr(dr, "find_folders", lambda t, n, p=None:
                        [{"id": "aaa"}, {"id": "bbb"}] if state["created"] else [])
    def fake_json(url, **kw):
        state["created"] = True
        return {"id": "bbb"}          # our own creation loses the tie-break
    monkeypatch.setattr(dr, "_json_request", fake_json)
    assert dr.ensure_folder("tok", "x") == "aaa"


def test_concurrent_writers_do_not_lose_each_others_entries(tmp_path):
    """Read-modify-write on one shared file loses entries: both writers read,
    both append, the second save erases the first's work."""
    from backend.drive_store import Catalog, L2
    path = str(tmp_path / "datasets.json")
    a = Catalog(path, shard="s1")
    b = Catalog(path, shard="s2")
    a.upsert(layer=L2, partition_keys={"name": "f"}, rows=100, bytes_=1000,
             account="acc-01", time_min="2024-01", time_max="2024-01")
    b.upsert(layer=L2, partition_keys={"name": "f"}, rows=50, bytes_=500,
             account="acc-02", time_min="2023-12", time_max="2024-02")

    merged = a.load_all_shards()["features_f"]
    assert merged["rows"] == 150 and merged["bytes"] == 1500
    assert merged["time_min"] == "2023-12" and merged["time_max"] == "2024-02"
    # Placement must not flip between shards.
    assert merged["account"] == "acc-01"


def test_an_unreadable_shard_does_not_hide_the_others(tmp_path):
    from backend.drive_store import Catalog, L2
    path = str(tmp_path / "datasets.json")
    a = Catalog(path, shard="good")
    a.upsert(layer=L2, partition_keys={"name": "f"}, rows=1)
    (tmp_path / "shards" / "broken.json").write_text("{not json")
    assert "features_f" in Catalog(path, shard="good").load_all_shards()


def test_concurrent_writers_spread_across_accounts():
    """All writers see the same "most free" account and would pile onto it,
    overfilling one while the rest idle -- and the 750 GB/day cap is per account,
    so that also serialises what could have run in parallel."""
    from backend.drive_store import choose_account
    tb = 1024 ** 4
    accs = [{"account_index": f"acc-{i}", "is_connected": True,
             "used": int(0.5 * tb), "limit": int(5 * tb), "free": int(4.5 * tb)}
            for i in range(4)]
    picks = {choose_account(accs, writer=f"w{i}") for i in range(8)}
    assert len(picks) > 1

    # Deterministic: the same writer always lands in the same place, so a
    # restarted session keeps writing where it was.
    assert choose_account(accs, writer="w1") == choose_account(accs, writer="w1")
    # And a single writer still gets the emptiest account.
    assert choose_account(accs) == "acc-0"


def test_spreading_never_picks_an_account_without_room():
    from backend.drive_store import choose_account
    tb = 1024 ** 4
    accs = [
        {"account_index": "full", "is_connected": True, "used": int(4.95 * tb),
         "limit": int(5 * tb), "free": int(0.05 * tb)},
        {"account_index": "roomy", "is_connected": True, "used": int(1 * tb),
         "limit": int(5 * tb), "free": int(4 * tb)},
    ]
    for i in range(10):
        assert choose_account(accs, writer=f"w{i}",
                              need_bytes=int(0.5 * tb)) == "roomy"


def test_every_reader_agrees_on_the_merged_total(tmp_path):
    """The bug this pins: the main file was both an input and an output, so a
    reader that loaded it and then merged the shards counted the same rows twice.
    Two writers each saw 150 while a third saw 200, then 300."""
    from backend.drive_store import Catalog, L2
    import json as _json
    path = str(tmp_path / "datasets.json")
    a = Catalog(path, shard="s1")
    b = Catalog(path, shard="s2")
    a.upsert(layer=L2, partition_keys={"name": "f"}, rows=100, account="acc-01")
    b.upsert(layer=L2, partition_keys={"name": "f"}, rows=50, account="acc-02")

    views = [
        a.load_all_shards()["features_f"]["rows"],
        b.load_all_shards()["features_f"]["rows"],
        Catalog(path, shard="s3").load_all_shards()["features_f"]["rows"],
        _json.load(open(path))["datasets"][0]["rows"],
    ]
    assert views == [150, 150, 150, 150], views


def test_a_pre_sharding_catalog_is_not_lost(tmp_path):
    """An install that predates sharding has entries only in the main file."""
    import json as _json
    from backend.drive_store import Catalog
    path = tmp_path / "datasets.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(_json.dumps({"datasets": [
        {"dataset": "old_one", "layer": "L2_features", "rows": 7, "updated_at": 1}]}))
    assert Catalog(str(path)).get("old_one")["rows"] == 7


def test_two_writers_on_one_dataset_do_not_overwrite_each_other(tmp_path):
    """Verified before the fix: both sessions started at part-00000 and the
    second overwrote the first locally. On Drive, which allows duplicate names,
    it would instead leave two files a glob reads as duplicated rows."""
    import polars as pl
    from backend.collections import Dataset
    a = Dataset("news", str(tmp_path), time_column="ts", writer="s1")
    b = Dataset("news", str(tmp_path), time_column="ts", writer="s2")
    a.push([{"ts": 1, "v": "A"}]); a.flush("x")
    b.push([{"ts": 2, "v": "B"}]); b.flush("x")

    files = sorted(os.listdir(a.dir))
    assert len(files) == 2, files
    rows = pl.read_parquet(os.path.join(a.dir, "*.parquet")).sort("ts").to_dicts()
    assert rows == [{"ts": 1, "v": "A"}, {"ts": 2, "v": "B"}]


def test_writer_id_defaults_to_something_unique_per_process(tmp_path):
    from backend.collections import Dataset
    ds = Dataset("x", str(tmp_path), time_column="ts")
    assert ds.writer and ds.writer != ""


def test_sharded_queue_gives_every_url_to_exactly_one_worker(tmp_path):
    """Verified before the fix: two crawlers sharing one file both reserved the
    same URL, because reserve() is a read-modify-write with nothing serialising
    it. Partitioning the key space removes the race rather than locking around
    it."""
    from backend.collections import RequestQueue
    workers = [RequestQueue("crawl", str(tmp_path), worker=i, workers=3) for i in range(3)]
    urls = [f"https://x.test/{i}" for i in range(30)]
    for q in workers:
        for u in urls:
            q.add(u)          # every worker sees every URL; each keeps only its own

    assert sum(q.stats()["total"] for q in workers) == len(urls)

    fetched = []
    for q in workers:
        while True:
            r = q.reserve()
            if not r:
                break
            fetched.append(r["url"])
            q.complete(r["key"])
    assert sorted(fetched) == sorted(urls)          # none missed
    assert len(fetched) == len(set(fetched))        # none twice


def test_each_queue_worker_has_its_own_file(tmp_path):
    from backend.collections import RequestQueue
    a = RequestQueue("crawl", str(tmp_path), worker=0, workers=2)
    b = RequestQueue("crawl", str(tmp_path), worker=1, workers=2)
    assert a.path != b.path
    # A single-worker queue keeps the original filename, so existing state loads.
    assert RequestQueue("crawl", str(tmp_path)).path.endswith("queue.json")


def test_queue_worker_index_is_validated(tmp_path):
    from backend.collections import RequestQueue, CollectionError
    with pytest.raises(CollectionError, match="outside"):
        RequestQueue("crawl", str(tmp_path), worker=5, workers=3)


# --------------------------------------------------------------------------
# scheduler: dynamic parallelism
# --------------------------------------------------------------------------
def _accounts(n, used_tb=0.0):
    tb = 1024 ** 4
    return [{"account_index": f"acc-{i:02d}", "is_connected": True,
             "used": int(used_tb * tb), "limit": int(5 * tb),
             "free": int((5 - used_tb) * tb)} for i in range(n)]


def test_small_jobs_are_not_split():
    """At ~32 MB/s and ~30s of startup, a 200 MB job spends most of its life
    starting up; every shard pays that 30s in parallel, so splitting cannot
    shorten it."""
    from backend.scheduler import plan_job
    p = plan_job(200 * 1024 ** 2, accounts=_accounts(15))
    assert p.slots and len(p.slots) == 1
    assert p.limited_by == "job_too_small_to_split"
    assert any("startup" in w for w in p.warnings)


def test_colab_session_cap_is_enforced_as_exact():
    """A fourth Colab session is refused outright with Precondition Failed, so
    asking for more is a caller error rather than something to clamp silently."""
    from backend.scheduler import plan_job, SchedulerError, COLAB
    with pytest.raises(SchedulerError, match="at most 3"):
        plan_job(100 * 1024 ** 3, accounts=_accounts(9), platforms={COLAB: 4})


def test_kaggle_cap_is_treated_as_a_floor_not_a_ceiling():
    """5 concurrent ran with no queueing and the real limit was never reached, so
    the number must not be presented as exact."""
    from backend.scheduler import PLATFORMS, KAGGLE, COLAB
    assert PLATFORMS[KAGGLE]["limit_is_exact"] is False
    assert PLATFORMS[COLAB]["limit_is_exact"] is True


def test_parallelism_is_capped_by_available_accounts():
    """Sessions sharing one account would serialise on its 750 GB/day allowance,
    so there is no point running more sessions than accounts."""
    from backend.scheduler import plan_job
    p = plan_job(10 * 1024 ** 4, accounts=_accounts(2))
    assert len(p.slots) == 2 and p.limited_by == "drive_accounts"
    assert len({s.account for s in p.slots}) == 2


def test_exhausted_daily_allowance_is_refused_with_the_reason():
    from backend.scheduler import plan_job, SchedulerError, DAILY_UPLOAD_BYTES
    accs = _accounts(2)
    used = {a["account_index"]: DAILY_UPLOAD_BYTES for a in accs}
    with pytest.raises(SchedulerError, match="daily upload allowance"):
        plan_job(1024 ** 3, accounts=accs, used_today=used)


def test_big_job_reports_days_not_just_transfer_time():
    """Throughput says 32 TB takes ~37 hours; the per-account daily cap says it
    takes days. Reporting only the first would be misleading."""
    from backend.scheduler import plan_job
    p = plan_job(32 * 1024 ** 4, accounts=_accounts(8))
    assert p.est_days > 1
    assert any("exceeds today's remaining allowance" in w for w in p.warnings)
    assert any("More accounts shorten this" in w for w in p.warnings)


def test_download_width_uses_the_measured_optimum_not_max_threads():
    """Measured inside Kaggle: 2 ranges peak at 119.3 MB/s and it declines from
    there -- 112 at 4, 107 at 8, 98 at 16. More connections contend."""
    from backend.scheduler import (download_parts_for, DOWNLOAD_PARTS_OPTIMUM,
                                   DOWNLOAD_RATES_MB_S)
    assert DOWNLOAD_PARTS_OPTIMUM == 2
    assert DOWNLOAD_RATES_MB_S[2] > DOWNLOAD_RATES_MB_S[1]
    assert DOWNLOAD_RATES_MB_S[2] > DOWNLOAD_RATES_MB_S[16]
    assert download_parts_for(500 * 1024 ** 2) == 2
    # A small file just pays extra round trips.
    assert download_parts_for(1024 ** 2) == 1


def test_split_ranges_tiles_the_file_exactly():
    """A gap silently truncates and an overlap silently duplicates; neither shows
    up as an error."""
    from backend.scheduler import split_ranges
    for total, parts in [(1000, 3), (200 * 1024 ** 2, 2), (7, 7), (10, 1)]:
        rs = split_ranges(total, parts)
        assert rs[0]["start"] == 0 and rs[-1]["end"] == total - 1
        assert sum(r["bytes"] for r in rs) == total
        for a, b in zip(rs, rs[1:]):
            assert b["start"] == a["end"] + 1


def test_aggregate_rate_applies_the_measured_penalty_once():
    from backend.scheduler import aggregate_rate_mb_s, KAGGLE, PLATFORMS
    solo = aggregate_rate_mb_s({KAGGLE: 1})
    assert abs(solo - PLATFORMS[KAGGLE]["upload_mb_s"]) < 1e-6
    five = aggregate_rate_mb_s({KAGGLE: 5})
    assert five > solo * 4          # scales
    assert five < solo * 5          # but not perfectly
