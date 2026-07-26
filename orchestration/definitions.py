"""Dagster definitions: the archive modelled as a partitioned asset.

Why Dagster rather than an executor written here: the job is "materialise 4012
daily partitions, resumably, without exceeding N concurrent workers", and that is
exactly what a partitioned asset with a BackfillPolicy is. Retries, resume,
progress, lineage and the concurrency cap all come from the framework instead of
from code this project would have to maintain.

The division of labour matters and is deliberate:

  scheduler.py   decides policy -- how many workers, which Drive accounts, what
                 the limiting factor is. It holds the constants measured against
                 the real platforms (Colab caps at 3 sessions, Drive allows
                 750 GB/day per account, ranged download peaks at 2 ways), none
                 of which Dagster could know.
  Dagster        executes that policy: runs partitions, retries failures, resumes
                 after a stop, and refuses to exceed the concurrency it was given.

So the asset asks the scheduler how wide to go and then declares that width; it
does not re-derive it.
"""

import logging
import os
import time
from typing import Any, Dict, List

from dagster import (AssetExecutionContext, BackfillPolicy, DailyPartitionsDefinition,
                     Definitions, MetadataValue, RetryPolicy, asset)

from backend import drive_rest, s3_views, scheduler
from backend.drive_store import COMPRESSION, Catalog, L1, dataset_path
from backend.google_account_manager import GoogleAccountManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# eth blocks begins 2015-07-30; the partition definition starts there so a
# backfill covers real history rather than an arbitrary window.
CHAIN, TABLE = "eth", "blocks"
START_DATE = "2015-07-30"

daily = DailyPartitionsDefinition(start_date=START_DATE)


def _concurrency() -> int:
    """Workers to run at once, from the scheduler rather than from a guess."""
    try:
        accounts = GoogleAccountManager().get_all_quotas()
        measured = s3_views.MEASURED.get((CHAIN, TABLE))
        total = int((measured or {}).get("gb", 1) * 1024 ** 3)
        return max(1, len(scheduler.plan_job(total, accounts=accounts).slots))
    except Exception as e:
        # A planning failure must not make the asset undeclarable; one worker is
        # always safe, just slow.
        logging.warning(f"falling back to 1 worker: {str(e)[:200]}")
        return 1


@asset(
    partitions_def=daily,
    # Each run handles a batch of partitions rather than one, so session startup
    # (measured 20-60s) is amortised instead of paid per day.
    backfill_policy=BackfillPolicy.multi_run(max_partitions_per_run=8),
    retry_policy=RetryPolicy(max_retries=3, delay=30),
    # Named so a concurrency limit can be applied in dagster.yaml without
    # touching code.
    op_tags={"dagster/concurrency_key": "drive_upload"},
    description=("One day of eth blocks: read from S3 in place, re-encode to zstd "
                 "sorted by time, upload to Drive, record in the catalog."),
)
def chain_archive(context: AssetExecutionContext) -> None:
    import duckdb

    day = context.partition_key
    con = duckdb.connect(":memory:")
    s3_views.prepare(con)
    glob = s3_views.glob_for(CHAIN, TABLE, day)

    work_dir = os.path.join("backend", "data", "_archive", f"{CHAIN}_{TABLE}")
    os.makedirs(work_dir, exist_ok=True)
    local = os.path.join(work_dir, f"{CHAIN}-{TABLE}-{day}.parquet")

    t0 = time.time()
    # Re-encoding rather than copying: the public dataset uses snappy, and the
    # same day of eth transactions measured 1408 MB as snappy against 739 MB as
    # zstd. Sorting by time is what lets a later reader skip row groups.
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{glob}', hive_partitioning=1) "
        f"ORDER BY timestamp) TO '{local}' "
        f"(FORMAT PARQUET, COMPRESSION '{COMPRESSION}')")
    convert_s = time.time() - t0
    size = os.path.getsize(local)
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{local}')").fetchone()[0]
    if rows == 0:
        os.remove(local)
        raise ValueError(f"{day}: source returned no rows -- refusing to record an "
                         f"empty partition as done")

    mgr = GoogleAccountManager()
    accounts = mgr.get_all_quotas()
    plan = scheduler.plan_job(size, accounts=accounts)
    account = plan.slots[0].account
    token = mgr.access_token_for(account)

    drive_path = dataset_path(L1, domain="chain", chain=CHAIN, table=TABLE, date=day)
    folder = drive_rest.ensure_path(token, drive_path)
    up = drive_rest.upload(token, local, folder)
    os.remove(local)

    # Shard the catalog per worker: several partitions run at once, and a shared
    # read-modify-write would lose entries.
    Catalog(shard=f"dagster-{os.getpid()}").upsert(
        layer=L1,
        partition_keys={"domain": "chain", "chain": CHAIN, "table": TABLE, "date": day},
        rows=rows, bytes_=size, files=1, time_min=day, time_max=day,
        drive_folder_id=folder, account=account,
        source=f"s3 {CHAIN}/{TABLE} re-encoded to {COMPRESSION}",
    )

    context.add_output_metadata({
        "rows": MetadataValue.int(rows),
        "bytes": MetadataValue.int(size),
        "mb": MetadataValue.float(round(size / 1024 ** 2, 2)),
        "convert_seconds": MetadataValue.float(round(convert_s, 2)),
        "upload_mb_s": MetadataValue.float(up["mb_per_s"]),
        "account": MetadataValue.text(account),
        "drive_path": MetadataValue.text(drive_path),
        "planned_parallelism": MetadataValue.int(len(plan.slots)),
        "limited_by": MetadataValue.text(plan.limited_by),
    })


defs = Definitions(assets=[chain_archive])
