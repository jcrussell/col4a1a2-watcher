"""Synchronous extraction of one paper with the Claude API (structured output)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic

from col4a1a2.digest import sha256_text
from col4a1a2.extract.llm import Reply, call, system_block
from col4a1a2.extract.prompt import PROMPT_VERSION, PaperInput, system_prompt, user_content
from col4a1a2.extract.schema import SCHEMA_VERSION, PaperExtraction

log = logging.getLogger(__name__)

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
    reply: Reply
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION

    @property
    def raw_json(self) -> str:
        return self.extraction.model_dump_json()


def _system_blocks() -> list[dict]:
    return system_block(system_prompt())


def _messages(p: PaperInput) -> list[dict]:
    return [{"role": "user", "content": user_content(p)}]


def count_tokens(client: anthropic.Anthropic, p: PaperInput, model: str) -> int:
    r = client.messages.count_tokens(model=model, system=_system_blocks(), messages=_messages(p))
    return int(r.input_tokens)


def extract_paper(
    client: anthropic.Anthropic,
    p: PaperInput,
    *,
    model: str,
    effort: str,
    text_sha256: str | None = None,
    archive=None,
    refresh: bool = False,
) -> ExtractionResult:
    """Run one paper through Claude and return the validated PaperExtraction.

    Streams, because the SDK refuses a non-streaming request at this
    max_tokens, and asks for server-side refusal fallbacks where the installed
    SDK supports them. The raw response is archived before it is validated, so
    a response that fails validation can still be inspected.
    """
    reply = call(
        client, site="extract", model=model, effort=effort, max_tokens=MAX_TOKENS,
        system=_system_blocks(), messages=_messages(p), output_format=PaperExtraction,
        stream=True, archive=archive, refresh=refresh,
        request={"prompt_version": PROMPT_VERSION, "pmid": p.pmid,
                 "input_sha256": text_sha256 or sha256_text(p.text)},
    )
    if reply.stop_reason == "refusal":
        raise ExtractionRefused(f"refused: {reply.stop_details}")
    if reply.stop_reason == "max_tokens":
        raise ExtractionTruncated("output hit max_tokens; raise MAX_TOKENS or split the paper")
    return ExtractionResult(extraction=reply.require(RuntimeError), reply=reply)
