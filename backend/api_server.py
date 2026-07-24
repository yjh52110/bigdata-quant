import os
import time
import logging
from collections import deque

import duckdb
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.google_account_manager import GoogleAccountManager
from backend.quant_ai_bridge import MultiKeyGeminiPool
from backend.duckdb_engine import find_parquet_files, mount_parquet_views, is_select_only, DATA_DIR
from backend.sync_status import get_sync_status
from backend.alerting import list_alert_rules, send_test_alert, telegram_configured
from backend.mcp_logs import read_recent_logs
from backend.transfer_log import get_today_totals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="ChainQuantPlatform Admin API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

QUANT_API_KEY = os.environ.get("QUANT_API_KEY")


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    """Mirrors the OPENBROWSER_API_KEY convention: if QUANT_API_KEY is set, every
    /api/* request must present a matching X-API-Key header. If unset, the server
    stays open for local dev -- but this is logged loudly so it's never silently
    left open in a real deployment."""
    if QUANT_API_KEY and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        if request.headers.get("x-api-key") != QUANT_API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-API-Key header"})
    return await call_next(request)


# Real request-latency telemetry, replacing the dashboard's old Math.random()
# 24h QPS chart. Only keeps a short in-memory rolling window (not persisted).
REQUEST_LOG_MAXLEN = 200
request_log = deque(maxlen=REQUEST_LOG_MAXLEN)
requests_total = 0


@app.middleware("http")
async def traffic_tracker(request: Request, call_next):
    global requests_total
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    requests_total += 1
    request_log.append({"t": time.time(), "path": request.url.path, "latency_ms": round(duration_ms, 1)})
    return response


os.makedirs(DATA_DIR, exist_ok=True)

# Long-lived in-memory DuckDB connection; views are (re)mounted from whatever
# parquet files exist on disk before every query so newly-ingested data shows
# up without restarting the server.
conn = duckdb.connect(':memory:')

account_manager = GoogleAccountManager()
gemini_pool = MultiKeyGeminiPool()

if not QUANT_API_KEY:
    logging.warning(
        "QUANT_API_KEY is not set -- the admin API is running with NO authentication. "
        "Set QUANT_API_KEY before exposing this beyond localhost."
    )


@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0", "auth_enabled": bool(QUANT_API_KEY)}


@app.get("/api/overview")
def get_overview():
    account_status = account_manager.get_account_pool_status()
    parquet_files = find_parquet_files()
    total_data_size = sum(os.path.getsize(f) for f in parquet_files) if parquet_files else 0
    gemini_status = gemini_pool.get_status()
    recent = list(request_log)
    avg_latency = sum(r["latency_ms"] for r in recent) / len(recent) if recent else None

    return {
        "activeAccounts": account_status.get("active_accounts", 0),
        "totalDataSize": f"{total_data_size / (1024**3):.4f} GB",
        "totalFiles": len(parquet_files),
        "apiLatency": f"{avg_latency:.1f}ms" if avg_latency is not None else "n/a",
        "geminiStatus": "Healthy" if gemini_status["active_keys"] > 0 else "Not configured",
        "syncStatus": "Active" if account_status.get("active_accounts", 0) > 0 else "No accounts connected",
    }


@app.get("/api/accounts")
def get_accounts():
    quotas = account_manager.get_all_quotas()
    status = account_manager.get_account_pool_status()
    return {
        "poolStatus": status,
        "accounts": quotas,
        "transferToday": get_today_totals(),
    }


@app.get("/api/data-assets")
def get_data_assets():
    parquet_files = find_parquet_files()
    assets = []
    total_size = 0
    synthetic_count = 0
    for file in parquet_files:
        size = os.path.getsize(file)
        total_size += size
        is_synthetic = os.path.basename(file).startswith("synthetic_")
        if is_synthetic:
            synthetic_count += 1
        assets.append({
            "filename": os.path.relpath(file, DATA_DIR),
            "size": size,
            "size_str": f"{size / (1024**2):.2f} MB",
            "is_synthetic": is_synthetic,
        })
    return {
        "assets": assets,
        "total_files": len(assets),
        "total_size": total_size,
        "synthetic_files": synthetic_count,
        "real_files": len(assets) - synthetic_count,
    }


@app.get("/api/overview/traffic")
def get_overview_traffic():
    """Real recent request latencies for this API process (in-memory, last
    200 requests) -- replaces the frontend's old randomly-generated 24h chart."""
    recent = list(request_log)
    avg_latency = sum(r["latency_ms"] for r in recent) / len(recent) if recent else 0
    return {
        "requests_total": requests_total,
        "avg_latency_ms": round(avg_latency, 2),
        "recent": recent,
    }


@app.get("/api/sync/status")
def get_sync_status_endpoint():
    """Replaces the old fake BigQuery/AWS-S3 cards with the platform's real
    sync architecture: rclone union mount health + compaction watchdog state."""
    return get_sync_status()


@app.get("/api/duckdb/tables")
def get_duckdb_tables():
    mounted = mount_parquet_views(conn)
    return {"tables": mounted, "files": find_parquet_files()}


class QueryRequest(BaseModel):
    query: str


@app.post("/api/duckdb/query")
def execute_query(request: QueryRequest):
    if not is_select_only(request.query):
        raise HTTPException(status_code=400, detail="SECURITY: only read-only SELECT statements are permitted.")
    try:
        mount_parquet_views(conn)
        start = time.time()
        result = conn.execute(request.query).df()
        duration_ms = (time.time() - start) * 1000
        result = result.replace({float('nan'): None})
        return {
            "columns": list(result.columns),
            "data": result.to_dict(orient="records"),
            "row_count": len(result),
            "duration_ms": round(duration_ms, 2),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/gemini/status")
def get_gemini_status():
    return gemini_pool.get_status()


@app.get("/api/mcp/logs")
def get_mcp_logs(limit: int = 100):
    return {"logs": read_recent_logs(limit)}


@app.get("/api/alerts")
def get_alerts():
    return {
        "telegram_configured": telegram_configured(),
        "rules": list_alert_rules(),
    }


@app.post("/api/alerts/test")
def trigger_test_alert():
    ok, detail = send_test_alert()
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"sent": True, "detail": detail}


@app.get("/api/infrastructure")
def get_infrastructure():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        "host_label": "Local compute host (this machine — not a Colab/Contabo cluster)",
        "cpu": {"percent": cpu_percent},
        "memory": {"total": memory.total, "used": memory.used, "percent": memory.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "uptime_s": time.time() - psutil.boot_time(),
    }
