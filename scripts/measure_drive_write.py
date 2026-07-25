#!/usr/bin/env python3
"""Measures datacenter-side Drive write speed by dispatching a Kaggle job.

This is the one number the project still lacks. Local uploads are capped by the
operator's home uplink (measured 0.52 MB/s), which says nothing about what the
pipeline will actually do -- ingestion runs inside Colab/Kaggle, on a datacenter
link. So the measurement has to happen there.

Prerequisites:

  1. ~/.kaggle/kaggle.json      Kaggle API token. Long-lived -- valid until you
                                revoke it. Placing it is yours to do.
  2. A Drive credential reaching the kernel, one of two ways:

     default          Kaggle secret DRIVE_OAUTH_JSON, holding the line printed by
                      scripts/export_drive_secret.py. The refresh token stays in
                      Kaggle's encrypted store, is injected at run time and is
                      masked in logs. Configure once, works for every later run,
                      and Colab reads the same name.

     --use-access-token
                      Mint a short-lived Drive access token locally and pass it
                      in the job params instead. It expires in about an hour and
                      cannot mint another, so it being in the stored kernel
                      source is bounded in a way a refresh token would not be.
                      Suitable for a one-off measurement; not for a pipeline,
                      because every run needs a fresh one.

Then:

    python3 scripts/measure_drive_write.py <your-kaggle-username> [--use-access-token]

It dispatches, polls until the run finishes, fetches the output, and prints the
throughput. Nothing here reads or transmits either credential: the Kaggle CLI
reads its own token file, and the Drive secret is read by the kernel from
Kaggle's secret store.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import kaggle_control as kc          # noqa: E402
from backend import kaggle_dispatch as kd         # noqa: E402

POLL_EVERY_S = 20
# Kaggle queues before running; a 5 MB job finishes well inside this, but the
# queue itself is unpredictable so allow a generous ceiling.
MAX_WAIT_S = 1800


def wait_for(ref: str) -> str:
    waited = 0
    last = None
    while waited < MAX_WAIT_S:
        st = kc._run(["kernels", "status", ref], timeout=60)
        text = (st["out"] + st["err"]).strip()
        state = None
        for candidate in kc.KERNEL_STATES:
            if candidate in text.upper():
                state = candidate
                break
        if state != last:
            print(f"  [{waited:4d}s] {state or text[:80]}")
            last = state
        if state in kd.DONE_STATES:
            return state
        time.sleep(POLL_EVERY_S)
        waited += POLL_EVERY_S
    return last or "TIMEOUT"


def report(result: dict) -> None:
    d = result.get("drive")
    print("\n--- 下载（公网 -> Kaggle）---")
    if result.get("mb_per_s") is not None:
        print(f"  {result.get('bytes', 0) / 1024 ** 2:.1f} MB  →  {result['mb_per_s']} MB/s")
    for f in result.get("failed_days", []) or []:
        print(f"  失败 {f['day']}: {f['err'][:120]}")

    print("\n--- 上传（Kaggle -> 云盘）---")
    if not d:
        print("  任务未带 drive_folder，或产物为空")
    elif d.get("error"):
        # Verbatim: the two common causes (missing secret, wrong secret name)
        # look identical unless the real message is shown.
        print(f"  失败: {d['error']}")
    else:
        print(f"  {d['total_files']} 个文件, {d['total_bytes'] / 1024 ** 2:.1f} MB")
        print(f"  ★ 机房侧云盘写入速度: {d['mb_per_s']} MB/s")
        q = d.get("quota") or {}
        if q.get("usage"):
            print(f"  云盘已用: {int(q['usage']) / 1024 ** 3:.3f} GB"
                  + (f" / {int(q['limit']) / 1024 ** 3:.0f} GB" if q.get("limit") else ""))

    specs = result.get("specs") or {}
    if specs:
        print(f"\n运行时: {specs.get('cpu')} vCPU"
              + (f" / {specs['ram_gb']} GB" if specs.get("ram_gb") else ""))
    print(f"总耗时: {result.get('elapsed_s')} s")


def mint_access_token() -> str:
    """Exchanges the stored refresh token for a ~1h access token.

    The value is returned, never printed: it goes straight into the job params.
    """
    from backend.google_account_manager import GoogleAccountManager, CREDENTIALS_FILE
    from backend import drive_rest

    mgr = GoogleAccountManager()
    if not mgr.accounts:
        raise SystemExit("error: no Google account connected -- connect one in the dashboard first")
    index = sorted(mgr.accounts)[0]
    account = mgr.accounts[index]
    if not account.get("refresh_token"):
        raise SystemExit(f"error: account {index!r} has no refresh token")
    with open(CREDENTIALS_FILE) as f:
        blk = json.load(f)
    blk = blk.get("web") or blk.get("installed")
    token = drive_rest.access_token(blk["client_id"], blk["client_secret"],
                                    mgr._decrypt(account["refresh_token"]))
    print(f"用账号 {index} 换到短期 access token（{len(token)} 字符，约 1 小时后失效，值不打印）")
    return token


def main() -> int:
    args = [a for a in sys.argv[1:]]
    use_access_token = "--use-access-token" in args
    args = [a for a in args if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    username = args[0]

    st = kc.cli_status()
    if not st["installed"]:
        print("error: kaggle CLI not installed", file=sys.stderr)
        return 1
    if not st["authenticated"]:
        print(f"error: {st['auth_hint']}", file=sys.stderr)
        return 1

    # A single day of Ethereum blocks is ~5 MB: enough to measure, small enough
    # not to spend quota proving the pipeline works.
    params = {"kind": "aws", "chain": "eth", "table": "blocks",
              "days": ["2024-01-15"], "drive_folder": "chainquant"}
    slug = "cq-measure-drive-write"
    if use_access_token:
        params["drive_access_token"] = mint_access_token()
        # A distinct slug, so the kernel carrying a token never overwrites the
        # Secrets-based one that is meant to be re-run.
        slug = "cq-write-speed-oneoff"

    print(f"派发 {username}/{slug} ...")
    try:
        job = kd.dispatch(username, slug, params, title="ChainQuant drive write measurement",
                          timeout=1800)
    except kd.DispatchError as e:
        print(f"派发失败: {e}", file=sys.stderr)
        return 1
    print(f"已派发: {job['ref']} (version {job['version']})\n轮询状态:")

    state = wait_for(job["ref"])
    print(f"\n最终状态: {state}")
    if state != "COMPLETE":
        logs = kd.logs(job["ref"])
        print("--- 日志尾部 ---")
        print(logs["logs"][-2000:])
        return 1

    dest = os.path.join("backend", "data", "kaggle_output", slug)
    out = kd.fetch_output(job["ref"], dest)
    if not out.get("result"):
        print(f"取回产物失败: {out.get('raw', '')[:300]}", file=sys.stderr)
        return 1
    report(out["result"])
    print(f"\n产物已存到 {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
