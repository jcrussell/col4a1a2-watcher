"""The shared cache of paid model answers."""

import json

import pytest

from col4a1a2.extract.challenge import ChallengeCache
from col4a1a2.extract.figures import FigureCache
from col4a1a2.extract.triage import TriageCache


def _row(cls, when, marker):
    base = {"model": "m", "requested_model": "m", "effort": "low", "written_at": when,
            "usage": {"input": 1, "output": 1}}
    if cls is TriageCache:
        return {**base, "pmid": "1", "triage_prompt_version": "t1", "read_full_text": True,
                "reason": marker}, ("1", "m")
    if cls is FigureCache:
        return {**base, "pmcid": "PMC1", "fig_id": "F1", "label": "Figure 1", "image_sha256": "h",
                "figure_prompt_version": "f1", "transcription": {"marker": marker}}, ("h", "m")
    from col4a1a2.extract.challenge import CHALLENGE_PROMPT_VERSION

    return {**base, "pmid": "1", "text_sha256": "t", "records_digest": "d",
            "challenge_prompt_version": CHALLENGE_PROMPT_VERSION, "extract_prompt_version": "v1",
            "report": {"marker": marker}, "challenge": {}}, ("t", "d", "m")


def _marker(row):
    return row.get("reason") or (row.get("transcription") or row.get("report"))["marker"]


@pytest.mark.parametrize("cls", [TriageCache, FigureCache, ChallengeCache])
def test_the_newest_written_at_wins_whatever_the_line_order(tmp_path, cls):
    """A union merge can put the older answer last, so picking by position would
    disagree with `check` and `review`, which pick by `written_at`."""
    path = tmp_path / "cache.jsonl"
    newer, key = _row(cls, "2026-11-01T00:00:00+00:00", "newer")
    older, _ = _row(cls, "2026-10-01T00:00:00+00:00", "older")
    path.write_text(json.dumps(newer) + "\n" + json.dumps(older) + "\n")
    assert _marker(cls(path).get(*key)) == "newer"


@pytest.mark.parametrize("cls", [TriageCache, FigureCache, ChallengeCache])
def test_a_second_model_does_not_see_the_first_s_answer(tmp_path, cls):
    path = tmp_path / "cache.jsonl"
    row, key = _row(cls, "2026-11-01T00:00:00+00:00", "x")
    path.write_text(json.dumps(row) + "\n")
    cache = cls(path)
    assert cache.get(*key) is not None
    assert cache.get(*key[:-1], "other-model") is None


@pytest.mark.parametrize("cls", [TriageCache, FigureCache, ChallengeCache])
def test_refresh_misses_on_purpose(tmp_path, cls):
    path = tmp_path / "cache.jsonl"
    row, key = _row(cls, "2026-11-01T00:00:00+00:00", "x")
    path.write_text(json.dumps(row) + "\n")
    cache = cls(path)
    assert cache.get(*key, refresh=True) is None and cache.hits == 0
