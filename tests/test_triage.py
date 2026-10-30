"""Triage cache and gene-linked-variant promoter (offline)."""

import json

from col4a1.extract.triage import TRIAGE_PROMPT_VERSION, TriageCache, TriageDecision
from col4a1.sources.pubtator import gene_linked_variants


def test_cache_round_trips_and_keys_on_prompt_version(tmp_path):
    p = tmp_path / "triage.jsonl"
    c = TriageCache(p)
    assert c.get("111") is None
    c.put("111", TriageDecision(read_full_text=True, reason="case report"), {"input": 10, "output": 2})

    reread = TriageCache(p)
    r = reread.get("111")
    assert r is not None and r["read_full_text"] is True
    assert r["triage_prompt_version"] == TRIAGE_PROMPT_VERSION
    assert reread.hits == 1

    # A bumped prompt version must miss, so decisions are re-made rather than reused.
    stale = json.loads(p.read_text().splitlines()[0])
    stale["triage_prompt_version"] = "t0"
    p.write_text(json.dumps(stale) + "\n")
    assert TriageCache(p).get("111") is None


def test_gene_linked_variants_filters_by_corresponding_gene():
    doc = {"passages": [{"annotations": [
        # COL4A1 (1282): kept
        {"text": "c.1537G>A", "infons": {"type": "Variant", "hgvs": "c.1537G>A", "gene_ids": [1282], "gene_id": 1282}},
        # COL3A1 (1281): dropped, even though it is a variant mention
        {"text": "p.Gly987Ser", "infons": {"type": "Variant", "hgvs": "p.G987S", "gene_ids": [1281], "gene_id": 1281}},
        # no gene link at all: dropped
        {"text": "p.G2019S", "infons": {"type": "Variant", "hgvs": "p.G2019S"}},
        # a gene mention is not a variant mention
        {"text": "COL4A1", "infons": {"type": "Gene", "identifier": "1282"}},
    ]}]}
    out = gene_linked_variants(doc, {"1282", "1284"})
    assert [v["hgvs"] for v in out] == ["c.1537G>A"]


def test_gene_linked_variants_deduplicates_repeated_mentions():
    ann = {"text": "c.1537G>A", "infons": {"type": "Variant", "hgvs": "c.1537G>A", "gene_id": 1282}}
    doc = {"passages": [{"annotations": [ann]}, {"annotations": [ann]}]}
    assert len(gene_linked_variants(doc, {"1282"})) == 1
