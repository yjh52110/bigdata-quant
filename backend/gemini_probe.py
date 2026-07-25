"""Live probe for Gemini API keys.

Google exposes no endpoint for "quota remaining", and the account's tier is
set by whether a billing account is linked to the API project -- a consumer
Gemini/Google One subscription does not change it. So rather than display a
number we'd have to invent, this makes one real minimal call per key and
reports exactly what came back, including the verbatim quota text from a 429.

Reference limits below are the published free-tier figures for orientation
only. They are NOT this account's limits and are labelled as such in the UI.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DOC_URL = "https://ai.google.dev/gemini-api/docs/rate-limits"

# Tier qualification, quoted from the official docs (verified 2026-07).
TIER_RULES = [
    {"tier": "Free", "qualification": "Active project or free trial", "billing_cap": "N/A"},
    {"tier": "Tier 1", "qualification": "Set up and link an active billing account", "billing_cap": "$250"},
    {"tier": "Tier 2", "qualification": "Paid $100 + 3 days from first successful payment", "billing_cap": "$2,000"},
    {"tier": "Tier 3", "qualification": "Paid $1,000 + 30 days from first successful payment", "billing_cap": "$20,000+"},
]


def _mask(key: str) -> str:
    return f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "***"


def list_models(api_key: str) -> Dict[str, Any]:
    """Asks Google which models this key can actually use.

    Model availability is per-account, not universal: a hardcoded name can
    404 with "no longer available to new users" on a freshly created account
    while working fine elsewhere. So the usable model has to be discovered,
    not assumed.
    """
    try:
        client = genai.Client(api_key=api_key)
        out = []
        for m in client.models.list():
            actions = list(getattr(m, "supported_actions", None) or [])
            if actions and "generateContent" not in actions:
                continue
            out.append({"name": m.name, "display": getattr(m, "display_name", ""), "actions": actions})
        return {"ok": True, "count": len(out), "models": out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:600], "models": []}


# Variants that are not general-purpose text generation, so they should never
# be auto-selected even though they list generateContent.
_SPECIAL_VARIANTS = ("image", "tts", "computer-use", "customtools", "omni")


def _version_key(name: str) -> tuple:
    """Sorts by actual version number, not lexically.

    A plain reverse sort is wrong here: "gemini-flash-latest" sorts above
    "gemini-3.6-flash" because 'f' > '3', which silently picked the weakest
    lite model. Parse the version instead, and treat unversioned aliases as
    lowest so an explicit version always wins.
    """
    m = re.match(r"gemini-(\d+)(?:\.(\d+))?", name)
    major = int(m.group(1)) if m else -1
    minor = int(m.group(2)) if m and m.group(2) else 0
    is_lite = "lite" in name
    return (major, minor, 0 if is_lite else 1)


def pick_default_model(api_key: str) -> Optional[str]:
    """Newest general-purpose flash model this account can actually use."""
    info = list_models(api_key)
    names = [m["name"].replace("models/", "") for m in info.get("models", [])]
    if not names:
        return None

    def usable(n: str) -> bool:
        return "preview" not in n and not any(v in n for v in _SPECIAL_VARIANTS)

    flash = [n for n in names if "flash" in n and usable(n)]
    if flash:
        return max(flash, key=_version_key)
    general = [n for n in names if usable(n)]
    return max(general, key=_version_key) if general else names[0]


def probe_key(api_key: str, model: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """One minimal generation call. Returns what actually happened."""
    started = time.time()
    result: Dict[str, Any] = {"alias": _mask(api_key), "model": model}
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents="ping")
        result.update({
            "ok": True,
            "status": "working",
            "latency_ms": round((time.time() - started) * 1000, 1),
            "reply_chars": len(resp.text or ""),
        })
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None:
            result["tokens"] = {
                "prompt": getattr(usage, "prompt_token_count", None),
                "total": getattr(usage, "total_token_count", None),
            }
    except Exception as e:
        msg = str(e)
        lowered = msg.lower()
        if "429" in msg or "resource_exhausted" in lowered or "quota" in lowered:
            status = "rate_limited"
        elif "api key not valid" in lowered or "401" in msg or "permission" in lowered or "403" in msg:
            status = "invalid_key"
        else:
            status = "error"
        result.update({
            "ok": False,
            "status": status,
            "latency_ms": round((time.time() - started) * 1000, 1),
            # Verbatim so the real quota metric and limit Google names stays visible.
            "detail": msg[:800],
        })
    return result


def probe_all(api_keys: List[str], model: Optional[str] = None) -> Dict[str, Any]:
    if not api_keys:
        return {
            "configured": False,
            "results": [],
            "note": "No GEMINI_API_KEY / GEMINI_API_KEYS configured.",
            "tier_rules": TIER_RULES,
            "doc_url": DOC_URL,
        }

    # Discover rather than assume: availability differs per account.
    model = model or pick_default_model(api_keys[0]) or "gemini-2.5-flash"
    results = [probe_key(k, model) for k in api_keys]
    working = sum(1 for r in results if r.get("ok"))
    limited = sum(1 for r in results if r.get("status") == "rate_limited")

    # A key that answers proves it works; it does not prove which tier it is
    # on, because Google returns no tier field. Only a 429 reveals the real
    # ceiling, so that's the one case where we can say something definite.
    if limited:
        tier_hint = "At least one key hit its quota ceiling -- see the verbatim message for the exact limit."
    elif working:
        tier_hint = ("Keys respond. Tier is not reported by the API; check the Projects page in AI Studio. "
                     "Note a consumer Gemini/Google One subscription does not grant API quota -- only a "
                     "linked billing account moves a project off the Free tier.")
    else:
        tier_hint = "No key answered; see the detail for each."

    return {
        "configured": True,
        "probed_at": time.time(),
        "model": model,
        "total_keys": len(results),
        "working_keys": working,
        "rate_limited_keys": limited,
        "results": results,
        "tier_hint": tier_hint,
        "tier_rules": TIER_RULES,
        "doc_url": DOC_URL,
    }
