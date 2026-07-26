"""Decide how much parallelism a job gets, across platforms and Drive accounts.

Every constant here was measured in this project rather than assumed, because the
decision is entirely a question of where the ceilings actually are:

  Colab concurrent sessions      3. A fourth is refused outright with
                                 "Precondition Failed".
  Kaggle concurrent sessions     at least 5 with no queueing; the true ceiling
                                 was not reached, so it is treated as a floor and
                                 marked as such rather than guessed upward.
  per-session upload to Drive    35.61 MB/s from Colab, 36.56 MB/s from Kaggle.
                                 Near-identical, so this is a Drive-side limit,
                                 not a platform one.
  upload under concurrency       31.78 MB/s each with 5 Kaggle sessions running,
                                 i.e. 13% off the solo rate. Aggregate scales.
  per-account daily upload       750 GB. This, not throughput, is what bounds a
                                 bulk migration: 8 sessions saturate one
                                 account's daily allowance in about 47 minutes.
  session startup               20-60s observed for trivial kernels.
  ranged download from Drive     55.86 MB/s sequential inside Kaggle; 119.3 MB/s
                                 with 2 parallel ranges, then *falling*: 112 at 4,
                                 107 at 8, 98 at 16. Extra connections contend
                                 rather than add. On the operator's home line the
                                 same test peaked at 8 ways for only 1.6x, so this
                                 optimum belongs to the datacenter link and would
                                 be wrong to reuse elsewhere.

The startup cost is why small jobs must not be split. At ~32 MB/s and ~30s of
startup, a job of X MB takes 30 + X/32 seconds; splitting into N shards gives
30 + X/(32N). The 30s is paid by every shard in parallel, so it never shrinks --
below roughly 1 GB the split buys nothing and adds failure modes.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

COLAB, KAGGLE = "colab", "kaggle"

# Measured 2026-07-26. MAX_SESSIONS for Kaggle is a floor, not a ceiling.
PLATFORMS: Dict[str, Dict[str, Any]] = {
    COLAB: {"max_sessions": 3, "limit_is_exact": True,
            "upload_mb_s": 35.61, "startup_s": 30,
            "note": "第 4 个会话被明确拒绝：Precondition Failed"},
    KAGGLE: {"max_sessions": 5, "limit_is_exact": False,
             "upload_mb_s": 36.56, "startup_s": 30,
             "note": "实测 5 个并发无排队，真实上限未触及，按下限处理"},
}

# Each additional concurrent session costs a little of every other's rate.
CONCURRENCY_EFFICIENCY = 31.78 / 36.56          # measured at 5 sessions
DAILY_UPLOAD_BYTES = 750 * 1024 ** 3           # per Drive account
STARTUP_S = 30

# Below this, splitting adds startup and failure modes without shortening the
# job -- see the module docstring for the arithmetic.
MIN_SHARD_BYTES = 1024 ** 3

# Parallel ranged reads of one Drive file. Measured inside Kaggle: 2 ways is the
# peak at 119.3 MB/s against 55.86 sequential, and it declines from there.
# More is not better here, so the default is the measured optimum rather than
# "as many as we have threads".
DOWNLOAD_PARTS_OPTIMUM = 2
DOWNLOAD_RATES_MB_S = {1: 55.86, 2: 119.3, 4: 112.12, 8: 106.96, 16: 98.4}
# A single range costs a round trip, so splitting a small file just adds them.
MIN_RANGE_BYTES = 8 * 1024 * 1024


class SchedulerError(ValueError):
    pass


@dataclass
class Slot:
    """One concurrent session paired with the Drive account it writes to."""

    platform: str
    index: int
    account: str
    writer_id: str = ""

    def __post_init__(self) -> None:
        if not self.writer_id:
            self.writer_id = f"{self.platform}-{self.index}"


@dataclass
class Plan:
    total_bytes: int
    slots: List[Slot] = field(default_factory=list)
    shard_bytes: int = 0
    est_seconds: float = 0.0
    est_days: float = 0.0
    limited_by: str = ""
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "total_gb": round(self.total_bytes / 1024 ** 3, 2),
            "parallelism": len(self.slots),
            "shard_bytes": self.shard_bytes,
            "shard_gb": round(self.shard_bytes / 1024 ** 3, 2) if self.shard_bytes else 0,
            "slots": [{"platform": s.platform, "account": s.account,
                       "writer_id": s.writer_id} for s in self.slots],
            "est_seconds": round(self.est_seconds, 1),
            "est_hours": round(self.est_seconds / 3600, 2),
            "est_days": round(self.est_days, 2),
            "limited_by": self.limited_by,
            "warnings": self.warnings,
        }


def aggregate_rate_mb_s(counts: Dict[str, int]) -> float:
    """Combined upload rate for a given mix of sessions.

    Applies the measured per-session penalty once any concurrency exists, rather
    than modelling it per additional session: the measurement covers 5 sessions
    at 13% off, and extrapolating a per-session decay from one data point would
    be inventing a curve.
    """
    total_sessions = sum(counts.values())
    rate = sum(PLATFORMS[p]["upload_mb_s"] * n for p, n in counts.items() if n)
    return rate * (CONCURRENCY_EFFICIENCY if total_sessions > 1 else 1.0)


def plan_job(total_bytes: int, *, accounts: List[Dict[str, Any]],
             platforms: Optional[Dict[str, int]] = None,
             used_today: Optional[Dict[str, int]] = None,
             max_parallelism: Optional[int] = None) -> Plan:
    """How many sessions to use, on which platforms, writing to which accounts.

    accounts is GoogleAccountManager.get_all_quotas() output. used_today maps an
    account to bytes already uploaded today, so a plan does not hand work to an
    account that has no allowance left.
    """
    if total_bytes <= 0:
        raise SchedulerError("total_bytes must be positive")

    available = platforms or {p: cfg["max_sessions"] for p, cfg in PLATFORMS.items()}
    for p in available:
        if p not in PLATFORMS:
            raise SchedulerError(f"unknown platform {p!r}; have {sorted(PLATFORMS)}")
        cap = PLATFORMS[p]["max_sessions"]
        if available[p] > cap and PLATFORMS[p]["limit_is_exact"]:
            raise SchedulerError(
                f"{p} allows at most {cap} concurrent sessions "
                f"({PLATFORMS[p]['note']}); asked for {available[p]}")

    plan = Plan(total_bytes=total_bytes)

    usable = [a for a in accounts if a.get("is_connected")]
    if not usable:
        raise SchedulerError("no connected Drive account")
    used = used_today or {}
    with_budget = [a for a in usable
                   if DAILY_UPLOAD_BYTES - used.get(a["account_index"], 0) > 0]
    if not with_budget:
        raise SchedulerError(
            f"every connected account has spent its {DAILY_UPLOAD_BYTES // 1024**3} GB "
            f"daily upload allowance; wait for the reset")

    # Three separate ceilings; the smallest decides, and which one it was gets
    # reported because the fix differs completely.
    by_sessions = sum(available.values())
    by_accounts = len(with_budget)
    by_size = max(1, total_bytes // MIN_SHARD_BYTES)
    parallelism = min(by_sessions, by_accounts, by_size)
    if max_parallelism:
        parallelism = min(parallelism, max_parallelism)
    parallelism = max(1, int(parallelism))

    limits = {"platform_sessions": by_sessions, "drive_accounts": by_accounts,
              "job_too_small_to_split": by_size}
    plan.limited_by = min(limits, key=lambda k: limits[k])
    if by_size <= parallelism and total_bytes < MIN_SHARD_BYTES * 2:
        plan.warnings.append(
            f"{total_bytes / 1024**2:.0f} MB is small relative to ~{STARTUP_S}s of "
            f"session startup; extra sessions would not shorten it")

    # Fill from the platform with the higher measured rate first, and pair each
    # session with a distinct account so the daily caps are spent in parallel.
    chosen: Dict[str, int] = {}
    order = sorted(available, key=lambda p: -PLATFORMS[p]["upload_mb_s"])
    remaining = parallelism
    for p in order:
        take = min(available[p], remaining)
        if take > 0:
            chosen[p] = take
            remaining -= take
        if remaining == 0:
            break

    ranked_accounts = sorted(
        with_budget,
        key=lambda a: -(DAILY_UPLOAD_BYTES - used.get(a["account_index"], 0)))
    slot_no = 0
    for p, n in chosen.items():
        for i in range(n):
            acct = ranked_accounts[slot_no % len(ranked_accounts)]["account_index"]
            plan.slots.append(Slot(platform=p, index=i, account=acct))
            slot_no += 1

    plan.shard_bytes = math.ceil(total_bytes / len(plan.slots))
    rate = aggregate_rate_mb_s(chosen)
    transfer_s = (total_bytes / 1024 ** 2) / rate
    plan.est_seconds = STARTUP_S + transfer_s

    # Daily allowance, which for anything large dominates the transfer time.
    budget = sum(DAILY_UPLOAD_BYTES - used.get(a["account_index"], 0)
                 for a in ranked_accounts[:len(plan.slots)])
    plan.est_days = max(plan.est_seconds / 86400, total_bytes / budget) if budget else float("inf")
    if total_bytes > budget:
        plan.warnings.append(
            f"{total_bytes / 1024**4:.2f} TB exceeds today's remaining allowance across "
            f"{len(plan.slots)} account(s) ({budget / 1024**3:.0f} GB); it will span "
            f"about {plan.est_days:.1f} days. More accounts shorten this; more sessions "
            f"do not.")
    return plan


def download_parts_for(total_bytes: int) -> int:
    """How many ranges to split a download into.

    Returns the measured optimum, except for files small enough that the extra
    round trip outweighs the gain.
    """
    if total_bytes < MIN_RANGE_BYTES * DOWNLOAD_PARTS_OPTIMUM:
        return 1
    return DOWNLOAD_PARTS_OPTIMUM


def estimate_download_seconds(total_bytes: int, parts: Optional[int] = None) -> Dict[str, Any]:
    """Predicted time using the measured rate for that width."""
    n = parts or download_parts_for(total_bytes)
    known = min(DOWNLOAD_RATES_MB_S, key=lambda k: abs(k - n))
    rate = DOWNLOAD_RATES_MB_S[known]
    return {"parts": n, "rate_mb_s": rate, "rate_measured_at_parts": known,
            "seconds": round((total_bytes / 1024 ** 2) / rate, 1)}


def split_ranges(total_bytes: int, parts: int) -> List[Dict[str, int]]:
    """Byte ranges for a parallel ranged download. Inclusive, contiguous, exact."""
    if parts < 1:
        raise SchedulerError("parts must be at least 1")
    step = total_bytes // parts
    out = []
    for i in range(parts):
        lo = i * step
        hi = total_bytes - 1 if i == parts - 1 else (i + 1) * step - 1
        out.append({"index": i, "start": lo, "end": hi, "bytes": hi - lo + 1})
    # Ranges must tile the file exactly: a gap silently truncates and an overlap
    # silently duplicates, and neither shows up as an error.
    assert sum(r["bytes"] for r in out) == total_bytes
    return out
