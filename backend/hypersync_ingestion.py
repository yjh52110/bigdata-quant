import asyncio
import glob
import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from hypersync import (
    HypersyncClient,
    ClientConfig,
    Query,
    BlockField,
    TransactionField,
    LogField,
    FieldSelection,
    StreamConfig,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "hypersync_output")
MANIFEST_FILE = os.path.join(BASE_DIR, "data", "ingestion_manifest.jsonl")

CHAIN_URLS = {
    "ethereum": "https://eth.hypersync.xyz",
    "bitcoin": "https://bitcoin.hypersync.xyz",
    "polygon": "https://polygon.hypersync.xyz",
    "arbitrum": "https://arbitrum.hypersync.xyz",
    "optimism": "https://optimism.hypersync.xyz",
    "bsc": "https://bsc.hypersync.xyz",
}


class HypersyncTokenMissing(RuntimeError):
    """Raised when no HYPERSYNC_BEARER_TOKEN is configured. Sign up for a free
    token at https://app.envio.dev/api-tokens -- Hypersync has required an
    API token for every chain since well before this project started, there
    is no anonymous/free-without-signup tier."""


@dataclass
class IngestionResult:
    output_dir: str
    files: List[str]
    is_synthetic: bool
    from_block: int
    to_block: int
    chain: str
    error: Optional[str] = None


def _append_manifest(entry: dict) -> None:
    os.makedirs(os.path.dirname(MANIFEST_FILE), exist_ok=True)
    entry["logged_at"] = time.time()
    with open(MANIFEST_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


async def extract_chain_data(
    from_block: int = 18000000,
    to_block: int = 18000100,
    chain_name: str = "ethereum",
    bearer_token: Optional[str] = None,
) -> IngestionResult:
    """
    Streams real on-chain data (blocks, transactions, logs) for [from_block, to_block]
    on `chain_name` directly to Parquet via Hypersync's native collect_parquet streaming
    API -- no manual Arrow/pandas conversion, no synthetic fallback. Requires a real
    HYPERSYNC_BEARER_TOKEN; raises HypersyncTokenMissing if absent instead of silently
    writing fake data.
    """
    # hypersync >=1.0 renamed the credential to api_token but still accepts
    # bearer_token, so both env var spellings are honoured here.
    token = bearer_token or os.environ.get("HYPERSYNC_API_TOKEN") or os.environ.get("HYPERSYNC_BEARER_TOKEN")
    if not token:
        raise HypersyncTokenMissing(
            "HYPERSYNC_API_TOKEN (or HYPERSYNC_BEARER_TOKEN) is not set. Get a free "
            "token at https://app.envio.dev/api-tokens and export it before ingesting real data."
        )

    chain_key = chain_name.lower()
    url = CHAIN_URLS.get(chain_key, CHAIN_URLS["ethereum"])
    output_dir = os.path.join(DATA_DIR, f"symbol={chain_name.upper()}", f"{from_block}_{to_block}")
    os.makedirs(output_dir, exist_ok=True)

    logging.info(f"Starting real Hypersync extraction: {chain_name} blocks {from_block}->{to_block} from {url}")

    config = ClientConfig(url=url, api_token=token, max_num_retries=3)
    client = HypersyncClient(config)

    query = Query(
        from_block=from_block,
        to_block=to_block,
        field_selection=FieldSelection(
            block=[BlockField.NUMBER, BlockField.HASH, BlockField.TIMESTAMP],
            transaction=[
                TransactionField.HASH,
                TransactionField.BLOCK_NUMBER,
                TransactionField.FROM,
                TransactionField.TO,
                TransactionField.VALUE,
                TransactionField.GAS_USED,
            ],
            log=[LogField.ADDRESS, LogField.TOPIC0],
        ),
    )

    stream_config = StreamConfig(batch_size=1000, concurrency=4)

    try:
        await client.collect_parquet(output_dir, query, stream_config)
    except Exception as e:
        logging.error(f"Hypersync extraction failed: {e}")
        _append_manifest({
            "chain": chain_name, "from_block": from_block, "to_block": to_block,
            "is_synthetic": False, "success": False, "error": str(e),
        })
        raise

    written = glob.glob(os.path.join(output_dir, "*.parquet"))
    if not written:
        err = "collect_parquet returned no error but wrote no parquet files (empty block range?)"
        logging.warning(err)
        _append_manifest({
            "chain": chain_name, "from_block": from_block, "to_block": to_block,
            "is_synthetic": False, "success": False, "error": err,
        })
        return IngestionResult(output_dir, [], False, from_block, to_block, chain_name, error=err)

    logging.info(f"Wrote {len(written)} real parquet file(s) to {output_dir}")
    _append_manifest({
        "chain": chain_name, "from_block": from_block, "to_block": to_block,
        "is_synthetic": False, "success": True, "files": written,
    })
    return IngestionResult(output_dir, written, False, from_block, to_block, chain_name)


def generate_synthetic_fixture(from_block: int, to_block: int, chain_name: str = "ethereum") -> IngestionResult:
    """
    Local dev/test fixture ONLY. Writes obviously-fake data (random hashes/addresses)
    to a path and filename prefixed `synthetic_`, with an is_synthetic=True column
    baked into every row, so it can never be mistaken for real ingested data downstream.
    Never called automatically as a fallback for extract_chain_data().
    """
    import random
    import polars as pl

    output_dir = os.path.join(DATA_DIR, f"symbol={chain_name.upper()}")
    os.makedirs(output_dir, exist_ok=True)
    rows = 1000
    df = pl.DataFrame({
        "block_number": [random.randint(from_block, to_block) for _ in range(rows)],
        "block_hash": [f"0x{''.join(random.choices('0123456789abcdef', k=64))}" for _ in range(rows)],
        "timestamp": [int(time.time()) - random.randint(0, 100000) for _ in range(rows)],
        "tx_hash": [f"0x{''.join(random.choices('0123456789abcdef', k=64))}" for _ in range(rows)],
        "from_addr": [f"0x{''.join(random.choices('0123456789abcdef', k=40))}" for _ in range(rows)],
        "to_addr": [f"0x{''.join(random.choices('0123456789abcdef', k=40))}" for _ in range(rows)],
        "value": [random.uniform(0.01, 10.0) for _ in range(rows)],
        "gas_used": [random.randint(21000, 500000) for _ in range(rows)],
        "is_synthetic": [True] * rows,
    })
    path = os.path.join(output_dir, f"synthetic_{from_block}_{to_block}.parquet")
    df.write_parquet(path)
    logging.warning(f"Wrote SYNTHETIC fixture data to {path} -- this is NOT real chain data.")
    _append_manifest({
        "chain": chain_name, "from_block": from_block, "to_block": to_block,
        "is_synthetic": True, "success": True, "files": [path],
    })
    return IngestionResult(output_dir, [path], True, from_block, to_block, chain_name)


if __name__ == "__main__":
    import sys

    print("Testing real Hypersync Python extraction module...")
    try:
        result = asyncio.run(extract_chain_data(18000000, 18000010))
        print(f"Real extraction succeeded! Files: {result.files}")
    except HypersyncTokenMissing as e:
        print(f"No token configured ({e}); writing a clearly-labeled synthetic fixture instead for local testing.")
        result = generate_synthetic_fixture(18000000, 18000010)
        print(f"Synthetic fixture written (is_synthetic=True): {result.files}")
        sys.exit(0)
