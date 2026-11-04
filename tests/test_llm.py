"""The single call path: archive before parsing, replay, refresh, provenance."""

import pytest
from pydantic import BaseModel, ValidationError

from col4a1a2 import schemas
from col4a1a2.config import Prices
from col4a1a2.extract.llm import ResponseArchive, SpendMeter, billed_attempts, call


class Out(BaseModel):
    answer: str


def _call(client, archive, refresh=False):
    return call(client, site="triage", model="m", effort="low", max_tokens=100,
                system=[{"type": "text", "text": "S"}],
                messages=[{"role": "user", "content": "U"}],
                output_format=Out, request={"prompt_version": "t1"},
                archive=archive, refresh=refresh)


def test_a_response_is_archived_with_its_provenance(fake_anthropic, tmp_path):
    archive = ResponseArchive(tmp_path / "responses")
    reply = _call(fake_anthropic(default_output=Out(answer="yes")), archive)
    assert reply.parsed == Out(answer="yes") and not reply.from_archive
    assert reply.provenance() == {"model": "fake-model", "requested_model": "m",
                                  "effort": "low", "written_at": reply.written_at}
    files = list((tmp_path / "responses").glob("*/*.response.json"))
    assert len(files) == 1
    assert schemas.model_for(files[0]) is schemas.ApiResponse


def test_a_response_that_fails_validation_is_still_archived(fake_anthropic, fake_response, tmp_path):
    """The point of archiving before parsing: when parsing raises, the raw
    answer is already kept, not just an error row with an exception name."""
    archive = ResponseArchive(tmp_path / "responses")
    client = fake_anthropic(fake_response(parsed_output='{"wrong": 1}'))
    with pytest.raises(ValidationError):
        _call(client, archive)
    files = list((tmp_path / "responses").glob("*/*.response.json"))
    assert len(files) == 1
    rec = schemas.ApiResponse.model_validate_json(files[0].read_text())
    assert rec.response.model_dump()["content"][0]["text"] == '{"wrong": 1}'


def test_an_identical_request_is_replayed_without_a_call(fake_anthropic, tmp_path):
    archive = ResponseArchive(tmp_path / "responses")
    client = fake_anthropic(default_output=Out(answer="yes"))
    first = _call(client, archive)
    again = _call(client, ResponseArchive(tmp_path / "responses"))
    assert len(client.calls) == 1, "the second answer must come from disk"
    assert again.from_archive and again.parsed == first.parsed
    assert again.written_at == first.written_at, "a replay keeps the original timestamp"


def test_refresh_calls_anyway_and_keeps_both_answers(fake_anthropic, fake_response, tmp_path):
    archive = ResponseArchive(tmp_path / "responses")
    client = fake_anthropic(fake_response(parsed_output=Out(answer="first")),
                            fake_response(parsed_output=Out(answer="second")))
    _call(client, archive)
    fresh = _call(client, archive, refresh=True)
    assert len(client.calls) == 2 and fresh.parsed.answer == "second"
    assert len(list((tmp_path / "responses").glob("*/*.response.json"))) == 2
    # The newest answer is what a later replay serves.
    assert _call(client, ResponseArchive(tmp_path / "responses")).parsed.answer == "second"


def test_a_different_model_is_a_different_request(fake_anthropic, tmp_path):
    archive = ResponseArchive(tmp_path / "responses")
    client = fake_anthropic(default_output=Out(answer="yes"))
    _call(client, archive)
    call(client, site="triage", model="other", effort="low", max_tokens=100,
         system=[{"type": "text", "text": "S"}], messages=[{"role": "user", "content": "U"}],
         output_format=Out, request={}, archive=archive)
    assert len(client.calls) == 2


def test_no_archive_means_no_files(fake_anthropic, tmp_path):
    _call(fake_anthropic(default_output=Out(answer="yes")), ResponseArchive(None))
    assert not any(tmp_path.iterdir())


def _it(type_, model, input_=0, output=0, cache_read=0):
    return {"type": type_, "model": model, "input_tokens": input_, "output_tokens": output,
            "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 0}


def _fallback(category, *, stop_reason="end_turn", stop_details=None, declined_output=0):
    """The documented shape: a decline on Opus 5.5 served by Opus 5."""
    return {"model": "claude-opus-5", "stop_reason": stop_reason, "stop_details": stop_details,
            "content": [{"type": "fallback", "from": {"model": "claude-opus-5-5"},
                         "to": {"model": "claude-opus-5"},
                         "trigger": {"type": "refusal", "category": category}},
                        {"type": "text", "text": "{}"}],
            "usage": {"input_tokens": 400, "output_tokens": 200, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0,
                      "iterations": [_it("message", "claude-opus-5-5", 500, declined_output),
                                     _it("fallback_message", "claude-opus-5", 400, 200)]}}


def test_a_plain_reply_is_one_attempt_on_the_served_model():
    raw = {"model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [],
           "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 5,
                     "cache_creation_input_tokens": 0,
                     "iterations": [_it("message", None, 7, 3, 5)]}}
    assert billed_attempts(raw, "claude-opus-5-5") == [
        ("claude-opus-5-5", {"input": 7, "output": 3, "cache_read": 5, "cache_write": 0})]


@pytest.mark.parametrize("category,declined_output,billed", [
    ("cyber", 0, False),               # declined before output, unbilled category
    (None, 0, False),
    ("bio", 0, True),                  # declined before output, billed category
    ("cyber", 50, True),               # declined mid-stream: billed whatever the category
])
def test_a_fallback_bills_each_attempt_at_its_own_model(category, declined_output, billed):
    attempts = billed_attempts(_fallback(category, declined_output=declined_output), "claude-opus-5-5")
    assert attempts[-1] == ("claude-opus-5", {"input": 400, "output": 200, "cache_read": 0, "cache_write": 0}), \
        "top-level usage alone misses the declined attempt and prices the rest as the requested model"
    assert [m for m, _ in attempts[:-1]] == (["claude-opus-5-5"] if billed else [])


def test_a_final_refusal_takes_its_category_from_stop_details():
    raw = {"model": "claude-opus-5-5", "stop_reason": "refusal", "content": [],
           "stop_details": {"type": "refusal", "category": "reasoning_extraction"},
           "usage": {"input_tokens": 9, "output_tokens": 0, "iterations": [_it("message", None, 9)]}}
    assert billed_attempts(raw, "claude-opus-5-5") == [
        ("claude-opus-5-5", {"input": 9, "output": 0, "cache_read": 0, "cache_write": 0})]
    raw["stop_details"]["category"] = "cyber"
    assert billed_attempts(raw, "claude-opus-5-5") == []


def test_the_meter_prices_each_attempt_and_stops_when_it_cannot():
    prices = {"a": Prices(input=1.0, output=10.0), "b": Prices(input=2.0, output=20.0)}
    meter = SpendMeter(prices.get, ResponseArchive(None), budget_usd=100.0)
    meter.charge([("a", {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}),
                  ("b", {"input": 0, "output": 1_000_000, "cache_read": 0, "cache_write": 0})])
    assert meter.spent == pytest.approx(21.0) and not meter.over_budget()
    meter.charge([("unknown", {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0})])
    assert meter.unpriced == {"unknown"} and meter.over_budget(), \
        "an unpriced model makes the spend unknown, which must not read as $0"


def test_a_replayed_answer_costs_nothing(fake_anthropic, tmp_path):
    archive = ResponseArchive(tmp_path)
    meter = SpendMeter(lambda m: Prices(input=1.0, output=1.0), archive, None)
    client = fake_anthropic()
    with meter.metered():
        _call(client, archive)
    paid = meter.spent
    assert paid > 0
    with meter.metered():
        reply = _call(client, archive)
    assert reply.from_archive and meter.spent == paid


@pytest.mark.parametrize("stop_reason", ["max_tokens", "end_turn"])
def test_a_billed_call_that_fails_is_charged(fake_anthropic, fake_response, stop_reason):
    """A truncation is billed, and so is output that does not parse; both raise
    before the caller sees a reply."""
    archive = ResponseArchive(None)
    meter = SpendMeter(lambda m: Prices(input=1.0, output=1.0), archive, None)
    client = fake_anthropic(fake_response(parsed_output="not json", stop_reason=stop_reason))
    with pytest.raises((RuntimeError, ValidationError)), meter.metered():
        _call(client, archive).require(RuntimeError)
    assert meter.spent > 0


def test_a_zero_budget_allows_nothing_new():
    assert SpendMeter(lambda m: None, ResponseArchive(None), budget_usd=0.0).over_budget()
    assert not SpendMeter(lambda m: None, ResponseArchive(None), budget_usd=None).over_budget()
