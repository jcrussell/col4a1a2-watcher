"""The spot-check queue.

A snapshot with nothing to review still gets its declared columns, which are
never derived from the first queued record. The queue reads only this
snapshot's challenges, so a missed-variant report is not repeated under every
snapshot as if each had produced it.
"""

from col4a1a2 import jsonl
from col4a1a2.cli import REVIEW_COLUMNS, cmd_review
from col4a1a2.extract.rows import records_digest_for
from conftest import (
    cmd_args,
    make_challenge_row,
    make_extraction_row,
    make_paper_row,
    make_settings,
    make_snapshot,
    make_variant,
)

def _snapshot(tmp_path, rows, papers):
    st = make_settings(tmp_path)
    return st, make_snapshot(st, papers=papers, extractions=rows)


def test_an_empty_queue_writes_a_header_not_a_crash(tmp_path):
    """Nothing to review and no file at all are different situations, and the
    curator should be able to tell them apart."""
    rows = [make_extraction_row(variants=[make_variant()])]
    st, snap = _snapshot(tmp_path, rows, [make_paper_row()])
    a = cmd_args(st, snap)
    assert cmd_review(a) == 0
    assert (snap / "review.jsonl").read_text() == ""
    assert not (snap / "review.tsv").exists()


def test_a_flagged_record_is_queued_with_the_declared_columns(tmp_path):
    """Every declared column is present on the row, so a consumer can rely on
    the shape rather than on whatever the first record happened to carry."""
    row = make_extraction_row(variants=[make_variant(confidence="low")])
    row["grounding"]["low_confidence"] = ["p.Gly1Arg"]
    st, snap = _snapshot(tmp_path, [row], [make_paper_row()])
    a = cmd_args(st, snap)
    assert cmd_review(a) == 0
    queued = jsonl.read(snap / "review.jsonl")
    assert len(queued) == 1
    assert set(queued[0]) == set(REVIEW_COLUMNS)
    assert queued[0]["flagged_because"] == "low-confidence"


def test_missed_variant_reports_are_scoped_to_this_snapshot(tmp_path, capsys):
    row = make_extraction_row()
    st, snap = _snapshot(tmp_path, [row], [make_paper_row()])
    a = cmd_args(st, snap)
    jsonl.write(st.curated_dir / "challenges.jsonl", [
        make_challenge_row("1", records_digest=records_digest_for(row),
                           report={"missed_grounded": ["mine"]}),
        make_challenge_row("999", report={"missed_grounded": ["not in this snapshot",
                                                              "nor this"]}),
    ])
    assert cmd_review(a) == 0
    out = capsys.readouterr().out
    assert "1 grounded missed-variant reports" in out
    assert "nor this" not in out


def test_a_challenge_of_superseded_records_is_not_attached(tmp_path, capsys):
    """Matching on the PMID alone would attach the verdict about a paper's
    earlier records to its re-extracted ones."""
    row = make_extraction_row(variants=[make_variant(confidence="low")])
    st, snap = _snapshot(tmp_path, [row], [make_paper_row()])
    a = cmd_args(st, snap)
    jsonl.write(st.curated_dir / "challenges.jsonl", [
        make_challenge_row("1", records_digest="digest-of-earlier-records",
                           report={"missed_grounded": ["stale"]}),
    ])
    assert cmd_review(a) == 0
    out = capsys.readouterr().out
    assert "stale" not in out
    queued = jsonl.read(snap / "review.jsonl")
    assert len(queued) == 1 and queued[0]["verdict"] == "", "no verdict from a stale challenge"
