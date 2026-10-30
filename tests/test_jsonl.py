"""JSON Lines round-tripping that survives real article text.

Found on an 802-paper snapshot: one paper's text contained U+2028 LINE
SEPARATOR. json.dumps leaves it raw, because it is valid inside a JSON string,
but str.splitlines() treats it as a line break -- so the file held 802 rows by
newline and 803 by splitlines(), and every reader died with "Unterminated
string".
"""

import json

import pytest

from col4a1 import jsonl

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
