import os

import duckdb
import polars as pl
import pytest

from backend.duckdb_engine import find_parquet_files, mount_parquet_views, is_select_only


@pytest.fixture
def data_dir(tmp_path):
    """Mirrors the real on-disk layout: a dataset root, then key=value
    partition dirs, then parquet files."""
    market = tmp_path / "market" / "symbol=BTCUSDT" / "interval=1m"
    market.mkdir(parents=True)
    pl.DataFrame({"close": [1.0, 2.0], "symbol": ["BTCUSDT"] * 2}).write_parquet(market / "a.parquet")
    pl.DataFrame({"close": [3.0], "symbol": ["BTCUSDT"]}).write_parquet(market / "b.parquet")

    chain = tmp_path / "hypersync_output" / "symbol=ETHEREUM"
    chain.mkdir(parents=True)
    pl.DataFrame({"block_number": [1, 2]}).write_parquet(chain / "c.parquet")
    return str(tmp_path)


def test_find_parquet_files_is_recursive(data_dir):
    found = find_parquet_files(data_dir)
    assert len(found) == 3, "must descend into nested partition dirs, not just the top level"


def test_market_and_chain_get_distinct_view_names(data_dir):
    con = duckdb.connect(":memory:")
    views = mount_parquet_views(con, data_dir)
    assert "market_btcusdt_1m" in views
    assert "chain_ethereum" in views


def test_files_in_same_partition_merge_into_one_view(data_dir):
    con = duckdb.connect(":memory:")
    mount_parquet_views(con, data_dir)
    # a.parquet has 2 rows, b.parquet has 1; the view must expose all 3.
    assert con.execute("SELECT count(*) FROM market_btcusdt_1m").fetchone()[0] == 3


def test_mounting_is_idempotent(data_dir):
    con = duckdb.connect(":memory:")
    mount_parquet_views(con, data_dir)
    mount_parquet_views(con, data_dir)
    assert con.execute("SELECT count(*) FROM market_btcusdt_1m").fetchone()[0] == 3


def test_empty_dir_mounts_nothing(tmp_path):
    con = duckdb.connect(":memory:")
    assert mount_parquet_views(con, str(tmp_path)) == []


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from t where x = 1",
    "SELECT a, b FROM t GROUP BY 1",
    "WITH x AS (SELECT 1) SELECT * FROM x",
])
def test_select_statements_allowed(sql):
    assert is_select_only(sql) is True


@pytest.mark.parametrize("sql", [
    "DROP TABLE t",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "ATTACH 'x.db'",
    "COPY t TO 'out.parquet'",
    "CREATE TABLE t (a INT)",
    "INSTALL httpfs",
    "not sql at all ((",
])
def test_non_select_statements_blocked(sql):
    assert is_select_only(sql) is False
