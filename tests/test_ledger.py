"""The processing ledger: the project's denominator.

Without it, a reader of the published table cannot distinguish "absent because
nobody looked" from "looked and found nothing". Three classes of candidate used
to leave no trace at all, and those are the cases most of these tests cover.
"""

import json

import pytest

from col4a1.ledger import DROPPED_AT, Ledger, identify


def test_identity_falls_back_through_pmid_doi_title():
    """The class this exists for has no PMID, so keying on PMID alone would be
    structurally unable to record it."""
    assert identify("123", "10.1/x", "T") == ("123", "pmid")
    assert identify(None, "10.1/X", "T") == ("10.1/x", "doi")
    ident, kind = identify(None, None, "  A  Title ")
    assert kind == "title_sha256" and len(ident) == 16
    # Whitespace and case must not change the key.
    assert identify(None, None, "a title")[0] == ident
    assert identify(None, None, None) == ("", "unknown")


def test_record_candidate_covers_every_disposition(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    for drop in DROPPED_AT:
        led.record_candidate(pmid=None if drop == "no_pmid" else f"p{drop}", doi="10.1/x",
                             title=f"t{drop}", journal="J", first_pub_date="2020-01-01",
                             is_open_access=True, has_pmcid=True, found_by={"europepmc"},
                             dropped_at=drop)
    seen = {r.get("dropped_at") for r in Ledger(tmp_path / "l.jsonl").fold().values()}
    assert seen == set(DROPPED_AT)


def test_limit_is_not_a_disposition():
    """`--limit` is which papers a run selected, not where a candidate left the
    pipeline. Storing it let a later limited run bury 739 already-extracted
    papers under `dropped_at=limit`."""
    assert "limit" not in DROPPED_AT
    assert "no_metadata" in DROPPED_AT


def test_a_disposition_cannot_regress_a_paper_already_examined(tmp_path):
    """The bug this guards: a `--limit 100` idempotence check ran after the real
    sweep and stamped `limit` onto papers already triaged, fetched and
    extracted, so the fold reported 652 cut by limit where the truth was 8."""
    path = tmp_path / "l.jsonl"
    led = Ledger(path)
    kw = dict(doi=None, title="T", journal="J", first_pub_date="2020-01-01",
              is_open_access=True, has_pmcid=True, found_by={"europepmc"})
    led.record_candidate(pmid="1", **kw)
    led.record(identity="1", stage="extracted", n_variants=3, text_sha256="h")
    # A later, narrower run tries to drop it before triage.
    led.record_candidate(pmid="1", dropped_at="oa_filter", **kw)
    state = Ledger(path).fold()["1"]
    assert state["dropped_at"] == ""
    assert state["stage"] == "extracted"
    assert state["n_variants"] == 3


def test_a_disposition_still_applies_to_an_untouched_candidate(tmp_path):
    """The guard must not freeze a candidate that really was dropped before
    triage, or a genuine filter would stop being recordable."""
    led = Ledger(tmp_path / "l.jsonl")
    kw = dict(doi=None, title="T", journal="J", first_pub_date="2020-01-01",
              has_pmcid=False, found_by={"pubtator"})
    led.record_candidate(pmid="2", is_open_access=False, dropped_at="no_metadata", **kw)
    assert led.fold()["2"]["dropped_at"] == "no_metadata"
    # And metadata arriving later clears it: the candidate is back in play.
    led.record_candidate(pmid="2", is_open_access=True, dropped_at="", **kw)
    assert led.fold()["2"]["dropped_at"] == ""


def test_summary_reports_kept_but_not_extracted(tmp_path):
    """The silent loss class: the gate wanted the paper, we hold its text, and
    no extraction exists. One paper sat here because the snapshot it was fetched
    into was never extracted, and nothing reported it."""
    led = Ledger(tmp_path / "l.jsonl")
    kw = dict(doi=None, title="T", journal="J", first_pub_date="2020-01-01",
              is_open_access=True, has_pmcid=True, found_by={"europepmc"})
    for pmid in ("1", "2", "3"):
        led.record_candidate(pmid=pmid, **kw)
        led.record_triage(pmid, {"keep": True})
    led.record(identity="1", stage="fetched", text_sha256="h")
    led.record(identity="2", stage="extracted", text_sha256="h", n_variants=1)
    led.record_triage("3", {"keep": False})
    s = led.summary()
    assert s["kept_not_extracted"] == 1


def test_fold_is_last_by_written_at_and_order_independent(tmp_path):
    """git's union merge driver orders lines by which side is "ours", so a
    position-based fold would give different state depending on merge
    direction."""
    path = tmp_path / "l.jsonl"
    early = {"identity": "1", "written_at": "2026-01-01T00:00:00+00:00", "n_variants": 1, "stage": "extracted"}
    late = {"identity": "1", "written_at": "2026-06-01T00:00:00+00:00", "n_variants": 7, "stage": "extracted"}
    for order in ([early, late], [late, early]):
        path.write_text("\n".join(json.dumps(r) for r in order) + "\n")
        assert Ledger(path).fold()["1"]["n_variants"] == 7, "later event must win regardless of line order"


def test_stage_only_advances(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        {"identity": "1", "written_at": "2026-01-01T00:00:00+00:00", "stage": "extracted"},
        {"identity": "1", "written_at": "2026-02-01T00:00:00+00:00", "stage": "fetched"},
    ]) + "\n")
    assert Ledger(path).fold()["1"]["stage"] == "extracted"


def test_malformed_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_text('{"identity": "1", "written_at": "2026-01-01T00:00:00+00:00"}\nnot json\n')
    assert set(Ledger(path).fold()) == {"1"}


def test_rows_may_not_carry_article_text(tmp_path):
    """The file is tracked and redistributing article content is forbidden."""
    led = Ledger(tmp_path / "l.jsonl")
    for banned in ("text", "abstract", "body"):
        with pytest.raises(ValueError, match=banned):
            led.record(identity="1", **{banned: "..."})


def test_needs_extraction_keys_on_freshness_not_stage(tmp_path):
    """A paper extracted last month whose bundle arrives this month has grown
    new text and must be selected again, even though its stage says extracted.
    This is the resume bug already fixed once inside cmd_extract, one layer up.
    """
    led = Ledger(tmp_path / "l.jsonl")
    led.record(identity="1", stage="extracted", text_sha256="aaa", prompt_version="v2", attempts=0)
    assert led.needs_extraction("1", "aaa", "v2") is False
    assert led.needs_extraction("1", "bbb", "v2") is True, "grown text must be re-extracted"
    assert led.needs_extraction("1", "aaa", "v3") is True, "a new prompt version re-extracts"
    assert led.needs_extraction("unseen", "aaa", "v2") is True


def test_a_deterministically_failing_paper_stops_being_selected(tmp_path):
    """Otherwise it keeps its rank forever and every run re-tries it first,
    making no progress."""
    led = Ledger(tmp_path / "l.jsonl")
    for n in (1, 2, 3):
        led.record(identity="1", stage="fetched", text_sha256="aaa",
                   prompt_version="v2", attempts=n, last_error="ExtractionTruncated")
    assert led.needs_extraction("1", "aaa", "v2", max_attempts=3) is False
    assert led.summary()["wedged"] == 1


def test_summary_counts_sum_to_the_identity_total(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    for i, drop in enumerate(["", "no_pmid", "oa_filter", "limit", "limit"]):
        led.record_candidate(pmid=f"p{i}", doi=None, title=f"t{i}", journal=None,
                             first_pub_date=None, is_open_access=True, has_pmcid=True,
                             found_by={"europepmc"}, dropped_at=drop)
    s = led.summary()
    assert sum(s["by_disposition"].values()) == s["identities"] == 5
    assert sum(s["by_stage"].values()) == s["identities"]


def test_an_unchanged_candidate_is_not_re_appended(tmp_path):
    """Every fetch run would otherwise append a row per candidate whether or not
    anything changed. The tracked file reached 11 MB over a handful of runs
    before this guard existed."""
    path = tmp_path / "l.jsonl"
    kw = dict(pmid="1", doi=None, title="T", journal="J", first_pub_date="2020-01-01",
              is_open_access=True, has_pmcid=True, found_by={"europepmc"}, dropped_at="")
    first = Ledger(path)
    first.record_candidate(**kw)
    assert first.appended == 1

    second = Ledger(path)
    second.record_candidate(**kw)
    assert second.appended == 0 and second.skipped_unchanged == 1

    # A real change still lands.
    third = Ledger(path)
    third.record_candidate(**{**kw, "dropped_at": "limit"})
    assert third.appended == 1
    assert Ledger(path).fold()["1"]["dropped_at"] == "limit"


def test_in_memory_state_tracks_appends_within_one_run(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.record(identity="1", stage="fetched", text_sha256="aaa", prompt_version="v2")
    assert led.needs_extraction("1", "aaa", "v2") is True, "fetched is not extracted"
    led.record(identity="1", stage="extracted", text_sha256="aaa", prompt_version="v2", attempts=0)
    assert led.needs_extraction("1", "aaa", "v2") is False


def test_a_repeated_triage_verdict_is_not_re_appended(tmp_path):
    """Unguarded this wrote one row per candidate per run -- 744 of them --
    regardless of what the decision cache said."""
    path = tmp_path / "l.jsonl"
    tr = {"keep": True, "llm_keep": True, "llm_reason": "case report"}
    first = Ledger(path)
    first.record_triage("1", tr)
    assert first.appended == 1
    second = Ledger(path)
    second.record_triage("1", tr)
    assert second.appended == 0 and second.skipped_unchanged == 1
    third = Ledger(path)
    third.record_triage("1", {**tr, "keep": False})
    assert third.appended == 1


def test_run_scoped_fields_are_not_ledger_state(tmp_path):
    """rank and limit_at describe a run, not a paper. Keeping them here meant
    every candidate row was rewritten whenever --limit changed."""
    import inspect

    sig = inspect.signature(Ledger.record_candidate)
    assert "rank" not in sig.parameters and "limit_at" not in sig.parameters
