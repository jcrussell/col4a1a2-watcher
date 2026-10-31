"""Synchronous extraction of one paper with the Claude API (structured output)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic

from col4a1a2.extract.prompt import PROMPT_VERSION, PaperInput, system_prompt, user_content
from col4a1a2.extract.schema import SCHEMA_VERSION, PaperExtraction

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
# A variant-dense cohort paper can need far more than the original 16k: 39
# variants at a few hundred output tokens each straddles that cap, and the cap
# raises ExtractionTruncated rather than degrading. Large max_tokens needs
# streaming to avoid request timeouts, so extract_paper streams.
MAX_TOKENS = 64000


class ExtractionRefused(RuntimeError):
    pass


class ExtractionTruncated(RuntimeError):
    pass


@dataclass
class ExtractionResult:
    extraction: PaperExtraction
    model: str
    prompt_version: str
    schema_version: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    stop_reason: str | None
    raw_json: str
    requested_model: str
    effort: str | None
    written_at: str

    def provenance(self) -> dict:
        return {"model": self.model, "requested_model": self.requested_model,
                "effort": self.effort, "written_at": self.written_at}


def _system_blocks() -> list[dict]:
    return [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]


def _messages(p: PaperInput) -> list[dict]:
    return [{"role": "user", "content": user_content(p)}]


def count_tokens(client: anthropic.Anthropic, p: PaperInput, model: str = DEFAULT_MODEL) -> int:
    r = client.messages.count_tokens(model=model, system=_system_blocks(), messages=_messages(p))
    return int(r.input_tokens)


def extract_paper(
    client: anthropic.Anthropic,
    p: PaperInput,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    archive=None,
    refresh: bool = False,
) -> ExtractionResult:
    """Run one paper through Claude and return the validated PaperExtraction.

    Streams, because the SDK refuses a non-streaming request at this
    max_tokens, and asks for server-side refusal fallbacks where the installed
    SDK supports them. The raw response is archived before it is validated, so
    a response that fails validation can still be inspected.
    """
    import hashlib

    from col4a1a2.extract.llm import call

    reply = call(
        client, site="extract", model=model, effort=effort, max_tokens=MAX_TOKENS,
        system=_system_blocks(), messages=_messages(p), output_format=PaperExtraction,
        stream=True, archive=archive, refresh=refresh,
        request={"prompt_version": PROMPT_VERSION, "pmid": p.pmid,
                 "input_sha256": hashlib.sha256(p.text.encode("utf-8")).hexdigest()},
    )
    if reply.stop_reason == "refusal":
        raise ExtractionRefused(f"refused: {reply.stop_details}")
    if reply.stop_reason == "max_tokens":
        raise ExtractionTruncated("output hit max_tokens; raise MAX_TOKENS or split the paper")
    parsed = reply.parsed
    if parsed is None:
        raise RuntimeError("no parsed output returned")
    return ExtractionResult(
        extraction=parsed,
        model=reply.model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        input_tokens=reply.usage["input"],
        output_tokens=reply.usage["output"],
        cache_read_tokens=reply.usage["cache_read"],
        cache_write_tokens=reply.usage["cache_write"],
        stop_reason=reply.stop_reason,
        raw_json=parsed.model_dump_json(),
        requested_model=reply.requested_model,
        effort=reply.effort,
        written_at=reply.written_at,
    )
