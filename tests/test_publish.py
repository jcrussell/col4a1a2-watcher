"""`publish`: built from tracked files alone, confident records by default, deterministic."""

import csv
import json
import re

import pytest

from col4a1a2 import jsonl, publish, schemas
from col4a1a2.extract.challenge import record_key
from col4a1a2.extract.rows import records_digest_for
from col4a1a2.ledger import Ledger
from conftest import make_challenge_row, make_extraction_row, make_settings, make_triage_row, make_variant


def _challenge(row, verdicts, *, disputed=(), digest=None):
    return make_challenge_row(
        row["pmid"], text_sha256=row["text_sha256"], records_digest=digest or records_digest_for(row),
        challenge={"pmid": row["pmid"], "duplicate_record_keys": [], "missed_variants": [], "notes": "",
                   "verdicts": [{"record_key": k, "verdict": v, "problem": f"problem with {k}"}
                                for k, v in verdicts.items()]},
        report={"pmid": row["pmid"], "disputed": list(disputed), "fabricated": [], "duplicates": []})


@pytest.fixture
def world(tmp_path):
    """Tracked files only -- no snapshot directory exists, exactly as in CI."""
    st = make_settings(tmp_path)
    led = Ledger(st.curated_dir / "ledger.jsonl")
    variants = [
        make_variant(as_written="c.1G>A", evidence_quotes=["Quote, with a comma and \"marks\"."]),
        make_variant(as_written="c.2G>A"),
        make_variant(as_written="c.3G>A", confidence="low"),
        make_variant(as_written="c.4G>A"),
    ]
    row = make_extraction_row("1", variants=variants)
    other = make_extraction_row("2", variants=[make_variant(as_written="c.9G>A")])
    jsonl.write(st.extractions, [row, other])
    keys = [record_key("1", i, v) for i, v in enumerate(variants)]
    jsonl.write(st.curated_dir / "challenges.jsonl", [
        _challenge(row, {keys[0]: "upheld", keys[1]: "disputed", keys[2]: "upheld", keys[3]: "unverifiable"},
                   disputed=[keys[1]]),
        # A challenge of earlier records for paper 2: stale, so paper 2 is not reviewed.
        _challenge(other, {record_key("2", 0, other["extraction"]["variants"][0]): "upheld"}, digest="earlier"),
    ])
    for pmid, keep, oa in (("1", True, True), ("2", True, True), ("3", True, False), ("4", False, True)):
        led.record_candidate(pmid=pmid, doi=None, title=f"Paper {pmid}", journal="J",
                             first_pub_date=f"2026-0{pmid}-01", is_open_access=oa, has_pmcid=oa,
                             found_by={"europepmc"}, dropped_at="")
        led.record_triage(pmid, {"keep": keep, "llm_keep": keep, "llm_reason": "r",
                                 "pubtator_gene_linked_variant": False})
    jsonl.append(st.curated_dir / "triage.jsonl", make_triage_row("1"))
    for r in (row, other):
        led.record(identity=r["pmid"], stage="extracted", text_sha256=r["text_sha256"],
                   prompt_version=r["prompt_version"], requested_model=r["requested_model"], attempts=0)
    assert not st.snapshot_root.exists()
    return st, keys


def test_the_default_release_is_confident_records_only(world):
    st, keys = world
    rel = publish.gather(st)
    assert [e["record_key"] for e in rel.evidence] == [keys[0]], \
        "disputed, low-confidence, unverifiable and stale-challenge records stay out"
    assert rel.n_extracted == 5
    assert "1 of 5 extracted records" in rel.selection()


def test_all_releases_everything_with_the_reasons(world):
    st, keys = world
    rel = publish.gather(st, include_all=True)
    by = {e["as_written"]: e for e in rel.evidence}
    assert len(by) == 5
    assert by["c.2G>A"]["reviewer_verdict"] == "disputed" and "disputed" in by["c.2G>A"]["review_flags"]
    assert by["c.2G>A"]["reviewer_problem"].startswith("problem with")
    assert by["c.3G>A"]["review_flags"] == "low-confidence"
    assert by["c.4G>A"]["reviewer_verdict"] == "unverifiable"
    assert by["c.9G>A"]["reviewer_verdict"] == "not reviewed", "a stale challenge must not attach"
    assert "including those the adversarial review disputed" in rel.selection()


def test_literature_status_for_each_class(world):
    st, _ = world
    status = {r["pmid"]: r["status"] for r in publish.gather(st).literature}
    assert status == {"1": "extracted", "2": "extracted", "3": "kept_not_read", "4": "not_relevant"}
    assert Ledger(st.curated_dir / "ledger.jsonl").summary()["kept_not_extracted"] == 1, \
        "the ledger's backlog and the release's statuses come from one rule"
    n = {r["pmid"]: r["n_records"] for r in publish.gather(st).literature}
    assert n["1"] == 1 and n["2"] == 0, "n_records counts this release's rows"


def test_the_release_validates_round_trips_and_is_byte_identical(world, tmp_path):
    st, _ = world
    out1, out2 = tmp_path / "a", tmp_path / "b"
    publish.write(publish.gather(st), out1)
    publish.write(publish.gather(st), out2)
    for p in sorted(out1.rglob("*")):
        if p.is_file():
            assert p.read_bytes() == (out2 / p.relative_to(out1)).read_bytes(), f"{p.name} differs between runs"
    for name in ("evidence.jsonl", "literature.jsonl"):
        for r in jsonl.read(out1 / "data" / name):
            schemas.validate(out1 / "data" / name, r)
    rows = list(csv.DictReader((out1 / "data" / "evidence.csv").open()))
    assert rows[0]["evidence_quotes"] == 'Quote, with a comma and "marks".'
    schemas.validate(out1 / "data" / "datapackage.json", json.loads((out1 / "data" / "datapackage.json").read_text()))


def test_every_output_carries_the_notice(world, tmp_path):
    st, _ = world
    rel = publish.gather(st)
    written = publish.write(rel, tmp_path / "s")
    pkg = json.loads((tmp_path / "s" / "data" / "datapackage.json").read_text())
    html = (tmp_path / "s" / "index.html").read_text()
    readme = (tmp_path / "s" / "data" / "README.txt").read_text()
    # Every table is shipped beside the README that carries the notice.
    assert all(p.parent == tmp_path / "s" / "data" for p in written if p.suffix in (".csv", ".jsonl"))
    for text in (pkg["description"], html, readme, *[r["description"] for r in pkg["resources"]]):
        assert "Machine-extracted" in text and "not clinical advice" in text
        assert "not yet spot-checked by a person" in text
    assert [f["name"] for f in pkg["resources"][0]["schema"]["fields"]] == list(schemas.EvidenceRow.model_fields)


def test_the_page_makes_no_requests_elsewhere(world, tmp_path):
    st, _ = world
    publish.write(publish.gather(st), tmp_path / "s")
    html = (tmp_path / "s" / "index.html").read_text()
    hosts = set(re.findall(r'(?:src|href)="(https?://[^/"]+)', html))
    assert hosts <= {"https://creativecommons.org"}, hosts
    assert "<script src" not in html and "<link" not in html


def test_a_gold_file_without_a_schema_stops_the_release(world):
    """The notice must never state a spot-check rate nothing validates."""
    st, _ = world
    (st.curated_dir / "gold").mkdir()
    (st.curated_dir / "gold" / "checks.jsonl").write_text("{}\n")
    with pytest.raises(RuntimeError, match="no schema"):
        publish.gather(st)


def test_a_record_the_reviewer_calls_a_duplicate_is_not_confident(world):
    """It would count the same carriers twice."""
    st, keys = world
    rows = jsonl.read(st.curated_dir / "challenges.jsonl")
    rows[0]["report"]["duplicates"] = [keys[0]]
    jsonl.write(st.curated_dir / "challenges.jsonl", rows)
    assert publish.gather(st).evidence == []
    flags = {e["record_key"]: e["review_flags"] for e in publish.gather(st, include_all=True).evidence}
    assert flags[keys[0]] == "duplicate"


def test_the_version_moves_when_only_challenges_change(world):
    """New challenges change which records are confident, so they date the release."""
    st, _ = world
    before = publish.gather(st).version
    rows = jsonl.read(st.curated_dir / "challenges.jsonl")
    rows[0]["written_at"] = "2027-01-01T00:00:00+00:00"
    jsonl.write(st.curated_dir / "challenges.jsonl", rows)
    assert publish.gather(st).version == "2027-01-01" != before


def test_a_placeholder_inside_the_data_is_left_alone(world, tmp_path):
    st, _ = world
    rows = jsonl.read(st.extractions)
    rows[0]["extraction"]["variants"][0]["phenotype_details"] = "@@NOTICE@@ <b>x</b>"
    jsonl.write(st.extractions, rows)
    publish.write(publish.gather(st), tmp_path / "s")
    html = (tmp_path / "s" / "index.html").read_text()
    assert "@@NOTICE@@ \\u003cb\\u003ex" in html, "data must be embedded verbatim and escaped"
