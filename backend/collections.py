"""Scraper-facing storage: append-only datasets, keyed blobs, and a crawl queue.

Modelled on Apify's three primitives, because they match how collection actually
behaves, but adapted to what this project measured rather than copied verbatim.

The adaptation that matters is buffering. A scraper emits tens to hundreds of
records at a time; writing each batch straight out would produce thousands of
tiny files, and small files are where this architecture falls apart -- measured
2026-07-25, a 5.9 MB upload ran at 1.53 MB/s against 36.56 MB/s for 200 MB, a 24x
penalty, and every later read pays it again. So records land in a local buffer
and are only flushed once they are worth a file.

  Dataset       append-only records -> buffered -> 200-500 MB zstd Parquet
  KVStore       blobs by key; small ones inline in Parquet, large ones standalone,
                with an index so a key can be resolved without listing Drive
  RequestQueue  URL frontier with dedup and retry state, checkpointed because a
                free runtime is capped at 12 hours and a crawl outlives it

Nothing here uploads: these produce local, correctly-shaped output that
drive_rest ships and Catalog records. Keeping the two apart means a crawl that
loses its network still has its data.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.drive_store import (BLOB_INLINE_MAX, COMPRESSION, MIN_FILE_BYTES,
                                 blob_placement)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Flush when the buffer would make a worthwhile file, or when it has been open
# long enough that waiting costs more than the fragmentation would.
FLUSH_BYTES = MIN_FILE_BYTES
FLUSH_SECONDS = 3600
FLUSH_RECORDS = 500_000

QUEUE_PENDING, QUEUE_RUNNING, QUEUE_DONE, QUEUE_FAILED = "pending", "running", "done", "failed"
MAX_ATTEMPTS = 3


class CollectionError(RuntimeError):
    pass


def _estimate_bytes(records: List[Dict[str, Any]]) -> int:
    """Rough uncompressed size. Only used to decide when to flush, so an
    approximation beats serialising the buffer twice."""
    if not records:
        return 0
    sample = records[: min(20, len(records))]
    per = sum(len(json.dumps(r, default=str)) for r in sample) / len(sample)
    return int(per * len(records))


class Dataset:
    """Append-only records, flushed into properly sized Parquet files."""

    def __init__(self, name: str, root: str, *, time_column: Optional[str] = None,
                 flush_bytes: int = FLUSH_BYTES, flush_seconds: int = FLUSH_SECONDS):
        self.name = name
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir, exist_ok=True)
        self.time_column = time_column
        self.flush_bytes = flush_bytes
        self.flush_seconds = flush_seconds
        self._buffer: List[Dict[str, Any]] = []
        self._opened = time.time()
        self.files: List[Dict[str, Any]] = []

    def push(self, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        batch = [r for r in records if r]
        self._buffer.extend(batch)
        return {"buffered": len(self._buffer), "flushed": self.maybe_flush()}

    def _should_flush(self) -> Optional[str]:
        if not self._buffer:
            return None
        if _estimate_bytes(self._buffer) >= self.flush_bytes:
            return "size"
        if time.time() - self._opened >= self.flush_seconds:
            return "age"
        if len(self._buffer) >= FLUSH_RECORDS:
            return "count"
        return None

    def maybe_flush(self) -> Optional[Dict[str, Any]]:
        reason = self._should_flush()
        return self.flush(reason) if reason else None

    def flush(self, reason: str = "explicit") -> Optional[Dict[str, Any]]:
        """Writes the buffer out. Returns None when there is nothing to write."""
        if not self._buffer:
            return None
        try:
            import polars as pl
        except ImportError:
            raise CollectionError("polars is required to write dataset files")

        df = pl.DataFrame(self._buffer, infer_schema_length=None)
        # Sorting is what makes row-group statistics tight enough for a predicate
        # to skip anything on read; unsorted output means every group spans the
        # whole period.
        if self.time_column and self.time_column in df.columns:
            df = df.sort(self.time_column)
        seq = len(self.files)
        path = os.path.join(self.dir, f"part-{seq:05d}.parquet")
        df.write_parquet(path, compression=COMPRESSION)

        info = {"path": path, "rows": df.height, "bytes": os.path.getsize(path),
                "reason": reason, "written_at": time.time()}
        if info["bytes"] < MIN_FILE_BYTES and reason != "size":
            # Flushing early is sometimes right -- a crawl that ends, or a shift
            # boundary -- but the cost is real and should not be silent.
            info["warning"] = (
                f"{info['bytes'] / 1024**2:.1f} MB is below the "
                f"{MIN_FILE_BYTES // 1024**2} MB floor (flushed on {reason}); "
                f"expect roughly 1.5 MB/s rather than 36 MB/s for this file")
            logging.info(f"{self.name}: {info['warning']}")
        self.files.append(info)
        self._buffer = []
        self._opened = time.time()
        return info

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name, "dir": self.dir,
            "files": len(self.files),
            "rows": sum(f["rows"] for f in self.files),
            "bytes": sum(f["bytes"] for f in self.files),
            "buffered_records": len(self._buffer),
            "undersized_files": sum(1 for f in self.files if "warning" in f),
        }


class KVStore:
    """Blobs addressed by key, with an index so a key resolves without listing.

    Drive listings are slow and the drive.file scope cannot browse at all, so a
    key that could only be found by scanning would be unusable from an MCP call.
    """

    def __init__(self, name: str, root: str):
        self.name = name
        self.dir = os.path.join(root, name)
        self.blob_dir = os.path.join(self.dir, "blobs")
        os.makedirs(self.blob_dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, "index.json")
        self.index: Dict[str, Dict[str, Any]] = {}
        self._inline: Dict[str, bytes] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.index_path) as f:
                self.index = json.load(f)
        except (OSError, json.JSONDecodeError):
            self.index = {}

    def _save(self) -> None:
        tmp = self.index_path + ".part"
        with open(tmp, "w") as f:
            json.dump(self.index, f, indent=2)
        os.replace(tmp, self.index_path)

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream",
            meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not key:
            raise CollectionError("key must not be empty")
        placement = blob_placement(len(data))
        entry: Dict[str, Any] = {
            "key": key, "bytes": len(data), "content_type": content_type,
            "placement": placement, "sha256": hashlib.sha256(data).hexdigest(),
            "meta": meta or {}, "updated_at": time.time(),
        }
        if placement == "inline":
            # Held for the next pack(): thousands of small standalone files is
            # precisely the pattern that collapses to 1.5 MB/s.
            self._inline[key] = data
            entry["packed"] = False
        else:
            safe = hashlib.sha256(key.encode()).hexdigest()[:32]
            path = os.path.join(self.blob_dir, safe)
            with open(path, "wb") as f:
                f.write(data)
            entry["path"] = path
        self.index[key] = entry
        self._save()
        return entry

    def pack(self) -> Optional[Dict[str, Any]]:
        """Packs pending inline blobs into one Parquet file with a BLOB column."""
        if not self._inline:
            return None
        try:
            import polars as pl
        except ImportError:
            raise CollectionError("polars is required to pack blobs")
        keys = sorted(self._inline)
        df = pl.DataFrame({
            "key": keys,
            "content_type": [self.index[k]["content_type"] for k in keys],
            "sha256": [self.index[k]["sha256"] for k in keys],
            "data": [self._inline[k] for k in keys],
        })
        seq = sum(1 for e in self.index.values() if e.get("pack_file"))
        path = os.path.join(self.dir, f"blobs-{seq:05d}.parquet")
        df.write_parquet(path, compression=COMPRESSION)
        for k in keys:
            self.index[k]["packed"] = True
            self.index[k]["pack_file"] = path
        self._inline = {}
        self._save()
        return {"path": path, "keys": len(keys), "bytes": os.path.getsize(path)}

    def locate(self, key: str) -> Optional[Dict[str, Any]]:
        return self.index.get(key)

    def stats(self) -> Dict[str, Any]:
        inline = sum(1 for e in self.index.values() if e["placement"] == "inline")
        return {
            "name": self.name, "keys": len(self.index),
            "inline_keys": inline, "standalone_keys": len(self.index) - inline,
            "pending_pack": len(self._inline),
            "bytes": sum(e["bytes"] for e in self.index.values()),
            "inline_max_bytes": BLOB_INLINE_MAX,
        }


class RequestQueue:
    """URL frontier with dedup and retry, persisted so a crawl can resume.

    Persistence is not optional here: a free Colab or Kaggle session is capped at
    12 hours, and any crawl worth running outlives one session.
    """

    def __init__(self, name: str, root: str, *, max_attempts: int = MAX_ATTEMPTS):
        self.name = name
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "queue.json")
        self.max_attempts = max_attempts
        self.requests: Dict[str, Dict[str, Any]] = {}
        self._load()

    @staticmethod
    def key_for(url: str, method: str = "GET", payload: Any = None) -> str:
        raw = f"{method.upper()} {url} {json.dumps(payload, sort_keys=True, default=str)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _load(self) -> None:
        try:
            with open(self.path) as f:
                self.requests = json.load(f)
        except (OSError, json.JSONDecodeError):
            self.requests = {}

    def save(self) -> None:
        tmp = self.path + ".part"
        with open(tmp, "w") as f:
            json.dump(self.requests, f, indent=2)
        # Atomic: a half-written queue would lose the crawl's position.
        os.replace(tmp, self.path)

    def add(self, url: str, *, method: str = "GET", payload: Any = None,
            meta: Optional[Dict[str, Any]] = None) -> Tuple[str, bool]:
        """Returns (key, added). added is False when it was already known."""
        key = self.key_for(url, method, payload)
        if key in self.requests:
            return key, False
        self.requests[key] = {
            "key": key, "url": url, "method": method.upper(), "payload": payload,
            "meta": meta or {}, "state": QUEUE_PENDING, "attempts": 0,
            "added_at": time.time(), "error": None,
        }
        self.save()
        return key, True

    def reserve(self) -> Optional[Dict[str, Any]]:
        for r in self.requests.values():
            if r["state"] == QUEUE_PENDING:
                r["state"] = QUEUE_RUNNING
                r["attempts"] += 1
                r["reserved_at"] = time.time()
                self.save()
                return r
        return None

    def complete(self, key: str) -> None:
        r = self.requests.get(key)
        if not r:
            raise CollectionError(f"unknown request {key!r}")
        r["state"] = QUEUE_DONE
        r["completed_at"] = time.time()
        self.save()

    def fail(self, key: str, error: str) -> Dict[str, Any]:
        r = self.requests.get(key)
        if not r:
            raise CollectionError(f"unknown request {key!r}")
        r["error"] = str(error)[:500]
        # Back to pending while attempts remain: a transient failure should not
        # cost the URL, and a permanent one should not retry forever.
        r["state"] = QUEUE_PENDING if r["attempts"] < self.max_attempts else QUEUE_FAILED
        self.save()
        return r

    def reclaim_stale(self, older_than_s: int = 3600) -> int:
        """Returns requests left RUNNING by a killed session to the queue.

        Without this, everything in flight when a 12-hour session ends is stuck.
        """
        now = time.time()
        n = 0
        for r in self.requests.values():
            if r["state"] == QUEUE_RUNNING and now - r.get("reserved_at", 0) > older_than_s:
                r["state"] = QUEUE_PENDING if r["attempts"] < self.max_attempts else QUEUE_FAILED
                n += 1
        if n:
            self.save()
        return n

    def stats(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for r in self.requests.values():
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        return {
            "name": self.name, "total": len(self.requests),
            "pending": counts.get(QUEUE_PENDING, 0),
            "running": counts.get(QUEUE_RUNNING, 0),
            "done": counts.get(QUEUE_DONE, 0),
            "failed": counts.get(QUEUE_FAILED, 0),
            "max_attempts": self.max_attempts,
        }
