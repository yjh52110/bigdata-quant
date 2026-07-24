import logging
import os
import time
from typing import Tuple

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Real trigger conditions this codebase can actually observe, replacing the
# dashboard's old hardcoded/decorative Feishu/DingTalk rows.
ALERT_RULES = [
    {"id": "duckdb_slow_query", "condition": "DuckDB query > 5s", "severity": "Warning", "channel": "telegram"},
    {"id": "vps_ram_high", "condition": "Host RAM > 95%", "severity": "Critical", "channel": "telegram"},
    {"id": "drive_rate_limited", "condition": "Google Drive API 403/429 response", "severity": "Error", "channel": "telegram"},
    {"id": "gemini_keys_exhausted", "condition": "All configured Gemini keys in cooldown", "severity": "Info", "channel": "telegram"},
]


def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def list_alert_rules() -> list:
    return ALERT_RULES


def send_telegram_message(text: str) -> Tuple[bool, str]:
    if not telegram_configured():
        return False, "Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars."
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if resp.status_code == 200:
            return True, "sent"
        logging.error(f"Telegram send failed: {resp.status_code} {resp.text}")
        return False, f"Telegram API error {resp.status_code}: {resp.text[:200]}"
    except requests.RequestException as e:
        logging.error(f"Telegram send exception: {e}")
        return False, f"Telegram send failed: {e}"


def send_test_alert() -> Tuple[bool, str]:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    return send_telegram_message(f"[ChainQuantPlatform] Test alert @ {stamp}")


def trigger_alert(rule_id: str, message: str) -> Tuple[bool, str]:
    rule = next((r for r in ALERT_RULES if r["id"] == rule_id), None)
    severity = rule["severity"] if rule else "Info"
    return send_telegram_message(f"[{severity}] {message}")
