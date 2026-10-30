"""Synchronous extraction of one paper with the Claude API (structured output)."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass

import anthropic

from col4a1.extract.prompt import PROMPT_VERSION, PaperInput, system_prompt, user_content
from col4a1.extract.schema import SCHEMA_VERSION, PaperExtraction

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
# A variant-dense cohort paper can need far more than the original 16k: 39
# variants at a few hundred output tokens each straddles that cap, and the cap
# raises ExtractionTruncated rather than degrading. Large max_tokens needs
# streaming to avoid request timeouts, so extract_paper streams.
MAX_TOKENS = 64000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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


def _system_blocks() -> list[dict]:
    return [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]


def _messages(p: PaperInput) -> list[dict]:
    return [{"role": "user", "content": user_content(p)}]


def count_tokens(client: anthropic.Anthropic, p: PaperInput, model: str = DEFAULT_MODEL) -> int:
    r = client.messages.count_tokens(model=model, system=_system_blocks(), messages=_messages(p))
    return int(r.input_tokens)


def _beta_parse_supports_fallbacks(client: anthropic.Anthropic) -> bool:
    params = inspect.signature(client.beta.messages.stream).parameters
    return "fallbacks" in params and "betas" in params


def extract_paper(
    client: anthropic.Anthropic,
    p: PaperInput,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> ExtractionResult:
    """Run one paper through Claude and return the validated PaperExtraction.

    Uses the beta parse endpoint with server-side refusal fallbacks when the
    installed SDK exposes them; otherwise the stable parse endpoint.
    """
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=_system_blocks(),
        messages=_messages(p),
        output_format=PaperExtraction,
        output_config={"effort": effort},
    )
    # Streaming is mandatory at this max_tokens: the SDK refuses a non-streaming
    # request that could exceed its ten-minute ceiling. get_final_message()
    # returns the same parsed object the non-streaming helper would.
    if _beta_parse_supports_fallbacks(client):
        with client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs) as st:
            resp = st.get_final_message()
    else:
        with client.messages.stream(**kwargs) as st:
            resp = st.get_final_message()

    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        details = getattr(resp, "stop_details", None)
        raise ExtractionRefused(f"refused: {details}")
    if stop == "max_tokens":
        raise ExtractionTruncated("output hit max_tokens; raise MAX_TOKENS or split the paper")

    parsed = resp.parsed_output
    if parsed is None:
        raise RuntimeError("no parsed output returned")
    usage = resp.usage
    return ExtractionResult(
        extraction=parsed,
        model=getattr(resp, "model", model),
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        stop_reason=stop,
        raw_json=parsed.model_dump_json(),
    )
