"""Every file we write has a declared schema, and the writers enforce it.

The failure guarded against is quiet rather than loud: a writer spelling a field
`n_variant` where every reader wants `n_variants` produces a file that parses,
folds and reports cleanly, having lost the field. The ledger is the project's
denominator; it cannot be where a typo hides. Validating on write turns that
into an exception at the call site.
"""

import json

import pytest
from pydantic import ValidationError

from col4a1a2 import jsonl, schemas

from conftest import REPO


def test_every_jsonl_and_json_file_we_write_has_a_model():
    """A new artefact without a schema is a test failure, not a silent pass."""
    written = set()
    for src in (REPO / "src").rglob("*.py"):
        text = src.read_text(encoding="utf-8")
        for name in ("ledger.jsonl", "discovery.jsonl", "triage.jsonl",
                     "figures.jsonl", "challenges.jsonl", "papers.jsonl",
                     "extractions.jsonl", "review.jsonl", "manifest.json",
                     "labels.json"):
            if name in text:
                written.add(name)
    assert written, "found no artefact names in src/, so this test proves nothing"
    assert written <= set(schemas.MODELS), f"no model for {written - set(schemas.MODELS)}"


def test_the_repository_s_own_data_validates():
    """The models describe the files we actually have, not an ideal of them."""
    checked = 0
    for path in sorted((REPO / "data").rglob("*.jsonl")):
        model = schemas.model_for(path)
        assert model is not None, f"no model for {path}"
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                model.model_validate(json.loads(line))
                checked += 1
    for path in sorted((REPO / "data").rglob("manifest.json")):
        schemas.validate(path, json.loads(path.read_text(encoding="utf-8")))
        checked += 1
    assert checked > 1000, f"only {checked} rows checked; is data/ present?"


def test_a_misspelled_field_is_rejected_on_write(tmp_path):
    good = {"identity": "1", "ledger_version": 1, "written_at": "2026-10-31T00:00:00+00:00",
            "n_variants": 3}
    jsonl.append(tmp_path / "ledger.jsonl", good)
    assert len(jsonl.read(tmp_path / "ledger.jsonl")) == 1
    with pytest.raises(ValidationError):
        jsonl.append(tmp_path / "ledger.jsonl", {**good, "n_variant": 3})
    # The bad row did not reach disk.
    assert len(jsonl.read(tmp_path / "ledger.jsonl")) == 1


def test_a_bad_row_cannot_truncate_a_good_file(tmp_path):
    """`open("w")` truncates on open, so validation has to happen before the
    file is opened or a rejected row destroys the previous contents."""
    path = tmp_path / "discovery.jsonl"
    rows = [{"query_hash": "h", "slice": "2020-01-01:2020-12-31", "date_from": "2020-01-01",
             "date_to": "2020-12-31", "hits_available": 1, "hits_fetched": 1,
             "complete": True, "completed_at": "2026-10-31T00:00:00+00:00"}]
    jsonl.write(path, rows)
    with pytest.raises(ValidationError):
        jsonl.write(path, rows + [{"query_hash": "h", "nonsense": 1}])
    assert jsonl.read(path) == rows


def test_an_unmodelled_filename_is_written_without_validation(tmp_path):
    """Scratch files and test fixtures must not need a model; only the
    artefacts in MODELS are governed."""
    jsonl.append(tmp_path / "scratch.jsonl", {"anything": True})
    assert jsonl.read(tmp_path / "scratch.jsonl") == [{"anything": True}]


def test_validation_can_be_turned_off_explicitly(tmp_path):
    jsonl.append(tmp_path / "ledger.jsonl", {"not": "a ledger event"}, validate=False)
    assert jsonl.read(tmp_path / "ledger.jsonl") == [{"not": "a ledger event"}]


def test_write_document_validates_and_indents(tmp_path):
    path = tmp_path / "manifest.json"
    schemas.write_document(path, {"fetched_at": "2026-10-31T00:00:00+00:00", "papers": 3})
    text = path.read_text()
    assert "\n  " in text, "the one artefact a person reads in a diff must be indented"
    assert json.loads(text)["papers"] == 3
    with pytest.raises(ValidationError):
        schemas.write_document(path, {"fetched_at": "x", "paper": 3})
    assert json.loads(path.read_text())["papers"] == 3


def test_a_ledger_event_may_not_carry_article_text():
    """Tracked file, and redistributing article content is forbidden. `Ledger`
    rejects the known field names at runtime; the schema rejects them too."""
    with pytest.raises(ValidationError):
        schemas.LedgerEvent.model_validate(
            {"identity": "1", "ledger_version": 1, "written_at": "t", "text": "body"})


def test_the_committed_schemas_match_the_models():
    """`schemas/` is documentation someone outside this repo will rely on, so a
    model change without re-exporting is a failure, not stale documentation.

        uv run col4a1a2-dev schemas
    """
    from col4a1a2.dev.schemas import build

    for rel, doc in build().items():
        path = REPO / "schemas" / rel
        assert path.is_file(), f"{rel} missing; run `col4a1a2-dev schemas`"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == doc, f"{rel} is stale; run `col4a1a2-dev schemas`"
