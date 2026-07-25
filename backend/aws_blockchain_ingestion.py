"""Ingestion from the AWS Public Blockchain Data set.

Free, anonymous, already Parquet, already partitioned by date -- someone has
done the extraction for you, so this is the cheapest way to get real BTC/ETH
chain data. Files are copied down as-is; no parsing or conversion needed.

The catch is size, and it is not uniform. Measured for a single Ethereum day
(2026-07-01):

    blocks              5.5 MB
    contracts          24.5 MB
    token_transfers   258.0 MB
    transactions      784.4 MB
    logs              921.6 MB
    traces           2642.2 MB

traces alone is ~950 GB/year. Every call here therefore previews the byte
count first and refuses to exceed an explicit budget, so a careless date
range cannot quietly start a multi-terabyte download.
"""

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHAIN_DIR = os.path.join(BASE_DIR, "data", "chain")

BUCKET = "https://aws-public-blockchain.s3.us-east-2.amazonaws.com"
PREFIX = "v1.0"

CHAINS = {"eth": "ETH", "btc": "BTC"}
TABLES = {
    "eth": ["blocks", "transactions", "logs", "traces", "token_transfers", "contracts"],
    "btc": ["blocks", "transactions"],
}

DEFAULT_MAX_GB = 2.0


class BudgetExceeded(RuntimeError):
    """Raised before any bytes are transferred, not part-way through."""


def _daterange(start: str, end: str) -> List[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    if d1 < d0:
        raise ValueError("end_date is before start_date")
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _list_day(chain: str, table: str, day: str) -> List[Dict]:
    """Objects for one date partition. Empty list if that day isn't published."""
    prefix = f"{PREFIX}/{chain}/{table}/date={day}/"
    url = f"{BUCKET}/?list-type=2&prefix={quote(prefix, safe='')}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    keys = re.findall(r"<Key>([^<]*)</Key>", resp.text)
    sizes = re.findall(r"<Size>(\d+)</Size>", resp.text)
    return [{"key": k, "size": int(s)} for k, s in zip(keys, sizes) if k.endswith(".parquet")]


def preview(chain: str, table: str, start_date: str, end_date: str) -> Dict:
    """Byte count for a range without downloading anything."""
    chain = chain.lower()
    if chain not in CHAINS:
        raise ValueError(f"Unknown chain '{chain}' (expected {', '.join(CHAINS)})")
    if table not in TABLES[chain]:
        raise ValueError(f"Unknown table '{table}' for {chain} (expected {', '.join(TABLES[chain])})")

    days, total, missing = [], 0, []
    for day in _daterange(start_date, end_date):
        objs = _list_day(chain, table, day)
        if not objs:
            missing.append(day)
            continue
        size = sum(o["size"] for o in objs)
        total += size
        days.append({"date": day, "bytes": size, "files": len(objs)})

    return {
        "chain": chain, "table": table,
        "days": days, "missing_days": missing,
        "total_bytes": total,
        "total_gb": round(total / 1024 ** 3, 3),
    }


def ingest_aws_blockchain(
    chain: str = "eth",
    table: str = "blocks",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_gb: float = DEFAULT_MAX_GB,
    out_dir: Optional[str] = None,
) -> Dict:
    """Downloads a date range into the Parquet store.

    Idempotent: days already on disk are skipped and don't count toward the
    budget. Defaults to the single most recent complete day, because a
    careless default here is measured in gigabytes.
    """
    chain = chain.lower()
    if end_date is None:
        end_date = (date.today() - timedelta(days=2)).isoformat()
    if start_date is None:
        start_date = end_date

    plan = preview(chain, table, start_date, end_date)
    out_dir = out_dir or os.path.join(CHAIN_DIR, f"symbol={CHAINS[chain]}", f"table={table}")
    os.makedirs(out_dir, exist_ok=True)

    todo, skipped = [], []
    for day in plan["days"]:
        target = os.path.join(out_dir, f"{chain}-{table}-{day['date']}.parquet")
        if os.path.exists(target):
            skipped.append(day["date"])
        else:
            todo.append((day, target))

    budget = max_gb * 1024 ** 3
    needed = sum(d["bytes"] for d, _ in todo)
    if needed > budget:
        raise BudgetExceeded(
            f"This range needs {needed / 1024**3:.2f} GB but the budget is {max_gb} GB. "
            f"Narrow the dates, pick a smaller table (blocks is ~5 MB/day vs traces at ~2.6 GB/day), "
            f"or raise max_gb deliberately."
        )

    written, failed, total_bytes = [], [], 0
    for day, target in todo:
        objs = _list_day(chain, table, day["date"])
        try:
            # One request per object, streamed to a temp file then renamed, so
            # an interrupted download never leaves a half file that a later run
            # would mistake for complete.
            tmp = target + ".part"
            with open(tmp, "wb") as f:
                for o in objs:
                    with requests.get(f"{BUCKET}/{quote(o['key'])}", stream=True, timeout=600) as r:
                        r.raise_for_status()
                        for block in r.iter_content(chunk_size=1024 * 1024):
                            f.write(block)
            os.replace(tmp, target)
            total_bytes += day["bytes"]
            written.append({"date": day["date"], "bytes": day["bytes"], "path": target})
            logging.info(f"{chain}/{table} {day['date']}: {day['bytes'] / 1024**2:.1f} MB -> {target}")
        except Exception as e:
            if os.path.exists(target + ".part"):
                os.remove(target + ".part")
            failed.append({"date": day["date"], "error": str(e)})
            logging.error(f"{chain}/{table} {day['date']}: {e}")

    return {
        "chain": chain, "table": table,
        "days_written": written, "days_skipped": skipped,
        "days_failed": failed, "missing_days": plan["missing_days"],
        "total_bytes": total_bytes,
        "total_gb": round(total_bytes / 1024 ** 3, 3),
        "output_dir": out_dir,
    }


if __name__ == "__main__":
    import json
    import sys

    ch = sys.argv[1] if len(sys.argv) > 1 else "eth"
    tb = sys.argv[2] if len(sys.argv) > 2 else "blocks"
    sd = sys.argv[3] if len(sys.argv) > 3 else None
    ed = sys.argv[4] if len(sys.argv) > 4 else None
    print(json.dumps(ingest_aws_blockchain(ch, tb, sd, ed), indent=2, default=str))
