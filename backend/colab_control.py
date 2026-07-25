"""Live Colab status via the official CLI (googlecolab/google-colab-cli).

Colab has no REST API, so this shells out to the official CLI and parses its
output. That is enough to report real session state without needing a worker
to be connected first.

What can and cannot be shown, so the UI never invents numbers:

  can    -- CLI installed/authenticated, active sessions and their hardware,
            the hardware variants the CLI accepts, measured specs of a live
            session, and the documented limits
  cannot -- remaining quota. Google publishes no figure and offers no
            endpoint; the FAQ states limits "sometimes fluctuate".
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
    {"item": "配额数值", "value": "官方不公布",
     "note": "原文 resources are not guaranteed and not unlimited, usage limits sometimes fluctuate"},
    {"item": "多账号扩额度", "value": "明确禁止",
     "note": "禁止清单原文 using multiple accounts to work around access or resource usage restrictions"},
]

DOC_LINKS = [
    {"title": "Colab 官方 FAQ / 资源限制", "url": "https://research.google.com/colaboratory/faq.html"},
    {"title": "官方 CLI 仓库", "url": "https://github.com/googlecolab/google-colab-cli"},
    {"title": "官方 MCP 服务器", "url": "https://github.com/googlecolab/colab-mcp"},
    {"title": "google.colab Python API 源码", "url": "https://github.com/googlecolab/colabtools"},
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
    for s in sessions[:MAX_STATUS_LOOKUPS]:
        s.update(session_status(s["name"]))
    return {"available": True, "sessions": sessions, "count": len(sessions), **status}


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


def overview() -> Dict[str, Any]:
    s = list_sessions()
    return {
        **s,
        "hardware_options": HARDWARE,
        "documented_limits": DOCUMENTED_LIMITS,
        "doc_links": DOC_LINKS,
        # Stated explicitly so the UI never shows a fabricated quota figure.
        "quota_available": False,
        "quota_note": "Google 不公布 Colab 配额数值，也不提供查询接口。此处只显示实测值与官方文档条款。",
    }
