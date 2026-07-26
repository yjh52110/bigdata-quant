import asyncio
import os
import secrets
import time
import logging
from collections import deque
from typing import Any

import duckdb
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from backend.google_account_manager import GoogleAccountManager
from backend.quant_ai_bridge import MultiKeyGeminiPool
from backend.duckdb_engine import find_parquet_files, mount_parquet_views, is_select_only, DATA_DIR
from backend.sync_status import get_sync_status
from backend.alerting import list_alert_rules, send_test_alert, telegram_configured
from backend.mcp_logs import read_recent_logs
from backend.transfer_log import get_today_totals
from backend.binance_ingestion import ingest_binance_klines
from backend.gemini_probe import probe_all, list_models, pick_default_model, TIER_RULES, DOC_URL
from backend import s3_views
from backend.drive_store import (Catalog, LAYERS, MIN_FILE_BYTES, MAX_FILE_BYTES,
                                 COMPRESSION, placement_report)
from backend import sources as source_registry
from backend import compaction
from backend.kaggle_control import overview as kaggle_overview
from backend.kaggle_dispatch import (
    dispatch as kaggle_dispatch, refresh_jobs as kaggle_refresh_jobs,
    fetch_output as kaggle_fetch_output, logs as kaggle_logs,
    list_jobs as kaggle_list_jobs, DispatchError as KaggleDispatchError,
)
from backend.colab_control import (
    overview as colab_overview, probe_session as colab_probe_session,
    probe_entitlements as colab_probe_entitlements,
)
from backend.aws_blockchain_ingestion import (
    ingest_aws_blockchain, preview as aws_preview, BudgetExceeded,
    CHAINS as AWS_CHAINS, TABLES as AWS_TABLES,
)
from backend.job_queue import (
    submit_job, claim_job, report_result, heartbeat,
    list_workers, list_jobs, get_job, queue_stats, WORKER_TIMEOUT_S,
)
from backend.mcp_users import (
    list_users as list_mcp_users,
    create_user as create_mcp_user,
    set_disabled as set_mcp_user_disabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="ChainQuantPlatform Admin API")

QUANT_API_KEY = os.environ.get("QUANT_API_KEY")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
OAUTH_CALLBACK_PATH = "/api/accounts/oauth/callback"
OAUTH_REDIRECT_URI = f"{PUBLIC_BASE_URL}{OAUTH_CALLBACK_PATH}"

AUTH_EXEMPT_PATHS = {"/api/health", OAUTH_CALLBACK_PATH}

OAUTH_SETUP_HINT = (
    "Create an OAuth client (type: Web application) in Google Cloud Console, add "
    f"{OAUTH_REDIRECT_URI} as an authorized redirect URI, and save the downloaded "
    "JSON to backend/data/credentials.json."
)

# Maps the OAuth `state` nonce -> {account_index, code_verifier}. Because the
# callback can't carry our X-API-Key header, this nonce is what proves the
# redirect belongs to a flow we actually started. The PKCE verifier rides along
# because the exchange happens in a later request with a fresh Flow object.
_pending_oauth: dict = {}


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    """Mirrors the OPENBROWSER_API_KEY convention: if QUANT_API_KEY is set, every
    /api/* request must present a matching X-API-Key header. If unset, the server
    stays open for local dev -- but this is logged loudly so it's never silently
    left open in a real deployment."""
    # /api/health is public so the dashboard can discover whether auth is on.
    # The OAuth callback is public because Google redirects the user's browser
    # there directly and cannot attach our X-API-Key header; it is instead
    # protected by the OAuth `state` nonce checked in the handler.
    if QUANT_API_KEY and request.url.path.startswith("/api/") and request.url.path not in AUTH_EXEMPT_PATHS:
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


# Added LAST so it ends up OUTERMOST in Starlette's middleware stack (add_middleware
# inserts at position 0). This matters: api_key_guard returns 401 early without
# calling the next handler, so if CORS sat inside it the 401 would carry no CORS
# headers and the browser would surface it as an opaque network failure ("could
# not reach backend") instead of a clean "incorrect password".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(DATA_DIR, exist_ok=True)

# Long-lived in-memory DuckDB connection; views are (re)mounted from whatever
# parquet files exist on disk before every query so newly-ingested data shows
# up without restarting the server.
conn = duckdb.connect(':memory:')

# httpfs is installed once here, not inside a query. Installing it lazily made
# the first /api/s3/query after startup return the CREATE VIEW result shape
# (a "Count" column, no rows) instead of the SELECT's -- a wrong answer rather
# than an error, which is the worst failure mode available.
try:
    s3_views.prepare(conn)
    logging.info("httpfs ready; S3 datasets can be queried in place")
except Exception as e:
    logging.warning(f"httpfs unavailable, /api/s3/query will fail: {str(e)[:200]}")

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
    try:
        account_manager.get_oauth_flow(redirect_uri=OAUTH_REDIRECT_URI)
        oauth_ready, oauth_hint = True, None
    except Exception:
        oauth_ready, oauth_hint = False, OAUTH_SETUP_HINT

    return {
        "poolStatus": status,
        "accounts": quotas,
        "transferToday": get_today_totals(),
        "oauthConfigured": oauth_ready,
        "oauthHint": oauth_hint,
    }


class AuthUrlRequest(BaseModel):
    account_index: str


@app.post("/api/accounts/auth-url")
def create_auth_url(req: AuthUrlRequest):
    """Starts the OAuth flow for one Drive account.

    Uses a loopback redirect back into this API rather than the old
    `urn:ietf:wg:oauth:2.0:oob` copy-paste flow, which Google has retired --
    OOB would fail outright for any OAuth client created today.
    """
    if not req.account_index.strip():
        raise HTTPException(status_code=400, detail="account_index must not be empty")
    nonce = secrets.token_urlsafe(24)
    try:
        flow = account_manager.get_oauth_flow(redirect_uri=OAUTH_REDIRECT_URI)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account consent",
            state=nonce,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=OAUTH_SETUP_HINT)
    except (ValueError, KeyError):
        # A placeholder/invalid credentials.json lands here rather than
        # FileNotFoundError, which would otherwise surface as an opaque 500.
        raise HTTPException(status_code=400, detail=f"credentials.json is not a valid OAuth client secrets file. {OAUTH_SETUP_HINT}")
    _pending_oauth[nonce] = {
        "account_index": req.account_index.strip(),
        # Generated by the Flow when it built the URL; the callback needs the
        # same value or Google refuses the code exchange.
        "code_verifier": getattr(flow, "code_verifier", None),
    }
    return {"auth_url": auth_url, "redirect_uri": OAUTH_REDIRECT_URI}


@app.get(OAUTH_CALLBACK_PATH, response_class=HTMLResponse)
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Google redirects the browser here after consent."""
    def page(title: str, detail: str, ok: bool) -> str:
        color = "#34d399" if ok else "#f87171"
        return (
            f"<html><body style='background:#0f172a;color:#e2e8f0;font-family:system-ui;"
            f"display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            f"<div style='text-align:center'><h2 style='color:{color}'>{title}</h2>"
            f"<p style='color:#94a3b8'>{detail}</p></div></body></html>"
        )

    if error:
        return HTMLResponse(page("Authorization failed", error, False), status_code=400)
    pending = _pending_oauth.pop(state, None)
    if not pending:
        return HTMLResponse(page("Invalid or expired request", "Unknown OAuth state nonce.", False), status_code=400)
    account_index = pending["account_index"]
    try:
        account_manager.handle_callback(account_index, code, redirect_uri=OAUTH_REDIRECT_URI,
                                        code_verifier=pending.get("code_verifier"))
    except Exception as e:
        logging.error(f"OAuth callback failed for {account_index}: {e}")
        return HTMLResponse(page("Could not connect account", str(e), False), status_code=400)
    return HTMLResponse(page("Account connected", f"“{account_index}” is now in the pool. You can close this tab.", True))


class IngestRequest(BaseModel):
    source: str = "binance"
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    months: int = 1
    chain: str = "ethereum"
    from_block: int = 18000000
    to_block: int = 18000100
    # AWS public dataset
    table: str = "blocks"
    start_date: str = ""
    end_date: str = ""
    max_gb: float = 2.0


@app.get("/api/aws/catalog")
def aws_catalog():
    """Chains/tables available, with measured daily sizes so a table can be
    picked knowingly -- eth traces is ~500x eth blocks per day."""
    return {
        "chains": AWS_CHAINS,
        "tables": AWS_TABLES,
        "measured_daily_mb": {
            "eth": {"blocks": 5.5, "contracts": 24.5, "token_transfers": 258.0,
                    "transactions": 784.4, "logs": 921.6, "traces": 2642.2},
            "btc": {"blocks": 0.1, "transactions": 551.6},
        },
        "measured_on": "2026-07-01",
    }


class AwsPreviewRequest(BaseModel):
    chain: str = "eth"
    table: str = "blocks"
    start_date: str
    end_date: str


@app.post("/api/aws/preview")
def aws_preview_endpoint(req: AwsPreviewRequest):
    """Byte count for a range before committing to the download."""
    try:
        return aws_preview(req.chain, req.table, req.start_date, req.end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AWS listing failed: {e}")


@app.post("/api/ingest")
def trigger_ingest(req: IngestRequest):
    """Runs an ingestion job synchronously and reports what actually landed."""
    if req.source == "binance":
        try:
            result = ingest_binance_klines(req.symbol, req.interval, req.months)
            mount_parquet_views(conn)
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Binance ingestion failed: {e}")
    if req.source == "aws":
        try:
            result = ingest_aws_blockchain(
                req.chain if req.chain in AWS_CHAINS else "eth",
                req.table,
                req.start_date or None,
                req.end_date or None,
                req.max_gb,
            )
            mount_parquet_views(conn)
            return result
        except BudgetExceeded as e:
            raise HTTPException(status_code=413, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"AWS ingestion failed: {e}")
    if req.source == "hypersync":
        try:
            from backend.hypersync_ingestion import extract_chain_data
            result = asyncio.run(extract_chain_data(req.from_block, req.to_block, req.chain))
            mount_parquet_views(conn)
            return {"files": result.files, "chain": result.chain, "is_synthetic": result.is_synthetic}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Hypersync ingestion failed: {e}")
    raise HTTPException(status_code=400, detail=f"Unknown source '{req.source}' (expected 'binance', 'aws' or 'hypersync')")


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


@app.get("/api/gemini/models")
def gemini_models():
    """Which models this account can actually use. Availability is per-account."""
    if not gemini_pool.api_keys:
        return {"ok": False, "error": "No GEMINI_API_KEY configured", "models": []}
    return list_models(gemini_pool.api_keys[0])


@app.post("/api/gemini/probe")
def probe_gemini(model: str = ""):
    """Makes one real call per configured key. Google exposes no quota-remaining
    endpoint, so an actual request is the only way to learn a key's real state."""
    return probe_all(gemini_pool.api_keys, model or None)


@app.get("/api/gemini/reference")
def gemini_reference():
    return {"tier_rules": TIER_RULES, "doc_url": DOC_URL}


@app.get("/api/mcp/logs")
def get_mcp_logs(limit: int = 100):
    return {"logs": read_recent_logs(limit)}


@app.get("/api/mcp/users")
def get_mcp_users():
    return {"users": list_mcp_users()}


class CreateUserRequest(BaseModel):
    user_id: str
    daily_quota: int = 500
    rate_per_min: int = 20


@app.post("/api/mcp/users")
def add_mcp_user(req: CreateUserRequest):
    if not req.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id must not be empty")
    user = create_mcp_user(req.user_id.strip(), req.daily_quota, req.rate_per_min)
    # The only time the raw key is ever returned; it is not recoverable later.
    return {"user_id": user.user_id, "api_key": user.api_key,
            "daily_quota": user.daily_quota, "rate_per_min": user.rate_per_min}


class ToggleUserRequest(BaseModel):
    user_id: str
    disabled: bool


@app.post("/api/mcp/users/toggle")
def toggle_mcp_user(req: ToggleUserRequest):
    if not set_mcp_user_disabled(req.user_id, req.disabled):
        raise HTTPException(status_code=404, detail=f"No such user '{req.user_id}'")
    return {"user_id": req.user_id, "disabled": req.disabled}


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


class SubmitJobRequest(BaseModel):
    type: str = "sql"
    sql: str = ""
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    months: int = 1
    drive_path: str = ""


@app.post("/api/jobs")
def create_job(req: SubmitJobRequest):
    if req.type == "sql":
        if not is_select_only(req.sql):
            raise HTTPException(status_code=400, detail="SECURITY: only read-only SELECT statements are permitted.")
        payload = {"sql": req.sql, "drive_path": req.drive_path}
    elif req.type == "ingest_binance":
        payload = {"symbol": req.symbol.upper(), "interval": req.interval,
                   "months": req.months, "drive_path": req.drive_path}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown job type '{req.type}'")
    try:
        return submit_job(req.type, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/jobs")
def get_jobs(limit: int = 50):
    return {"jobs": list_jobs(limit), "stats": queue_stats()}


@app.get("/api/jobs/{job_id}")
def get_job_detail(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job")
    return job


@app.get("/api/colab/status")
def colab_status():
    """Live Colab state via the official CLI. Reports what is measurable and
    states plainly that quota is not, rather than showing a made-up figure."""
    return colab_overview()


@app.post("/api/colab/probe/{session}")
def colab_probe(session: str):
    """Measures a live session's real CPU/RAM/disk/GPU by running a probe in it."""
    r = colab_probe_session(session)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "probe failed"))
    return r


@app.post("/api/colab/entitlements")
def colab_entitlements():
    """Measures which machine types this account can actually obtain.

    No API reports an account's entitlements, so each variant is requested and
    the backend's accept/reject is recorded. Slow by nature (one session
    creation per variant), so the result is cached and served by
    /api/colab/status; this endpoint is the explicit re-measure.
    """
    return colab_probe_entitlements()


@app.get("/api/kaggle/status")
def kaggle_status():
    """Kaggle free-compute status. Unlike Colab, the weekly GPU/TPU quota is
    genuinely readable here (`kaggle quota`), so real numbers are returned --
    or an explicit not-authenticated state, never a placeholder figure."""
    return kaggle_overview()


class KaggleDispatchRequest(BaseModel):
    username: str
    slug: str
    kind: str = "aws"
    # aws
    chain: str = "eth"
    table: str = "blocks"
    days: list[str] = []
    # binance
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    months: list[str] = []
    timeout: int | None = None
    mb: int = 200
    drive_folder: str = "chainquant"


@app.post("/api/kaggle/dispatch")
def kaggle_dispatch_job(req: KaggleDispatchRequest):
    """Pushes one ingest job to Kaggle and returns immediately.

    Kaggle queues the run, so this does not wait: poll /api/kaggle/jobs for
    state and call /api/kaggle/output/... once a job reports COMPLETE.
    """
    if req.kind == "aws":
        if not req.days:
            raise HTTPException(status_code=400, detail="days must not be empty for an aws job")
        params = {"kind": "aws", "chain": req.chain, "table": req.table, "days": req.days}
    elif req.kind == "uploadbench":
        params = {"kind": "uploadbench", "mb": req.mb, "drive_folder": req.drive_folder}
    elif req.kind == "drivecheck":
        # No parameters: it only measures reachability from inside the kernel.
        params = {"kind": "drivecheck"}
    elif req.kind == "binance":
        if not req.months:
            raise HTTPException(status_code=400, detail="months must not be empty for a binance job")
        params = {"kind": "binance", "symbol": req.symbol, "interval": req.interval,
                  "months": req.months}
    else:
        raise HTTPException(status_code=400, detail=f"unknown kind: {req.kind}")

    try:
        return kaggle_dispatch(req.username, req.slug, params, timeout=req.timeout)
    except KaggleDispatchError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/kaggle/jobs")
def kaggle_jobs(refresh: bool = False):
    """Dispatched jobs. refresh=true polls Kaggle for the non-terminal ones,
    which costs one CLI call each, so it is opt-in rather than automatic."""
    return {"jobs": list(reversed(kaggle_refresh_jobs())) if refresh else kaggle_list_jobs()}


@app.post("/api/kaggle/output/{owner}/{slug}")
def kaggle_output(owner: str, slug: str):
    dest = os.path.join(DATA_DIR, "kaggle_output", slug)
    return kaggle_fetch_output(f"{owner}/{slug}", dest)


@app.get("/api/kaggle/logs/{owner}/{slug}")
def kaggle_job_logs(owner: str, slug: str):
    return kaggle_logs(f"{owner}/{slug}")


@app.get("/api/worker/drive_rest", response_class=PlainTextResponse)
def worker_drive_rest():
    """Serves backend/drive_rest.py to a Colab worker at startup.

    The worker can't import from this repo, and pasting a copy into the
    notebook would fork the implementation. Serving it keeps one source of
    truth shared with the Kaggle dispatcher, which ships the same file inside
    its push folder. Auth-protected like every other /api route.
    """
    path = os.path.join(os.path.dirname(__file__), "drive_rest.py")
    with open(path) as f:
        return f.read()


@app.get("/api/datasources")
def data_sources():
    """The S3 catalogue plus the Drive catalogue, side by side.

    They are different in kind and the response says so: S3 is raw public data we
    read in place and never copy, Drive is only what this platform derived. Merging
    the two totals would suggest we hold 61 TB, which we do not.
    """
    chains = []
    for chain, info in sorted(s3_views.LAYOUT.items()):
        tables = []
        for t in info["tables"]:
            m = s3_views.MEASURED.get((chain, t))
            tables.append({
                "table": t,
                "gb": m["gb"] if m else None,
                "days": m["days"] if m else None,
                "earliest": m["earliest"] if m else None,
                "latest": m["latest"] if m else None,
            })
        chains.append({
            "chain": chain,
            "maintainer": info.get("maintainer"),
            "prefix": info["prefix"],
            "total_gb": s3_views.measured_total_gb(chain) or None,
            "tables": sorted(tables, key=lambda x: -(x["gb"] or 0)),
        })

    cat = Catalog()
    return {
        "sources": source_registry.catalogue(),
        "placement": placement_report(account_manager.get_all_quotas(), cat.list()),
        "s3": {
            "bucket": s3_views.BUCKET,
            "chains": sorted(chains, key=lambda c: -(c["total_gb"] or 0)),
            "total_gb": s3_views.measured_total_gb(),
            "note": ("原始链上数据，留在 S3 原地查询，不复制到云盘。"
                     "体积为逐分区列举后按年采样加权所得，非精确值。"),
        },
        "drive": {
            "catalog": cat.list(),
            "summary": cat.summary(),
            "layers": list(LAYERS),
            "rules": {
                "compression": COMPRESSION,
                "file_bytes_min": MIN_FILE_BYTES,
                "file_bytes_max": MAX_FILE_BYTES,
                "why": ("200MB 上传 36.56 MB/s，5.9MB 仅 1.53 MB/s（实测，差 24 倍）；"
                        "zstd 比 snappy 小 47%，解压只多 0.07s；文件内按时间排序才能让"
                        "行组统计足够紧、谓词下推真能跳过"),
            },
        },
    }


class S3QueryRequest(BaseModel):
    sql: str
    chain: str
    table: str
    date_prefix: str = ""


@app.post("/api/s3/query")
def s3_query(req: S3QueryRequest):
    """Runs a read-only SELECT against one S3 table, registered as a view.

    A date_prefix is strongly advised: the wildcard's directory listing is what a
    broad range spends its time on, and some tables run to tens of terabytes.
    """
    if not is_select_only(req.sql):
        raise HTTPException(status_code=400, detail="Only read-only SELECT statements are allowed")
    try:
        name = s3_views.register(conn, req.chain, req.table, req.date_prefix)
    except s3_views.S3ViewError as e:
        raise HTTPException(status_code=400, detail=str(e))
    started = time.time()
    try:
        cur = conn.execute(req.sql)
        # Read the description off the cursor before consuming it: the shared
        # connection's description reflects whatever statement ran last.
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {str(e)[:300]}")
    if cols == ["Count"] and not rows:
        # The shape DDL returns. Reaching here means the SELECT never ran.
        raise HTTPException(status_code=500,
                            detail="query returned a DDL result shape -- httpfs may not be loaded")
    return {
        "view": name,
        "columns": cols,
        "rows": [list(r) for r in rows[:500]],
        "row_count": len(rows),
        "truncated": len(rows) > 500,
        "elapsed_ms": round((time.time() - started) * 1000, 1),
    }


@app.get("/api/s3/schema/{chain}/{table}")
def s3_schema(chain: str, table: str, date_prefix: str = ""):
    """Column names and types, read from one file's footer (a few hundred KB)."""
    try:
        return {"chain": chain, "table": table,
                "columns": s3_views.describe(conn, chain, table, date_prefix)}
    except s3_views.S3ViewError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CompactionRequest(BaseModel):
    directory: str = ""
    time_column: str | None = None
    dry_run: bool = True


@app.post("/api/storage/compact")
def storage_compact(req: CompactionRequest):
    """Merges undersized Parquet parts. Defaults to a dry run.

    Fragmentation is the one problem here that worsens with use: a 5.9 MB file
    transfers at 1.53 MB/s against 36.56 MB/s for 200 MB, and that penalty is
    paid on every later read, not once. Inputs are only deleted after the merged
    output's row count is verified against them.
    """
    target = os.path.abspath(req.directory or DATA_DIR)
    root = os.path.abspath(DATA_DIR)
    # Confine it to the data directory: this is the one endpoint that deletes
    # files, so the path must not be able to point anywhere else.
    if not (target == root or target.startswith(root + os.sep)):
        raise HTTPException(status_code=400,
                            detail=f"directory must be inside {root}")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"no such directory: {target}")
    try:
        return compaction.compact(target, time_column=req.time_column,
                                  dry_run=req.dry_run)
    except compaction.CompactionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/storage/fragmentation")
def storage_fragmentation(directory: str = ""):
    """What compaction would do, without doing it."""
    target = os.path.abspath(directory or DATA_DIR)
    root = os.path.abspath(DATA_DIR)
    if not (target == root or target.startswith(root + os.sep)):
        raise HTTPException(status_code=400, detail=f"directory must be inside {root}")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"no such directory: {target}")
    return compaction.plan(target)


@app.post("/api/storage/catalog/backup")
def catalog_backup():
    """Copies the catalog to Drive, beside the data it describes.

    Paths are self-describing so most of the catalog could be rebuilt by walking
    Drive -- but which account holds a dataset exists nowhere else, and
    recovering that would mean searching every connected account.
    """
    from backend import drive_rest
    accounts = [a for a in account_manager.get_all_quotas() if a.get("is_connected")]
    if not accounts:
        raise HTTPException(status_code=400, detail="no connected Google account")
    index = accounts[0]["account_index"]
    try:
        token = account_manager.access_token_for(index)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not get a token: {str(e)[:200]}")
    try:
        return {"account": index, **Catalog().backup_to_drive(drive_rest, token)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])


@app.get("/api/workers")
def get_workers():
    return {"workers": list_workers(), "stats": queue_stats()}


# --- Worker-facing endpoints (called by the Colab notebook) ---

class HeartbeatRequest(BaseModel):
    worker_id: str
    label: str = ""
    runtime: str = ""
    specs: dict = {}


@app.post("/api/workers/heartbeat")
def worker_heartbeat(req: HeartbeatRequest):
    heartbeat(req.worker_id, req.label, req.runtime, req.specs)
    return {"ok": True, "timeout_s": WORKER_TIMEOUT_S}


class ClaimRequest(BaseModel):
    worker_id: str


@app.post("/api/jobs/claim")
def worker_claim(req: ClaimRequest):
    heartbeat(req.worker_id)
    job = claim_job(req.worker_id)
    return {"job": job}


class ResultRequest(BaseModel):
    worker_id: str
    ok: bool = True
    result: Any = None
    error: str = ""


@app.post("/api/jobs/{job_id}/result")
def worker_result(job_id: str, req: ResultRequest):
    if not report_result(job_id, req.worker_id, req.ok, req.result, req.error):
        raise HTTPException(status_code=409, detail="Job not found, or not held by this worker")
    return {"ok": True}


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
