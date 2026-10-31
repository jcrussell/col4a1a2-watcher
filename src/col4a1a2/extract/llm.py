"""Every Claude call goes through `call`, which keeps the raw response.

The tables keep parsed results, which is enough to re-run grounding, the
derive checks and the review queue for free. What they cannot keep is
everything the parse throws away: thinking, stop details, the exact request
parameters, and above all the text of a response that failed to parse, which
left an error row holding only an exception name. So the raw response is
archived *before* parsing, one JSON document per call under
`data/responses/<YYYY-MM>/`.

The archive is also an exact-replay cache. A request is identified by the
sha256 of its canonical form -- model, system, messages, output schema,
effort, max_tokens, betas -- so an identical request is answered from disk
at no cost, and `refresh=True` makes the call anyway and archives the new
answer alongside the old. The request text itself is not stored: it is the
snapshot's text, recoverable by the hash in `request`.

The directory is gitignored for the same licence reason as `data/raw/`:
thinking can paraphrase article text at length. It lives outside
`data/cache/` so that eviction never deletes something we paid for.

Provenance comes from here too. Every row built from a `Reply` records the
model the API says served it, the model we asked for, the effort, and when.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from col4a1a2.schemas import RESPONSE_SUFFIX as SUFFIX

log = logging.getLogger("col4a1a2")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Reply:
    parsed: BaseModel | None
    stop_reason: str | None
    stop_details: Any
    model: str
    requested_model: str
    effort: str | None
    usage: dict
    written_at: str
    digest: str
    from_archive: bool

    def provenance(self) -> dict:
        """The four fields every model-derived row carries."""
        return {"model": self.model, "requested_model": self.requested_model,
                "effort": self.effort, "written_at": self.written_at}


@dataclass
class Policy:
    """How a command calls one model: which, how hard, and whether to reuse answers.

    `refresh` bypasses both the per-table cache and the response archive, so
    the call is made and its answer appended as the newest row for its key.
    """

    model: str
    effort: str | None
    archive: ResponseArchive | None = None
    refresh: bool = False

    def kwargs(self) -> dict:
        return {"model": self.model, "effort": self.effort,
                "archive": self.archive, "refresh": self.refresh}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def request_digest(kwargs: dict) -> str:
    blob = json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ResponseArchive:
    """`<root>/<YYYY-MM>/<digest>.<stamp>.response.json`; newest per digest wins."""

    def __init__(self, root: Path | None):
        self.root = root
        self._index: dict[str, Path] | None = None
        # Answers served from disk, so callers can leave them out of spend.
        self.hits = 0

    def _scan(self) -> dict[str, Path]:
        if self._index is None:
            self._index = {}
            if self.root is not None and self.root.exists():
                for p in sorted(self.root.glob(f"*/*{SUFFIX}")):
                    self._index[p.name.split(".", 1)[0]] = p
        return self._index

    def get(self, digest: str) -> dict | None:
        p = self._scan().get(digest)
        return json.loads(p.read_text(encoding="utf-8")) if p else None

    def put(self, record: dict) -> Path | None:
        if self.root is None:
            return None
        from col4a1a2 import schemas

        # Microseconds, fixed width: a refresh within the same second must not
        # overwrite the answer it is meant to sit beside, and names must sort
        # in time order for `_scan` to pick the newest.
        stamp = dt.datetime.fromisoformat(record["written_at"]).strftime("%Y%m%dT%H%M%S%f")
        path = self.root / record["written_at"][:7] / f"{record['digest']}.{stamp}{SUFFIX}"
        schemas.write_document(path, record)
        self._scan()[record["digest"]] = path
        return path


def _dump(resp: Any) -> dict:
    return resp.model_dump(mode="json", warnings=False)


def _usage(raw: dict) -> dict:
    u = raw.get("usage") or {}
    return {"input": u.get("input_tokens", 0) or 0,
            "output": u.get("output_tokens", 0) or 0,
            "cache_read": u.get("cache_read_input_tokens", 0) or 0,
            "cache_write": u.get("cache_creation_input_tokens", 0) or 0}


def parse(raw: dict, output_format: type[BaseModel]) -> BaseModel | None:
    """Validate the response's JSON text against `output_format`.

    None when there is no text to parse -- a refusal, or thinking that used the
    whole budget -- so callers decide from the stop reason. Raises pydantic's
    ValidationError when there is text and it does not fit, by which point the
    response is already archived.
    """
    if raw.get("stop_reason") in ("refusal", "max_tokens"):
        return None
    for block in raw.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            return output_format.model_validate_json(block["text"])
    return None


def _supports_fallbacks(client: Any, stream: bool) -> bool:
    fn = client.beta.messages.stream if stream else client.beta.messages.create
    params = inspect.signature(fn).parameters
    return "fallbacks" in params and "betas" in params


def call(client: Any, *, site: str, model: str, effort: str | None, max_tokens: int,
         system: list[dict], messages: list[dict], output_format: type[BaseModel],
         request: dict, archive: ResponseArchive | None = None, refresh: bool = False,
         stream: bool = False, fallbacks: bool = True) -> Reply:
    """One structured-output call, archived before it is parsed.

    Uses the raw endpoints with `output_config.format` rather than the SDK's
    `parse()`, which validates inside the call and so raises before a caller
    could keep the response. `anthropic.transform_schema` produces exactly the
    schema `parse()` would send (checked for all four output models), so the
    grammar measured against the live API is unchanged.

    `request` is a small summary stored in place of the request text (prompt
    version, input hash, pmid). `stream` is required for large `max_tokens`.
    """
    import anthropic

    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(output_format)}}
    if effort:
        output_config["effort"] = effort
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system,
                                  messages=messages, output_config=output_config)
    use_beta = fallbacks and _supports_fallbacks(client, stream)
    if use_beta:
        kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
    digest = request_digest(kwargs)

    if archive is not None and not refresh:
        rec = archive.get(digest)
        if rec is not None:
            archive.hits += 1
            raw = rec["response"]
            return Reply(parsed=parse(raw, output_format), stop_reason=raw.get("stop_reason"),
                         stop_details=raw.get("stop_details"), model=raw.get("model") or model,
                         requested_model=model, effort=effort, usage=_usage(raw),
                         written_at=rec["written_at"], digest=digest, from_archive=True)

    api = client.beta.messages if use_beta else client.messages
    if stream:
        with api.stream(**kwargs) as st:
            resp = st.get_final_message()
    else:
        resp = api.create(**kwargs)

    written_at = _now()
    raw = _dump(resp)
    if archive is not None:
        archive.put({"digest": digest, "written_at": written_at, "site": site,
                     "requested_model": model, "effort": effort, "max_tokens": max_tokens,
                     "betas": kwargs.get("betas") or [], "request": request, "response": raw})
    return Reply(parsed=parse(raw, output_format), stop_reason=raw.get("stop_reason"),
                 stop_details=raw.get("stop_details"), model=raw.get("model") or model,
                 requested_model=model, effort=effort, usage=_usage(raw),
                 written_at=written_at, digest=digest, from_archive=False)
