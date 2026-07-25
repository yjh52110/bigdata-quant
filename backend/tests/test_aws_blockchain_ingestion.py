import os

import pytest

from backend import aws_blockchain_ingestion as aws


def _listing(entries):
    """Minimal S3 ListObjectsV2 XML with the fields the parser reads."""
    body = "".join(f"<Contents><Key>{k}</Key><Size>{s}</Size></Contents>" for k, s in entries)
    return f"<?xml version='1.0'?><ListBucketResult>{body}</ListBucketResult>"


class _Resp:
    def __init__(self, text="", status=200, content=b""):
        self.text, self.status_code, self.content = text, status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=None):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_daterange_inclusive():
    assert aws._daterange("2026-07-01", "2026-07-03") == ["2026-07-01", "2026-07-02", "2026-07-03"]


def test_daterange_single_day():
    assert aws._daterange("2026-07-01", "2026-07-01") == ["2026-07-01"]


def test_daterange_rejects_reversed():
    with pytest.raises(ValueError):
        aws._daterange("2026-07-05", "2026-07-01")


def test_unknown_chain_and_table_rejected():
    with pytest.raises(ValueError):
        aws.preview("dogecoin", "blocks", "2026-07-01", "2026-07-01")
    with pytest.raises(ValueError):
        aws.preview("btc", "traces", "2026-07-01", "2026-07-01")  # btc has no traces table


def test_preview_sums_bytes_and_reports_missing(monkeypatch):
    def fake_get(url, **kw):
        if "2026-07-02" in url:
            return _Resp(_listing([]))  # that day not published
        return _Resp(_listing([("v1.0/eth/blocks/date=x/part-0.parquet", 1000)]))

    monkeypatch.setattr(aws.requests, "get", fake_get)
    p = aws.preview("eth", "blocks", "2026-07-01", "2026-07-03")
    assert p["total_bytes"] == 2000
    assert p["missing_days"] == ["2026-07-02"]
    assert len(p["days"]) == 2


def test_budget_is_enforced_before_downloading(monkeypatch, tmp_path):
    calls = {"downloads": 0}

    def fake_get(url, **kw):
        if "list-type=2" in url:
            return _Resp(_listing([("k.parquet", 5 * 1024 ** 3)]))  # 5 GB
        calls["downloads"] += 1
        return _Resp(content=b"x")

    monkeypatch.setattr(aws.requests, "get", fake_get)
    with pytest.raises(aws.BudgetExceeded):
        aws.ingest_aws_blockchain("eth", "traces", "2026-07-01", "2026-07-01",
                                  max_gb=1.0, out_dir=str(tmp_path))
    assert calls["downloads"] == 0, "must refuse before transferring any bytes"


def test_ingest_writes_file_and_reports(monkeypatch, tmp_path):
    def fake_get(url, **kw):
        if "list-type=2" in url:
            return _Resp(_listing([("v1.0/eth/blocks/date=d/part-0.parquet", 10)]))
        return _Resp(content=b"parquet-bytes")

    monkeypatch.setattr(aws.requests, "get", fake_get)
    out = aws.ingest_aws_blockchain("eth", "blocks", "2026-07-01", "2026-07-01",
                                    max_gb=1.0, out_dir=str(tmp_path))
    assert len(out["days_written"]) == 1
    assert not out["days_failed"]
    files = list(tmp_path.glob("*.parquet"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"parquet-bytes"


def test_existing_days_are_skipped(monkeypatch, tmp_path):
    downloads = {"n": 0}

    def fake_get(url, **kw):
        if "list-type=2" in url:
            return _Resp(_listing([("k.parquet", 10)]))
        downloads["n"] += 1
        return _Resp(content=b"x")

    monkeypatch.setattr(aws.requests, "get", fake_get)
    aws.ingest_aws_blockchain("eth", "blocks", "2026-07-01", "2026-07-01", out_dir=str(tmp_path))
    second = aws.ingest_aws_blockchain("eth", "blocks", "2026-07-01", "2026-07-01", out_dir=str(tmp_path))
    assert downloads["n"] == 1, "an already-present day must not be re-downloaded"
    assert second["days_skipped"] == ["2026-07-01"]


def test_partial_download_leaves_no_file(monkeypatch, tmp_path):
    """An interrupted transfer must not leave something a later run mistakes
    for a complete day."""
    def fake_get(url, **kw):
        if "list-type=2" in url:
            return _Resp(_listing([("k.parquet", 10)]))
        raise ConnectionError("network died mid-transfer")

    monkeypatch.setattr(aws.requests, "get", fake_get)
    out = aws.ingest_aws_blockchain("eth", "blocks", "2026-07-01", "2026-07-01", out_dir=str(tmp_path))
    assert len(out["days_failed"]) == 1
    assert list(tmp_path.glob("*.parquet")) == []
    assert list(tmp_path.glob("*.part")) == []
