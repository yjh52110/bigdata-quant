"""Two ways to pull from an API: drain it, or watch it.

They look similar and fail differently, so they are modelled separately.

  pump    Backfill. Drain as fast as the rate limit allows, following a cursor
          until the source is exhausted. The binding constraint is the API's rate
          limit, not bandwidth. Output arrives in bulk, so file sizing takes care
          of itself.

  watch   Incremental. Ask only for what is new since last time, and store only
          what has not been seen. Runs forever, in short visits. Output arrives a
          handful of records at a time, which walks straight into the
          fragmentation problem -- measured, a 5.9 MB file transfers at
          1.53 MB/s against 36.56 MB/s for 200 MB -- so buffering and later
          compaction are not optional here, they are the point.

Three properties both depend on, and each is a way to lose data if got wrong:

  the cursor is written after the records, never before
      Advancing first and crashing second silently skips whatever was in flight.
      Everything here re-reads rather than risks that, because a duplicate is
      cheap and a hole is permanent.

  the run survives its runtime
      A free Colab or Kaggle session is capped at 12 hours and any real backfill
      outlives one. State lives on disk, not in the loop.

  the rate limit is respected, including what the server says
      A pump with no limiter gets the account banned, and the source's own
      Retry-After is more authoritative than any number configured here.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_RATE = 5.0          # requests per second
DEFAULT_BURST = 10
MAX_BACKOFF_S = 300


class PumpError(RuntimeError):
    pass


class RateLimited(Exception):
    """Raised by a fetch function when the source says to slow down.

    retry_after carries the server's own instruction, which outranks the
    configured rate: it knows its limits and we are guessing.
    """

    def __init__(self, message: str = "rate limited", retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    """Token bucket. Blocks rather than dropping, because a skipped page is a hole."""

    def __init__(self, rate_per_s: float = DEFAULT_RATE, burst: int = DEFAULT_BURST,
                 sleeper: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if rate_per_s <= 0:
            raise PumpError("rate_per_s must be positive")
        self.rate = rate_per_s
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._last = clock()
        self._sleep = sleeper
        self._clock = clock
        self.waited_s = 0.0

    def acquire(self, n: int = 1) -> float:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= n:
            self._tokens -= n
            return 0.0
        deficit = n - self._tokens
        wait = deficit / self.rate
        self._sleep(wait)
        self._tokens = 0.0
        self._last = self._clock()
        self.waited_s += wait
        return wait

    def penalise(self, retry_after: Optional[float]) -> float:
        """Honours a server-side Retry-After, capped so a bad header can't hang us."""
        wait = min(float(retry_after or 1.0), MAX_BACKOFF_S)
        self._sleep(wait)
        self._tokens = 0.0
        self._last = self._clock()
        self.waited_s += wait
        return wait


class Cursor:
    """Where a run got to, on disk so it outlives the runtime."""

    def __init__(self, name: str, root: str):
        self.name = name
        os.makedirs(root, exist_ok=True)
        self.path = os.path.join(root, f"{name}.cursor.json")
        self.state: Dict[str, Any] = {"position": None, "watermark": None,
                                      "pages": 0, "records": 0, "updated_at": None,
                                      "exhausted": False}
        self.load()

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.path) as f:
                self.state.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
        return self.state

    def save(self) -> None:
        self.state["updated_at"] = time.time()
        tmp = self.path + ".part"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2, default=str)
        # Atomic: a torn cursor would either replay everything or skip ahead.
        os.replace(tmp, self.path)

    def advance(self, *, position: Any = None, watermark: Any = None,
                records: int = 0, exhausted: bool = False) -> None:
        if position is not None:
            self.state["position"] = position
        if watermark is not None:
            self.state["watermark"] = watermark
        self.state["pages"] += 1
        self.state["records"] += records
        self.state["exhausted"] = exhausted
        self.save()


class SeenSet:
    """Ids already stored, so a watch does not re-store what it re-sees.

    Kept as hashes: ids can be long and this file is read on every visit.
    """

    def __init__(self, name: str, root: str, max_entries: int = 2_000_000):
        os.makedirs(root, exist_ok=True)
        self.path = os.path.join(root, f"{name}.seen.json")
        self.max_entries = max_entries
        self._seen: List[str] = []
        self._index: set = set()
        self.load()

    @staticmethod
    def digest(value: Any) -> str:
        return hashlib.sha256(str(value).encode()).hexdigest()[:16]

    def load(self) -> None:
        try:
            with open(self.path) as f:
                self._seen = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._seen = []
        self._index = set(self._seen)

    def save(self) -> None:
        tmp = self.path + ".part"
        with open(tmp, "w") as f:
            json.dump(self._seen[-self.max_entries:], f)
        os.replace(tmp, self.path)

    def __contains__(self, value: Any) -> bool:
        return self.digest(value) in self._index

    def add_many(self, values: Iterable[Any]) -> int:
        added = 0
        for v in values:
            d = self.digest(v)
            if d not in self._index:
                self._index.add(d)
                self._seen.append(d)
                added += 1
        if added:
            self.save()
        return added

    def stats(self) -> Dict[str, Any]:
        return {"known": len(self._index), "max_entries": self.max_entries}


def _sink_and_count(sink: Callable[[List[Dict[str, Any]]], Any],
                    records: List[Dict[str, Any]]) -> int:
    sink(records)
    return len(records)


def pump(*, name: str, state_root: str,
         fetch: Callable[[Any], Tuple[List[Dict[str, Any]], Any]],
         sink: Callable[[List[Dict[str, Any]]], Any],
         limiter: Optional[RateLimiter] = None,
         max_pages: Optional[int] = None,
         max_records: Optional[int] = None,
         deadline_s: Optional[float] = None,
         max_retries: int = 5) -> Dict[str, Any]:
    """Drains a paginated source, resuming from wherever it stopped.

    fetch(position) -> (records, next_position). Return next_position None to
    signal exhaustion.

    Stops on any of: exhaustion, max_pages, max_records, or deadline_s. The
    deadline exists because the runtime is capped at 12 hours and stopping
    deliberately beats being killed mid-write.
    """
    limiter = limiter or RateLimiter()
    cur = Cursor(name, state_root)
    if cur.state.get("exhausted"):
        return {"name": name, "status": "already_exhausted", **cur.state}

    started = time.time()
    pages = records = 0
    retries = 0
    stop = "exhausted"

    while True:
        if max_pages is not None and pages >= max_pages:
            stop = "max_pages"; break
        if max_records is not None and records >= max_records:
            stop = "max_records"; break
        if deadline_s is not None and time.time() - started >= deadline_s:
            stop = "deadline"; break

        limiter.acquire()
        try:
            batch, nxt = fetch(cur.state["position"])
            retries = 0
        except RateLimited as e:
            retries += 1
            if retries > max_retries:
                stop = "rate_limited"; break
            limiter.penalise(e.retry_after)
            continue
        except Exception as e:
            # The cursor has not moved, so a retry re-reads the same page rather
            # than skipping it.
            raise PumpError(f"{name}: fetch failed at {cur.state['position']!r}: {e}") from e

        n = _sink_and_count(sink, batch) if batch else 0
        # Records first, cursor second: the reverse loses whatever was in flight.
        cur.advance(position=nxt, records=n, exhausted=nxt is None)
        pages += 1
        records += n
        if nxt is None:
            stop = "exhausted"; break

    return {"name": name, "status": stop, "pages_this_run": pages,
            "records_this_run": records, "waited_s": round(limiter.waited_s, 2),
            "elapsed_s": round(time.time() - started, 2), "cursor": dict(cur.state)}


def watch(*, name: str, state_root: str,
          fetch: Callable[[Any], List[Dict[str, Any]]],
          sink: Callable[[List[Dict[str, Any]]], Any],
          id_of: Callable[[Dict[str, Any]], Any],
          watermark_of: Optional[Callable[[Dict[str, Any]], Any]] = None,
          limiter: Optional[RateLimiter] = None,
          seen: Optional[SeenSet] = None) -> Dict[str, Any]:
    """One incremental visit: ask for what is new, store what has not been seen.

    fetch(watermark) -> records. The watermark is whatever the source orders by;
    dedup by id covers the overlap that every "since" API returns at its
    boundary.
    """
    limiter = limiter or RateLimiter()
    cur = Cursor(name, state_root)
    seen = seen or SeenSet(name, state_root)

    limiter.acquire()
    try:
        batch = fetch(cur.state.get("watermark"))
    except RateLimited as e:
        limiter.penalise(e.retry_after)
        return {"name": name, "status": "rate_limited", "stored": 0,
                "cursor": dict(cur.state)}

    fresh = [r for r in batch if id_of(r) not in seen]
    stored = _sink_and_count(sink, fresh) if fresh else 0

    new_watermark = None
    if watermark_of and batch:
        marks = [watermark_of(r) for r in batch if watermark_of(r) is not None]
        if marks:
            candidate = max(marks)
            old = cur.state.get("watermark")
            # Never move the watermark backwards: a source that returns an
            # out-of-order page would otherwise cause a permanent gap.
            new_watermark = candidate if old is None or str(candidate) > str(old) else old

    if fresh:
        seen.add_many(id_of(r) for r in fresh)
    cur.advance(watermark=new_watermark, records=stored)

    return {"name": name, "status": "ok", "fetched": len(batch),
            "duplicates": len(batch) - len(fresh), "stored": stored,
            "watermark": cur.state.get("watermark"), "seen": seen.stats()}
