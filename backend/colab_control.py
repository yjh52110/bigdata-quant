"""Live Colab status via the official CLI (googlecolab/google-colab-cli).

Colab has no REST API, so this shells out to the official CLI and parses its
output. That is enough to report real session state without needing a worker
to be connected first.

What can and cannot be shown, so the UI never invents numbers:

  can    -- CLI installed/authenticated, active sessions and their hardware,
            the hardware variants the CLI accepts, measured specs of a live
            session, and the documented limits
  cannot -- remaining quota as a number a program can read. The notebook UI
            *does* show an account-specific projection ("this runtime may last
            up to 68h20m at your current usage level"), but nothing exposes it:
            introspecting google.colab in a live session turns up no quota,
            usage, limit or duration symbol at all. So it is surfaced here as a
            measured observation with its source named, never as a live value.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CLI_TIMEOUT_S = 45
STATE_DIR = os.path.expanduser("~/.config/colab-cli")

# Accelerator variants the CLI accepts, per `colab help new`.
HARDWARE = {
    "cpu": ["DEFAULT"],
    "gpu": ["T4", "L4", "G4", "H100", "A100"],
    "tpu": ["v5e1", "v6e1"],
}

# Documented limits, quoted from Colab's own FAQ (verified 2026-07).
DOCUMENTED_LIMITS = [
    {"item": "单次会话上限", "value": "12 小时",
     "note": "免费版，官方原文 depending on availability and your usage patterns"},
    {"item": "空闲超时", "value": "会断开",
     "note": "关闭浏览器标签页同样会断"},
    {"item": "本地磁盘", "value": "会话结束清空",
     "note": "要保留的数据必须写入云盘"},
    {"item": "配额数值", "value": "网页端可见 / 代码读不到",
     "note": "网页端资源面板会按当前用量给出预计可持续时长；但在运行会话里遍历 google.colab 全部公开接口，没有任何 quota/usage/limit/duration 符号，CLI 也没有对应命令"},
    {"item": "预计可持续时长（实测观测）", "value": "CPU 68h20m / GPU(T4) 4h50m",
     "note": "2026-07 免费档网页端读数，同一账号 GPU 比 CPU 少一个量级；官方原文 usage limits sometimes fluctuate，故只作观测值不作承诺"},
    {"item": "云盘 FUSE 挂载", "value": "头无交互不可用",
     "note": "colab drivemount / drive.mount() 需要网页端点授权弹窗，headless 下报 ValueError: mount failed"},
    {"item": "云盘 REST API", "value": "可达（实测 33.8ms）",
     "note": "Colab 内访问 www.googleapis.com/drive/v3 返回 401 missing authentication credential，即链路通、仅缺令牌——本项目走的正是这条路，不依赖 FUSE"},
    {"item": "孤儿会话", "value": "CLI 关不掉",
     "note": "colab stop 靠本地 sessions.json 解析会话名；该文件丢失后服务端仍在跑的会话在列表里显示为 [?]，只能到网页端「管理会话」终止"},
    {"item": "多账号扩额度", "value": "明确禁止",
     "note": "禁止清单原文 using multiple accounts to work around access or resource usage restrictions"},
    # Verified from the CLI's own command list: `pay` is described as "Open the
    # Colab signup page to manage compute units", so paid tiers do have a
    # balance -- but none of the 20 commands can print it.
    {"item": "付费档 compute units", "value": "CLI 读不到",
     "note": "CLI 只有 pay 命令（原文 Open the Colab signup page to manage compute units），全部命令中无任何一条能返回余额，只能在 Colab 网页端查看"},
]

# Plan figures, quoted from Colab's own signup page (verified 2026-07).
# Compute units are the billing unit for PAID runtimes only: an account on the
# free tier sits at 0 units and consumes none. The per-hour burn rate differs
# per machine type and Google only shows it in the notebook UI when a runtime
# is selected, so no rate is stored here -- there is nothing to read it from.
PLANS = [
    {"plan": "免费版", "units": "0", "extra": "不消耗计算单元；能否拿到 GPU 看当时余量"},
    {"plan": "Pay As You Go", "units": "按需购买", "extra": "无需订阅，只为实际用量付费"},
    {"plan": "Colab Pro", "units": "每月 100", "extra": "更快 GPU、更多内存、更高 Gemini 配额"},
    {"plan": "Colab Pro+", "units": "每月 600", "extra": "含 Pro 全部；关闭浏览器后仍可后台运行最长 24 小时"},
    {"plan": "Colab Enterprise", "units": "GCP 计费", "extra": "笔记本存到 GCP，集成 BigQuery / Vertex AI"},
]
UNITS_EXPIRY_NOTE = "计算单元 90 天后过期。每小时消耗速率随机型不同，只在网页端选定运行时后显示，CLI 与 API 都读不到。"

DOC_LINKS = [
    {"title": "Colab 官方 FAQ / 资源限制", "url": "https://research.google.com/colaboratory/faq.html"},
    {"title": "官方 CLI 仓库", "url": "https://github.com/googlecolab/google-colab-cli"},
    {"title": "官方 MCP 服务器", "url": "https://github.com/googlecolab/colab-mcp"},
    {"title": "google.colab Python API 源码", "url": "https://github.com/googlecolab/colabtools"},
    {"title": "Colab 套餐与 compute units（余额在此查看）", "url": "https://colab.research.google.com/signup"},
]


def _env() -> Dict[str, str]:
    """CLI env with a CA bundle pinned.

    python.org builds ship without a populated cert store, so the CLI's
    WebSocket layer (stdlib ssl, unlike requests which bundles certifi) fails
    with CERTIFICATE_VERIFY_FAILED. Point it at certifi explicitly rather
    than requiring the user to have run Install Certificates.command.
    """
    env = dict(os.environ)
    try:
        import certifi
        env.setdefault("SSL_CERT_FILE", certifi.where())
        env.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass
    return env


def _run(args: List[str], timeout: int = CLI_TIMEOUT_S) -> Dict[str, Any]:
    try:
        p = subprocess.run(["colab", *args], capture_output=True, timeout=timeout, env=_env())
        return {"ok": p.returncode == 0, "out": p.stdout.decode(errors="ignore"),
                "err": p.stderr.decode(errors="ignore")}
    except FileNotFoundError:
        return {"ok": False, "out": "", "err": "colab CLI not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": f"colab CLI timed out after {timeout}s"}


def cli_status() -> Dict[str, Any]:
    installed = shutil.which("colab") is not None
    version = ""
    if installed:
        # The CLI has no --version flag (it prints usage), so read the
        # installed distribution's metadata instead.
        try:
            from importlib.metadata import version as _v
            version = _v("google-colab-cli")
        except Exception:
            version = "unknown"

    token_path = os.path.join(STATE_DIR, "token.json")
    authed = os.path.exists(token_path) and os.path.getsize(token_path) > 0
    return {
        "installed": installed,
        "version": version,
        "authenticated": authed,
        "auth_hint": None if authed else "在终端运行 `colab sessions`，按提示完成一次性 Google 授权",
    }


_SESSION_RE = re.compile(r"\[([^\]]+)\]\s+(\S+)\s*\|\s*Hardware:\s*(\S+)\s*\|\s*Variant:\s*(\S+)")
_STATUS_RE = re.compile(r"Status:\s*(\S+)")
_LAST_EXEC_RE = re.compile(r"Last Execution:\s*(.+?)\s*$", re.M)

# `sessions` omits Status, `status` includes it, and status costs ~0.4s vs
# ~2s for sessions -- cheap enough to enrich each row. Capped so a large
# account can't turn one page load into a long serial CLI run.
MAX_STATUS_LOOKUPS = 8


def _local_session_names() -> set:
    """Names the CLI still has a local mapping for.

    `colab sessions` queries the server, but `colab stop` resolves the name
    through this local file. If it is lost or reset, a running session shows up
    in the listing as [?] and becomes unstoppable from the CLI -- it then burns
    free-tier allowance until the idle timeout. Only the keys are read; no
    values, and never the sibling token file.
    """
    try:
        with open(os.path.join(STATE_DIR, "sessions.json")) as f:
            data = json.load(f)
        return set(data.keys()) if isinstance(data, dict) else set()
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()


def session_status(name: str) -> Dict[str, Any]:
    """IDLE/BUSY plus last-execution time for one session."""
    r = _run(["status", "-s", name], timeout=20)
    text = r["out"] + r["err"]
    st = _STATUS_RE.search(text)
    last = _LAST_EXEC_RE.search(text)
    return {
        "status": st.group(1) if st else None,
        "last_execution": last.group(1).strip() if last else None,
    }


def list_sessions() -> Dict[str, Any]:
    """Active sessions, parsed from CLI text output (no JSON mode exists)."""
    status = cli_status()
    if not status["installed"]:
        return {"available": False, "reason": "colab CLI not installed", "sessions": [], **status}
    if not status["authenticated"]:
        return {"available": False, "reason": "not authenticated", "sessions": [], **status}

    r = _run(["sessions"])
    if not r["ok"] and "No active sessions" not in (r["out"] + r["err"]):
        return {"available": False, "reason": (r["err"] or r["out"])[:300], "sessions": [], **status}

    sessions = [
        {"name": m.group(1), "machine": m.group(2), "hardware": m.group(3), "variant": m.group(4)}
        for m in _SESSION_RE.finditer(r["out"])
    ]
    local = _local_session_names()
    for s in sessions:
        # [?] means the server has it but the CLI lost the name mapping.
        s["orphan"] = s["name"] == "?" or s["name"] not in local
    for s in sessions[:MAX_STATUS_LOOKUPS]:
        if not s["orphan"]:
            s.update(session_status(s["name"]))
    orphans = sum(1 for s in sessions if s["orphan"])
    return {
        "available": True, "sessions": sessions, "count": len(sessions),
        "orphan_count": orphans,
        "orphan_hint": ("有会话在服务端仍在运行，但 CLI 已丢失本地名称映射，无法用 colab stop 关闭。"
                        "请到 Colab 网页端「管理会话」手动终止，否则它会一直占用免费额度直到空闲超时。")
                       if orphans else None,
        **status,
    }


_PROBE = (
    "import os,shutil,subprocess,json\n"
    "d={'cpu_count':os.cpu_count()}\n"
    "try:\n"
    "    d['ram_gb']=round(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/1024**3,1)\n"
    "except Exception: pass\n"
    "try:\n"
    "    t,_,f=shutil.disk_usage('/content')\n"
    "    d['disk_total_gb']=round(t/1024**3,1); d['disk_free_gb']=round(f/1024**3,1)\n"
    "except Exception: pass\n"
    "try:\n"
    "    g=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader'],"
    "capture_output=True,timeout=8).stdout.decode().strip()\n"
    "    d['gpu']=g.splitlines()[0] if g else 'none'\n"
    "except Exception: d['gpu']='none'\n"
    "print('__SPECS__'+json.dumps(d))\n"
)


def probe_session(name: str) -> Dict[str, Any]:
    """Measures a live session's real specs by executing a probe inside it."""
    try:
        p = subprocess.run(["colab", "exec", "-s", name], input=_PROBE.encode(),
                           capture_output=True, timeout=180, env=_env())
        text = p.stdout.decode(errors="ignore") + p.stderr.decode(errors="ignore")
    except FileNotFoundError:
        return {"ok": False, "error": "colab CLI not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "probe timed out"}

    m = re.search(r"__SPECS__(\{.*\})", text)
    if not m:
        return {"ok": False, "error": (text[-400:] or "no specs returned").strip()}
    try:
        return {"ok": True, "specs": json.loads(m.group(1)), "probed_at": time.time()}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"could not parse specs: {e}"}


ENTITLEMENT_CACHE = os.path.join(os.path.dirname(__file__), "data", "colab_entitlements.json")


def probe_entitlements(stop_after: bool = True) -> Dict[str, Any]:
    """Measures which machine types this account can actually obtain.

    There is no API that reports an account's entitlements, so the only honest
    way to know is to ask for each variant and record whether the backend
    accepts it. Result is cached because each attempt costs a session
    creation (tens of seconds).
    """
    attempts: List[Dict[str, Any]] = []
    targets = [("cpu", None, [])] + \
              [("gpu", v, ["--gpu", v]) for v in HARDWARE["gpu"]] + \
              [("tpu", v, ["--tpu", v]) for v in HARDWARE["tpu"]]

    for kind, variant, flags in targets:
        name = f"probe-{kind}-{(variant or 'default').lower()}"
        r = _run(["new", "-s", name, *flags], timeout=240)
        text = r["out"] + r["err"]
        granted = "Session READY" in text
        row: Dict[str, Any] = {"kind": kind, "variant": variant or "DEFAULT", "granted": granted}
        if granted:
            pr = probe_session(name)
            if pr.get("ok"):
                row["specs"] = pr["specs"]
            if stop_after:
                _run(["stop", "-s", name], timeout=60)
        else:
            # Verbatim so the real reason stays visible instead of a guess.
            m = re.search(r"(Backend rejected[^\n]*)", text)
            row["reason"] = (m.group(1) if m else text.strip()[-200:])
        attempts.append(row)

    result = {"probed_at": time.time(), "attempts": attempts,
              "granted": [f"{a['kind']}:{a['variant']}" for a in attempts if a["granted"]]}
    try:
        os.makedirs(os.path.dirname(ENTITLEMENT_CACHE), exist_ok=True)
        with open(ENTITLEMENT_CACHE, "w") as f:
            json.dump(result, f, indent=2)
    except OSError as e:
        logging.warning(f"could not cache entitlements: {e}")
    return result


def cached_entitlements() -> Optional[Dict[str, Any]]:
    try:
        with open(ENTITLEMENT_CACHE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def overview() -> Dict[str, Any]:
    s = list_sessions()
    return {
        **s,
        "plans": PLANS,
        "units_expiry_note": UNITS_EXPIRY_NOTE,
        "entitlements": cached_entitlements(),
        "hardware_options": HARDWARE,
        "documented_limits": DOCUMENTED_LIMITS,
        "doc_links": DOC_LINKS,
        # Stated explicitly so the UI never shows a fabricated quota figure.
        "quota_available": False,
        "quota_note": ("配额数字只在 Colab 网页端资源面板显示，CLI、REST 与运行时里的 "
                       "google.colab 都读不到（已逐一核对）。此处只显示实测值、网页端观测值"
                       "与官方文档条款，不做任何推算。"),
    }
