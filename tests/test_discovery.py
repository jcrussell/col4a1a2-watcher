"""Time-sliced discovery: slice generation, sealing, and query invalidation."""

import datetime as dt

import pytest

from col4a1a2.discovery import (
    DiscoveryLog,
    Slice,
    is_sealed,
    make_slices,
    plan,
    query_hash,
)

TODAY = dt.date(2026, 10, 30)
Q = "(TITLE:COL4A1 OR ABSTRACT:COL4A1)"


def _covers_exactly(slices, date_from, date_to):
    """No gap and no overlap. FIRST_PDATE is inclusive at both ends, so a slice
    must end the day before the next begins or the boundary day is fetched twice."""
    assert slices[0].date_from == date_from
    assert slices[-1].date_to == date_to
    for a, b in zip(slices, slices[1:]):
        assert dt.date.fromisoformat(b.date_from) - dt.date.fromisoformat(a.date_to) == dt.timedelta(days=1)
    for s in slices:
        assert dt.date.fromisoformat(s.date_from) <= dt.date.fromisoformat(s.date_to)


def test_slices_cover_a_long_window_exactly():
    sl = make_slices("2015-01-01", "2026-10-29", today=TODAY)
    _covers_exactly(sl, "2015-01-01", "2026-10-29")


def test_slices_cover_a_leap_year_exactly():
    sl = make_slices("2024-01-01", "2024-12-31", today=dt.date(2030, 1, 1))
    _covers_exactly(sl, "2024-01-01", "2024-12-31")
    assert any(s.date_to == "2024-02-29" for s in make_slices(
        "2024-01-01", "2024-12-31", today=dt.date(2024, 3, 1)))


def test_recent_slices_are_monthly_and_old_ones_are_yearly():
    sl = make_slices("2015-01-01", "2026-10-29", today=TODAY)
    widths = [(dt.date.fromisoformat(s.date_to) - dt.date.fromisoformat(s.date_from)).days for s in sl]
    assert max(widths) > 300, "the backfill should use wide slices"
    assert min(widths) < 40, "the recent window should use narrow slices"


def test_a_wide_slice_does_not_straddle_the_recency_cutoff():
    """Otherwise the settled part of a year could never seal."""
    sl = make_slices("2024-01-01", "2026-10-29", today=TODAY)
    cutoff = TODAY - dt.timedelta(days=365)
    for s in sl:
        a, b = dt.date.fromisoformat(s.date_from), dt.date.fromisoformat(s.date_to)
        assert not (a < cutoff <= b), f"slice {s.key} straddles the cutoff"


def test_single_day_window():
    sl = make_slices("2026-01-01", "2026-01-01", today=TODAY)
    assert sl == [Slice("2026-01-01", "2026-01-01")]


def test_inverted_window_is_empty():
    assert make_slices("2026-02-01", "2026-01-01", today=TODAY) == []


def test_sealing_follows_the_measured_recency_window():
    """Measured lag: median 3 days, p95 118, p99 802. Twelve months catches ~98%."""
    assert is_sealed(Slice("2015-01-01", "2015-12-31"), today=TODAY) is True
    assert is_sealed(Slice("2026-09-01", "2026-09-30"), today=TODAY) is False


def test_plan_partitions_into_open_and_sealed_without_losing_any(tmp_path):
    """Both lists get queried; sealing only changes the cache lifetime. Skipping
    sealed slices outright drops their papers from the candidate set, which an
    earlier version did -- shrinking a 744-candidate sweep to 260."""
    dlog = DiscoveryLog(tmp_path / "d.jsonl")
    todo, sealed = plan("2015-01-01", "2026-10-29", Q, dlog, today=TODAY)
    assert sealed == [] and todo, "nothing recorded yet, so everything is open"
    all_slices = set(s.key for s in todo)

    for sl in todo:
        dlog.record(query=Q, sl=sl, hits_available=5, hits_fetched=5, complete=True)
    todo2, sealed2 = plan("2015-01-01", "2026-10-29", Q, DiscoveryLog(tmp_path / "d.jsonl"), today=TODAY)
    assert sealed2, "settled slices must seal"
    assert todo2, "the recency window must stay open"
    assert set(s.key for s in todo2) | set(s.key for s in sealed2) == all_slices, \
        "every slice must still be accounted for"
    assert all(is_sealed(s, today=TODAY) for s in sealed2)
    assert not any(is_sealed(s, today=TODAY) for s in todo2)


def test_editing_the_query_invalidates_every_slice(tmp_path):
    dlog = DiscoveryLog(tmp_path / "d.jsonl")
    todo, _ = plan("2015-01-01", "2026-10-29", Q, dlog, today=TODAY)
    for sl in todo:
        dlog.record(query=Q, sl=sl, hits_available=1, hits_fetched=1, complete=True)
    reread = DiscoveryLog(tmp_path / "d.jsonl")
    assert reread.completed(Q)
    assert reread.completed(Q + " AND OPEN_ACCESS:Y") == set(), "a changed query must re-open everything"
    assert query_hash(Q) != query_hash(Q + " AND OPEN_ACCESS:Y")


def test_query_hash_ignores_whitespace_only_changes():
    assert query_hash("(A OR  B)") == query_hash("(A OR B)")


def test_an_incomplete_slice_never_seals(tmp_path):
    """A slice truncated by --max-search has not been covered, so recording it
    complete would silently freeze a partial sweep."""
    dlog = DiscoveryLog(tmp_path / "d.jsonl")
    sl = Slice("2015-01-01", "2015-12-31")
    dlog.record(query=Q, sl=sl, hits_available=100, hits_fetched=10, complete=False)
    assert sl.key not in DiscoveryLog(tmp_path / "d.jsonl").completed(Q)


def test_rediscover_queries_everything(tmp_path):
    dlog = DiscoveryLog(tmp_path / "d.jsonl")
    for sl in make_slices("2015-01-01", "2026-10-29", today=TODAY):
        dlog.record(query=Q, sl=sl, hits_available=1, hits_fetched=1, complete=True)
    todo, sealed = plan("2015-01-01", "2026-10-29", Q, DiscoveryLog(tmp_path / "d.jsonl"),
                        rediscover=True, today=TODAY)
    assert sealed == [] and len(todo) > 1


def test_malformed_discovery_line_is_skipped(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text('{"query_hash": "x", "slice": "a:b", "complete": true, "completed_at": "z"}\nnope\n')
    assert len(DiscoveryLog(path).rows) == 1
