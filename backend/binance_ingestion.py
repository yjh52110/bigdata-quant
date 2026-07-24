"""Bulk market-data ingestion from data.binance.vision.

Binance publishes its full historical klines as free monthly ZIP archives --
no API key, no rate limit, no egress cost. For bar-level backtesting this is
both the cheapest and the highest-quality source available, so it is the
default way to get real, queryable data into this platform.
"""

import io
import logging
import os
import zipfile
from datetime import date
from typing import List, Optional

import polars as pl
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKET_DIR = os.path.join(BASE_DIR, "data", "market")
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

# Binance kline CSVs ship with no header row; this is the documented column order.
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def _recent_months(n: int) -> List[str]:
    """Last n complete months, oldest first. The current month is skipped
    because Binance only publishes a monthly archive once the month closes."""
    today = date.today()
    year, month = today.year, today.month
    months = []
    for _ in range(n):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        months.append(f"{year:04d}-{month:02d}")
    return list(reversed(months))


def _normalize_epoch(col: str) -> pl.Expr:
    """Binance switched kline timestamps from milliseconds to microseconds in
    newer archives, so the same symbol can yield either width depending on the
    month requested. Detect per-row by magnitude instead of assuming one."""
    c = pl.col(col).cast(pl.Int64)
    micros = pl.when(c > 10_000_000_000_000).then(c).otherwise(c * 1000)
    return micros.cast(pl.Datetime(time_unit="us")).alias(col)


def ingest_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    months: int = 1,
    out_dir: Optional[str] = None,
) -> dict:
    """Downloads monthly kline archives and writes one Parquet file per month.

    Idempotent: months already present on disk are skipped, so this can be
    re-run to top up without re-downloading history.
    """
    symbol = symbol.upper()
    out_dir = out_dir or os.path.join(MARKET_DIR, f"symbol={symbol}", f"interval={interval}")
    os.makedirs(out_dir, exist_ok=True)

    written, skipped, failed, total_rows = [], [], [], 0

    for ym in _recent_months(months):
        target = os.path.join(out_dir, f"{symbol}-{interval}-{ym}.parquet")
        if os.path.exists(target):
            skipped.append(ym)
            continue

        url = f"{BASE_URL}/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip"
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 404:
                failed.append({"month": ym, "error": "not published (404)"})
                logging.warning(f"{symbol} {interval} {ym}: not available")
                continue
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                name = zf.namelist()[0]
                raw = zf.read(name)

            df = pl.read_csv(io.BytesIO(raw), has_header=False, new_columns=COLUMNS)
            df = df.drop("ignore").with_columns([
                _normalize_epoch("open_time"),
                _normalize_epoch("close_time"),
                pl.lit(symbol).alias("symbol"),
                pl.lit(interval).alias("interval"),
            ]).sort("open_time")  # sorted so DuckDB can skip row groups on time-range scans

            df.write_parquet(target, compression="zstd")
            total_rows += df.height
            written.append({"month": ym, "rows": df.height, "path": target})
            logging.info(f"{symbol} {interval} {ym}: wrote {df.height} rows -> {target}")

        except Exception as e:
            failed.append({"month": ym, "error": str(e)})
            logging.error(f"{symbol} {interval} {ym}: {e}")

    return {
        "symbol": symbol,
        "interval": interval,
        "months_written": written,
        "months_skipped": skipped,
        "months_failed": failed,
        "total_rows": total_rows,
        "output_dir": out_dir,
    }


if __name__ == "__main__":
    import json
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    iv = sys.argv[2] if len(sys.argv) > 2 else "1m"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    print(json.dumps(ingest_binance_klines(sym, iv, n), indent=2, default=str))
