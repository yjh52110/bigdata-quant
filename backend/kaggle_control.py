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

# Free-tier figures gathered 2026-07 by web search, each tagged with where it
# came from so the panel never presents a blog number as an official one.
# "official" = stated on kaggle.com (docs or a Kaggle staff post).
# The live `kaggle quota` reading always overrides these once a token is placed.
FREE_TIER = [
    {"item": "CPU 时长", "value": "不限额", "source": "community",
     "note": "只受单次会话上限约束，不消耗每周配额。对本项目最关键的一条：入库管道是 CPU 活"},
    {"item": "GPU 每周配额", "value": "30 小时（保底）", "source": "official",
     "note": "Kaggle 官方帖原文 we make every effort to provide 30hrs of guaranteed quota each week, and depending on our demand/supply ... our algorithm decides if we can spare to lend out some bonus quota each week"},
    {"item": "TPU 每周配额", "value": "约 20 小时", "source": "official",
     "note": "kaggle.com 文档 You can use up to 20 hours per week"},
    {"item": "单次会话上限", "value": "12 小时（TPU 9 小时）", "source": "conflicting",
     "note": "多数来源为 CPU/GPU 12h、TPU 9h，但 kaggle.com 论坛也有回答称 9h。这是唯一各来源不一致的数字，以你账号实际表现为准"},
    {"item": "内存", "value": "约 29–30 GB", "source": "community",
     "note": "2023 年从 13 GB 提升；同时 CPU 核数从 2 提到 4。约为 Colab 免费档 12.7 GB 的 2.4 倍"},
    {"item": "持久磁盘", "value": "20 GB", "source": "community",
     "note": "/kaggle/working 会随 notebook 保存，跨会话保留——与 Colab 会话结束即清空不同"},
    {"item": "GPU 型号", "value": "P100 16GB 或 T4×2", "source": "community",
     "note": "免费档可选单张 P100 或双张 T4（各 16GB）"},
    {"item": "开启 GPU 的前置条件", "value": "需手机验证", "source": "official-ish",
     "note": "未验证时加速器选项为灰。与 SDK 里的 is_phone_verified 属性一致；一个手机号无法验证大量账号"},
    {"item": "Colab 订阅联动", "value": "可加时", "source": "official",
     "note": "kaggle.com 文档原文 Once the account is verified to have an active Colab subscription, you will be granted additional GPU hours。你当前 0 计算单元即无订阅，故不适用"},
]

# How each platform can and cannot reach Drive. The Colab rows are measured
# (see backend/colab_control.py); the Kaggle REST row is inferred from
# enable_internet allowing arbitrary egress and is marked unverified until a
# token lets the drivecheck job run.
DRIVE_ACCESS = [
    {"platform": "Colab", "method": "FUSE 挂载", "works": False, "verified": True,
     "note": "drive.mount() / colab drivemount 需网页端授权弹窗，headless 实测报 ValueError: mount failed。官方仓库 colabtools#4182「让 mount 支持 Secrets」至今未实现"},
    {"platform": "Colab", "method": "Drive REST API", "works": True, "verified": True,
     "note": "实测 33.8ms 返回 401 missing authentication credential——链路通、仅缺令牌。本项目走的正是这条"},
    {"platform": "Kaggle", "method": "FUSE 挂载", "works": False, "verified": True,
     "note": "Kaggle 根本没有这个功能：google.colab.drive 是 Colab 专有模块，在 Kaggle 里直接 KeyError"},
    {"platform": "Kaggle", "method": "Drive REST API", "works": True, "verified": False,
     "note": "enable_internet 开启后允许任意出网，凭证经 Kaggle Secrets 注入。尚未实测——放入令牌后派发一次 drivecheck 任务即可验证并测速"},
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
        "free_tier": FREE_TIER,
        "drive_access": DRIVE_ACCESS,
        "free_tier_note": ("以下为 2026-07 联网检索所得，逐条标注来源：official 表示 kaggle.com "
                           "文档或官方员工帖，community 表示多个第三方来源一致，conflicting 表示各来源不一致。"
                           "放入令牌后，kaggle quota 的实时读数优先于这张表。"),
        "kernel_states": KERNEL_STATES,
        "doc_links": DOC_LINKS,
    }
