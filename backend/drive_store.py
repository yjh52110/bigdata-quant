"""Storage conventions for the Drive side, and the catalog that indexes them.

Drive holds derived and unarchived data only. Raw chain data stays on S3 --
measured 2026-07-26, the public dataset is 61.31 TB across six chains, whole-file
Drive reads run at 29.96 MB/s against 43-324 MB/s in place, and a single-column
query transfers 0.29% of a file rather than all of it. Copying it in would be
slower, coarser and 61 TB larger.

Three data shapes, three treatments:

  tabular      Parquet + zstd. Chain extracts, klines, features, signals.
  text         Parquet + zstd with the body as a string column. News, posts,
               announcements. Keeps it SQL-queryable, and zstd handles prose
               far better than it handles the random bytes used in the
               throughput benchmarks.
  binary       Small blobs ride inside a Parquet BLOB column; large ones sit as
               standalone Drive files with a catalog row pointing at them. The
               split exists because of the small-file penalty below.

Numbers behind the rules, all measured in this project:

  zstd over snappy          739 MB vs 1408 MB for the same day of eth
                            transactions; decompression cost 0.07s
  file size 200-500 MB      200 MB uploads at 36.56 MB/s, 5.9 MB at 1.53 MB/s --
                            a 24x penalty for fragmenting
  sort by time in-file      otherwise every row group's min/max spans the whole
                            period and predicate pushdown skips nothing
  a catalog is required     drive.file scope only sees files this app created,
                            and Drive listings are slow; nothing can discover
                            what exists by browsing
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DRIVE_ROOT = "chainquant"

# Refinement stage, not data source: the seven source types in the design all
# flow through the same clean -> refine path, so layering by stage keeps one set
# of conventions instead of seven.
L1 = "L1_normalized"   # cleaned detail, only for sources with no public archive
L2 = "L2_features"     # factors -- the compressed value, and the main asset
L3 = "L3_signals"      # signals and strategy output, the delivered artefact
LAYERS = (L1, L2, L3)

COMPRESSION = "zstd"
TARGET_FILE_BYTES = 300 * 1024 * 1024      # middle of the measured sweet spot
MIN_FILE_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 500 * 1024 * 1024
# Above this a blob is stored standalone rather than inside a Parquet row, to
# keep row groups from ballooning past what a reader wants to buffer.
BLOB_INLINE_MAX = 8 * 1024 * 1024

CATALOG_DIR = "_catalog"
CATALOG_FILE = "datasets.json"
LOCAL_CATALOG = os.path.join(os.path.dirname(__file__), "data", CATALOG_DIR, CATALOG_FILE)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StoreError(ValueError):
    pass


def dataset_path(layer: str, **keys: str) -> str:
    """Builds a partition path: dataset_path(L2, name="addr_flow_1d", date="2024-01").

    Key order is the caller's, because it becomes the directory nesting and
    therefore what partition pruning can skip on. Put the coarsest filter first.
    """
    if layer not in LAYERS:
        raise StoreError(f"unknown layer {layer!r}; expected one of {LAYERS}")
    if not keys:
        raise StoreError("at least one partition key is required")
    parts = [f"{DRIVE_ROOT}/{layer}"]
    for k, v in keys.items():
        if not _KEY_RE.match(k):
            raise StoreError(f"partition key {k!r} must be lower_snake_case")
        if not _VALUE_RE.match(str(v)):
            raise StoreError(f"partition value {v!r} for {k!r} has characters that "
                             f"would break the path or the derived view name")
        parts.append(f"{k}={v}")
    return "/".join(parts)


def view_name_for(path: str) -> str:
    """View name a path would get, matching duckdb_engine's existing rule.

    duckdb_engine builds a name from the leading directory plus every key=value
    value, so this has to agree with it or the dashboard and the catalog disagree
    about what a dataset is called.
    """
    parts = [p for p in path.split("/") if p and p != DRIVE_ROOT]
    if not parts:
        raise StoreError(f"no dataset in path {path!r}")
    prefix = parts[0]
    for layer, short in ((L1, "l1"), (L2, "features"), (L3, "signals")):
        if prefix == layer:
            prefix = short
            break
    values = [p.split("=", 1)[1] for p in parts[1:] if "=" in p]
    name = "_".join([prefix] + values).lower()
    return re.sub(r"[^a-z0-9_]", "_", name)


def plan_files(total_bytes: int) -> Dict[str, Any]:
    """How many parts to split a dataset into, and whether that is sane.

    Splitting below MIN_FILE_BYTES is the single most expensive mistake here:
    measured 1.53 MB/s at 5.9 MB against 36.56 MB/s at 200 MB.
    """
    if total_bytes <= 0:
        raise StoreError("total_bytes must be positive")
    if total_bytes < MIN_FILE_BYTES:
        return {"parts": 1, "part_bytes": total_bytes, "warning": (
            f"{total_bytes / 1024**2:.1f} MB is below the {MIN_FILE_BYTES // 1024**2} MB "
            f"floor -- accumulate more before writing, or accept roughly 1.5 MB/s "
            f"instead of 36 MB/s")}
    parts = max(1, round(total_bytes / TARGET_FILE_BYTES))
    part = total_bytes / parts
    while part > MAX_FILE_BYTES:
        parts += 1
        part = total_bytes / parts
    return {"parts": parts, "part_bytes": int(part), "warning": None}


def blob_placement(size_bytes: int) -> str:
    """"inline" to put the blob in a Parquet column, "standalone" for its own file."""
    return "inline" if size_bytes <= BLOB_INLINE_MAX else "standalone"


def write_options(sort_key: Optional[str] = None) -> Dict[str, Any]:
    """Parquet options every writer in this project should use."""
    return {
        "compression": COMPRESSION,
        # Sorting is not cosmetic: without it row-group statistics span the whole
        # partition and no predicate can skip a group.
        "sort_by": sort_key,
        "row_group_size": 122_880,
    }


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------
CATALOG_FIELDS = ("dataset", "layer", "path", "partition_keys", "drive_folder_id",
                  "rows", "bytes", "files", "time_min", "time_max", "schema",
                  "source", "updated_at")


class Catalog:
    """What exists, where, and how big -- without listing Drive.

    Needed rather than nice to have: the drive.file scope only exposes files this
    application created, so there is no browsing fallback, and Drive listings are
    slow enough that answering "what data do you have" by scanning would dominate
    an MCP call's latency.
    """

    def __init__(self, path: str = LOCAL_CATALOG):
        self.path = path
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.entries = {e["dataset"]: e for e in data.get("datasets", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            self.entries = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".part"
        with open(tmp, "w") as f:
            json.dump({"updated_at": time.time(),
                       "datasets": sorted(self.entries.values(), key=lambda e: e["dataset"])},
                      f, indent=2, ensure_ascii=False)
        # Atomic: a truncated catalog would make every dataset look missing.
        os.replace(tmp, self.path)

    def upsert(self, *, layer: str, partition_keys: Dict[str, str],
               rows: int = 0, bytes_: int = 0, files: int = 0,
               time_min: Optional[str] = None, time_max: Optional[str] = None,
               schema: Optional[List[Dict[str, str]]] = None,
               drive_folder_id: Optional[str] = None,
               source: Optional[str] = None) -> Dict[str, Any]:
        path = dataset_path(layer, **partition_keys)
        name = view_name_for(path)
        entry = {
            "dataset": name, "layer": layer, "path": path,
            "partition_keys": list(partition_keys),
            "drive_folder_id": drive_folder_id,
            "rows": rows, "bytes": bytes_, "files": files,
            "time_min": time_min, "time_max": time_max,
            "schema": schema or [], "source": source,
            "updated_at": time.time(),
        }
        existing = self.entries.get(name)
        if existing:
            # Accumulate rather than overwrite: partitions are written
            # incrementally, and losing the running totals would make the catalog
            # describe only the most recent batch.
            entry["rows"] += existing.get("rows", 0)
            entry["bytes"] += existing.get("bytes", 0)
            entry["files"] += existing.get("files", 0)
            if existing.get("time_min") and (not time_min or existing["time_min"] < time_min):
                entry["time_min"] = existing["time_min"]
            if existing.get("time_max") and (not time_max or existing["time_max"] > time_max):
                entry["time_max"] = existing["time_max"]
            entry["drive_folder_id"] = drive_folder_id or existing.get("drive_folder_id")
            entry["schema"] = schema or existing.get("schema") or []
        self.entries[name] = entry
        self.save()
        return entry

    def get(self, dataset: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(dataset)

    def list(self, layer: Optional[str] = None) -> List[Dict[str, Any]]:
        out = list(self.entries.values())
        if layer:
            out = [e for e in out if e["layer"] == layer]
        return sorted(out, key=lambda e: e["dataset"])

    def summary(self) -> Dict[str, Any]:
        by_layer: Dict[str, Dict[str, int]] = {}
        for e in self.entries.values():
            agg = by_layer.setdefault(e["layer"], {"datasets": 0, "rows": 0, "bytes": 0, "files": 0})
            agg["datasets"] += 1
            for k in ("rows", "bytes", "files"):
                agg[k] += e.get(k, 0) or 0
        return {
            "total_datasets": len(self.entries),
            "total_bytes": sum(e.get("bytes", 0) or 0 for e in self.entries.values()),
            "by_layer": by_layer,
            # Stated so a reader does not mistake this for the raw chain volume.
            "note": "仅统计云盘上的派生数据；原始链上数据留在 S3，不计入此处",
        }
