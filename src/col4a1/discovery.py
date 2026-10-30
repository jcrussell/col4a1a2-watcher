"""Time-sliced Europe PMC discovery.

One query over eleven years is the wrong unit of work, for three measured
reasons:

- **The whole sweep re-runs weekly.** Search pages are cached with a 7-day TTL
  and the sweep is 303 rows holding 17.4 MB, so a monthly watch re-downloads the
  entire history to find twenty new papers. A *sealed* slice -- a closed date
  range against a fixed query string, so an immutable question -- is cached
  forever instead, and only the open window pays. Note that sealing changes the
  cache lifetime and not whether the slice is queried: skipping the query
  outright would drop those papers from the candidate set, which an earlier
  version of this did, shrinking a 744-candidate sweep to 260.
- **Cursor chains invalidate wholesale.** Pagination embeds the previous page's
  cursor in each cache key, so one paper added to the result set shifts every
  later cursor and invalidates the chain. Slices bound the blast radius to one
  slice.
- **Coverage becomes recordable.** Per slice we can compare the hit count
  against the rows actually fetched, which is the honest version of a coverage
  claim; over one huge window it can only be an aggregate.

Two details that are easy to get wrong. `FIRST_PDATE:[a TO b]` is inclusive at
both ends, so a slice must end the day before the next begins or the boundary
day is fetched twice. And the query string is hashed into the slice key, so
editing the query invalidates every slice rather than silently leaving stale
coverage behind.

**Recency is measured, not guessed.** Europe PMC indexes retroactively. Over the
cached corpus, the lag between `firstIndexDate` and `firstPublicationDate` has a
median of 3 days, a 90th percentile of 45, a 95th of 118 and a 99th of 802, with
a worst case of 3,501. So roughly 6% of records are indexed more than 90 days
after publication and 2% more than a year: a twelve-month open window catches
about 98%, which makes sealing a cost optimisation with a known small loss
rather than a correctness guarantee. `--rediscover` is the escape hatch. Note
also that 8% are indexed *before* their stated publication date, and
`firstPublicationDate` itself changes between e-publication and print, so a
record can legitimately appear in a slice earlier than expected.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from col4a1 import jsonl

log = logging.getLogger("col4a1")

# Measured: a twelve-month open window covers ~98% of indexing lag.
DEFAULT_RECENCY_DAYS = 365


def query_hash(query: str) -> str:
    return hashlib.sha256(" ".join(query.split()).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Slice:
    date_from: str
    date_to: str

    @property
    def key(self) -> str:
        return f"{self.date_from}:{self.date_to}"


def _month_end(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def make_slices(date_from: str, date_to: str, *, width: str = "year",
                recent_width: str = "month",
                recency_days: int = DEFAULT_RECENCY_DAYS,
                today: dt.date | None = None) -> list[Slice]:
    """Cover [date_from, date_to] exactly, with no gap and no overlap.

    Wide slices for the settled backfill, narrow ones inside the recency window
    where every run re-queries, because a month's query is small and a year's is
    not. Both ends of each slice are inclusive, matching `FIRST_PDATE`.
    """
    start = dt.date.fromisoformat(date_from)
    end = dt.date.fromisoformat(date_to)
    if end < start:
        return []
    now = today or dt.date.today()
    cutoff = now - dt.timedelta(days=recency_days)

    out: list[Slice] = []
    cur = start
    while cur <= end:
        narrow = cur >= cutoff
        w = recent_width if narrow else width
        if w == "month":
            stop = _month_end(cur)
        elif w == "year":
            stop = dt.date(cur.year, 12, 31)
            # Do not let a wide slice straddle the recency cutoff: the part
            # inside the window must stay open while the part outside can seal.
            if cur < cutoff <= stop:
                stop = cutoff - dt.timedelta(days=1)
        else:
            raise ValueError(f"unknown slice width {w!r}")
        stop = min(stop, end)
        out.append(Slice(cur.isoformat(), stop.isoformat()))
        cur = stop + dt.timedelta(days=1)
    return out


def is_sealed(sl: Slice, *, recency_days: int = DEFAULT_RECENCY_DAYS,
              today: dt.date | None = None) -> bool:
    """A slice seals once its whole range is outside the recency window."""
    now = today or dt.date.today()
    return dt.date.fromisoformat(sl.date_to) < now - dt.timedelta(days=recency_days)


class DiscoveryLog:
    """Append-only record of which slices have been swept, folded on read.

    Keyed by (query hash, slice), so editing the query re-opens everything.
    """

    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").split("\n"):
                if not line.strip():
                    continue
                try:
                    self.rows.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("discovery log: skipping malformed line in %s", path)
        self.appended = 0

    def fold(self) -> dict[tuple[str, str], dict]:
        out: dict[tuple[str, str], dict] = {}
        for r in sorted(self.rows, key=lambda r: r.get("completed_at", "")):
            out[(r.get("query_hash", ""), r.get("slice", ""))] = r
        return out

    def completed(self, query: str) -> set[str]:
        qh = query_hash(query)
        return {sl for (q, sl), r in self.fold().items() if q == qh and r.get("complete")}

    def record(self, *, query: str, sl: Slice, hits_available: int | None,
               hits_fetched: int, complete: bool) -> dict:
        row = {
            "query_hash": query_hash(query),
            "slice": sl.key,
            "date_from": sl.date_from,
            "date_to": sl.date_to,
            "hits_available": hits_available,
            "hits_fetched": hits_fetched,
            "complete": complete,
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(jsonl.dumps(row) + "\n")
        self.rows.append(row)
        self.appended += 1
        return row


def plan(date_from: str, date_to: str, query: str, dlog: DiscoveryLog, *,
         rediscover: bool = False, recency_days: int = DEFAULT_RECENCY_DAYS,
         today: dt.date | None = None) -> tuple[list[Slice], list[Slice]]:
    """Split the window into open slices and sealed ones.

    Both are queried; the caller caches the sealed ones permanently rather than
    skipping them, or their papers would vanish from the candidate set.
    """
    slices = make_slices(date_from, date_to, recency_days=recency_days, today=today)
    if rediscover:
        return slices, []
    done = dlog.completed(query)
    todo, skipped = [], []
    for sl in slices:
        if sl.key in done and is_sealed(sl, recency_days=recency_days, today=today):
            skipped.append(sl)
        else:
            todo.append(sl)
    return todo, skipped
