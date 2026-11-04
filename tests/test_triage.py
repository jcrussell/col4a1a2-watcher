"""Triage cache and gene-linked-variant promoter (offline)."""

import json

from conftest import make_provenance

from col4a1a2.config import Prices
from col4a1a2.extract.llm import Policy, ResponseArchive, SpendMeter
from col4a1a2.extract.triage import TRIAGE_PROMPT_VERSION, TriageCache, TriageDecision, llm_verdict
from col4a1a2.sources.pubtator import gene_linked_variants


def test_cache_round_trips_and_keys_on_prompt_version(tmp_path):
    p = tmp_path / "triage.jsonl"
    c = TriageCache(p)
    assert c.get("111", "m") is None
    c.put("111", TriageDecision(read_full_text=True, reason="case report"), {"input": 10, "output": 2},
          make_provenance())

    reread = TriageCache(p)
    r = reread.get("111", "m")
    assert r is not None and r["read_full_text"] is True
    assert r["triage_prompt_version"] == TRIAGE_PROMPT_VERSION
    assert reread.hits == 1

    # A bumped prompt version must miss, so decisions are re-made rather than reused.
    stale = json.loads(p.read_text().split("\n")[0])
    stale["triage_prompt_version"] = "t0"
    p.write_text(json.dumps(stale) + "\n")
    assert TriageCache(p).get("111", "m") is None


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


def test_a_spent_budget_defers_new_verdicts_but_still_serves_cached_ones(fake_anthropic, tmp_path):
    """Deferred means no verdict at all, so the ledger shows the paper untriaged
    rather than recording a guess."""
    cache = TriageCache(tmp_path / "triage.jsonl")
    client = fake_anthropic(default_output=TriageDecision(read_full_text=True, reason="case report"))
    archive = ResponseArchive(None)
    meter = SpendMeter(lambda m: Prices(input=1.0, output=1.0), archive, budget_usd=1e-9)
    policy = Policy("m", "low", archive)
    first = llm_verdict(client, cache, meter, policy, "1", "T", "A")
    assert first["read_full_text"] is True and meter.over_budget()
    assert llm_verdict(client, cache, meter, policy, "2", "T", "A") is None
    assert len(client.calls) == 1, "no call once the budget is spent"
    assert llm_verdict(client, cache, meter, policy, "1", "T", "A")["read_full_text"] is True, \
        "a cached verdict costs nothing and is served whatever the budget"
