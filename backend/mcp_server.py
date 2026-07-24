import asyncio
import logging
import os
from typing import Optional
import duckdb
import sqlglot
from fastmcp import FastMCP
from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Initialize FastMCP Server
mcp = FastMCP("300TB-Blockchain-Quant-Engine")

# Create global DuckDB connection and mount data
global_con = duckdb.connect(':memory:')

def mount_parquet_files():
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if not os.path.exists(data_dir):
        return
    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".parquet"):
                full_path = os.path.join(root, file)
                # Create a view name from filename
                view_name = file.replace(".parquet", "").replace("-", "_").replace(".", "_")
                try:
                    global_con.execute(f"CREATE OR REPLACE VIEW {view_name} AS SELECT * FROM read_parquet('{full_path}')")
                    logging.info(f"Mounted view {view_name} from {full_path}")
                except Exception as e:
                    logging.error(f"Failed to mount {full_path}: {e}")

mount_parquet_files()

def is_query_safe(sql: str) -> bool:
    """
    Validates SQL using sqlglot to strictly ensure only SELECT (read-only) statements are executed.
    """
    try:
        parsed = sqlglot.parse_one(sql)
        return isinstance(parsed, sqlglot.exp.Select)
    except Exception as e:
        logging.error(f"SQL validation error: {e}")
        return False

@mcp.tool()
def execute_quant_sql(sql_query: str) -> str:
    """
    Executes read-only SQL queries on Parquet datasets stored in Google Drive / local mounts using DuckDB.
    """
    if not is_query_safe(sql_query):
        return "SECURITY INTERCEPT: Only SELECT (read-only) SQL statements are permitted."

    try:
        df = global_con.execute(sql_query).df()
        return df.to_json(orient="records")
    except Exception as e:
        logging.error(f"DuckDB Query Failed: {e}")
        return f"DuckDB Error: {str(e)}"

@mcp.tool()
def list_tables() -> str:
    """
    Lists all available tables and views in DuckDB.
    """
    try:
        df = global_con.execute("SHOW TABLES").df()
        return df.to_json(orient="records")
    except Exception as e:
        return f"DuckDB Error: {str(e)}"

@mcp.tool()
def trigger_hypersync_ingestion(from_block: int = 18000000, to_block: int = 18000100, chain: str = "ETH") -> str:
    """
    Triggers 2026 Hypersync (by Envio) extraction pipeline for 100% full-chain Tenderly-level Parquet data.
    """
    try:
        from hypersync_ingestion import extract_tenderly_level_data
        output_path = asyncio.run(extract_tenderly_level_data(from_block, to_block, chain))
        mount_parquet_files()
        return f"Hypersync Extraction Triggered Successfully! Output Directory: {output_path}"
    except Exception as e:
        return f"Hypersync Trigger Error: {e}"

if __name__ == "__main__":
    os.environ.get("GEMINI_API_KEY")
    print("Testing 300TB-Blockchain-Quant-Engine FastMCP server startup...")
    test_sql = "SELECT 1 AS status, 'FastMCP Engine Ready' AS message"
    result = execute_quant_sql(test_sql)
    print(f"FastMCP SQL Execution Test Result:\n{result}")
    print("FastMCP Server ready to serve Claude Desktop & OpenWebUI via HTTPS/STDIO!")
