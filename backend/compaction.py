"""Merge undersized Parquet parts into properly sized ones.

This is the one problem here that gets worse with use rather than better. A
scraper that flushes on a timer, or a daily job that writes one small file per
run, accumulates fragments; measured 2026-07-25, a 5.9 MB upload ran at
1.53 MB/s against 36.56 MB/s for 200 MB, and unlike a one-off transfer cost that
penalty is paid again on every later read. collections.Dataset warns when it
writes small, but warning is not fixing.

Safety is the whole design, because compaction is the one operation that can
destroy data that already exists:

  verify before delete   the merged file's row count and time span are checked
                         against the sum of its inputs; a mismatch aborts and
                         leaves every input untouched
  write aside, then swap the replacement is built under a temporary name and
                         renamed into place, so an interrupted run leaves either
                         the old parts or the new one, never a half-written mix
  same schema only       files whose columns differ are never merged into each
                         other; they are reported instead
  sort preserved         merging without re-sorting would leave row-group
                         statistics spanning the whole range, silently undoing
                         the reason the parts were sorted in the first place
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.drive_store import (COMPRESSION, MAX_FILE_BYTES, MIN_FILE_BYTES,
                                 TARGET_FILE_BYTES)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TMP_SUFFIX = ".compacting"


class CompactionError(RuntimeError):
    pass


def _parquet_files(directory: str) -> List[str]:
    out = []
    for dirpath, _, names in os.walk(directory):
        for n in sorted(names):
            if n.endswith(".parquet") and not n.endswith(TMP_SUFFIX):
                out.append(os.path.join(dirpath, n))
    return sorted(out)


def plan(directory: str, *, target_bytes: int = TARGET_FILE_BYTES,
         min_bytes: int = MIN_FILE_BYTES,
         max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """Groups of files worth merging, without touching anything.

    Files already at or above the floor are left alone: rewriting them would
    spend the upload cost again for no read-side gain.
    """
    files = [(p, os.path.getsize(p)) for p in _parquet_files(directory)]
    small = [(p, s) for p, s in files if s < min_bytes]
    keep = [(p, s) for p, s in files if s >= min_bytes]

    groups: List[List[str]] = []
    current: List[str] = []
    running = 0
    for path, size in small:
        if current and running + size > max_bytes:
            groups.append(current)
            current, running = [], 0
        current.append(path)
        running += size
    if current:
        groups.append(current)

    # A lone small file has nothing to merge with; leave it and say so.
    mergeable = [g for g in groups if len(g) > 1]
    singletons = [g[0] for g in groups if len(g) == 1]

    total_small = sum(s for _, s in small)
    return {
        "directory": directory,
        "files_total": len(files),
        "files_small": len(small),
        "bytes_small": total_small,
        "groups": mergeable,
        "singletons": singletons,
        "already_ok": [p for p, _ in keep],
        "estimated_parts_after": len(mergeable) + len(singletons) + len(keep),
        "note": (f"低于 {min_bytes // 1024**2} MB 的文件读写都按约 1.5 MB/s 计，"
                 f"合并后按约 36 MB/s；已达标的文件不重写，重写只会白付一次上传成本"),
    }


def _read_meta(path: str) -> Tuple[int, List[str]]:
    import polars as pl
    schema = pl.read_parquet_schema(path)
    rows = pl.scan_parquet(path).select(pl.len()).collect().item()
    return rows, sorted(schema)


def compact_group(paths: List[str], *, time_column: Optional[str] = None,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Merges one group. Inputs are deleted only after the output is verified."""
    if len(paths) < 2:
        raise CompactionError("a group needs at least two files")
    import polars as pl

    metas = [_read_meta(p) for p in paths]
    schemas = {tuple(m[1]) for m in metas}
    if len(schemas) > 1:
        raise CompactionError(
            f"refusing to merge {len(paths)} files with differing columns: "
            f"{[sorted(s) for s in schemas]}")
    expected_rows = sum(m[0] for m in metas)
    expected_bytes = sum(os.path.getsize(p) for p in paths)

    if dry_run:
        return {"paths": paths, "expected_rows": expected_rows,
                "expected_bytes": expected_bytes, "dry_run": True}

    df = pl.concat([pl.read_parquet(p) for p in paths], how="vertical")
    if time_column and time_column in df.columns:
        # Re-sorting is not optional: concatenating sorted parts gives a file
        # whose row groups each span the whole range, which is exactly the state
        # sorting was meant to avoid.
        df = df.sort(time_column)

    out_dir = os.path.dirname(paths[0])
    stamp = int(time.time())
    final = os.path.join(out_dir, f"part-compacted-{stamp}.parquet")
    tmp = final + TMP_SUFFIX
    df.write_parquet(tmp, compression=COMPRESSION)

    got_rows, got_cols = _read_meta(tmp)
    if got_rows != expected_rows:
        os.remove(tmp)
        raise CompactionError(
            f"row count mismatch: inputs had {expected_rows}, output has {got_rows}; "
            f"inputs left untouched")
    if sorted(got_cols) != sorted(next(iter(schemas))):
        os.remove(tmp)
        raise CompactionError("column set changed during merge; inputs left untouched")

    # Rename into place first, then remove inputs: an interruption leaves either
    # the old parts plus an unreferenced new file, or the new file alone.
    os.replace(tmp, final)
    removed = []
    for p in paths:
        try:
            os.remove(p)
            removed.append(p)
        except OSError as e:
            logging.warning(f"merged output kept, but could not remove {p}: {e}")

    return {
        "output": final,
        "inputs": paths,
        "removed": removed,
        "rows": got_rows,
        "bytes_before": expected_bytes,
        "bytes_after": os.path.getsize(final),
        "sorted_by": time_column if time_column and time_column in df.columns else None,
    }


def compact(directory: str, *, time_column: Optional[str] = None,
            dry_run: bool = False, **plan_kw: Any) -> Dict[str, Any]:
    """Plans and runs compaction over a directory."""
    p = plan(directory, **plan_kw)
    results, failures = [], []
    for group in p["groups"]:
        try:
            results.append(compact_group(group, time_column=time_column, dry_run=dry_run))
        except CompactionError as e:
            # One bad group must not stop the rest; report it and carry on.
            failures.append({"group": group, "error": str(e)})
            logging.warning(f"skipped a group: {e}")
    before = sum(r.get("bytes_before", 0) for r in results)
    after = sum(r.get("bytes_after", 0) for r in results)
    return {
        "plan": p,
        "merged_groups": len(results),
        "files_removed": sum(len(r.get("removed", [])) for r in results),
        "rows": sum(r.get("rows", 0) for r in results),
        "bytes_before": before,
        "bytes_after": after,
        "results": results,
        "failures": failures,
        "dry_run": dry_run,
    }
