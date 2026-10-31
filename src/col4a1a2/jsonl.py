"""JSON Lines reading and writing that survives real article text.

Found the hard way on a 802-paper snapshot: one paper's full text contained
U+2028 LINE SEPARATOR. `json.dumps` does not escape it, because it is valid
inside a JSON string, but `str.splitlines()` treats it as a line break -- so the
file had 802 rows by newline and 803 by `splitlines()`, and the reader died with
"Unterminated string". The same applies to U+2029, U+0085, and the vertical tab
and form feed.

Two defences, because the files are read by other tools too:

- **Writing escapes the separators**, so the line really is one line to any
  reader. The escapes are valid JSON and decode back to the original character,
  so nothing is lost.
- **Reading splits on "\\n" only**, never `splitlines()`.

Writers also validate. Every row goes through the schema registered for its
filename in `schemas.py`, so a field name that no reader expects raises here
rather than landing on disk and disappearing into a fold. `schemas` is imported
inside the functions: it imports the extraction and challenge models, which
import this module, and a top-level import would close that cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

# Characters that are legal raw inside a JSON string but that Python's
# str.splitlines() treats as line boundaries.
_LINE_BREAKERS = {
    " ": "\\u2028",
    " ": "\\u2029",
    "\u0085": "\\u0085",
    "\u000b": "\\u000b",
    "\u000c": "\\u000c",
    "\u001c": "\\u001c",
    "\u001d": "\\u001d",
    "\u001e": "\\u001e",
}


def _validate(path: Path, row: Any) -> None:
    from col4a1a2 import schemas

    schemas.validate(path, row)


def dumps(obj: Any) -> str:
    """One JSON line, guaranteed free of anything a reader may treat as a break."""
    s = json.dumps(obj, ensure_ascii=False)
    for ch, esc in _LINE_BREAKERS.items():
        if ch in s:
            s = s.replace(ch, esc)
    return s


def iter_lines(path: Path) -> Iterator[dict]:
    """Yield each row, splitting on newline only and skipping blank lines."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            yield json.loads(line)


def read(path: Path) -> list[dict]:
    return list(iter_lines(path))


def read_tolerant(path: Path, *, what: str = "file") -> list[dict]:
    """Like `read`, but skips malformed lines rather than raising.

    For tracked append-only logs, which can be hand-edited or badly
    merge-resolved: one bad line must not break every command that reads them.
    """
    import logging

    log = logging.getLogger("col4a1a2")
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("%s: skipping malformed line in %s", what, path)
    return out


def write(path: Path, rows: Iterable[Any], *, validate: bool = True) -> int:
    """Write every row, validating each against the file's schema first.

    Validation happens before the file is opened, so a bad row cannot truncate
    a good file: `open("w")` truncates on open, and raising halfway through
    would leave a partial file where a whole one was.
    """
    rows = list(rows)
    if validate:
        for r in rows:
            _validate(path, r)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(dumps(r) + "\n")
            n += 1
    return n


def append(path: Path, row: Any, *, validate: bool = True) -> None:
    if validate:
        _validate(path, row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps(row) + "\n")
