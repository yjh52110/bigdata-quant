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

import hashlib
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
                  "account", "rows", "bytes", "files", "time_min", "time_max",
                  "schema", "source", "updated_at")


# --------------------------------------------------------------------------
# Placement across several Drive accounts
# --------------------------------------------------------------------------
# Why placement lives here rather than in rclone: this app authorises drive.file,
# which grants access only to files it created itself. An rclone union would
# upload under rclone's own client id, and those files would then be invisible to
# this application -- the very isolation that made drive.file safe also rules out
# federating accounts at the filesystem layer. So the catalog does what a
# metadata service does in any distributed store: it records where each dataset
# lives, and reads go straight there.
#
# One rule dominates: a dataset is never split across accounts. Splitting would
# force every read to fan out over several tokens for no gain, since a dataset
# here is tens to hundreds of MB against a 5 TB per-account limit.

# Domains pinned to a preferred account. Anything unpinned, or pinned to a full
# account, falls through to most-free-space.
DOMAIN_PINS: Dict[str, str] = {}

# Leave headroom rather than filling an account: Drive slows and starts refusing
# writes near the limit, and the daily 750 GB upload cap is per account, so a
# spread also parallelises ingestion.
ACCOUNT_HEADROOM = 0.90


class PlacementError(RuntimeError):
    pass


def choose_account(accounts: List[Dict[str, Any]], *, domain: Optional[str] = None,
                   need_bytes: int = 0,
                   existing: Optional[str] = None,
                   writer: Optional[str] = None) -> str:
    """Which account should hold this dataset.

    accounts is what GoogleAccountManager.get_all_quotas() returns.

    Order of preference:
      1. wherever the dataset already lives -- moving it would orphan the
         folder id every reader holds
      2. the account pinned to this domain, if it has room
      3. the connected account with the most free space
    """
    usable = [a for a in accounts
              if a.get("is_connected") and a.get("free", 0) > need_bytes
              and a.get("used", 0) < a.get("limit", 0) * ACCOUNT_HEADROOM]
    if not usable:
        connected = [a for a in accounts if a.get("is_connected")]
        if not connected:
            raise PlacementError("no connected Drive account")
        raise PlacementError(
            f"no account has room for {need_bytes / 1024**3:.2f} GB within "
            f"{ACCOUNT_HEADROOM:.0%} of its limit; connected: "
            f"{[a['account_index'] for a in connected]}")

    if existing and any(a["account_index"] == existing for a in usable):
        return existing
    pinned = DOMAIN_PINS.get(domain or "")
    if pinned and any(a["account_index"] == pinned for a in usable):
        return pinned

    ranked = sorted(usable, key=lambda a: (-a.get("free", 0), a["account_index"]))
    if writer and len(ranked) > 1:
        # Concurrent writers all see the same "most free" account and would all
        # pile onto it, overfilling one while the rest sit idle -- and Drive's
        # 750 GB/day cap is per account, so that also serialises what could have
        # run in parallel. Offsetting by a hash of the writer id spreads them
        # deterministically, with no coordination and no shared lock.
        offset = int(hashlib.sha256(writer.encode()).hexdigest(), 16) % len(ranked)
        # Only among accounts that can actually hold it, so the spread never
        # picks somewhere too full.
        return ranked[offset]["account_index"]
    return ranked[0]["account_index"]


def placement_report(accounts: List[Dict[str, Any]],
                     entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-account usage as this platform sees it, beside Drive's own figure.

    The two differ on purpose: Drive's number covers everything in the account,
    ours covers only what this platform wrote. A gap is expected, not a fault.
    """
    by_account: Dict[str, Dict[str, Any]] = {}
    for a in accounts:
        by_account[a["account_index"]] = {
            "account": a["account_index"], "email": a.get("email"),
            "connected": a.get("is_connected", False),
            "drive_used": a.get("used", 0), "drive_limit": a.get("limit", 0),
            "drive_free": a.get("free", 0),
            "our_datasets": 0, "our_bytes": 0, "domains": [],
        }
    unplaced = 0
    for e in entries:
        acc = e.get("account")
        if not acc or acc not in by_account:
            unplaced += 1
            continue
        slot = by_account[acc]
        slot["our_datasets"] += 1
        slot["our_bytes"] += e.get("bytes", 0) or 0
        domain = (e.get("partition_keys") or [None])[0]
        if domain and domain not in slot["domains"]:
            slot["domains"].append(domain)
    return {
        "accounts": sorted(by_account.values(), key=lambda x: x["account"]),
        "unplaced_datasets": unplaced,
        "pins": dict(DOMAIN_PINS),
        "note": ("drive_used 为该账号的全部占用，our_bytes 只是本平台写入的部分，"
                 "两者不一致属正常。数据集不跨账号拆分：拆了每次读取都要多个令牌扇出，"
                 "而单个数据集只有几十到几百 MB，5 TB 的账号完全装得下"),
    }


# Concurrency, and why the catalog is sharded rather than locked.
#
# Several Kaggle or Colab sessions can be writing at once, and Drive offers no
# lock and no conditional write, so read-modify-write on one shared file loses
# entries: two writers both read, both append, and the second save erases the
# first's work. Each writer therefore owns one shard file and never touches
# another's; a reader merges them. No coordination is needed because no two
# writers ever write the same bytes.
#
# The merge rule for a dataset seen in several shards is last-write-wins on
# updated_at, with counts summed, because shards describe disjoint partitions of
# the same dataset -- one session doing January and another doing February both
# legitimately contribute rows.
DEFAULT_SHARD = "local"


class Catalog:
    """What exists, where, and how big -- without listing Drive.

    Needed rather than nice to have: the drive.file scope only exposes files this
    application created, so there is no browsing fallback, and Drive listings are
    slow enough that answering "what data do you have" by scanning would dominate
    an MCP call's latency.
    """

    def __init__(self, path: str = LOCAL_CATALOG, shard: str = DEFAULT_SHARD):
        self.path = path
        self.shard = shard
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.load()

    @property
    def shard_dir(self) -> str:
        return os.path.join(os.path.dirname(self.path), "shards")

    def shard_path(self, shard: Optional[str] = None) -> str:
        return os.path.join(self.shard_dir, f"{shard or self.shard}.json")

    def _any_shard_exists(self) -> bool:
        return os.path.isdir(self.shard_dir) and any(
            f.endswith(".json") for f in os.listdir(self.shard_dir))

    @staticmethod
    def merge(entries_lists: List[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
        """Combines shards. Counts add; descriptive fields follow the newest."""
        out: Dict[str, Dict[str, Any]] = {}
        for entries in entries_lists:
            for e in entries:
                name = e.get("dataset")
                if not name:
                    continue
                cur = out.get(name)
                if cur is None:
                    out[name] = dict(e)
                    continue
                merged = dict(cur if cur.get("updated_at", 0) >= e.get("updated_at", 0) else e)
                for k in ("rows", "bytes", "files"):
                    merged[k] = (cur.get(k) or 0) + (e.get(k) or 0)
                for k, better in (("time_min", min), ("time_max", max)):
                    vals = [v for v in (cur.get(k), e.get(k)) if v]
                    merged[k] = better(vals) if vals else None
                # Placement must not flip-flop between shards: the earliest
                # recorded account wins, matching upsert's no-relocation rule.
                first = cur if cur.get("updated_at", 0) <= e.get("updated_at", 0) else e
                merged["account"] = first.get("account") or merged.get("account")
                out[name] = merged
        return out

    def load_all_shards(self) -> Dict[str, Dict[str, Any]]:
        """Everything every writer has recorded."""
        lists = []
        own = f"{self.shard}.json"
        if os.path.isdir(self.shard_dir):
            for fn in sorted(os.listdir(self.shard_dir)):
                # Skip our own file: the in-memory copy below supersedes it, and
                # counting both would double every row this writer contributed.
                if not fn.endswith(".json") or fn == own:
                    continue
                try:
                    with open(os.path.join(self.shard_dir, fn)) as f:
                        lists.append(json.load(f).get("datasets", []))
                except (OSError, json.JSONDecodeError):
                    logging.warning(f"skipping unreadable catalog shard {fn}")
        lists.append(list(self.entries.values()))
        return self.merge(lists)

    def load(self) -> None:
        """Loads this writer's own entries.

        The shard file is this writer's authority; the main file is a merged
        snapshot written for backup and single-file reads. Reading the main file
        back into entries would count every other writer's rows as our own --
        which is exactly what it did until a test caught a third reader seeing
        200 rows where the two writers each saw 150.

        The main file is still read once, when no shard exists yet, so an
        install that predates sharding keeps its catalog.
        """
        if os.path.exists(self.shard_path()):
            source = self.shard_path()
        elif self._any_shard_exists():
            # Other writers exist, so this is a new writer, not a pre-sharding
            # install. Starting from the merged file would claim their rows as
            # ours and double them on the next merge.
            self.entries = {}
            return
        else:
            source = self.path
        try:
            with open(source) as f:
                data = json.load(f)
            self.entries = {e["dataset"]: e for e in data.get("datasets", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            self.entries = {}

    def _write(self, target: str, datasets: List[Dict[str, Any]], shard: Optional[str]) -> None:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".part"
        with open(tmp, "w") as f:
            json.dump({"updated_at": time.time(), "shard": shard,
                       "datasets": sorted(datasets, key=lambda e: e["dataset"])},
                      f, indent=2, ensure_ascii=False)
        # Atomic: a truncated catalog would make every dataset look missing.
        os.replace(tmp, target)

    def save(self) -> None:
        # Our shard holds only our own work; the main file holds the merged view,
        # so a single-file read or a backup sees everything without needing the
        # shard directory.
        self._write(self.shard_path(), list(self.entries.values()), self.shard)
        self._write(self.path, list(self.load_all_shards().values()), None)

    def upsert(self, *, layer: str, partition_keys: Dict[str, str],
               rows: int = 0, bytes_: int = 0, files: int = 0,
               time_min: Optional[str] = None, time_max: Optional[str] = None,
               schema: Optional[List[Dict[str, str]]] = None,
               drive_folder_id: Optional[str] = None,
               account: Optional[str] = None,
               source: Optional[str] = None) -> Dict[str, Any]:
        path = dataset_path(layer, **partition_keys)
        name = view_name_for(path)
        entry = {
            "dataset": name, "layer": layer, "path": path,
            "partition_keys": list(partition_keys),
            "drive_folder_id": drive_folder_id,
            "account": account,
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
            # Never silently relocate: readers hold the folder id, and a dataset
            # that moved would leave them pointing at nothing.
            entry["account"] = existing.get("account") or account
            entry["schema"] = schema or existing.get("schema") or []
        self.entries[name] = entry
        self.save()
        return entry

    # ----------------------------------------------------------------------
    # Backup
    # ----------------------------------------------------------------------
    # The catalog is small (about 1 KB per dataset) and irreplaceable in one
    # respect: paths are self-describing, so most of it could be rebuilt by
    # walking Drive -- but which account holds a dataset exists nowhere else, and
    # recovering it would mean searching every connected account. Keeping a copy
    # beside the data costs nothing and removes that scenario.
    def backup_to_drive(self, drive_rest, token: str) -> Dict[str, Any]:
        """Uploads the catalog next to the data it describes."""
        if not os.path.exists(self.path):
            raise StoreError("nothing to back up: catalog file does not exist")
        folder = drive_rest.ensure_path(token, f"{DRIVE_ROOT}/{CATALOG_DIR}")
        # Overwrite rather than accumulate: an old catalog is worse than none,
        # because it would confidently point at datasets that have moved.
        existing = drive_rest.find_file(token, CATALOG_FILE, folder)
        info = drive_rest.upload(token, self.path, folder, name=CATALOG_FILE)
        return {"uploaded": info, "replaced": bool(existing), "folder_id": folder,
                "datasets": len(self.entries)}

    def restore_from_drive(self, drive_rest, token: str, *,
                           overwrite: bool = False) -> Dict[str, Any]:
        """Pulls the catalog back. Refuses to clobber a local one unless asked.

        Overwriting by default would turn a stale backup into data loss on any
        machine that happened to be ahead.
        """
        if os.path.exists(self.path) and not overwrite:
            return {"restored": False, "reason": "local catalog exists; pass overwrite=True"}
        folder = drive_rest.ensure_path(token, f"{DRIVE_ROOT}/{CATALOG_DIR}")
        found = drive_rest.find_file(token, CATALOG_FILE, folder)
        if not found:
            return {"restored": False, "reason": "no catalog on Drive"}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        drive_rest.download(token, found["id"], self.path)
        self.load()
        return {"restored": True, "datasets": len(self.entries)}

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
