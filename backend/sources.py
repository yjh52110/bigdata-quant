"""One contract for every data source, so adding the Nth costs the same as the 2nd.

The platform draws on seven kinds of source -- chain data, exchanges, prediction
markets, social, news, content platforms, and its own derived tables. Written as
seven pipelines that is seven things to maintain; the only way it stays tractable
is if every source, however different its origin, produces the same shape:
partitioned zstd Parquet plus one catalog row.

Access mode is the axis that actually matters, and there are three, not two:

  in_place   Already analytics-ready where it lives; querying it beats copying it.
             Measured 2026-07-26: the AWS public dataset is 61.31 TB, a single
             column of a 1400 MB file transfers 0.29% of it, and reads run at
             43-324 MB/s in place against 29.96 MB/s from Drive. Copying would
             cost ~62 hours to end up slower and coarser. Most designs assume
             everything must be ingested first; for this class of source that
             assumption is simply wrong, so it is a first-class mode here.

  batch      Published as periodic archives. Fetch, normalise, store. Idempotent
             by period, so a re-run skips what exists rather than duplicating it.

  poll       Only reachable through an API or a page, with no archive. If we do
             not capture it, it is gone -- these are the sources that genuinely
             justify Drive storage.

What a source must declare is deliberately small: where its output lands, how it
is partitioned, and what its rows look like. Everything source-specific stays
inside its fetch function.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.drive_store import L1, L2, LAYERS, dataset_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

IN_PLACE = "in_place"
BATCH = "batch"
POLL = "poll"
MODES = (IN_PLACE, BATCH, POLL)

# Broad shape of the payload, because it decides the storage treatment rather
# than anything about the origin: tabular and text both become Parquet columns,
# binary splits between an inline BLOB column and standalone files.
TABULAR, TEXT, BINARY = "tabular", "text", "binary"
SHAPES = (TABULAR, TEXT, BINARY)


class SourceError(ValueError):
    pass


@dataclass
class Source:
    """One data source and the contract it fulfils."""

    name: str
    domain: str                       # chain / market / news / social / predict / content
    mode: str                         # in_place | batch | poll
    shape: str = TABULAR
    layer: str = L1
    partition_keys: List[str] = field(default_factory=list)
    # Left None for in_place sources: there is nothing to fetch.
    fetch: Optional[Callable[..., Dict[str, Any]]] = None
    # Where it is read from when mode is in_place, e.g. an s3:// glob builder.
    locator: Optional[str] = None
    freshness_days: Optional[float] = None
    time_column: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise SourceError(f"{self.name}: mode must be one of {MODES}, got {self.mode!r}")
        if self.shape not in SHAPES:
            raise SourceError(f"{self.name}: shape must be one of {SHAPES}, got {self.shape!r}")
        if self.layer not in LAYERS:
            raise SourceError(f"{self.name}: layer must be one of {LAYERS}, got {self.layer!r}")
        if self.mode == IN_PLACE:
            if not self.locator:
                raise SourceError(f"{self.name}: an in_place source needs a locator")
            if self.fetch is not None:
                raise SourceError(
                    f"{self.name}: an in_place source must not define fetch -- declaring "
                    f"both is the mistake this mode exists to prevent")
        else:
            if self.fetch is None:
                raise SourceError(f"{self.name}: a {self.mode} source needs a fetch function")
            if not self.partition_keys:
                raise SourceError(
                    f"{self.name}: {self.mode} sources must declare partition_keys -- "
                    f"unpartitioned output cannot be pruned or written incrementally")
        # Sorting within a file is what makes row-group statistics tight enough
        # for predicate pushdown to skip anything, so the column has to be named.
        if self.mode != IN_PLACE and self.shape in (TABULAR, TEXT) and not self.time_column:
            raise SourceError(f"{self.name}: declare time_column so output can be time-sorted")

    def output_path(self, **values: str) -> str:
        missing = [k for k in self.partition_keys if k not in values]
        if missing:
            raise SourceError(f"{self.name}: missing partition values for {missing}")
        ordered = {k: values[k] for k in self.partition_keys}
        return dataset_path(self.layer, **ordered)

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name, "domain": self.domain, "mode": self.mode,
            "shape": self.shape, "layer": self.layer,
            "partition_keys": self.partition_keys, "locator": self.locator,
            "freshness_days": self.freshness_days, "notes": self.notes,
            "stored_on_drive": self.mode != IN_PLACE,
        }


REGISTRY: Dict[str, Source] = {}


def register(source: Source) -> Source:
    if source.name in REGISTRY:
        raise SourceError(f"duplicate source name {source.name!r}")
    REGISTRY[source.name] = source
    return source


def get(name: str) -> Source:
    if name not in REGISTRY:
        raise SourceError(f"unknown source {name!r}; have {sorted(REGISTRY)}")
    return REGISTRY[name]


def by_mode(mode: str) -> List[Source]:
    return sorted((s for s in REGISTRY.values() if s.mode == mode), key=lambda s: s.name)


def catalogue() -> Dict[str, Any]:
    return {
        "sources": [s.summary() for s in sorted(REGISTRY.values(), key=lambda s: s.name)],
        "by_mode": {m: [s.name for s in by_mode(m)] for m in MODES},
        "note": ("in_place 的源不占云盘：它们在原处已是分析就绪格式，复制过来只会更慢更粗。"
                 "batch 与 poll 的产出才写入云盘。"),
    }


# --------------------------------------------------------------------------
# The sources this platform actually has today
# --------------------------------------------------------------------------
def _binance_fetch(**kw: Any) -> Dict[str, Any]:
    from backend.binance_ingestion import ingest_binance_klines
    return ingest_binance_klines(**kw)


register(Source(
    name="aws_chain",
    domain="chain",
    mode=IN_PLACE,
    locator="s3://aws-public-blockchain",
    freshness_days=2,
    notes=("11 条链、61.31 TB，已是 Parquet。经 backend/s3_views.py 原地查询。"
           "sonarx 维护的五条链实测滞后 8 天，AWS 自维护的 1-2 天"),
))

register(Source(
    name="binance_klines",
    domain="market",
    mode=BATCH,
    shape=TABULAR,
    layer=L1,
    partition_keys=["domain", "exchange", "symbol", "interval"],
    fetch=_binance_fetch,
    time_column="open_time",
    freshness_days=1,
    notes="月度归档，公开无需密钥。已下载的月份跳过，因此重跑幂等",
))

register(Source(
    name="addr_flow_1d",
    domain="chain",
    mode=BATCH,
    shape=TABULAR,
    layer=L2,
    partition_keys=["name", "date"],
    # Computed on Kaggle rather than locally; dispatch is the fetch.
    fetch=lambda **kw: {"dispatch": "kaggle_dispatch.factor", **kw},
    time_column="date",
    notes=("由 eth token_transfers 原地算出（11 列只读 5 列）。"
           "实测 7 天 → 113 万行 / 43 MB，源表 391.8 GB"),
))
