import asyncio
import logging
import os
import time

import duckdb
from fastmcp import FastMCP

from backend.duckdb_engine import mount_parquet_views, is_select_only, DATA_DIR
from backend.mcp_logs import log_invocation
from backend.quant_ai_bridge import MultiKeyGeminiPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

mcp = FastMCP("ChainQuant-Engine")

global_con = duckdb.connect(':memory:')
gemini_pool = MultiKeyGeminiPool()

mount_parquet_views(global_con, DATA_DIR)


def _log(action: str, status: str, start_time: float, detail: str = "", client: str = "MCP"):
    log_invocation(client=client, action=action, status=status, duration_ms=(time.time() - start_time) * 1000, detail=detail)


@mcp.tool()
def execute_quant_sql(sql_query: str) -> str:
    """
    Executes read-only SQL queries against Parquet datasets ingested via Hypersync,
    using DuckDB. Only SELECT statements are permitted.
    """
    start = time.time()
    if not is_select_only(sql_query):
        _log("execute_quant_sql", "403 blocked", start, detail="non-SELECT rejected")
        return "SECURITY INTERCEPT: Only SELECT (read-only) SQL statements are permitted."

    try:
        mount_parquet_views(global_con, DATA_DIR)
        df = global_con.execute(sql_query).df()
        _log("execute_quant_sql", "200 OK", start, detail=sql_query[:120])
        return df.to_json(orient="records")
    except Exception as e:
        _log("execute_quant_sql", "500 error", start, detail=str(e))
        return f"DuckDB Error: {str(e)}"


@mcp.tool()
def list_tables() -> str:
    """
    Lists all available tables and views currently mounted in DuckDB.
    """
    start = time.time()
    try:
        mount_parquet_views(global_con, DATA_DIR)
        df = global_con.execute("SHOW TABLES").df()
        _log("list_tables", "200 OK", start)
        return df.to_json(orient="records")
    except Exception as e:
        _log("list_tables", "500 error", start, detail=str(e))
        return f"DuckDB Error: {str(e)}"


@mcp.tool()
def run_blockchain_backtest(raw_sql: str) -> str:
    """
    Runs a read-only SQL backtest query in DuckDB (validated via sqlglot), then asks
    the configured Gemini key pool for a risk/tuning diagnosis of the result. Requires
    GEMINI_API_KEY / GEMINI_API_KEYS to be set for the AI diagnosis step; the SQL
    result is still returned even if no Gemini key is configured.
    """
    start = time.time()
    if not is_select_only(raw_sql):
        _log("run_blockchain_backtest", "403 blocked", start, detail="non-SELECT rejected")
        return "ERROR: security intercept -- only read-only SELECT statements are permitted."

    try:
        mount_parquet_views(global_con, DATA_DIR)
        df = global_con.execute(raw_sql).df()
    except Exception as e:
        _log("run_blockchain_backtest", "500 error", start, detail=str(e))
        return f"ERROR: DuckDB query failed: {e}"

    if not gemini_pool.api_keys:
        _log("run_blockchain_backtest", "200 OK (no AI)", start, detail=f"{len(df)} rows, no Gemini key configured")
        return f"Backtest OK: {len(df)} rows returned. AI diagnosis skipped (no GEMINI_API_KEY configured)."

    prompt = f"DuckDB backtest result summary: {df.tail(5).to_json()}\nGive a risk diagnosis and parameter-tuning suggestion."
    diagnosis = gemini_pool.generate_content_with_retry(prompt)
    _log("run_blockchain_backtest", "200 OK", start, detail=f"{len(df)} rows, AI diagnosis={'ok' if diagnosis else 'failed'}")
    if diagnosis is None:
        return f"Backtest OK: {len(df)} rows returned. AI diagnosis failed (Gemini keys exhausted or errored)."
    return f"Backtest OK: {len(df)} rows returned.\nAI diagnosis: {diagnosis[:500]}"


@mcp.tool()
def trigger_hypersync_ingestion(from_block: int = 18000000, to_block: int = 18000100, chain: str = "ethereum") -> str:
    """
    Triggers a real Hypersync extraction for [from_block, to_block] on `chain`,
    streaming blocks/transactions/logs straight to Parquet. Requires
    HYPERSYNC_BEARER_TOKEN (free tier: https://app.envio.dev/api-tokens).
    """
    start = time.time()
    try:
        from backend.hypersync_ingestion import extract_chain_data, HypersyncTokenMissing
        result = asyncio.run(extract_chain_data(from_block, to_block, chain))
        mount_parquet_views(global_con, DATA_DIR)
        _log("trigger_hypersync_ingestion", "200 OK", start, detail=f"{chain} {from_block}-{to_block}")
        return f"Hypersync extraction complete. Files: {result.files}"
    except Exception as e:
        _log("trigger_hypersync_ingestion", "500 error", start, detail=str(e))
        return f"Hypersync trigger error: {e}"


if __name__ == "__main__":
    print("Testing ChainQuant-Engine FastMCP server startup...")
    test_result = execute_quant_sql("SELECT 1 AS status, 'FastMCP Engine Ready' AS message")
    print(f"FastMCP SQL Execution Test Result:\n{test_result}")
    print("FastMCP Server ready to serve Claude Desktop & OpenWebUI via HTTPS/STDIO!")
    mcp.run()
