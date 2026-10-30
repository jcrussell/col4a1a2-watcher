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
