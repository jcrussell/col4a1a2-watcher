"""One shape for the three caches of paid model answers.

Triage verdicts, figure transcriptions and challenge verdicts are each an
append-only JSONL file keyed by what was asked: the input's identity, the
prompt version, and the model requested. The newest `written_at` per key
wins, the same rule `check`, `review` and the ledger use. File position cannot
decide it: every curated file is merged with `merge=union`, which can reorder
lines, and `--no-llm-cache` appends a second row under the same key.
"""

from __future__ import annotations

from pathlib import Path

from col4a1a2 import jsonl


class KeyedCache:
    """Rows keyed by `KEY` (field names); the newest `written_at` per key wins."""

    KEY: tuple[str, ...] = ()
    WHAT = "cache"

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[tuple, dict] = {}
        for r in sorted(jsonl.read_tolerant(path, what=self.WHAT), key=lambda r: r.get("written_at", "")):
            self.entries[self.key_of(r)] = r
        self.hits = 0
        self.misses = 0

    def key_of(self, row: dict) -> tuple:
        return tuple(row.get(f, "") for f in self.KEY)

    def lookup(self, key: tuple, *, refresh: bool = False) -> dict | None:
        """The cached row for `key`; None on a miss or when `refresh` asks for a new call."""
        if refresh:
            return None
        r = self.entries.get(key)
        if r is not None:
            self.hits += 1
        return r

    def store(self, row: dict) -> dict:
        """Append a validated row, then index it if it is the newest for its key."""
        jsonl.append(self.path, row)
        key = self.key_of(row)
        held = self.entries.get(key)
        if held is None or row.get("written_at", "") >= held.get("written_at", ""):
            self.entries[key] = row
        self.misses += 1
        return row
