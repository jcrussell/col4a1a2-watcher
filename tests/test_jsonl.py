"""JSON Lines round-tripping that survives real article text.

Found on an 802-paper snapshot: one paper's text contained U+2028 LINE
SEPARATOR. json.dumps leaves it raw, because it is valid inside a JSON string,
but str.splitlines() treats it as a line break -- so the file held 802 rows by
newline and 803 by splitlines(), and every reader died with "Unterminated
string".
"""

import json

import pytest

from col4a1a2 import jsonl

# Everything str.splitlines() treats as a break and that we therefore escape.
BREAKERS = ["\u2028", "\u2029", "\u0085", "\u000b", "\u000c", "\u001c", "\u001d", "\u001e"]
# The subset json.dumps leaves raw, which is where the hazard actually lies.
# JSON requires escaping U+0000-U+001F, so the C0 controls are already safe;
# these three sit above that range and are not.
UNESCAPED_BY_JSON = ["\u2028", "\u2029", "\u0085"]



@pytest.mark.parametrize("ch", BREAKERS)
def test_round_trip_survives_every_unicode_line_breaker(tmp_path, ch):
    row = {"pmid": "1", "text": f"before{ch}after"}
    path = tmp_path / "x.jsonl"
    jsonl.write(path, [row, {"pmid": "2", "text": "plain"}])

    raw = path.read_text(encoding="utf-8")
    assert len(raw.split("\n")) - 1 == 2, "one row per newline"
    assert len(raw.splitlines()) == 2, "splitlines() must agree, or other tools break"

    back = jsonl.read(path)
    assert len(back) == 2
    assert back[0]["text"] == f"before{ch}after", "the character must survive, not be stripped"


@pytest.mark.parametrize("ch", UNESCAPED_BY_JSON)
def test_the_hazard_is_real_with_plain_json_dumps(ch):
    """Guards the premise: if json.dumps ever starts escaping these, this test
    fails and the helper becomes unnecessary. The C0 controls are excluded
    because JSON already requires them escaped."""
    line = json.dumps({"t": f"a{ch}b"}, ensure_ascii=False)
    assert len(line.splitlines()) == 2, f"{hex(ord(ch))} no longer splits; revisit jsonl.py"


def test_escapes_are_valid_json(tmp_path):
    path = tmp_path / "x.jsonl"
    jsonl.write(path, [{"t": "a b"}])
    # Parse with the stdlib directly, not our reader.
    assert json.loads(path.read_text(encoding="utf-8").strip())["t"] == "a b"


def test_blank_lines_and_missing_files_are_tolerated(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2}\n\n', encoding="utf-8")
    assert jsonl.read(path) == [{"a": 1}, {"a": 2}]
    assert jsonl.read(tmp_path / "absent.jsonl") == []


def test_read_is_strict_and_read_tolerant_is_not(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\nbroken\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        jsonl.read(path)
    assert jsonl.read_tolerant(path) == [{"a": 1}]


def test_append_creates_parents(tmp_path):
    path = tmp_path / "deep" / "x.jsonl"
    jsonl.append(path, {"a": 1})
    assert jsonl.read(path) == [{"a": 1}]


def test_the_real_snapshot_that_exposed_this_parses(tmp_path):
    from pathlib import Path

    snap = Path(__file__).resolve().parents[1] / "data/raw/2026-10-30-rest/papers.jsonl"
    if not snap.exists():
        pytest.skip("snapshot not present")
    rows = jsonl.read(snap)
    assert len(rows) == 802
    assert any(" " in (r.get("text") or "") for r in rows), \
        "this snapshot is the regression case; it should still contain the separator"


# ------------------------------------------------------------ crash safety

def test_a_failed_rewrite_leaves_the_old_file(tmp_path, monkeypatch):
    """`open("w")` truncates on open, so a crash mid-write used to leave half a
    tracked file. The rewrite now goes to a temp file and is renamed over."""
    import os

    p = tmp_path / "x.jsonl"
    jsonl.write(p, [{"a": 1}, {"a": 2}])
    before = p.read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        jsonl.write(p, [{"a": 3}])
    assert p.read_bytes() == before
    assert [q.name for q in tmp_path.iterdir()] == ["x.jsonl"], "the temp file must be cleaned up"


def test_append_refuses_to_glue_onto_a_torn_line(tmp_path):
    """A writer killed mid-line leaves a fragment with no newline. Appending
    after it would fuse the next row onto the fragment and lose both."""
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2, "b"')
    assert jsonl.has_torn_tail(p)
    with pytest.raises(jsonl.TornTail):
        jsonl.append(p, {"a": 3})
    assert p.read_text() == '{"a": 1}\n{"a": 2, "b"'


def test_every_appender_validates(tmp_path):
    """Five writers used to append with `open("a")` directly, bypassing the
    schema check that constraint 1 promises. Each must now refuse a bad row."""
    from pydantic import ValidationError

    from col4a1a2.discovery import DiscoveryLog, Slice
    from col4a1a2.extract.challenge import ChallengeCache
    from col4a1a2.extract.figures import FigureCache
    from col4a1a2.extract.triage import TriageCache, TriageDecision

    bad = {"model": "m", "requested_model": "m", "effort": "low"}  # no written_at
    with pytest.raises(ValidationError):
        TriageCache(tmp_path / "triage.jsonl").put(
            "1", TriageDecision(read_full_text=True, reason="r"), {"input": 1, "output": 1}, bad)
    with pytest.raises(ValidationError):
        FigureCache(tmp_path / "figures.jsonl").put(
            pmcid="PMC1", fig=type("F", (), {"fig_id": "F1", "label": "Figure 1"})(),
            image_sha256="h", transcription=type("T", (), {"model_dump": lambda self: {}})(),
            usage={"input": 1, "output": 1}, provenance=bad)
    with pytest.raises(ValidationError):
        ChallengeCache(tmp_path / "challenges.jsonl").put(
            pmid="1", text_sha256="t", records_digest="d",
            challenge=type("C", (), {"model_dump": lambda self, **k: {}})(),
            report=type("R", (), {"as_dict": lambda self: {}})(),
            usage={"input": 1, "output": 1, "cache_read": 0, "effort": "low"}, provenance=bad)
    with pytest.raises(ValidationError):
        jsonl.append(tmp_path / "extractions.jsonl", {"pmid": "1", "prompt_version": "v1",
                                                      "text_sha256": "t"})  # no requested_model
    log = DiscoveryLog(tmp_path / "discovery.jsonl")
    with pytest.raises(ValidationError):
        log.record(query="q", sl=Slice(date_from="2020-01-01", date_to="2021-01-01"),
                   hits_available=None, hits_fetched="many", complete=True)
    assert not any(tmp_path.iterdir()), "nothing invalid may reach disk"


def test_a_second_writer_is_refused_and_told_who_holds_the_lock(tmp_path):
    import os

    from col4a1a2.cli import LockHeld, hold_lock

    lock = tmp_path / "data" / ".lock"
    first = hold_lock(lock)
    with pytest.raises(LockHeld, match=f"pid {os.getpid()}"):
        hold_lock(lock)
    first.close()
    hold_lock(lock).close()  # released when the holder goes away
