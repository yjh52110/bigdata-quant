#!/usr/bin/env python3
"""Measures datacenter-side Drive write speed by dispatching a Kaggle job.

This is the one number the project still lacks. Local uploads are capped by the
operator's home uplink (measured 0.52 MB/s), which says nothing about what the
pipeline will actually do -- ingestion runs inside Colab/Kaggle, on a datacenter
link. So the measurement has to happen there.

Prerequisites, both of which involve placing a credential and are therefore
yours to do, not this script's:

  1. ~/.kaggle/kaggle.json      Kaggle API token (Settings -> API -> Create New Token)
  2. Kaggle secret DRIVE_OAUTH_JSON
                                the line printed by scripts/export_drive_secret.py,
                                pasted into a notebook's Add-ons -> Secrets

Then:

    python3 scripts/measure_drive_write.py <your-kaggle-username>

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


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    username = sys.argv[1]

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
