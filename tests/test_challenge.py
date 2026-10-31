"""Adversarial review: verifying the reviewer before believing it."""

import json

import pytest

from col4a1a2.extract.challenge import (
    CHALLENGE_PROMPT_VERSION,
    ChallengeCache,
    ChallengeFailed,
    PaperChallenge,
    RecordVerdict,
    Verdict,
    challenge_paper,
    check,
    effort_for,
    record_key,
    records_digest,
    slim_records,
)

from conftest import make_variant, make_provenance, make_paper_extraction

TEXT = ("The proband carried c.1537G>A. "
        "Only one family was studied. "
        "We also observed c.4493C>T in an unrelated patient.")


def _variant(as_written="c.1537G>A", **kw):
    # Through the schema, so a fixture cannot drift from the record it stands for.
    return make_variant(**{"as_written": as_written, "hgvs_c": as_written, "hgvs_p": "",
                           "variant_type": "other", "inh_inherited": 1, "inh_unknown": 0,
                           "evidence_quotes": ["The proband carried c.1537G>A."], **kw})


def test_record_key_is_stable_and_not_a_list_position():
    """A list index handed to a human can point into a record list that
    latest_extractions has since superseded."""
    v = _variant()
    assert record_key("1", 0, v) == record_key("1", 0, dict(v))
    assert record_key("1", 0, v) != record_key("1", 1, v)
    assert record_key("1", 0, v) != record_key("1", 0, _variant(n_probands=9))


def test_slim_records_attach_provenance_rather_than_asking_for_it():
    recs = slim_records("1", [_variant(), _variant("c.9A>T")], figure_sourced=["c.9A>T"])
    assert "provenance_note" not in recs[0]
    assert "figure" in recs[1]["provenance_note"]
    # The free-text phenotype prose is deliberately absent; it halves the payload.
    assert "phenotype_details" not in recs[0] and "functional_evidence" not in recs[0]


def test_effort_scales_with_record_count():
    assert effort_for(1) == "medium"
    assert effort_for(8) == "high"
    assert effort_for(38) == "xhigh"


def _ch(pmid="1", verdicts=(), missed=(), dups=()):
    return PaperChallenge(pmid=pmid, verdicts=list(verdicts),
                          missed_variants=list(missed), duplicate_record_keys=list(dups))


def test_a_grounded_refutation_is_kept_and_an_ungrounded_one_is_fabrication():
    recs = slim_records("1", [_variant()], [])
    key = recs[0]["record_key"]
    good = _ch(verdicts=[RecordVerdict(record_key=key, verdict=Verdict.disputed,
                                       problem="only one family", refuting_quote="Only one family was studied.")])
    rep = check(good, recs, TEXT)
    assert rep.disputed == [key] and rep.fabricated == []
    assert rep.fabrication_rate == 0.0

    bad = _ch(verdicts=[RecordVerdict(record_key=key, verdict=Verdict.disputed,
                                      problem="invented", refuting_quote="Twelve families were studied.")])
    rep = check(bad, recs, TEXT)
    assert rep.disputed == [] and len(rep.fabricated) == 1
    assert rep.fabrication_rate == 1.0


def test_an_incomplete_verdict_list_is_detected():
    """A reviewer handed 38 records can return five and still satisfy the
    schema, which would store a partial audit as a complete one."""
    recs = slim_records("1", [_variant(), _variant("c.9A>T")], [])
    one = _ch(verdicts=[RecordVerdict(record_key=recs[0]["record_key"], verdict=Verdict.upheld)])
    assert check(one, recs, TEXT).incomplete is True
    both = _ch(verdicts=[RecordVerdict(record_key=r["record_key"], verdict=Verdict.upheld) for r in recs])
    assert check(both, recs, TEXT).incomplete is False


def test_duplicate_verdicts_for_one_record_are_incomplete():
    recs = slim_records("1", [_variant(), _variant("c.9A>T")], [])
    k = recs[0]["record_key"]
    twice = _ch(verdicts=[RecordVerdict(record_key=k, verdict=Verdict.upheld),
                          RecordVerdict(record_key=k, verdict=Verdict.upheld)])
    assert check(twice, recs, TEXT).incomplete is True


def test_missed_variants_are_split_into_grounded_and_invented():
    """Grounding this is what makes extraction recall computable at all."""
    recs = slim_records("1", [_variant()], [])
    ch = _ch(verdicts=[RecordVerdict(record_key=recs[0]["record_key"], verdict=Verdict.upheld)],
             missed=["c.4493C>T", "c.9999G>A"])
    rep = check(ch, recs, TEXT)
    assert rep.missed_grounded == ["c.4493C>T"]
    assert rep.missed_invented == ["c.9999G>A"]


def test_verdicts_for_unknown_keys_are_ignored():
    recs = slim_records("1", [_variant()], [])
    ch = _ch(verdicts=[RecordVerdict(record_key=recs[0]["record_key"], verdict=Verdict.upheld),
                       RecordVerdict(record_key="bogus", verdict=Verdict.disputed,
                                     refuting_quote="Only one family was studied.")])
    rep = check(ch, recs, TEXT)
    assert rep.upheld == [recs[0]["record_key"]] and rep.disputed == []


def test_cache_keys_on_both_text_and_records(tmp_path):
    """Both halves are load-bearing: the reviewer's quotes are grounded against
    the paper text, so a verdict made against pre-supplement text must not be
    served for post-supplement text."""
    recs = slim_records("1", [_variant()], [])
    ch = _ch(verdicts=[RecordVerdict(record_key=recs[0]["record_key"], verdict=Verdict.upheld)])
    rep = check(ch, recs, TEXT)
    c = ChallengeCache(tmp_path / "c.jsonl")
    d = records_digest(recs)
    assert c.get("textA", d, "m") is None
    c.put(pmid="1", text_sha256="textA", records_digest=d,
          challenge=ch, report=rep, usage={"input": 1, "output": 2, "cache_read": 0, "effort": "low"},
          provenance=make_provenance())

    again = ChallengeCache(tmp_path / "c.jsonl")
    assert again.get("textA", d, "m") is not None
    assert again.get("textB", d, "m") is None, "different text must miss"
    assert again.get("textA", "otherdigest", "m") is None, "different records must miss"


def test_cache_misses_on_a_bumped_challenge_version(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps({
        "pmid": "1", "text_sha256": "t", "records_digest": "d",
        "challenge_prompt_version": "c0", "requested_model": "m", "report": {}, "challenge": {},
    }) + "\n")
    assert ChallengeCache(path).get("t", "d", "m") is None
    path.write_text(json.dumps({
        "pmid": "1", "text_sha256": "t", "records_digest": "d",
        "challenge_prompt_version": CHALLENGE_PROMPT_VERSION, "requested_model": "m",
        "report": {}, "challenge": {},
    }) + "\n")
    assert ChallengeCache(path).get("t", "d", "m") is not None


def test_malformed_cache_line_is_skipped(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text("not json\n")
    assert ChallengeCache(path).entries == {}


@pytest.mark.parametrize("stop,match", [("refusal", "refused"), ("max_tokens", "max_tokens")])
def test_challenge_fails_closed(fake_anthropic, fake_response, stop, match):
    """A failed challenge must never be recorded as 'nothing to refute'."""
    client = fake_anthropic(fake_response(parsed_output=None, stop_reason=stop))
    with pytest.raises(ChallengeFailed, match=match):
        challenge_paper(client, pmid="1", source_text=TEXT, records=[], model="m")


def test_challenge_rejects_a_missing_parsed_output(fake_anthropic, fake_response):
    client = fake_anthropic(fake_response(parsed_output=None, stop_reason="end_turn"))
    with pytest.raises(ChallengeFailed, match="no parsed output"):
        challenge_paper(client, pmid="1", source_text=TEXT, records=[], model="m")


def test_challenge_sends_the_records_and_the_fallback_beta(fake_anthropic):
    recs = slim_records("1", [_variant()], [])
    ch = _ch(verdicts=[RecordVerdict(record_key=recs[0]["record_key"], verdict=Verdict.upheld)])
    client = fake_anthropic(default_output=ch)
    out, usage, prov = challenge_paper(client, pmid="1", source_text=TEXT, records=recs, model="m")
    assert out == ch
    assert prov["requested_model"] == "m" and prov["effort"] == "medium"
    sent = client.calls[0]
    assert sent["betas"] and sent["fallbacks"] == "default"
    assert sent["output_config"]["effort"] == "medium"
    assert recs[0]["record_key"] in sent["messages"][0]["content"]
    assert "PAPER TEXT START" in sent["messages"][0]["content"]
    assert "effort" not in usage, "effort is provenance, not usage"
    assert set(usage) == {"input", "output", "cache_read", "cache_write"}


def test_the_fake_client_exercises_the_beta_branch(fake_anthropic):
    """llm.py inspects create() and stream() for `betas` and `fallbacks`. A
    fake declared `def create(self, **kw)` would silently exercise only the
    stable branch forever."""
    from col4a1a2.extract.llm import _supports_fallbacks

    assert _supports_fallbacks(fake_anthropic(), stream=True) is True
    assert _supports_fallbacks(fake_anthropic(), stream=False) is True


def test_extract_paper_is_now_covered_at_all(fake_anthropic, fake_response):
    from col4a1a2.extract.client import ExtractionRefused, extract_paper
    from col4a1a2.extract.prompt import PaperInput

    p = PaperInput(pmid="1", pmcid=None, title="T", text="TITLE: T", abstract_only=False)
    ex = make_paper_extraction()
    res = extract_paper(fake_anthropic(default_output=ex), p, model="m", effort="high")
    assert res.extraction == ex and res.reply.stop_reason == "end_turn"
    assert res.reply.requested_model == "m" and res.reply.written_at

    client = fake_anthropic(fake_response(parsed_output=None, stop_reason="refusal"))
    with pytest.raises(ExtractionRefused):
        extract_paper(client, p, model="m", effort="high")
