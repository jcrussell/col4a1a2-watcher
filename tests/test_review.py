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

CONFIG = Path(__file__).resolve().parents[1] / "config" / "col4a1a2.toml"


def _variant(as_written="p.Gly1Arg", confidence="high"):
    return {"gene": "COL4A1", "as_written": as_written, "hgvs_c": "c.1G>A",
            "hgvs_p": as_written, "n_probands": 1, "n_families": 1,
            "confidence": confidence, "evidence_quotes": ["a quote"]}


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
    rows = [{"pmid": "1", "text_sha256": "h", "prompt_version": "v2",
             "extraction": {"variants": [_variant()]},
             "grounding": {"total": 1, "grounded": 1, "ungrounded": [],
                           "low_confidence": [], "figure_sourced": []}}]
    snap = _snapshot(tmp_path, "s1", rows, [{"pmid": "1", "citation": "A et al."}])
    a, _ = _args(tmp_path, snap)
    assert cmd_review(a) == 0
    text = (snap / "review_queue.tsv").read_text()
    assert text.strip() == "\t".join(REVIEW_COLUMNS)


def test_a_flagged_record_is_queued_with_the_declared_columns(tmp_path):
    rows = [{"pmid": "1", "text_sha256": "h", "prompt_version": "v2",
             "extraction": {"variants": [_variant(confidence="low")]},
             "grounding": {"total": 1, "grounded": 1, "ungrounded": [],
                           "low_confidence": ["p.Gly1Arg"], "figure_sourced": []}}]
    snap = _snapshot(tmp_path, "s1", rows, [{"pmid": "1", "citation": "A et al."}])
    a, _ = _args(tmp_path, snap)
    assert cmd_review(a) == 0
    lines = (snap / "review_queue.tsv").read_text().strip().split("\n")
    assert lines[0].split("\t") == REVIEW_COLUMNS
    assert len(lines) == 2
    assert "low-confidence" in lines[1]


def test_missed_variant_reports_are_scoped_to_this_snapshot(tmp_path, capsys):
    rows = [{"pmid": "1", "text_sha256": "h", "prompt_version": "v2",
             "extraction": {"variants": []},
             "grounding": {"total": 0, "grounded": 0, "ungrounded": [],
                           "low_confidence": [], "figure_sourced": []}}]
    snap = _snapshot(tmp_path, "s1", rows, [{"pmid": "1", "citation": "A et al."}])
    a, st = _args(tmp_path, snap)
    jsonl.write(st.curated_dir / "challenges.jsonl", [
        {"pmid": "1", "report": {"missed_grounded": ["mine"]}},
        {"pmid": "999", "report": {"missed_grounded": ["not in this snapshot", "nor this"]}},
    ])
    assert cmd_review(a) == 0
    out = capsys.readouterr().out
    assert "1 grounded missed-variant reports" in out
    assert "nor this" not in out
