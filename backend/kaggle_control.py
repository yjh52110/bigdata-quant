"""Kaggle free-compute status via the official CLI (Kaggle/kaggle-cli).

Kaggle is the opposite of Colab in the two ways that matter to this project:

  quota     Colab exposes no quota figure to any interface. Kaggle has a
            dedicated `kaggle quota` command, and the official SDK's own type
            (ApiAcceleratorQuota) declares time_used / time_reserved /
            total_time_allowed / minimum_time_allowed per accelerator plus a
            quota_refresh_time. So the numbers here are read, not estimated.

  dispatch  Colab runtimes cannot be connected to from outside, which is why
            this repo drives them with a polling worker. Kaggle kernels are
            pushed and run through the API (`kernels push` with --accelerator
            and --timeout, then `status` / `logs` / `output`), so work can be
            dispatched directly with no worker to keep alive.

Nothing here hardcodes a quota number: if the CLI is not authenticated the
payload says so rather than filling in a plausible figure.
"""

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CLI_TIMEOUT_S = 45
CONFIG_DIR = os.path.expanduser("~/.kaggle")

# Worker states the SDK declares (KernelWorkerStatus), so the UI can label a
# dispatched run without inventing states.
KERNEL_STATES = ["QUEUED", "RUNNING", "COMPLETE", "ERROR",
                 "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED", "NEW_SCRIPT"]

# Capabilities verified from the installed CLI's own command surface, not docs.
CAPABILITIES = [
    {"item": "配额可程序化读取", "value": "可以",
     "note": "kaggle quota 返回每周 GPU/TPU 额度；SDK 类型 ApiAcceleratorQuota 定义了 time_used / total_time_allowed / quota_refresh_time"},
    {"item": "外部派发任务", "value": "可以",
     "note": "kaggle kernels push -p <目录> --accelerator <型号> -t <秒>，再用 status / logs / output 轮询取回；不需要常驻 worker"},
    {"item": "免费存储", "value": "datasets",
     "note": "kaggle datasets create / version 可程序化建库与增量发版，作为数据落地位置"},
    # Easy and costly assumption to get wrong: quota is attached to the Kaggle
    # account, not the Google account used to sign in. The CLI says so itself
    # ("First, you will need a Kaggle account"). N Google accounts therefore do
    # NOT yield N quotas -- and registering N Kaggle accounts runs into the same
    # one-account-per-person rule that Colab states outright.
    {"item": "额度挂在哪", "value": "Kaggle 账号，不是谷歌号",
     "note": "谷歌号只用于登录；CLI 原文 First, you will need a Kaggle account。有 N 个谷歌号不等于有 N 份额度"},
    {"item": "加速器与账号验证", "value": "受账号验证约束",
     "note": "SDK 有 is_phone_verified 账号属性；一个手机号无法验证大量账号，多账号扩额度与 Colab 同属违规方向"},
    {"item": "对本项目的真实价值", "value": "可调度，而非更多 GPU",
     "note": "本管道是 DuckDB 聚合与 parquet 写入，吃 CPU 与带宽、不用 GPU。Kaggle 的价值在于能被外部直接派发，一个账号即可"},
    {"item": "与 Colab 的关键差别", "value": "可被主动调用",
     "note": "Colab 运行时无法从外部连接，只能靠 worker 轮询；Kaggle 是标准 REST，可直接调度"},
]

DOC_LINKS = [
    {"title": "官方 CLI 仓库", "url": "https://github.com/Kaggle/kaggle-cli"},
    {"title": "kernel-metadata.json 规范", "url": "https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels_metadata.md"},
    {"title": "输出格式选项", "url": "https://github.com/Kaggle/kaggle-cli/blob/main/docs/output_format.md"},
    {"title": "Kaggle 官方文档", "url": "https://www.kaggle.com/docs/api"},
]

AUTH_HINT = ("在 kaggle.com → Settings → API → Create New Token 下载 kaggle.json，"
             "放到 ~/.kaggle/kaggle.json（chmod 600）。令牌请自行放置，本平台不代为输入。")


def _run(args: List[str], timeout: int = CLI_TIMEOUT_S) -> Dict[str, Any]:
    try:
        p = subprocess.run(["kaggle", *args], capture_output=True, timeout=timeout)
        return {"ok": p.returncode == 0, "out": p.stdout.decode(errors="ignore"),
                "err": p.stderr.decode(errors="ignore")}
    except FileNotFoundError:
        return {"ok": False, "out": "", "err": "kaggle CLI not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": f"kaggle CLI timed out after {timeout}s"}


def cli_status() -> Dict[str, Any]:
    installed = shutil.which("kaggle") is not None
    version = ""
    if installed:
        try:
            from importlib.metadata import version as _v
            version = _v("kaggle")
        except Exception:
            version = "unknown"

    # Existence only -- the token file itself is never read.
    authed = any(os.path.exists(os.path.join(CONFIG_DIR, f)) and
                 os.path.getsize(os.path.join(CONFIG_DIR, f)) > 0
                 for f in ("kaggle.json", "access_token"))
    if not authed:
        authed = bool(os.environ.get("KAGGLE_KEY") and os.environ.get("KAGGLE_USERNAME"))

    return {"installed": installed, "version": version, "authenticated": authed,
            "auth_hint": None if authed else AUTH_HINT}


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalise(block: Any) -> Dict[str, Any]:
    """Maps one accelerator's quota to hours, keeping the raw values.

    The CLI reports seconds; hours is what the UI shows, so convert once here
    rather than in the component. Missing fields stay None instead of 0 so an
    absent value can't read as "no quota left".
    """
    if not isinstance(block, dict):
        return {}
    used = _num(block.get("timeUsed", block.get("time_used")))
    total = _num(block.get("totalTimeAllowed", block.get("total_time_allowed")))
    reserved = _num(block.get("timeReserved", block.get("time_reserved")))
    out: Dict[str, Any] = {
        "used_s": used, "total_s": total, "reserved_s": reserved,
        "has_ever_run": block.get("hasEverRun", block.get("has_ever_run")),
    }
    if used is not None:
        out["used_h"] = round(used / 3600, 1)
    if total is not None:
        out["total_h"] = round(total / 3600, 1)
    if used is not None and total is not None:
        out["remaining_h"] = round(max(0.0, total - used) / 3600, 1)
        out["pct_used"] = round(used / total * 100, 1) if total else None
    return out


def quota() -> Dict[str, Any]:
    """Weekly GPU/TPU accelerator quota, as reported by the CLI."""
    status = cli_status()
    if not status["installed"]:
        return {"available": False, "reason": "kaggle CLI not installed", **status}
    if not status["authenticated"]:
        return {"available": False, "reason": "not authenticated", **status}

    r = _run(["quota", "--format", "json"])
    text = r["out"] + r["err"]
    if not r["ok"]:
        return {"available": False, "reason": text.strip()[:300] or "quota call failed", **status}

    # Tolerate log lines around the JSON body.
    m = re.search(r"[\{\[].*[\}\]]", text, re.S)
    if not m:
        return {"available": False, "reason": text.strip()[:300] or "no JSON in output", **status}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"available": False, "reason": f"could not parse quota JSON: {e}", **status}

    if isinstance(data, list):
        data = data[0] if data else {}

    return {
        "available": True,
        "refresh_time": data.get("quotaRefreshTime", data.get("quota_refresh_time")),
        "gpu": _normalise(data.get("gpuQuota", data.get("gpu_quota"))),
        "tpu": _normalise(data.get("tpuQuota", data.get("tpu_quota"))),
        "raw": data,
        **status,
    }


def overview() -> Dict[str, Any]:
    return {
        **quota(),
        "capabilities": CAPABILITIES,
        "kernel_states": KERNEL_STATES,
        "doc_links": DOC_LINKS,
    }
