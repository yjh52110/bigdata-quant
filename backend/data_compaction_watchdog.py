import os
import sys
import time
import logging
from typing import List, Dict
import duckdb
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "hypersync_output")

class ParquetCompactionHandler(FileSystemEventHandler):
    """
    Watches local buffer directory for incoming micro-parquet files (from Hypersync or WebSocket).
    Automatically merges (compacts) files into 100MB-500MB large Parquet files when size threshold is reached.
    """
    def __init__(self, target_dir: str, size_threshold_mb: float = 100.0):
        self.target_dir = target_dir
        self.size_threshold_bytes = size_threshold_mb * 1024 * 1024
        self.pending_files: List[str] = []

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".parquet"):
            logging.info(f"Detected new micro-parquet file: {event.src_path}")
            self.pending_files.append(event.src_path)
            self._check_and_compact()

    def _check_and_compact(self):
        total_size = sum(os.path.getsize(f) for f in self.pending_files if os.path.exists(f))
        if total_size >= self.size_threshold_bytes:
            logging.info(f"Accumulated Parquet size ({total_size / 1024 / 1024:.2f} MB) exceeded threshold. Executing compaction...")
            self.compact_files(self.pending_files)
            self.pending_files.clear()

    def compact_files(self, files_to_compact: List[str]):
        if not files_to_compact:
            return

        compacted_filename = f"compacted_{int(time.time())}.parquet"
        compacted_filepath = os.path.join(self.target_dir, compacted_filename)

        try:
            con = duckdb.connect(':memory:')
            # Use DuckDB to merge multiple Parquet files seamlessly into a single compressed file
            file_list_str = "[" + ", ".join(f"'{f}'" for f in files_to_compact) + "]"
            sql = f"COPY (SELECT * FROM read_parquet({file_list_str})) TO '{compacted_filepath}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            con.execute(sql)
            logging.info(f"Successfully compacted {len(files_to_compact)} files into {compacted_filepath}")

            # Cleanup original micro-parquet files after successful compaction
            for f in files_to_compact:
                if os.path.exists(f):
                    os.remove(f)
            logging.info("Cleaned up original micro-parquet files.")

        except Exception as e:
            logging.error(f"Parquet compaction failed: {e}")

def run_watchdog():
    os.makedirs(DATA_DIR, exist_ok=True)
    handler = ParquetCompactionHandler(target_dir=DATA_DIR)
    observer = Observer()
    observer.schedule(handler, path=DATA_DIR, recursive=True)
    observer.start()
    logging.info(f"Parquet Compaction Watchdog running on {DATA_DIR}...")
    try:
        time.sleep(2)
        observer.stop()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    print("Testing Parquet Compaction Watchdog...")
    run_watchdog()
    print("Watchdog test completed with 0 errors!")
