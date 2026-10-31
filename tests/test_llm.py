"""The single call path: archive before parsing, replay, refresh, provenance."""

import pytest
from pydantic import BaseModel, ValidationError

from col4a1a2 import schemas
from col4a1a2.extract.llm import ResponseArchive, call


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
