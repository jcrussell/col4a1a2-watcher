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
Decisions are cached by (pmid, prompt version) so refetching is free.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

TRIAGE_PROMPT_VERSION = "t1"
TRIAGE_MODEL = "claude-haiku-5-5"

log = logging.getLogger("col4a1")

SYSTEM = """You decide whether a paper is worth reading in full for a COL4A1/COL4A2 variant-evidence database.

Answer yes only if the paper plausibly reports one or more specific COL4A1 or COL4A2 sequence variants carried by one or more specific individuals (patients, probands, families, or fetuses). Papers about new patients, case reports, case series, diagnostic cohorts and gene-panel studies are yes, even if the abstract does not name the variant.

Answer no for: expression, transcriptomic, proteomic or single-cell studies where COL4A1/COL4A2 is only a measured or differentially expressed gene; GWAS or association studies of common SNPs near the gene; animal or cell-line studies with no human variant carriers; narrative reviews and guidelines with no new patients; papers where COL4A1/COL4A2 is only a downstream marker of a different gene's variant; and papers about variants in other genes.

If the abstract is ambiguous but the paper is clinical and about these genes, answer yes: a false yes costs one full-text read, a false no loses the evidence permanently."""


class TriageDecision(BaseModel):
    read_full_text: bool = Field(
        description="True if the paper plausibly reports COL4A1/COL4A2 variants in specific individuals"
    )
    reason: str = Field(description="One short clause naming the deciding feature of the abstract")


class TriageCache:
    """Append-only JSONL cache of decisions, keyed by (pmid, prompt version)."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[tuple[str, str], dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.entries[(r["pmid"], r.get("triage_prompt_version", ""))] = r
        self.hits = 0
        self.misses = 0

    def get(self, pmid: str) -> dict | None:
        r = self.entries.get((pmid, TRIAGE_PROMPT_VERSION))
        if r is not None:
            self.hits += 1
        return r

    def put(self, pmid: str, decision: TriageDecision, usage: dict) -> dict:
        r = {
            "pmid": pmid,
            "triage_prompt_version": TRIAGE_PROMPT_VERSION,
            "model": TRIAGE_MODEL,
            "read_full_text": decision.read_full_text,
            "reason": decision.reason,
            "usage": usage,
        }
        self.entries[(pmid, TRIAGE_PROMPT_VERSION)] = r
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.misses += 1
        return r


def triage_paper(client, title: str, abstract: str | None, *, model: str = TRIAGE_MODEL) -> tuple[TriageDecision, dict]:
    """One Haiku call on title + abstract. Raises on API failure; caller decides."""
    user = f"TITLE: {title or ''}\n\nABSTRACT: {abstract or '(no abstract available)'}"
    resp = client.beta.messages.parse(
        model=model,
        max_tokens=2000,
        output_config={"effort": "low"},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=TriageDecision,
    )
    usage = {
        "input": resp.usage.input_tokens,
        "output": resp.usage.output_tokens,
        "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    }
    return resp.parsed_output, usage
