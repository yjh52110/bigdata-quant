"""ChainQuant Colab worker.

Paste this into a Google Colab cell and run it. The worker polls your admin
API for jobs, runs them on Colab's free CPU, and posts the results back.

Why polling: a free Colab runtime has no inbound networking and no stable
address, so nothing can be pushed to it. Every request here is outbound,
which is the only shape that works.

Two job types:
  sql             - run a read-only DuckDB query over Parquet in Drive
  ingest_binance  - download klines from data.binance.vision straight into
                    Drive. Colab->Drive is inside Google's network, so this
                    keeps the transfer off your home connection entirely.

Limits worth knowing before you rely on this:
  * A free session is capped around 12h and drops when the tab closes.
  * Local disk is wiped between sessions -- write anything you want to keep
    to Drive.
  * Colab is meant for interactive use; Google may throttle or stop
    long-running background work, so treat this as best-effort compute,
    not as infrastructure you can promise uptime on.

Colab exposes no external REST API -- it has no entry in Google's API
discovery directory and colab.googleapis.com returns 404 -- so nothing can
be submitted to it or queried from outside. It does provide an in-notebook
Python API (google.colab), and this worker uses two parts of it: userdata to
read secrets out of Colab Secrets rather than the notebook body, and
runtime.unassign to hand the machine back when idle.

Storage goes through the Drive REST API (backend/drive_rest.py), NOT
drive.mount(). Measured 2026-07: mount() fails headless with `ValueError:
mount failed` because it requires the notebook's consent popup, and
colabtools#4182 ("allow drive.mount() with Secrets") is still open -- so an
unattended worker cannot use FUSE at all. The REST path needs no popup and
answered in 33.8ms from a live runtime. It is also the same code path the
Kaggle dispatcher uses, so both platforms write Drive identically.
"""

import os
import time
import uuid

# ---------------------------------------------------------------- settings
# Your admin API must be reachable from Colab, i.e. a public URL (a tunnel
# or a small VPS). http://localhost:8000 will NOT work from Colab.
API_BASE = os.environ.get("CHAINQUANT_API", "https://your-host.example.com")

WORKER_LABEL = os.environ.get("WORKER_LABEL", "colab-1")
POLL_SECONDS = 5
# Local scratch inside the runtime. Results are pushed to Drive over REST at
# the end of each job; nothing is expected to survive the session.
WORK_ROOT = os.environ.get("WORK_ROOT", "/content/chainquant")
# Destination folder inside Drive (created on demand, via drive.file scope).
DRIVE_FOLDER = os.environ.get("DRIVE_FOLDER", "chainquant")
DRIVE_SECRET_NAME = os.environ.get("DRIVE_SECRET_NAME", "DRIVE_OAUTH_JSON")

# Release the Colab runtime once the queue has been empty this long, so an
# unattended worker stops burning the session allowance. 0 disables it.
IDLE_UNASSIGN_S = int(os.environ.get("IDLE_UNASSIGN_S", "0"))


def _secret(name, env_fallback=None):
    """Reads one value from Colab Secrets, falling back to the environment.

    Notebooks get saved to Drive and shared, so anything pasted into a cell
    leaks with the file. google.colab.userdata keeps it out of the document.
    """
    try:
        from google.colab import userdata  # type: ignore
        return userdata.get(name)
    except Exception:
        return os.environ.get(env_fallback or name, "")


def _api_key():
    """Prefers Colab Secrets over an env var or a literal in the notebook.

    Notebooks get saved to Drive and shared, so a key pasted into a cell
    leaks with the file. google.colab.userdata keeps it out of the document.
    Add it under the key icon in Colab's left sidebar as QUANT_API_KEY.
    """
    return _secret("QUANT_API_KEY")


API_KEY = _api_key()


def setup():
    """Installs deps, fetches the shared Drive client, and checks Drive access.

    drive_rest.py is pulled from the admin API rather than pasted in here so
    there is exactly one implementation shared with the Kaggle dispatcher.
    """
    import subprocess
    subprocess.run(["pip", "install", "-q", "duckdb", "polars", "requests"], check=False)
    os.makedirs(WORK_ROOT, exist_ok=True)

    import requests
    r = requests.get(f"{API_BASE}/api/worker/drive_rest", headers=_headers(), timeout=30)
    r.raise_for_status()
    with open("/content/drive_rest.py", "w") as f:
        f.write(r.text)
    import sys
    if "/content" not in sys.path:
        sys.path.insert(0, "/content")

    raw = _secret(DRIVE_SECRET_NAME)
    if not raw:
        print(f"No {DRIVE_SECRET_NAME} in Colab Secrets -- results stay in {WORK_ROOT} "
              f"and will be lost when the session ends.")
        return
    import drive_rest
    try:
        token = drive_rest.token_from_secret(raw)
        info = drive_rest.about(token)
        q = info.get("storageQuota", {})
        used = int(q.get("usage", 0)) / 1024 ** 3
        limit = int(q.get("limit", 0)) / 1024 ** 3 if q.get("limit") else None
        print(f"Drive reachable as {info.get('user', {}).get('emailAddress')} -- "
              f"{used:.2f} GB used" + (f" of {limit:.0f} GB" if limit else ""))
    except Exception as e:
        print(f"Drive check failed: {e}")


def push_to_drive(local_dir, subfolder):
    """Uploads a finished job's output. Returns None when Drive isn't set up."""
    raw = _secret(DRIVE_SECRET_NAME)
    if not raw:
        return None
    import drive_rest
    token = drive_rest.token_from_secret(raw)
    return drive_rest.upload_tree(token, local_dir, f"{DRIVE_FOLDER}/{subfolder}")


def _headers():
    return {"X-API-Key": API_KEY} if API_KEY else {}


def release_runtime():
    """Hands the runtime back via google.colab.runtime.unassign().

    Colab has no external API to stop a session, but it does expose this
    from inside the notebook, which is the only way to release compute
    without a human closing the tab.
    """
    try:
        from google.colab import runtime as colab_runtime  # type: ignore
        print("Idle limit reached -- unassigning the Colab runtime.")
        colab_runtime.unassign()
        return True
    except Exception as e:
        print(f"Could not unassign runtime ({type(e).__name__}: {e})")
        return False


SESSION_START = time.time()
# Colab's documented free-tier ceiling. Google publishes no quota figure and
# offers no endpoint to query one -- "usage limits sometimes fluctuate" -- so
# elapsed session time against this cap is the only concrete number available.
COLAB_MAX_SESSION_S = 12 * 3600


def runtime_specs():
    import shutil

    specs = {"elapsed_s": round(time.time() - SESSION_START), "max_session_s": COLAB_MAX_SESSION_S}
    try:
        specs["cpu_count"] = os.cpu_count()
        specs["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        total, _, free = shutil.disk_usage("/content" if os.path.exists("/content") else "/")
        specs["disk_free_gb"] = round(free / 1024 ** 3, 1)
        specs["disk_total_gb"] = round(total / 1024 ** 3, 1)
    except OSError:
        pass
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, timeout=5)
        gpu = out.stdout.decode().strip()
        specs["gpu"] = gpu.splitlines()[0] if gpu else "none"
    except Exception:
        specs["gpu"] = "none"
    return specs


def run_sql(payload):
    import duckdb

    data_dir = payload.get("drive_path") or WORK_ROOT
    if not os.path.isdir(data_dir):
        raise RuntimeError(
            f"Data directory '{data_dir}' does not exist on this worker. Mount Drive and "
            f"ingest data into it first, or pass an explicit drive_path with the job."
        )

    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='8GB'")

    # Must match backend/duckdb_engine.mount_parquet_views exactly, or the
    # same SQL resolves to different view names here than on the server.
    ROOT_PREFIX = {"hypersync_output": "chain", "market": "market"}

    mounted = []
    for root, _, files in os.walk(data_dir):
        for f in files:
            if not f.endswith(".parquet"):
                continue
            parts = os.path.relpath(os.path.join(root, f), data_dir).split(os.sep)
            partition = [p for p in parts if "=" in p]
            if not partition:
                continue
            prefix = ROOT_PREFIX.get(parts[0], parts[0].replace("-", "_"))
            values = [p.split("=", 1)[1] for p in partition]
            view = "_".join([prefix] + values).lower().replace("-", "_").replace(".", "_")
            if view in mounted:
                continue
            idx = parts.index(partition[-1])
            glob = os.path.join(data_dir, *parts[: idx + 1], "**", "*.parquet")
            try:
                con.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)")
                mounted.append(view)
            except Exception as e:
                print(f"  skip {glob}: {e}")

    print(f"  mounted views: {mounted}")
    if not mounted:
        raise RuntimeError(
            f"No Parquet partitions found under '{data_dir}', so there is nothing to query. "
            f"Expected a layout like market/symbol=BTCUSDT/interval=1m/*.parquet."
        )

    df = con.execute(payload["sql"]).fetchdf().head(5000)
    return {"rows": df.to_dict(orient="records"), "row_count": len(df), "views": mounted}


def run_ingest(payload):
    import io
    import zipfile
    from datetime import date

    import polars as pl
    import requests

    symbol = payload["symbol"].upper()
    interval = payload["interval"]
    out_dir = os.path.join(payload.get("drive_path") or WORK_ROOT,
                           "market", f"symbol={symbol}", f"interval={interval}")
    os.makedirs(out_dir, exist_ok=True)

    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]

    today = date.today()
    year, month = today.year, today.month
    months = []
    for _ in range(int(payload["months"])):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        months.append(f"{year:04d}-{month:02d}")

    written, skipped, total_rows = [], [], 0
    for ym in reversed(months):
        target = os.path.join(out_dir, f"{symbol}-{interval}-{ym}.parquet")
        if os.path.exists(target):
            skipped.append(ym)
            continue
        url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip"
        r = requests.get(url, timeout=180)
        if r.status_code == 404:
            print(f"  {ym}: not published")
            continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            raw = zf.read(zf.namelist()[0])
        df = pl.read_csv(io.BytesIO(raw), has_header=False, new_columns=cols)
        # Binance switched these epochs from ms to us; detect per row by size.
        norm = lambda c: (pl.when(pl.col(c).cast(pl.Int64) > 10_000_000_000_000)
                          .then(pl.col(c).cast(pl.Int64))
                          .otherwise(pl.col(c).cast(pl.Int64) * 1000)
                          .cast(pl.Datetime(time_unit="us")).alias(c))
        df = df.drop("ignore").with_columns([
            norm("open_time"), norm("close_time"),
            pl.lit(symbol).alias("symbol"), pl.lit(interval).alias("interval"),
        ]).sort("open_time")
        df.write_parquet(target, compression="zstd")
        total_rows += df.height
        written.append(ym)
        print(f"  {ym}: {df.height} rows -> Drive")

    result = {"symbol": symbol, "interval": interval, "written": written,
              "skipped": skipped, "total_rows": total_rows, "out_dir": out_dir}

    if written:
        # Local disk is wiped when the session ends, so anything worth keeping
        # has to go to Drive before we report the job done.
        try:
            pushed = push_to_drive(out_dir, f"market/symbol={symbol}/interval={interval}")
            if pushed is None:
                result["drive"] = {"skipped": "no DRIVE_OAUTH_JSON secret configured"}
            else:
                result["drive"] = pushed
        except Exception as e:
            result["drive"] = {"error": str(e)[:300]}
    return result


def main():
    import requests

    setup()
    worker_id = f"{WORKER_LABEL}-{uuid.uuid4().hex[:6]}"
    runtime = "colab" if os.path.exists("/content") else "local"
    print(f"Worker {worker_id} polling {API_BASE} every {POLL_SECONDS}s. Interrupt to stop.\n")

    idle = 0
    while True:
        try:
            r = requests.post(f"{API_BASE}/api/jobs/claim", json={"worker_id": worker_id},
                              headers=_headers(), timeout=30)
            r.raise_for_status()
            job = r.json().get("job")

            if not job:
                idle += 1
                # Heartbeat anyway so the dashboard keeps showing us online.
                requests.post(f"{API_BASE}/api/workers/heartbeat",
                              json={"worker_id": worker_id, "label": WORKER_LABEL,
                                    "runtime": runtime, "specs": runtime_specs()},
                              headers=_headers(), timeout=30)
                if idle % 12 == 1:
                    print(f"[{time.strftime('%H:%M:%S')}] idle, waiting for jobs...")
                if IDLE_UNASSIGN_S and idle * POLL_SECONDS >= IDLE_UNASSIGN_S:
                    release_runtime()
                    break
                time.sleep(POLL_SECONDS)
                continue

            idle = 0
            print(f"[{time.strftime('%H:%M:%S')}] job {job['id']} ({job['type']})")
            started = time.time()
            try:
                result = run_sql(job["payload"]) if job["type"] == "sql" else run_ingest(job["payload"])
                ok, err = True, ""
            except Exception as e:
                result, ok, err = None, False, f"{type(e).__name__}: {e}"
                print(f"  failed: {err}")

            requests.post(f"{API_BASE}/api/jobs/{job['id']}/result",
                          json={"worker_id": worker_id, "ok": ok, "result": result, "error": err},
                          headers=_headers(), timeout=60)
            print(f"  {'done' if ok else 'failed'} in {time.time() - started:.1f}s")

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"  poll error ({type(e).__name__}: {e}); retrying in {POLL_SECONDS}s")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
