"""Every Claude call goes through `call`, which keeps the raw response.

The tables keep parsed results, which is enough to re-run grounding, the
derive checks and the review queue for free. What they cannot keep is
everything the parse throws away: thinking, stop details, the exact request
parameters, and above all the text of a response that fails to parse, of
which an error row holds only the exception name. So the raw response is
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

import contextlib
import datetime as dt
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from col4a1a2.digest import sha256_json
from col4a1a2.schemas import RESPONSE_SUFFIX as SUFFIX

log = logging.getLogger("col4a1a2")

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# A refusal that arrives before any output is billed only in these categories,
# per Anthropic's refusals page (as of September 2026; it says the set may change).
BILLED_REFUSAL_CATEGORIES = frozenset({"bio", "frontier_llm", "reasoning_extraction"})


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

    def usage_row(self) -> dict:
        """All four token counts, so rows and spend include cache writes."""
        return dict(self.usage)

    def require(self, exc: type[Exception]) -> BaseModel:
        """The parsed output, or `exc` saying why there is none.

        Refusal and max_tokens are checked before the missing-output case, so
        the reason survives instead of collapsing into "no output".
        """
        if self.stop_reason == "refusal":
            raise exc(f"refused: {self.stop_details}")
        if self.stop_reason == "max_tokens":
            raise exc("output hit max_tokens before a complete answer was produced")
        if self.parsed is None:
            raise exc(f"no parsed output (stop_reason={self.stop_reason})")
        return self.parsed


def output_schema(output_format: type[BaseModel]) -> dict:
    """The JSON schema the API receives for `output_format`: what the grammar budget binds.

    Identical to what the SDK's `parse()` would send, checked for all four
    output models.
    """
    import anthropic

    return anthropic.transform_schema(output_format)


def schema_shape(output_format: type[BaseModel]) -> dict:
    """The measures the structured-output grammar budget depends on.

    Measured: 45 properties accepted, 48 rejected, and cutting 1,300 bytes of
    description from the 48-property schema does not help -- the ceiling is
    structural, so property count is what binds.
    """
    s = output_schema(output_format)
    text = json.dumps(s)
    props = len(s.get("properties", {})) + sum(len(d.get("properties", {})) for d in s.get("$defs", {}).values())
    return {"properties": props, "anyOf": text.count("anyOf"), "objects": text.count('"object"'),
            "enums": text.count('"enum"'), "arrays": text.count('"array"'), "bytes": len(text)}


def system_block(text: str) -> list[dict]:
    """A system prompt marked for prompt caching, as every call site sends it."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


class SpendMeter:
    """What a run has spent, and whether it may spend more.

    Wrap each model call in `with meter.metered():`. The meter charges what the
    archive recorded as paid while the block ran, so a call billed and then
    rejected (a refusal, a truncation, output that does not parse) is charged
    like any other, and an answer replayed from disk, which records nothing,
    costs nothing.

    `price_for` maps a model to its prices. Each billed attempt is priced at the
    rates of the model that ran it, since a fallback can serve a turn on a model
    other than the one requested. An attempt on a model with no price makes the
    spend unknown, and a run with a budget then stops rather than spend blind.
    """

    def __init__(self, price_for, archive: ResponseArchive, budget_usd: float | None):
        self.price_for = price_for
        self.archive = archive
        self.budget_usd = budget_usd
        self.spent = 0.0
        self.unpriced: set[str] = set()

    def over_budget(self) -> bool:
        # None is no budget; 0 is a real one, allowing only answers already paid for.
        return self.budget_usd is not None and (self.spent >= self.budget_usd or bool(self.unpriced))

    @contextlib.contextmanager
    def metered(self):
        mark = len(self.archive.paid)
        try:
            yield
        finally:
            for attempts in self.archive.paid[mark:]:
                self.charge(attempts)

    def charge(self, attempts: list[tuple[str, dict]]) -> None:
        for model, usage in attempts:
            price = self.price_for(model)
            if price is None:
                if model not in self.unpriced:
                    log.warning("no price for %s; spend from here on is unknown", model)
                self.unpriced.add(model)
                continue
            self.spent += price.cost(**usage)


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
    return dt.datetime.now(dt.UTC).isoformat()


def request_digest(kwargs: dict) -> str:
    return sha256_json(kwargs)


class ResponseArchive:
    """`<root>/<YYYY-MM>/<digest>.<stamp>.response.json`; newest per digest wins.

    With no root nothing is stored or replayed, but paid calls are still
    counted, so a measurement that must call live is metered all the same.
    """

    def __init__(self, root: Path | None):
        self.root = root
        self._index: dict[str, Path] | None = None
        self.hits = 0
        # The billed attempts of every call this run paid for, in order.
        self.paid: list[list[tuple[str, dict]]] = []

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
        self.paid.append(billed_attempts(record["response"], record["requested_model"]))
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
    return _counts(raw.get("usage") or {})


def _counts(u: dict) -> dict:
    return {"input": u.get("input_tokens", 0) or 0,
            "output": u.get("output_tokens", 0) or 0,
            "cache_read": u.get("cache_read_input_tokens", 0) or 0,
            "cache_write": u.get("cache_creation_input_tokens", 0) or 0}


def billed_attempts(raw: dict, requested_model: str) -> list[tuple[str, dict]]:
    """Each attempt the API bills for this response, with the model whose prices apply.

    Top-level `usage` covers only the attempt that produced the message. With
    fallbacks on, `usage.iterations` lists every attempt: a model that declined
    is a `message` entry and the one that served the turn a `fallback_message`.
    Each is billed at its own model's rates, except a decline before any output,
    which is billed only in `BILLED_REFUSAL_CATEGORIES`. A declined hop's
    category is on the `fallback` block that hands over from it, in order; the
    last attempt's, if it declined too, is the response's `stop_details`.
    """
    served = raw.get("model") or requested_model
    its = (raw.get("usage") or {}).get("iterations") or []
    if not its:
        return [(served, _usage(raw))]
    handoffs = [((b.get("trigger") or {}).get("category"))
                for b in raw.get("content") or [] if b.get("type") == "fallback"]
    out = []
    for i, it in enumerate(its):
        model = it.get("model") or served
        counts = _counts(it)
        last = i == len(its) - 1
        if last:
            declined = raw.get("stop_reason") == "refusal"
            category = (raw.get("stop_details") or {}).get("category")
        else:
            declined = it.get("type") == "message"
            category = handoffs.pop(0) if handoffs else None
        if declined and not counts["output"] and category not in BILLED_REFUSAL_CATEGORIES:
            continue
        out.append((model, counts))
    return out


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
    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": output_schema(output_format)}}
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
