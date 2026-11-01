"""Shared fakes. The suite never touches the network and needs no API key.

`FakeAnthropic` stands in for the Anthropic client, so `triage_paper`,
`transcribe_figure`, `extract_paper` and `challenge_paper` are tested offline.

One non-obvious requirement: `extract/llm.py` decides which endpoint to use
by inspecting `client.beta.messages.create` and `.stream` for `betas` and
`fallbacks`. A fake declared `def create(self, **kw)` would silently exercise
only the stable branch forever, so the signatures are declared explicitly.

Responses are dumped the way the SDK's are (`model_dump`), with the canned
output as the JSON text block, because `llm.call` archives and then parses
that text rather than trusting a pre-parsed object.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

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

    def model_dump(self, **_kw) -> dict:
        out = self.parsed_output
        if out is None:
            content = []
        else:
            text = out if isinstance(out, str) else out.model_dump_json()
            content = [{"type": "text", "text": text}]
        return {"content": content, "stop_reason": self.stop_reason,
                "stop_details": self.stop_details, "model": self.model,
                "usage": {"input_tokens": self.usage.input_tokens,
                          "output_tokens": self.usage.output_tokens,
                          "cache_read_input_tokens": self.usage.cache_read_input_tokens,
                          "cache_creation_input_tokens": self.usage.cache_creation_input_tokens}}


class _Messages:
    def __init__(self, owner: FakeAnthropic):
        self._owner = owner

    def _next(self, kw) -> FakeResponse:
        self._owner.calls.append(kw)
        if self._owner.raise_with is not None:
            raise self._owner.raise_with
        if self._owner.responses:
            return self._owner.responses.pop(0)
        return FakeResponse(parsed_output=self._owner.default_output)

    # Signatures declared so `llm._supports_fallbacks` sees both names and the
    # beta branch is actually exercised.
    def stream(self, *, betas=None, fallbacks=None, **kw):
        return self._next({**kw, "betas": betas, "fallbacks": fallbacks})

    def create(self, *, betas=None, fallbacks=None, **kw):
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


# ---------------------------------------------------------------- settings

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config" / "col4a1a2.toml"


def make_settings(root: Path | None = None, **over):
    """The real config, rooted at `root` (a tmp_path) so nothing touches data/.

    Keyword overrides replace individual settings, for testing that a setting
    actually reaches the code it governs.
    """
    import dataclasses

    from col4a1a2.config import load_settings

    st = load_settings(CONFIG, repo=root) if root is not None else load_settings(CONFIG)
    if root is not None:
        st.curated_dir.mkdir(parents=True, exist_ok=True)
    return dataclasses.replace(st, **over) if over else st


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def corpus_file(rel: str) -> Path:
    """A file from the real corpus under data/, or a skip on a fresh clone."""
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} is not present (corpus data is not in git)")
    return path


def cmd_args(st, snap=None, **over) -> argparse.Namespace:
    """Arguments for any `cmd_*`, with every flag at its default."""
    a = dict(settings=st, snapshot=str(snap) if snap is not None else None, pmid=None, limit=None,
             model=None, effort=None, count_only=False, budget_usd=None, no_llm_cache=False,
             all=False, tsv=None, json=False)
    a.update(over)
    return argparse.Namespace(**a)


def make_snapshot(st, name: str = "s1", *, papers=(), extractions=(), manifest: bool = True) -> Path:
    from col4a1a2 import jsonl, schemas

    snap = st.snapshot_root / name
    snap.mkdir(parents=True)
    if manifest:
        schemas.write_document(snap / "manifest.json", {"fetched_at": "2026-10-31T00:00:00+00:00"})
    jsonl.write(snap / "papers.jsonl", list(papers))
    # Extraction results are tracked, beside the ledger; each names its snapshot.
    if extractions:
        jsonl.write(st.extractions, [{**r, "snapshot": name} for r in extractions])
    return snap


# ---------------------------------------------------------------- row fixtures

def _configured_extract_model() -> str:
    return make_settings().extract_model


# What every model-derived row carries. The requested model is the configured
# one, so rows built here count as current for commands run on that config.
EXTRACT_MODEL = _configured_extract_model()
PROVENANCE = {"model": EXTRACT_MODEL, "requested_model": EXTRACT_MODEL, "effort": "high",
              "written_at": "2026-10-31T00:00:00+00:00"}


def make_provenance(model: str = "m", **over) -> dict:
    return {"model": model, "requested_model": model, "effort": "low",
            "written_at": "2026-10-31T00:00:00+00:00", **over}


def make_triage_row(pmid: str = "1", **over) -> dict:
    return {"pmid": pmid, "triage_prompt_version": "t1", **PROVENANCE, "read_full_text": True,
            "reason": "r", "usage": {"input": 1, "output": 1}, **over}


def make_paper_extraction(pmid: str = "1", **over):
    from col4a1a2.extract.schema import PaperExtraction, PaperType

    return PaperExtraction(**{"pmid": pmid, "paper_type": PaperType.case_report,
                              "input_was_abstract_only": False, "has_variant_level_data": False, **over})


#
# Built through the real Pydantic models rather than written out by hand, so a
# fixture cannot drift from the schema it is meant to represent.

def make_variant(**over) -> dict:
    from col4a1a2.extract.schema import Confidence, Gene, VariantEvidence

    v = VariantEvidence(
        gene=Gene.COL4A1, as_written="p.Gly1Arg", hgvs_c="c.1G>A", hgvs_p="p.Gly1Arg",
        variant_type="missense_glycine", n_probands=1, n_families=1,
        inh_de_novo_confirmed=0, inh_de_novo_assumed=0, inh_inherited=0, inh_unknown=1,
        same_family_as_prior_report=False,
        evidence_quotes=["a quote"], confidence=Confidence.high,
    )
    return {**v.model_dump(mode="json"), **over}


def make_extraction_row(pmid="1", variants=None, **over) -> dict:
    from col4a1a2.extract.prompt import PROMPT_VERSION

    ex = make_paper_extraction(pmid, has_variant_level_data=True)
    row = {
        "pmid": pmid, "pmcid": f"PMC{pmid}", **PROVENANCE, "snapshot": "s1", "prompt_version": PROMPT_VERSION,
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
    from col4a1a2.extract.challenge import CHALLENGE_PROMPT_VERSION

    row = {"pmid": pmid, "text_sha256": "h", "records_digest": "d",
           "challenge_prompt_version": CHALLENGE_PROMPT_VERSION, "extract_prompt_version": "v1",
           **PROVENANCE,
           "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0,
                     "effort": "medium"},
           "challenge": {"pmid": pmid, "duplicate_record_keys": [], "missed_variants": [],
                         "verdicts": [], "notes": ""},
           "report": {"pmid": pmid}}
    row.update(over)
    return row
