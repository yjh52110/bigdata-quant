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
import time
from typing import Any, Dict, List

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


def probe_all(api_keys: List[str], model: str = "gemini-2.5-flash") -> Dict[str, Any]:
    if not api_keys:
        return {
            "configured": False,
            "results": [],
            "note": "No GEMINI_API_KEY / GEMINI_API_KEYS configured.",
            "tier_rules": TIER_RULES,
            "doc_url": DOC_URL,
        }

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
