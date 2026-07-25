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
LAYOUT: Dict[str, Dict[str, Any]] = {
    "eth": {"version": "v1.0", "tables": ["blocks", "contracts", "logs",
                                          "token_transfers", "traces", "transactions"]},
    "btc": {"version": "v1.0", "tables": ["blocks", "transactions"]},
    "bnb": {"version": "v1.1", "tables": ["blocks", "transactions"]},
    "cronos": {"version": "v1.1", "tables": ["blocks", "decoded-events", "logs",
                                             "receipts", "transactions"]},
    "ton": {"version": "v1.1", "tables": [
        "account_states", "balances_history", "blocks", "dex_pools", "dex_trades",
        "jetton_events", "jetton_metadata", "jetton_metadata_snapshots", "messages",
        "nft_events", "nft_items", "nft_metadata", "nft_sales", "nft_transfers",
        "transactions"]},
    "stellar": {"version": "v1.1", "tables": ["ledgers"]},
}

# Sizes measured by sampling S3 earlier in the project. Present so a caller can
# refuse a query that would scan terabytes before issuing it, not for display.
APPROX_TABLE_GB = {
    ("eth", "traces"): 3570, ("btc", "transactions"): 1723,
    ("eth", "transactions"): 1344, ("eth", "logs"): 1002,
    ("eth", "token_transfers"): 355, ("eth", "blocks"): 17.5,
}

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
    return f"{S3_ROOT}/{info['version']}/{chain}/{table}/{pattern}/*.parquet"


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
    prefix = f"{info['version']}/{chain}/{table}/"
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
    prefix = f"{info['version']}/{chain}/{table}/"
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
    total = APPROX_TABLE_GB.get((chain, table))
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
