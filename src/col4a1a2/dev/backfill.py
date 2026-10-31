#!/usr/bin/env python
"""Backfill ledger extraction outcomes from snapshots that predate the ledger.

    uv run col4a1a2-dev backfill data/raw/2026-10-30-batch100 [...]

The ledger was added after 100 papers had already been extracted, and `extract`
correctly skips work it has already done, so those outcomes would never appear.
Idempotent: re-running appends identical events and the fold is unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from col4a1a2.config import REPO

from col4a1a2.config import load_settings
from col4a1a2.ledger import Ledger


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    snaps = [Path(s) for s in argv]
    if not snaps:
        print(__doc__)
        return 2
    st = load_settings()
    led = Ledger(st.curated_dir / "ledger.jsonl")

    # Collect across every snapshot first and keep the newest row per paper.
    # Snapshot directory names are dates, so lexicographic order is chronological.
    # Processing snapshots in argument order instead would let an older snapshot
    # overwrite a newer one, which is how the first version of this failed to be
    # idempotent.
    newest: dict[str, tuple[str, dict]] = {}
    for snap in sorted(snaps, key=lambda s: s.name):
        path = snap / "extractions.jsonl"
        if not path.is_file():
            print(f"{snap.name}: no extractions.jsonl, skipped")
            continue
        seen = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                newest[r["pmid"]] = (snap.name, r)
                seen += 1
        print(f"{snap.name}: {seen} extraction rows")

    current = led.fold()
    for pmid, (snap_name, r) in sorted(newest.items()):
        cur = current.get(pmid, {})
        # Never let an older snapshot overwrite a newer record. The fold is
        # last-by-written_at, so appending stale data would regress state --
        # which is what happens if this is run with a subset of the snapshots.
        if cur.get("snapshot") and str(cur["snapshot"]) > snap_name:
            continue
        if r.get("error"):
            if cur.get("last_error") == r["error"] and cur.get("text_sha256") == r.get("text_sha256"):
                continue
            led.record(identity=pmid, stage="fetched", snapshot=snap_name,
                       text_sha256=r.get("text_sha256"), prompt_version=r.get("prompt_version"),
                       last_error=r["error"], attempts=(cur.get("attempts") or 0) + 1)
            continue
        if (cur.get("text_sha256") == r.get("text_sha256")
                and cur.get("prompt_version") == r.get("prompt_version")
                and cur.get("stage") == "extracted"):
            continue
        g = r.get("grounding") or {}
        ex = r.get("extraction") or {}
        led.record(identity=pmid, stage="extracted", snapshot=snap_name,
                   text_sha256=r.get("text_sha256"), prompt_version=r.get("prompt_version"),
                   n_variants=len(ex.get("variants") or []),
                   n_grounded=g.get("grounded"), n_ungrounded=len(g.get("ungrounded") or []),
                   needs_review=bool(g.get("needs_review")),
                   n_figure_sourced=len(g.get("figure_sourced") or []),
                   attempts=0, last_error=None)

    print(f"appended {led.appended} events")
    s = led.summary()
    print(f"  with records: {s['with_records']}   needing review: {s['needs_review']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
