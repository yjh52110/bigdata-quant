import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import logging
from typing import List

import duckdb
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.transfer_log import record_transfer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "hypersync_output")
STATE_FILE = os.path.join(BASE_DIR, "data", "compaction_state.json")
RCLONE_REMOTE = os.environ.get("RCLONE_UNION_REMOTE", "gdrive_union:quant-data")


def _read_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(**updates) -> None:
    state = _read_state()
    state.update(updates)
    state["last_heartbeat_at"] = time.time()
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def upload_to_union(local_path: str) -> bool:
    """Copies a compacted parquet file up to the rclone union Drive mount.

    This closes the gap the original design doc never actually implemented:
    compaction alone only merged files on local disk and never pushed the
    result anywhere near the Google Drive storage pool the whole platform is
    built around.
    """
    if shutil.which("rclone") is None:
        logging.warning("rclone binary not found on PATH; compacted file stays local only.")
        return False
    dest = f"{RCLONE_REMOTE}/{os.path.basename(local_path)}"
    try:
        subprocess.run(["rclone", "copyto", local_path, dest], check=True, capture_output=True, timeout=300)
        size = os.path.getsize(local_path)
        record_transfer("upload", size, account_index="union")
        logging.info(f"Uploaded {local_path} -> {dest} ({size} bytes)")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        logging.error(f"rclone upload failed: {stderr}")
        return False
    except subprocess.TimeoutExpired:
        logging.error("rclone upload timed out")
        return False


class ParquetCompactionHandler(FileSystemEventHandler):
    """
    Watches local buffer directory for incoming micro-parquet files (from Hypersync).
    Automatically merges (compacts) files into large Parquet files once the pending
    batch crosses size_threshold_mb, then uploads the result to the Drive union mount.
    """
    def __init__(self, target_dir: str, size_threshold_mb: float = 100.0):
        self.target_dir = target_dir
        self.size_threshold_bytes = size_threshold_mb * 1024 * 1024
        self.pending_files: List[str] = []

    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".parquet"):
            return
        if "compacted_" in os.path.basename(event.src_path):
            # Catches both our own output (compacted_*.parquet) and DuckDB's
            # transient write-ahead temp file (tmp_compacted_*.parquet) --
            # without this the watchdog re-ingests its own output as if it
            # were new source data.
            return
        logging.info(f"Detected new micro-parquet file: {event.src_path}")
        self.pending_files.append(event.src_path)
        self._check_and_compact()

    def _check_and_compact(self):
        total_size = sum(os.path.getsize(f) for f in self.pending_files if os.path.exists(f))
        if total_size >= self.size_threshold_bytes:
            logging.info(f"Accumulated Parquet size ({total_size / 1024 / 1024:.2f} MB) exceeded threshold. Compacting...")
            self.compact_files(self.pending_files)
            self.pending_files.clear()

    def compact_files(self, files_to_compact: List[str]):
        if not files_to_compact:
            return

        compacted_filename = f"compacted_{int(time.time())}.parquet"
        compacted_filepath = os.path.join(self.target_dir, compacted_filename)

        try:
            con = duckdb.connect(':memory:')
            file_list_str = "[" + ", ".join(f"'{f}'" for f in files_to_compact) + "]"
            sql = f"COPY (SELECT * FROM read_parquet({file_list_str}, union_by_name=true)) TO '{compacted_filepath}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            con.execute(sql)
            logging.info(f"Compacted {len(files_to_compact)} files into {compacted_filepath}")

            uploaded = upload_to_union(compacted_filepath)

            for f in files_to_compact:
                if os.path.exists(f):
                    os.remove(f)
            logging.info("Cleaned up original micro-parquet files.")

            state = _read_state()
            _write_state(
                last_compaction_at=time.time(),
                files_compacted_total=state.get("files_compacted_total", 0) + len(files_to_compact),
                compactions_run=state.get("compactions_run", 0) + 1,
                last_upload_ok=uploaded,
                last_error=None,
            )
        except Exception as e:
            logging.error(f"Parquet compaction failed: {e}")
            _write_state(last_error=str(e))


def run_watchdog(heartbeat_interval: float = 5.0, max_seconds: float = None, size_threshold_mb: float = 100.0):
    os.makedirs(DATA_DIR, exist_ok=True)
    handler = ParquetCompactionHandler(target_dir=DATA_DIR, size_threshold_mb=size_threshold_mb)
    observer = Observer()
    observer.schedule(handler, path=DATA_DIR, recursive=True)
    observer.start()
    _write_state(running=True, pid=os.getpid())
    logging.info(f"Parquet Compaction Watchdog running on {DATA_DIR} (pid={os.getpid()}, threshold={size_threshold_mb}MB)...")

    stop = {"flag": False}

    def _handle_signal(signum, frame):
        logging.info(f"Received signal {signum}, shutting down watchdog...")
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    start = time.time()
    try:
        while not stop["flag"]:
            _write_state(running=True)
            time.sleep(heartbeat_interval)
            if max_seconds is not None and (time.time() - start) > max_seconds:
                logging.info("max_seconds reached, stopping (test mode).")
                break
    finally:
        observer.stop()
        observer.join()
        _write_state(running=False)
        logging.info("Watchdog stopped cleanly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time Parquet compaction + Drive-union upload daemon.")
    parser.add_argument("--max-seconds", type=float, default=None,
                         help="Exit after N seconds (testing only). Omit to run forever as a real daemon.")
    parser.add_argument("--size-threshold-mb", type=float, default=100.0,
                         help="Pending-batch size that triggers compaction (default 100MB, matches the design doc's 100-500MB target).")
    args = parser.parse_args()
    run_watchdog(max_seconds=args.max_seconds, size_threshold_mb=args.size_threshold_mb)
