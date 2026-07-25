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
