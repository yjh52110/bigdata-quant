import io
import zipfile
from datetime import date, datetime, timezone

import polars as pl
import pytest

from backend import binance_ingestion as bi


def _fake_zip(rows) -> bytes:
    csv = "\n".join(",".join(str(c) for c in r) for r in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BTCUSDT-1m-2025-01.csv", csv)
    return buf.getvalue()


class _Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_recent_months_excludes_current_month():
    months = bi._recent_months(3)
    assert len(months) == 3
    assert f"{date.today():%Y-%m}" not in months, "current month has no published archive yet"
    assert months == sorted(months), "must be returned oldest-first"


def test_microsecond_timestamps_are_parsed_correctly():
    df = pl.DataFrame({"open_time": [1735689600000000]}).with_columns(bi._normalize_epoch("open_time"))
    assert df["open_time"][0] == datetime(2025, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def test_millisecond_timestamps_are_parsed_correctly():
    """Older archives use ms; both widths must land on the same instant."""
    df = pl.DataFrame({"open_time": [1735689600000]}).with_columns(bi._normalize_epoch("open_time"))
    assert df["open_time"][0] == datetime(2025, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def test_ingest_writes_parquet_and_reports_rows(tmp_path, monkeypatch):
    rows = [[1735689600000000 + i * 60_000_000, 1, 2, 0.5, 1.5, 10,
             1735689659999999 + i * 60_000_000, 100, 5, 4, 40, 0] for i in range(3)]
    monkeypatch.setattr(bi.requests, "get", lambda *a, **k: _Resp(_fake_zip(rows)))

    out = bi.ingest_binance_klines("BTCUSDT", "1m", months=1, out_dir=str(tmp_path))
    assert out["total_rows"] == 3
    assert len(out["months_written"]) == 1

    written = list(tmp_path.glob("*.parquet"))
    assert len(written) == 1
    df = pl.read_parquet(written[0])
    assert "ignore" not in df.columns, "the unused trailing column should be dropped"
    assert df["symbol"][0] == "BTCUSDT"
    assert df["open_time"].is_sorted(), "must be time-sorted so DuckDB can skip row groups"


def test_existing_months_are_skipped(tmp_path, monkeypatch):
    rows = [[1735689600000000, 1, 2, 0.5, 1.5, 10, 1735689659999999, 100, 5, 4, 40, 0]]
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _Resp(_fake_zip(rows))

    monkeypatch.setattr(bi.requests, "get", fake_get)

    bi.ingest_binance_klines("BTCUSDT", "1m", months=1, out_dir=str(tmp_path))
    second = bi.ingest_binance_klines("BTCUSDT", "1m", months=1, out_dir=str(tmp_path))

    assert calls["n"] == 1, "re-running must not re-download an already-present month"
    assert len(second["months_skipped"]) == 1
    assert second["total_rows"] == 0


def test_missing_month_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(bi.requests, "get", lambda *a, **k: _Resp(b"", status=404))
    out = bi.ingest_binance_klines("NOPEUSDT", "1m", months=1, out_dir=str(tmp_path))
    assert out["total_rows"] == 0
    assert len(out["months_failed"]) == 1
    assert "404" in out["months_failed"][0]["error"]
