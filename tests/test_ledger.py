"""The processing ledger: the project's denominator.

Without it, a reader of the published table cannot distinguish "absent because
nobody looked" from "looked and found nothing". Three classes of candidate used
to leave no trace at all, and those are the cases most of these tests cover.
"""

import json

import pytest

from col4a1.ledger import Ledger, identify


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
    for drop in ("", "no_pmid", "oa_filter", "limit"):
        led.record_candidate(pmid=None if drop == "no_pmid" else f"p{drop}", doi="10.1/x",
                             title=f"t{drop}", journal="J", first_pub_date="2020-01-01",
                             is_open_access=True, has_pmcid=True, found_by={"europepmc"},
                             dropped_at=drop)
    seen = {r.get("dropped_at") for r in Ledger(tmp_path / "l.jsonl").fold().values()}
    assert seen == {"", "no_pmid", "oa_filter", "limit"}


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
