"""The spot-check queue.

Two defects found by running it over every snapshot: it crashed on the one
snapshot that had nothing to review, because the TSV columns were derived from
the first queued record, and it read every challenge ever written rather than
this snapshot's, so the same 275 missed-variant reports were printed under each
of four snapshots as if each had produced them.
"""

import argparse
from pathlib import Path

from col4a1a2 import jsonl
from col4a1a2.cli import REVIEW_COLUMNS, cmd_review
from col4a1a2.config import load_settings
from conftest import (
    make_challenge_row,
    make_extraction_row,
    make_paper_row,
    make_variant,
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "col4a1a2.toml"


def _snapshot(tmp_path, name, rows, papers):
    snap = tmp_path / "data" / "raw" / name
    snap.mkdir(parents=True)
    jsonl.write(snap / "papers.jsonl", papers)
    jsonl.write(snap / "extractions.jsonl", rows)
    return snap


def _args(tmp_path, snap):
    st = load_settings(CONFIG, repo=tmp_path)
    st.curated_dir.mkdir(parents=True, exist_ok=True)
    return argparse.Namespace(settings=st, snapshot=str(snap), tsv=None), st


def test_an_empty_queue_writes_a_header_not_a_crash(tmp_path):
    """Nothing to review and no file at all are different situations, and the
    curator should be able to tell them apart."""
    rows = [make_extraction_row(variants=[make_variant()])]
    snap = _snapshot(tmp_path, "s1", rows, [make_paper_row()])
    a, _ = _args(tmp_path, snap)
    assert cmd_review(a) == 0
    assert (snap / "review_queue.jsonl").read_text() == ""
    assert not (snap / "review_queue.tsv").exists()


def test_a_flagged_record_is_queued_with_the_declared_columns(tmp_path):
    """Every declared column is present on the row, so a consumer can rely on
    the shape rather than on whatever the first record happened to carry."""
    row = make_extraction_row(variants=[make_variant(confidence="low")])
    row["grounding"]["low_confidence"] = ["p.Gly1Arg"]
    snap = _snapshot(tmp_path, "s1", [row], [make_paper_row()])
    a, _ = _args(tmp_path, snap)
    assert cmd_review(a) == 0
    queued = jsonl.read(snap / "review_queue.jsonl")
    assert len(queued) == 1
    assert set(queued[0]) == set(REVIEW_COLUMNS)
    assert queued[0]["flagged_because"] == "low-confidence"


def test_missed_variant_reports_are_scoped_to_this_snapshot(tmp_path, capsys):
    snap = _snapshot(tmp_path, "s1", [make_extraction_row()], [make_paper_row()])
    a, st = _args(tmp_path, snap)
    jsonl.write(st.curated_dir / "challenges.jsonl", [
        make_challenge_row("1", report={"missed_grounded": ["mine"]}),
        make_challenge_row("999", report={"missed_grounded": ["not in this snapshot",
                                                              "nor this"]}),
    ])
    assert cmd_review(a) == 0
    out = capsys.readouterr().out
    assert "1 grounded missed-variant reports" in out
    assert "nor this" not in out
