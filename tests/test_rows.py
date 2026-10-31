"""Which stored rows count as current: one definition, used by cli and check."""

import json

from col4a1a2.extract.prompt import PROMPT_VERSION
from col4a1a2.extract.rows import challenge_for, current_challenges, latest_extractions, records_digest_for
from conftest import make_challenge_row, make_extraction_row, make_settings, make_variant


def test_latest_extractions_supersedes_earlier_rows_and_drops_errors(tmp_path):

    st = make_settings()
    cur = {"prompt_version": PROMPT_VERSION, "requested_model": st.extract_model}
    p = tmp_path / "extractions.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {**cur, "pmid": "1", "text_sha256": "a", "written_at": "1", "extraction": {"variants": []}},
        {**cur, "pmid": "2", "text_sha256": "b", "written_at": "2", "error": "ExtractionRefused"},
        {**cur, "pmid": "1", "text_sha256": "c", "written_at": "3", "extraction": {"variants": [1, 2]}},
        # A comparison model's newer row must not displace the current one.
        {**cur, "requested_model": "other", "pmid": "1", "text_sha256": "d", "written_at": "4",
         "extraction": {"variants": []}},
        # Nor may a retired prompt's.
        {**cur, "prompt_version": "v0", "pmid": "3", "text_sha256": "e", "written_at": "5",
         "extraction": {"variants": []}},
    ]) + "\n")
    rows = latest_extractions(p, st.extract_model)
    assert [r["pmid"] for r in rows] == ["1"]
    assert rows[0]["text_sha256"] == "c", "the newer current row must win"


def test_a_challenge_is_attached_only_to_the_records_it_reviewed():
    """Matching on the PMID alone would attach a verdict about superseded records."""
    st = make_settings()
    row = make_extraction_row("1", variants=[make_variant()])
    exact = make_challenge_row("1", text_sha256=row["text_sha256"], records_digest=records_digest_for(row))
    stale = make_challenge_row("1", text_sha256=row["text_sha256"], records_digest="earlier",
                               written_at="2026-12-01T00:00:00+00:00")
    other_text = make_challenge_row("1", text_sha256="grown", records_digest=records_digest_for(row),
                                    written_at="2026-12-02T00:00:00+00:00")
    current = current_challenges([exact, stale, other_text], st.extract_model)
    assert challenge_for(row, current) == exact, "newer but non-matching challenges must not win"
    assert challenge_for(make_extraction_row("1", variants=[make_variant(n_probands=7)]), current) is None
