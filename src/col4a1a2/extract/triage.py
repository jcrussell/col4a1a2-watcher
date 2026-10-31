"""Cheap abstract-level triage: is this paper worth a full-text read?

Measured on the 20-paper 2026-10-29 snapshot (5 papers truly report
COL4A1/COL4A2 variants in named individuals):

    gate                                        kept  recall  precision
    previous rule (gene + clinical keyword)       20    1.00     0.25
    patient words outnumber method words           6    1.00     0.83
    this module (Haiku on title + abstract)        8    1.00     0.62

Haiku keeps two or three more papers than the keyword rule, and its extra
keeps are the ambiguous clinical ones the prompt deliberately tells it to
keep: a research-network paper whose patient data is not published yet, an
exome cohort that turns out to have been pre-screened negative for these
genes, and a systematic review carrying only aggregate counts. None of those
is decidable from the abstract, and the keyword rule excluded them by luck
rather than by signal. Recall is what matters here, so we prefer the model.

Cost: $0.000103 per paper measured over 742 decisions, so about 8 cents for
the whole gate. (This said $0.0012 for some weeks by applying Haiku 4.5 prices
to a Haiku 5.5 model.)
Decisions are cached by (pmid, prompt version, requested model) so refetching
is free and a second model's verdicts sit alongside instead of hiding behind
the first's.
"""

from __future__ import annotations

import logging

from col4a1a2.digest import sha256_text
from col4a1a2.extract.cache import KeyedCache
from col4a1a2.patterns import CLINICAL_RE, GENE_RE
from pydantic import BaseModel, Field

TRIAGE_PROMPT_VERSION = "t1"
MAX_TRIAGE_TOKENS = 2000

log = logging.getLogger("col4a1a2")

SYSTEM = """You decide whether a paper is worth reading in full for a COL4A1/COL4A2 variant-evidence database.

Answer yes only if the paper plausibly reports one or more specific COL4A1 or COL4A2 sequence variants carried by one or more specific individuals (patients, probands, families, or fetuses). Papers about new patients, case reports, case series, diagnostic cohorts and gene-panel studies are yes, even if the abstract does not name the variant.

Answer no for: expression, transcriptomic, proteomic or single-cell studies where COL4A1/COL4A2 is only a measured or differentially expressed gene; GWAS or association studies of common SNPs near the gene; animal or cell-line studies with no human variant carriers; narrative reviews and guidelines with no new patients; papers where COL4A1/COL4A2 is only a downstream marker of a different gene's variant; and papers about variants in other genes.

If the abstract is ambiguous but the paper is clinical and about these genes, answer yes: a false yes costs one full-text read, a false no loses the evidence permanently."""


class TriageDecision(BaseModel):
    read_full_text: bool = Field(
        description="True if the paper plausibly reports COL4A1/COL4A2 variants in specific individuals"
    )
    reason: str = Field(description="One short clause naming the deciding feature of the abstract")


class TriageCache(KeyedCache):
    """Decisions keyed by (pmid, prompt version, requested model)."""

    KEY = ("pmid", "triage_prompt_version", "requested_model")
    WHAT = "triage cache"

    def get(self, pmid: str, requested_model: str, *, refresh: bool = False) -> dict | None:
        return self.lookup((pmid, TRIAGE_PROMPT_VERSION, requested_model), refresh=refresh)

    def put(self, pmid: str, decision: TriageDecision, usage: dict, provenance: dict) -> dict:
        return self.store({
            "pmid": pmid,
            "triage_prompt_version": TRIAGE_PROMPT_VERSION,
            **provenance,
            "read_full_text": decision.read_full_text,
            "reason": decision.reason,
            "usage": usage,
        })


class TriageFailed(RuntimeError):
    pass


def triage_paper(client, title: str, abstract: str | None, *, model: str, effort: str,
                 archive=None, refresh: bool = False) -> tuple[TriageDecision, dict, dict]:
    """One call on title + abstract. Raises on API failure; caller decides.

    Returns the decision, its usage, and the provenance every cached row carries.
    """
    from col4a1a2.extract.llm import call, system_block

    user = f"TITLE: {title or ''}\n\nABSTRACT: {abstract or '(no abstract available)'}"
    reply = call(
        client, site="triage", model=model, effort=effort, max_tokens=MAX_TRIAGE_TOKENS,
        system=system_block(SYSTEM), messages=[{"role": "user", "content": user}],
        output_format=TriageDecision, fallbacks=False, archive=archive, refresh=refresh,
        request={"prompt_version": TRIAGE_PROMPT_VERSION, "input_sha256": sha256_text(user)},
    )
    return reply.require(TriageFailed), reply.usage_row(), reply.provenance()


def keyword_signals(title: str, abstract: str | None, *, hints: list[dict], linked: list[dict],
                    search_hit: bool, gene_ids: set[str]) -> dict:
    """The keyword triage signals and the rule built from them.

    The rule is the fallback when the model gate is unavailable, and the
    signals are recorded for every paper either way, so the dev triage tool can
    score the rule that is actually deployed.
    """
    ta = f"{title} {abstract or ''}"
    tr = {
        "title_abstract_mention": bool(GENE_RE.search(ta)),
        "clinical_keyword": bool(CLINICAL_RE.search(ta)),
        "pubtator_gene_variant": any(h["type"] == "Gene" and str(h.get("identifier")) in gene_ids for h in hints)
                                 and any(h["type"] in ("Variant", "Mutation") for h in hints),
        "pubtator_gene_linked_variant": bool(linked),
        "pubtator_linked_hgvs": [v["hgvs"] for v in linked if v.get("hgvs")],
        "pubtator_search_hit": search_hit,
    }
    tr["keyword_keep"] = (tr["pubtator_search_hit"] or tr["pubtator_gene_variant"]
                          or (tr["title_abstract_mention"] and tr["clinical_keyword"]))
    return tr
