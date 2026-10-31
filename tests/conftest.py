"""Shared fakes. The suite never touches the network and needs no API key.

`FakeAnthropic` is the project's first stand-in for the Anthropic client, which
is what has kept `triage_paper`, `transcribe_figure`, `extract_paper` and
`challenge_paper` untested until now.

One non-obvious requirement: `extract/client.py` decides which endpoint to use
by inspecting `client.beta.messages.stream`'s signature for `betas` and
`fallbacks`. A fake declared `def stream(self, **kw)` would silently exercise
only the stable branch forever, so the signature is declared explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: object = None
    stop_reason: str | None = "end_turn"
    stop_details: object = None
    model: str = "fake-model"
    usage: FakeUsage = field(default_factory=FakeUsage)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self


class _Messages:
    def __init__(self, owner: "FakeAnthropic"):
        self._owner = owner

    def _next(self, kw) -> FakeResponse:
        self._owner.calls.append(kw)
        if self._owner.raise_with is not None:
            raise self._owner.raise_with
        if self._owner.responses:
            return self._owner.responses.pop(0)
        return FakeResponse(parsed_output=self._owner.default_output)

    # Signature declared so `_beta_parse_supports_fallbacks` sees both names
    # and the beta branch is actually exercised.
    def stream(self, *, betas=None, fallbacks=None, **kw):
        return self._next({**kw, "betas": betas, "fallbacks": fallbacks})

    def parse(self, *, betas=None, fallbacks=None, **kw):
        return self._next({**kw, "betas": betas, "fallbacks": fallbacks})

    def count_tokens(self, **kw):
        self._owner.calls.append(kw)
        return FakeUsage(input_tokens=self._owner.token_count)


class _Beta:
    def __init__(self, owner):
        self.messages = _Messages(owner)


class FakeAnthropic:
    """Replays canned Pydantic models, so schema drift breaks the test."""

    def __init__(self, *responses, default_output=None, raise_with=None, token_count=1234):
        self.responses = list(responses)
        self.default_output = default_output
        self.raise_with = raise_with
        self.token_count = token_count
        self.calls: list[dict] = []
        self.beta = _Beta(self)
        self.messages = _Messages(self)


@pytest.fixture
def fake_anthropic():
    return FakeAnthropic


@pytest.fixture
def fake_response():
    return FakeResponse


# ---------------------------------------------------------------- row fixtures
#
# Built through the real Pydantic models rather than written out by hand, so a
# fixture cannot drift from the schema it is meant to represent. Hand-written
# dicts here were missing nine required fields and only the writers' schema
# validation noticed.

def make_variant(**over) -> dict:
    from col4a1a2.extract.schema import (
        Confidence,
        Gene,
        InheritanceCounts,
        Phenotypes,
        PhenotypeStatus,
        Segregation,
        VariantEvidence,
    )

    absent = PhenotypeStatus.not_assessed
    v = VariantEvidence(
        gene=Gene.COL4A1, as_written="p.Gly1Arg", hgvs_c="c.1G>A", hgvs_p="p.Gly1Arg",
        variant_type="missense_glycine", n_probands=1, n_families=1,
        inheritance=InheritanceCounts(de_novo_confirmed=0, de_novo_assumed=0,
                                      inherited=0, unknown=1),
        segregation=Segregation(),
        phenotypes=Phenotypes(cerebrovascular=absent, small_vessel=absent,
                              renal_hanac=absent, ocular=absent, muscle=absent,
                              other=absent),
        same_family_as_prior_report=False,
        evidence_quotes=["a quote"], confidence=Confidence.high,
    )
    return {**v.model_dump(mode="json"), **over}


def make_extraction_row(pmid="1", variants=None, **over) -> dict:
    from col4a1a2.extract.schema import PaperExtraction, PaperType

    ex = PaperExtraction(pmid=pmid, paper_type=PaperType.case_report,
                         input_was_abstract_only=False, has_variant_level_data=True)
    row = {
        "pmid": pmid, "pmcid": f"PMC{pmid}", "model": "test", "prompt_version": "v2",
        "text_sha256": "h", "schema_version": "1", "stop_reason": "end_turn",
        "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0},
        "grounding": {"total": 1, "grounded": 1, "ungrounded": [], "low_confidence": [],
                      "region_counts": {}, "quote_regions": [], "figure_sourced": [],
                      "model_transcribed_only": [], "overconfident_transcribed": [],
                      "needs_review": False},
        "extraction": {**ex.model_dump(mode="json"),
                       "variants": list(variants if variants is not None else [])},
    }
    row.update(over)
    return row


def make_paper_row(pmid="1", **over) -> dict:
    row = {"pmid": pmid, "pmcid": f"PMC{pmid}", "title": "T", "citation": "A et al.",
           "text": "a quote", "text_sha256": "h", "n_chars": 7, "abstract_only": False,
           "triage": {"keep": True}}
    row.update(over)
    return row


def make_challenge_row(pmid="1", **over) -> dict:
    row = {"pmid": pmid, "text_sha256": "h", "records_digest": "d",
           "challenge_prompt_version": "c1", "extract_prompt_version": "v2",
           "model": "test",
           "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0,
                     "effort": "medium"},
           "challenge": {"pmid": pmid, "duplicate_record_keys": [], "missed_variants": [],
                         "verdicts": [], "notes": ""},
           "report": {"pmid": pmid}}
    row.update(over)
    return row
