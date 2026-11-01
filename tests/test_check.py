"""`col4a1a2 check`: each rule must fire on the breakage it exists for."""

import json
import subprocess

import pytest
from conftest import (
    make_challenge_row,
    make_extraction_row,
    make_settings,
    make_snapshot,
    make_triage_row,
    make_variant,
)

from col4a1a2 import jsonl
from col4a1a2.check import Checker
from col4a1a2.extract.rows import records_digest_for
from col4a1a2.ledger import Ledger


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo, msg, *args):
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", *args, "-m", msg)


@pytest.fixture
def world(tmp_path):
    """A consistent little repository: one paper triaged, extracted, challenged, committed."""
    st = make_settings(tmp_path)
    cur = st.curated_dir
    led = Ledger(cur / "ledger.jsonl")
    led.record(identity="1", stage="candidate", dropped_at="")
    jsonl.append(cur / "triage.jsonl", make_triage_row("1"))
    row = make_extraction_row("1", variants=[make_variant()])
    led.record(identity="1", stage="extracted", text_sha256=row["text_sha256"],
               prompt_version=row["prompt_version"], requested_model=row["requested_model"], attempts=0)
    jsonl.append(cur / "challenges.jsonl", make_challenge_row("1", text_sha256=row["text_sha256"],
                                                              records_digest=records_digest_for(row)))
    led.record(identity="1", stage="challenged")
    snap = make_snapshot(st, extractions=[row])
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "data/curated")
    _commit(tmp_path, "base")
    return st, tmp_path, snap


def _codes(st, repo, **kw):
    return {(f.level, f.code) for f in Checker(st, repo=repo, **kw).run()}


def _errors(st, repo, **kw):
    return {c for lvl, c in _codes(st, repo, **kw) if lvl == "ERROR"}


def test_a_consistent_world_is_clean(world):
    st, repo, _ = world
    assert _errors(st, repo, allow_rewrite=False) == set()


def test_a_torn_line_and_an_invalid_row_are_errors(world):
    st, repo, _ = world
    with (st.curated_dir / "triage.jsonl").open("a") as f:
        f.write('{"pmid": "1", "oops": true}\n{"pmid"')
    errs = _errors(st, repo, allow_rewrite=False)
    assert {"torn-tail", "invalid-row"} <= errs


def test_a_byte_identical_duplicate_is_a_warning_not_a_re_run(world):
    st, repo, _ = world
    path = st.curated_dir / "triage.jsonl"
    first = path.read_text().split("\n")[0]
    with path.open("a") as f:
        f.write(first + "\n")
    assert ("WARN", "duplicate-row") in _codes(st, repo, allow_rewrite=False)
    newer = {**json.loads(first), "written_at": "2026-11-01T00:00:00+00:00"}
    path.write_text(first + "\n" + json.dumps(newer) + "\n")
    assert ("WARN", "duplicate-row") not in _codes(st, repo, allow_rewrite=True)


def test_a_dangling_pmid_is_an_error(world):
    st, repo, _ = world
    jsonl.append(st.curated_dir / "triage.jsonl", make_triage_row("999", read_full_text=False))
    assert "dangling-pmid" in _errors(st, repo, allow_rewrite=False)


def test_rewriting_a_log_needs_the_migration_switch(world):
    st, repo, _ = world
    path = st.curated_dir / "triage.jsonl"
    r = json.loads(path.read_text().split("\n")[0])
    jsonl.write(path, [{**r, "reason": "edited"}])
    assert "rewritten-log" in _errors(st, repo, allow_rewrite=False)
    assert "rewritten-log" not in _errors(st, repo, allow_rewrite=True)


def test_a_migration_may_not_lose_a_paper(world):
    """Dropping the only events that record a paper: a rewrite is allowed, a
    loss is refused."""
    st, repo, _ = world
    path = st.curated_dir / "ledger.jsonl"
    rows = jsonl.read(path)
    jsonl.write(path, [r for r in rows if r["identity"] != "1"])
    assert "migration-lost-identity" in _errors(st, repo, allow_rewrite=True)


def test_a_migration_may_not_lose_a_triage_verdict(world):
    st, repo, _ = world
    led = Ledger(st.curated_dir / "ledger.jsonl")
    led.record_triage("1", {"keep": True, "llm_keep": True, "llm_reason": "r",
                            "pubtator_gene_linked_variant": False})
    _commit(repo, "verdict", "-a")
    path = st.curated_dir / "ledger.jsonl"
    jsonl.write(path, [{k: v for k, v in r.items() if k != "triage"} for r in jsonl.read(path)])
    assert "migration-lost-verdict" in _errors(st, repo, allow_rewrite=True)


def test_an_index_entry_without_its_data_is_an_error(world):
    """The direction that matters: resume would skip a paper with no result."""
    st, repo, _snap = world
    st.extractions.write_text("")
    assert "index-without-data" in _errors(st, repo, allow_rewrite=False)


def test_data_without_its_index_is_only_a_warning(world):
    st, repo, _snap = world
    jsonl.append(st.extractions, make_extraction_row("2", text_sha256="other"))
    codes = _codes(st, repo, allow_rewrite=False)
    assert ("WARN", "data-without-index") in codes
    assert not {c for lvl, c in codes if lvl == "ERROR"}


def test_a_challenge_of_records_that_changed_is_an_error(world):
    st, repo, _snap = world
    row = jsonl.read(st.extractions)[0]
    row["extraction"]["variants"][0]["n_probands"] = 7
    jsonl.write(st.extractions, [row])
    assert "challenge-of-missing-records" in _errors(st, repo, allow_rewrite=False)


def test_a_challenged_paper_without_a_current_challenge_is_an_error(world):
    st, repo, _ = world
    (st.curated_dir / "challenges.jsonl").write_text("")
    assert "challenged-without-row" in _errors(st, repo, allow_rewrite=True)


def test_a_snapshot_without_a_manifest_is_an_error(world):
    st, repo, snap = world
    (snap / "manifest.json").unlink()
    assert "no-manifest" in _errors(st, repo, allow_rewrite=False)


def test_without_snapshots_the_cross_checks_are_skipped_not_failed(world):
    st, repo, _snap = world
    import shutil

    shutil.rmtree(st.snapshot_root)
    codes = _codes(st, repo, allow_rewrite=False)
    assert ("INFO", "no-snapshots") in codes
    assert not {c for lvl, c in codes if lvl == "ERROR"}
