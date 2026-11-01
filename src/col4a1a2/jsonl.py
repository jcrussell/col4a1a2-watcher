"""JSON Lines reading and writing that survives real article text.

Article text can contain U+2028 LINE SEPARATOR. `json.dumps` does not escape
it, because it is valid inside a JSON string, but `str.splitlines()` treats it
as a line break, so a reader splitting that way sees one row as two and fails
with "Unterminated string". The same applies to U+2029, U+0085, and the
vertical tab and form feed.

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
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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


def loads_lines(text: str, *, tolerant: bool = False, what: str = "file",
                where: object = None) -> list[dict]:
    """Parse JSON Lines text, splitting on newline only and skipping blank lines.

    Strict by default. `tolerant` skips malformed lines with a warning instead,
    for tracked logs that a hand edit or a bad merge can damage: one bad line
    must not break every command that reads them.
    """
    out: list[dict] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if not tolerant:
                raise
            import logging

            logging.getLogger("col4a1a2").warning("%s: skipping malformed line in %s", what, where)
    return out


def read(path: Path) -> list[dict]:
    return loads_lines(path.read_text(encoding="utf-8")) if path.exists() else []


def read_tolerant(path: Path, *, what: str = "file") -> list[dict]:
    """Like `read`, but skips malformed lines rather than raising."""
    if not path.exists():
        return []
    return loads_lines(path.read_text(encoding="utf-8"), tolerant=True, what=what, where=path)


def write_table(path: Path, columns: list[str], rows: Iterable[dict], *, delimiter: str = ",") -> None:
    """A delimited export for spreadsheets and other tools; nothing here reads it back.

    Cell whitespace, newlines included, is collapsed to single spaces and a
    list becomes its items joined with " | ", so every row is one line. CSV
    quotes as needed; TSV, which has no quoting, escapes instead.
    """
    import csv
    import io

    def cell(v: Any) -> str:
        if isinstance(v, list):
            v = " | ".join(str(x) for x in v)
        elif isinstance(v, bool):
            v = "true" if v else "false"
        return " ".join(str("" if v is None else v).split())

    buf = io.StringIO()
    quoting = dict(quoting=csv.QUOTE_NONE, escapechar="\\") if delimiter == "\t" else dict(quoting=csv.QUOTE_MINIMAL)
    w = csv.DictWriter(buf, fieldnames=columns, delimiter=delimiter, extrasaction="ignore",
                       lineterminator="\n", **quoting)
    w.writeheader()
    for r in rows:
        w.writerow({c: cell(r.get(c)) for c in columns})
    replace_atomically(path, buf.getvalue())


def replace_atomically(path: Path, data: str) -> None:
    """Write `data` to a sibling temp file, fsync it, then rename over `path`.

    A crash leaves either the old file or the new one, never a truncated mix:
    `open("w")` truncates on open, so writing in place would lose the old
    contents the moment it started.
    """
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write(path: Path, rows: Iterable[Any], *, validate: bool = True) -> int:
    """Replace the file with `rows`, validating every row before touching disk."""
    rows = list(rows)
    if validate:
        for r in rows:
            _validate(path, r)
    replace_atomically(path, "".join(dumps(r) + "\n" for r in rows))
    return len(rows)


class TornTail(RuntimeError):
    """The file's last line has no newline: a writer died mid-line."""


def has_torn_tail(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as f:
        f.seek(-1, 2)
        return f.read(1) != b"\n"


def append(path: Path, row: Any, *, validate: bool = True) -> None:
    """Append one validated row as a single write.

    Refuses to append after a torn last line: the new row would be glued onto
    the fragment and both would be lost to every reader.
    """
    if validate:
        _validate(path, row)
    if has_torn_tail(path):
        raise TornTail(f"{path} ends mid-line; run `col4a1a2 check` and repair it before writing")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps(row) + "\n")
