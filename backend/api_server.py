import os
import glob
import psutil
import duckdb
import sqlglot
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any

from backend.google_account_manager import GoogleAccountManager

app = FastAPI(title="OpenBrowser Admin API")

# Allow CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Initialize DuckDB connection (in-memory for querying parquet)
conn = duckdb.connect(':memory:')

account_manager = GoogleAccountManager()

@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/api/overview")
def get_overview():
    # Read actual stats where possible, mock others
    account_status = account_manager.get_account_pool_status()
    parquet_files = glob.glob(os.path.join(DATA_DIR, "*.parquet"))
    total_data_size = sum(os.path.getsize(f) for f in parquet_files) if parquet_files else 0

    return {
        "activeAccounts": account_status.get("active_accounts", 0),
        "totalDataSize": f"{total_data_size / (1024**3):.2f} GB", # Convert to GB
        "apiLatency": "45ms", # mocked
        "geminiStatus": "Healthy", # mocked
        "syncStatus": "Active" # mocked
    }

@app.get("/api/accounts")
def get_accounts():
    quotas = account_manager.get_all_quotas()
    status = account_manager.get_account_pool_status()
    return {
        "poolStatus": status,
        "accounts": quotas
    }

@app.get("/api/data-assets")
def get_data_assets():
    parquet_files = glob.glob(os.path.join(DATA_DIR, "*.parquet"))
    assets = []
    total_size = 0
    for file in parquet_files:
        size = os.path.getsize(file)
        total_size += size
        assets.append({
            "filename": os.path.basename(file),
            "size": size,
            "size_str": f"{size / (1024**2):.2f} MB"
        })
    return {
        "assets": assets,
        "total_files": len(assets),
        "total_size": total_size
    }

@app.get("/api/duckdb/tables")
def get_duckdb_tables():
    parquet_files = glob.glob(os.path.join(DATA_DIR, "*.parquet"))
    tables = [os.path.basename(f).replace('.parquet', '') for f in parquet_files]
    return {"tables": tables, "files": parquet_files}

class QueryRequest(BaseModel):
    query: str

@app.post("/api/duckdb/query")
def execute_query(request: QueryRequest):
    try:
        # Validate with sqlglot (can catch parsing errors before passing to duckdb)
        parsed = sqlglot.parse_one(request.query)
        # Execute query
        result = conn.execute(request.query).df()
        # Convert nan to None to avoid json serialization errors
        result = result.replace({float('nan'): None})
        return {"columns": list(result.columns), "data": result.to_dict(orient="records")}
    except sqlglot.errors.ParseError as e:
        raise HTTPException(status_code=400, detail=f"SQL Parse Error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/gemini/status")
def get_gemini_status():
    # Mock data for gemini pool
    return {
        "status": "Healthy",
        "activeKeys": 5,
        "exhaustedKeys": 0,
        "requestsToday": 12050
    }

@app.get("/api/infrastructure")
def get_infrastructure():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        "cpu": {
            "percent": cpu_percent
        },
        "memory": {
            "total": memory.total,
            "used": memory.used,
            "percent": memory.percent
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "percent": disk.percent
        }
    }
