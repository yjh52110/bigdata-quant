import argparse
import asyncio
import logging
import os
import time

import duckdb
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from backend.duckdb_engine import mount_parquet_views, is_select_only, DATA_DIR
from backend.mcp_logs import log_invocation
from backend.mcp_users import authorize
from backend.quant_ai_bridge import MultiKeyGeminiPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

mcp = FastMCP("ChainQuant-Engine")

# One shared catalog holding the mounted views. Every tool call runs on its own
# cursor derived from this, so a slow query from one user can't serialize behind
# or corrupt another's -- the previous single shared connection made all users
# contend on one execution context.
catalog = duckdb.connect(":memory:")

# Caps applied per connection so a single `SELECT *` can't consume the whole box.
QUERY_MEMORY_LIMIT = os.environ.get("MCP_QUERY_MEMORY_LIMIT", "1GB")
QUERY_TIMEOUT_S = float(os.environ.get("MCP_QUERY_TIMEOUT_S", "30"))
MAX_RESULT_ROWS = int(os.environ.get("MCP_MAX_RESULT_ROWS", "5000"))

gemini_pool = MultiKeyGeminiPool()
mount_parquet_views(catalog, DATA_DIR)


class ToolError(Exception):
    """Returned to the caller as a plain message rather than a stack trace."""


def _caller():
    """Resolves the calling user from the HTTP request, consuming one unit of
    their quota. Raises ToolError when unauthenticated or over limit."""
    headers = get_http_headers()
    api_key = headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()
    user, err = authorize(api_key or None)
    if err:
        raise ToolError(err)
    return user


def _run_sql(sql: str):
    """Executes read-only SQL on an isolated cursor with memory and time caps."""
    cur = catalog.cursor()
    cur.execute(f"SET memory_limit='{QUERY_MEMORY_LIMIT}'")
    try:
        return cur.execute(sql).fetchdf().head(MAX_RESULT_ROWS)
    finally:
        cur.close()


async def _run_sql_guarded(sql: str):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_run_sql, sql), timeout=QUERY_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise ToolError(f"Query exceeded the {QUERY_TIMEOUT_S:g}s limit and was cancelled.")


def _log(action: str, status: str, start: float, user_id: str, detail: str = ""):
    log_invocation(client=user_id, action=action, status=status,
                   duration_ms=(time.time() - start) * 1000, detail=detail)


@mcp.tool()
async def execute_quant_sql(sql_query: str) -> str:
    """Run a read-only SQL query against the ingested market and on-chain
    Parquet datasets using DuckDB. Only SELECT statements are permitted."""
    start = time.time()
    try:
        user = _caller()
    except ToolError as e:
        _log("execute_quant_sql", "401 denied", start, "anonymous", str(e))
        return f"ACCESS DENIED: {e}"

    if not is_select_only(sql_query):
        _log("execute_quant_sql", "403 blocked", start, user.user_id, "non-SELECT rejected")
        return "SECURITY INTERCEPT: Only SELECT (read-only) SQL statements are permitted."

    try:
        mount_parquet_views(catalog, DATA_DIR)
        df = await _run_sql_guarded(sql_query)
    except ToolError as e:
        _log("execute_quant_sql", "408 timeout", start, user.user_id, str(e))
        return str(e)
    except Exception as e:
        _log("execute_quant_sql", "500 error", start, user.user_id, str(e))
        return f"DuckDB Error: {e}"

    _log("execute_quant_sql", "200 OK", start, user.user_id, sql_query[:120])
    return df.to_json(orient="records")


@mcp.tool()
async def list_tables() -> str:
    """List the tables and views currently available to query."""
    start = time.time()
    try:
        user = _caller()
    except ToolError as e:
        _log("list_tables", "401 denied", start, "anonymous", str(e))
        return f"ACCESS DENIED: {e}"
    try:
        mount_parquet_views(catalog, DATA_DIR)
        df = await _run_sql_guarded("SHOW TABLES")
    except Exception as e:
        _log("list_tables", "500 error", start, user.user_id, str(e))
        return f"DuckDB Error: {e}"
    _log("list_tables", "200 OK", start, user.user_id)
    return df.to_json(orient="records")


@mcp.tool()
async def run_blockchain_backtest(raw_sql: str) -> str:
    """Run a read-only SQL backtest query, then ask Gemini for a risk and
    parameter-tuning diagnosis of the result. Requires GEMINI_API_KEY(S) for
    the AI step; the query result is returned either way."""
    start = time.time()
    try:
        user = _caller()
    except ToolError as e:
        _log("run_blockchain_backtest", "401 denied", start, "anonymous", str(e))
        return f"ACCESS DENIED: {e}"

    if not is_select_only(raw_sql):
        _log("run_blockchain_backtest", "403 blocked", start, user.user_id, "non-SELECT rejected")
        return "ERROR: security intercept -- only read-only SELECT statements are permitted."

    try:
        mount_parquet_views(catalog, DATA_DIR)
        df = await _run_sql_guarded(raw_sql)
    except ToolError as e:
        _log("run_blockchain_backtest", "408 timeout", start, user.user_id, str(e))
        return str(e)
    except Exception as e:
        _log("run_blockchain_backtest", "500 error", start, user.user_id, str(e))
        return f"ERROR: DuckDB query failed: {e}"

    if not gemini_pool.api_keys:
        _log("run_blockchain_backtest", "200 OK (no AI)", start, user.user_id, f"{len(df)} rows")
        return f"Backtest OK: {len(df)} rows returned. AI diagnosis skipped (no GEMINI_API_KEY configured)."

    prompt = f"DuckDB backtest result summary: {df.tail(5).to_json()}\nGive a risk diagnosis and parameter-tuning suggestion."
    diagnosis = await asyncio.to_thread(gemini_pool.generate_content_with_retry, prompt)
    _log("run_blockchain_backtest", "200 OK", start, user.user_id, f"{len(df)} rows, AI={'ok' if diagnosis else 'failed'}")
    if diagnosis is None:
        return f"Backtest OK: {len(df)} rows returned. AI diagnosis failed (Gemini keys exhausted or errored)."
    return f"Backtest OK: {len(df)} rows returned.\nAI diagnosis: {diagnosis[:500]}"


@mcp.tool()
async def ingest_market_data(symbol: str = "BTCUSDT", interval: str = "1m", months: int = 1) -> str:
    """Download free historical klines from data.binance.vision into the
    Parquet store so they become queryable. No API key required."""
    start = time.time()
    try:
        user = _caller()
    except ToolError as e:
        _log("ingest_market_data", "401 denied", start, "anonymous", str(e))
        return f"ACCESS DENIED: {e}"
    try:
        from backend.binance_ingestion import ingest_binance_klines
        result = await asyncio.to_thread(ingest_binance_klines, symbol, interval, months)
        mount_parquet_views(catalog, DATA_DIR)
    except Exception as e:
        _log("ingest_market_data", "500 error", start, user.user_id, str(e))
        return f"Ingestion error: {e}"
    _log("ingest_market_data", "200 OK", start, user.user_id, f"{symbol} {interval} x{months}")
    return (f"Ingested {result['total_rows']} rows for {result['symbol']} {result['interval']}: "
            f"{len(result['months_written'])} new month(s), {len(result['months_skipped'])} already present.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChainQuant MCP server")
    parser.add_argument("--transport", default="http", choices=["http", "stdio", "sse", "streamable-http"],
                        help="http (default) serves remote users; stdio is local-only single-user")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.transport == "stdio":
        logging.warning("stdio transport is single-user and local-only; remote users cannot connect.")
        mcp.run(transport="stdio")
    else:
        logging.info(f"Serving MCP over {args.transport} on {args.host}:{args.port}")
        mcp.run(transport=args.transport, host=args.host, port=args.port)
