"""Query the AWS Public Blockchain dataset in place, without ingesting it.

Measured 2026-07-25/26, and the reason this module exists rather than an
ingestion path:

  column pruning   Reading one column of a 1400.9 MB daily file transferred
                   4.1 MB -- 0.29% of the file. Copying whole files to Drive
                   throws that away, because Drive downloads are whole-file.
  read speed       S3 in place: 43 MB/s single-stream, 324.5 MB/s with parallel
                   connections. Drive read-back: 29.96 MB/s.
  copy cost        The full 7.85 TB would take ~62 hours to move and 4.1 TB of
                   Drive space, to end up with something slower and coarser.

So raw chain data stays on S3. Drive holds only derived tables (features,
signals) and sources with no public archive.

Two details that are easy to get wrong:

  * `s3://` is required, not the https:// form. DuckDB refuses globs on generic
    HTTP paths ("Globs (`*`) for generic HTTP file is are not supported").
  * The bucket is public, but DuckDB still needs a secret to resolve a region;
    an anonymous config secret is enough.
"""

import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BUCKET = "aws-public-blockchain"
S3_ROOT = f"s3://{BUCKET}"
HTTPS_ROOT = f"https://{BUCKET}.s3.amazonaws.com/"
REGION = "us-east-1"
_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

# Verified by listing the bucket on 2026-07-26. Kept as data rather than
# discovered per call, because listing every prefix costs a round trip and the
# set changes rarely -- refresh_layout() re-derives it on demand.
# `prefix` rather than a version plus chain name, because five of these sit one
# level deeper under a provider folder (v1.1/sonarx/<chain>/) -- assuming
# "{version}/{chain}/" would silently miss half the catalogue.
LAYOUT: Dict[str, Dict[str, Any]] = {
    "eth": {"prefix": "v1.0/eth", "maintainer": "aws",
            "tables": ["blocks", "contracts", "logs", "token_transfers",
                       "traces", "transactions"]},
    "btc": {"prefix": "v1.0/btc", "maintainer": "aws",
            "tables": ["blocks", "transactions"]},
    "bnb": {"prefix": "v1.1/bnb", "maintainer": "aws",
            "tables": ["blocks", "transactions"]},
    "cronos": {"prefix": "v1.1/cronos", "maintainer": "aws",
               "tables": ["blocks", "decoded-events", "logs", "receipts", "transactions"]},
    "ton": {"prefix": "v1.1/ton", "maintainer": "aws", "tables": [
        "account_states", "balances_history", "blocks", "dex_pools", "dex_trades",
        "jetton_events", "jetton_metadata", "jetton_metadata_snapshots", "messages",
        "nft_events", "nft_items", "nft_metadata", "nft_sales", "nft_transfers",
        "transactions"]},
    "stellar": {"prefix": "v1.1/stellar", "maintainer": "aws", "tables": ["ledgers"]},
    # Third-party maintained; measured 8 days behind against 1-2 for the AWS set.
    "arbitrum": {"prefix": "v1.1/sonarx/arbitrum", "maintainer": "sonarx",
                 "tables": ["approvals", "blocks", "logs", "receipts", "traces",
                            "transactions", "transactions_failed", "transfers"]},
    "base": {"prefix": "v1.1/sonarx/base", "maintainer": "sonarx",
             "tables": ["approvals", "blocks", "logs", "receipts", "traces",
                        "transactions", "transactions_failed", "transfers"]},
    "aptos": {"prefix": "v1.1/sonarx/aptos", "maintainer": "sonarx",
              "tables": ["blocks", "changes", "deposits", "events", "transactions",
                         "transactions_failed", "transfers", "withdrawals"]},
    "xrp": {"prefix": "v1.1/sonarx/xrp", "maintainer": "sonarx",
            "tables": ["affected_nodes", "blocks", "transactions",
                       "transactions_failed", "transfers"]},
    "provenance": {"prefix": "v1.1/sonarx/provenance", "maintainer": "sonarx",
                   "tables": ["begin_block_events", "block_results", "blocks",
                              "end_block_events", "events", "finalize_block_events",
                              "logs", "signatures"]},
}

# Sizes and spans measured 2026-07-26 by listing every date partition and
# sampling one day per year, then weighting by that year's real partition
# count. An earlier attempt extrapolated 365 days per year from a single
# recent sample and treated stray date=1970-01-01 partitions as the start,
# which inflated several chains several-fold -- hence the per-year weighting.
MEASURED: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("arbitrum", "blocks"): {"gb": 219.4, "days": 1878, "earliest": "2021-05-28", "latest": "2026-07-18"},
    ("arbitrum", "logs"): {"gb": 1641.7, "days": 1877, "earliest": "2021-05-29", "latest": "2026-07-18"},
    ("arbitrum", "receipts"): {"gb": 313.0, "days": 1877, "earliest": "2021-05-29", "latest": "2026-07-18"},
    ("arbitrum", "traces"): {"gb": 10333.8, "days": 1418, "earliest": "2022-08-31", "latest": "2026-07-18"},
    ("arbitrum", "transactions"): {"gb": 765.2, "days": 1877, "earliest": "2021-05-29", "latest": "2026-07-18"},
    ("arbitrum", "transfers"): {"gb": 345.4, "days": 1877, "earliest": "2021-05-29", "latest": "2026-07-18"},
    ("base", "blocks"): {"gb": 37.1, "days": 1130, "earliest": "2023-06-15", "latest": "2026-07-18"},
    ("base", "logs"): {"gb": 5045.7, "days": 1115, "earliest": "2023-06-15", "latest": "2026-07-18"},
    ("base", "receipts"): {"gb": 568.7, "days": 1130, "earliest": "2023-06-15", "latest": "2026-07-18"},
    ("base", "traces"): {"gb": 27023.6, "days": 1130, "earliest": "2023-06-15", "latest": "2026-07-18"},
    ("base", "transactions"): {"gb": 2742.4, "days": 1130, "earliest": "2023-06-15", "latest": "2026-07-18"},
    ("base", "transfers"): {"gb": 1349.4, "days": 1122, "earliest": "2023-06-15", "latest": "2026-07-18"},
    ("bnb", "blocks"): {"gb": 140.3, "days": 2156, "earliest": "2020-08-29", "latest": "2026-07-24"},
    ("bnb", "transactions"): {"gb": 3910.5, "days": 2156, "earliest": "2020-08-29", "latest": "2026-07-24"},
    ("btc", "blocks"): {"gb": 0.3, "days": 6408, "earliest": "2009-01-03", "latest": "2026-07-25"},
    ("btc", "transactions"): {"gb": 2019.3, "days": 6408, "earliest": "2009-01-03", "latest": "2026-07-25"},
    ("cronos", "blocks"): {"gb": 15.7, "days": 1663, "earliest": "2021-11-08", "latest": "2026-07-24"},
    ("cronos", "decoded-events"): {"gb": 2.2, "days": 234, "earliest": "2025-10-06", "latest": "2026-07-24"},
    ("cronos", "logs"): {"gb": 33.6, "days": 1663, "earliest": "2021-11-08", "latest": "2026-07-24"},
    ("cronos", "receipts"): {"gb": 12.3, "days": 1663, "earliest": "2021-11-08", "latest": "2026-07-24"},
    ("cronos", "transactions"): {"gb": 32.6, "days": 1663, "earliest": "2021-11-08", "latest": "2026-07-24"},
    ("eth", "blocks"): {"gb": 18.3, "days": 4012, "earliest": "2015-07-30", "latest": "2026-07-24"},
    ("eth", "contracts"): {"gb": 30.3, "days": 4004, "earliest": "2015-08-07", "latest": "2026-07-24"},
    ("eth", "logs"): {"gb": 1024.1, "days": 4003, "earliest": "2015-08-08", "latest": "2026-07-24"},
    ("eth", "token_transfers"): {"gb": 391.8, "days": 3887, "earliest": "2015-10-27", "latest": "2026-07-24"},
    ("eth", "traces"): {"gb": 3562.0, "days": 4012, "earliest": "2015-07-30", "latest": "2026-07-24"},
    ("eth", "transactions"): {"gb": 1200.7, "days": 4004, "earliest": "2015-08-07", "latest": "2026-07-24"},
}

def measured_total_gb(chain: Optional[str] = None) -> float:
    """Sampled total, in GB. Raw chain data -- none of this is on Drive."""
    return round(sum(v["gb"] for (c, _), v in MEASURED.items()
                     if chain is None or c == chain), 1)

_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


class S3ViewError(ValueError):
    pass


def _check(chain: str, table: str) -> Dict[str, Any]:
    if chain not in LAYOUT:
        raise S3ViewError(f"unknown chain {chain!r}; have {sorted(LAYOUT)}")
    info = LAYOUT[chain]
    if table not in info["tables"]:
        raise S3ViewError(f"{chain} has no table {table!r}; have {info['tables']}")
    return info


def prepare(con) -> None:
    """Installs httpfs and the anonymous secret. Idempotent."""
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # A public bucket still needs a secret for region resolution.
    con.execute(f"CREATE OR REPLACE SECRET aws_public_blockchain "
                f"(TYPE s3, PROVIDER config, REGION '{REGION}');")


def glob_for(chain: str, table: str, date_prefix: str = "") -> str:
    """S3 glob for a chain/table, optionally narrowed by a date prefix.

    date_prefix is a left-anchored partial date: "2024", "2024-01" or
    "2024-01-15". Narrowing it matters: the wildcard's directory listing is what
    a broad range spends its time on (measured 3.67s for one day versus 6.98s
    for a whole-year glob filtered to the same day).
    """
    info = _check(chain, table)
    if date_prefix and not _DATE_RE.match(date_prefix):
        raise S3ViewError(f"date_prefix must look like 2024, 2024-01 or 2024-01-15, "
                          f"got {date_prefix!r}")
    pattern = f"date={date_prefix}*" if date_prefix else "date=*"
    return f"{S3_ROOT}/{info['prefix']}/{table}/{pattern}/*.parquet"


def read_expr(chain: str, table: str, date_prefix: str = "") -> str:
    """The read_parquet(...) expression, hive partitioning on so `date` is a column."""
    return f"read_parquet('{glob_for(chain, table, date_prefix)}', hive_partitioning=1)"


def view_name(chain: str, table: str, date_prefix: str = "") -> str:
    parts = ["s3", chain, table.replace("-", "_")]
    if date_prefix:
        parts.append(date_prefix.replace("-", "_"))
    return "_".join(parts)


def register(con, chain: str, table: str, date_prefix: str = "",
             name: Optional[str] = None) -> str:
    """Creates a view over the S3 data. No bytes move until it is queried."""
    prepare(con)
    name = name or view_name(chain, table, date_prefix)
    con.execute(f"CREATE OR REPLACE VIEW {name} AS "
                f"SELECT * FROM {read_expr(chain, table, date_prefix)}")
    return name


def register_all(con, date_prefix: str, chains: Optional[List[str]] = None) -> List[str]:
    """Registers every table of every chain for one date prefix.

    A prefix is required on purpose: a view spanning all history would make the
    first query pay for listing every partition directory.
    """
    if not date_prefix:
        raise S3ViewError("date_prefix is required -- an all-history view makes the "
                          "first query list every partition")
    prepare(con)
    made = []
    for chain in (chains or sorted(LAYOUT)):
        for table in LAYOUT[chain]["tables"]:
            try:
                made.append(register(con, chain, table, date_prefix))
            except Exception as e:
                logging.warning(f"could not register {chain}.{table}: {str(e)[:120]}")
    return made


def describe(con, chain: str, table: str, date_prefix: str = "") -> List[Dict[str, str]]:
    """Column names and types, read from one file's footer (a few hundred KB)."""
    prepare(con)
    sample = _first_key(chain, table, date_prefix)
    if not sample:
        raise S3ViewError(f"no parquet found for {chain}.{table} {date_prefix}")
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{sample}')").fetchall()
    return [{"name": r[0], "type": r[1]} for r in rows]


def _first_key(chain: str, table: str, date_prefix: str = "") -> Optional[str]:
    info = _check(chain, table)
    prefix = f"{info['prefix']}/{table}/"
    if date_prefix:
        prefix += f"date={date_prefix}"
    url = f"{HTTPS_ROOT}?list-type=2&max-keys=10&prefix={urllib.parse.quote(prefix)}"
    try:
        xml = urllib.request.urlopen(url, timeout=60).read()
    except Exception as e:
        raise S3ViewError(f"could not list {prefix}: {str(e)[:160]}")
    for c in ET.fromstring(xml).findall("s3:Contents", _S3_NS):
        key = c.find("s3:Key", _S3_NS).text
        if key.endswith(".parquet"):
            return f"{S3_ROOT}/{key}"
    return None


def date_range(chain: str, table: str) -> Dict[str, Any]:
    """Earliest and latest partition.

    S3 caps a listing page at 1000 keys, so the naive first page reports a
    truncated range -- it looked like eth stopped in 2018. Paginating to the end
    is what gives the real latest date.
    """
    info = _check(chain, table)
    prefix = f"{info['prefix']}/{table}/"
    earliest = latest = None
    token = None
    pages = 0
    while True:
        url = (f"{HTTPS_ROOT}?list-type=2&delimiter=/&prefix={urllib.parse.quote(prefix)}"
               + (f"&continuation-token={urllib.parse.quote(token)}" if token else ""))
        xml = ET.fromstring(urllib.request.urlopen(url, timeout=60).read())
        days = sorted(p.find("s3:Prefix", _S3_NS).text.split("date=")[-1].rstrip("/")
                      for p in xml.findall("s3:CommonPrefixes", _S3_NS))
        if days:
            earliest = earliest or days[0]
            latest = days[-1]
        pages += 1
        trunc = xml.find("s3:IsTruncated", _S3_NS)
        token_el = xml.find("s3:NextContinuationToken", _S3_NS)
        if trunc is None or trunc.text != "true" or token_el is None:
            break
        token = token_el.text
    return {"chain": chain, "table": table, "earliest": earliest, "latest": latest,
             "partitions_listed_pages": pages}


def refresh_layout() -> Dict[str, Dict[str, Any]]:
    """Re-derives LAYOUT from the bucket, for when AWS adds a chain."""
    def sub(prefix: str) -> List[str]:
        url = f"{HTTPS_ROOT}?list-type=2&delimiter=/&prefix={urllib.parse.quote(prefix)}"
        xml = ET.fromstring(urllib.request.urlopen(url, timeout=60).read())
        return [p.find("s3:Prefix", _S3_NS).text for p in xml.findall("s3:CommonPrefixes", _S3_NS)]

    found: Dict[str, Dict[str, Any]] = {}
    for version_prefix in sub(""):
        version = version_prefix.rstrip("/")
        for chain_prefix in sub(version_prefix):
            chain = chain_prefix.rstrip("/").split("/")[-1]
            tables = [t.rstrip("/").split("/")[-1] for t in sub(chain_prefix)]
            if tables:
                found[chain] = {"version": version, "tables": sorted(tables)}
    return found


def estimated_scan_gb(chain: str, table: str, days: int, columns_fraction: float = 1.0) -> float:
    """Rough bytes a query would move, so a caller can refuse the expensive ones.

    columns_fraction is the share of the file a narrow projection reads. Measured
    0.0029 for a single well-compressed numeric column of eth transactions -- it
    is column-specific, so pass a value you have reason to believe.
    """
    m = MEASURED.get((chain, table))
    total = m["gb"] if m else None
    if total is None:
        return float("nan")
    span = date_range(chain, table)
    if not (span["earliest"] and span["latest"]):
        return float("nan")
    from datetime import date
    y0, m0, d0 = (int(x) for x in span["earliest"].split("-"))
    y1, m1, d1 = (int(x) for x in span["latest"].split("-"))
    all_days = max(1, (date(y1, m1, d1) - date(y0, m0, d0)).days + 1)
    return round(total * (days / all_days) * columns_fraction, 3)
