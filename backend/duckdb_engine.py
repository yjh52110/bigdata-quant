import logging
import os
from typing import List

import duckdb
import sqlglot

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def find_parquet_files(data_dir: str = DATA_DIR) -> List[str]:
    """Recursively find every .parquet file under data_dir (data lives in nested
    symbol=X/from_to/ directories written by hypersync_ingestion.py -- a shallow
    glob misses all of it)."""
    found = []
    for root, _, files in os.walk(data_dir):
        for f in files:
            if f.endswith(".parquet"):
                found.append(os.path.join(root, f))
    return found


def mount_parquet_views(con: duckdb.DuckDBPyConnection, data_dir: str = DATA_DIR) -> List[str]:
    """Mounts every parquet file under data_dir as a DuckDB view. View names are
    derived from the parent `symbol=X` directory when present (so all block ranges
    for a chain merge into one queryable view), falling back to the filename."""
    # Dataset root directory -> view-name prefix, so on-chain and market data
    # don't collide in the same namespace (a BTCUSDT kline view called
    # "chain_btcusdt" would be actively misleading).
    ROOT_PREFIX = {"hypersync_output": "chain", "market": "market"}

    mounted = []
    for path in find_parquet_files(data_dir):
        rel_parts = os.path.relpath(path, data_dir).split(os.sep)
        partition_idx = [i for i, p in enumerate(rel_parts) if "=" in p]

        if partition_idx:
            last = partition_idx[-1]
            prefix = ROOT_PREFIX.get(rel_parts[0], rel_parts[0].replace("-", "_"))
            values = [rel_parts[i].split("=", 1)[1] for i in partition_idx]
            view_name = "_".join([prefix] + values).lower().replace("-", "_").replace(".", "_")
            # Glob the deepest partition dir so every file for that key merges
            # into one view. Rebuild from the real path -- joining data_dir
            # straight to the partition segment drops intermediate dirs.
            glob_path = os.path.join(data_dir, *rel_parts[: last + 1], "**", "*.parquet")
        else:
            view_name = os.path.basename(path).replace(".parquet", "").replace("-", "_").replace(".", "_")
            glob_path = path

        if view_name in mounted:
            continue
        try:
            con.execute(
                f"CREATE OR REPLACE VIEW {view_name} AS "
                f"SELECT * FROM read_parquet('{glob_path}', union_by_name=true)"
            )
            mounted.append(view_name)
        except Exception as e:
            logging.error(f"Failed to mount view for {glob_path}: {e}")
    return mounted


def is_select_only(sql: str) -> bool:
    """Validates SQL using sqlglot to strictly ensure only read-only SELECT
    statements are accepted -- blocks ATTACH/COPY/INSTALL/DDL/DML."""
    try:
        parsed = sqlglot.parse_one(sql)
        return isinstance(parsed, sqlglot.exp.Select)
    except Exception as e:
        logging.error(f"SQL validation error: {e}")
        return False
