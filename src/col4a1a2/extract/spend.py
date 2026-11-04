"""What Claude has cost us, totalled over everything we kept. Local only.

Two sources, never both for one call:

- **The response archive** holds one document per paid call, a refresh
  included, so each is priced exactly from its billed attempts. A table row
  built from a replayed answer adds nothing, because it is not a new document.
- **Table rows written before the archive's first call** (`triage`, `figures`,
  `extractions`, `challenges`) carry the token counts their call reported and
  are priced from those. That is an estimate: rows from then record no
  fallback hops, and none has fired in any archived call since.

Calls that were never archived and wrote no row, such as `col4a1a2-dev triage
--llm` and `col4a1a2-dev probe`, are not counted.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from col4a1a2 import jsonl
from col4a1a2.extract.llm import billed_attempts
from col4a1a2.schemas import RESPONSE_SUFFIX

TABLES = {"triage": "triage", "figures": "figure", "extractions": "extract", "challenges": "challenge"}
TOKENS = ("input", "output", "cache_read", "cache_write")


def archived_calls(root: Path) -> Iterator[dict]:
    for p in sorted(root.glob(f"*/*{RESPONSE_SUFFIX}")) if root.exists() else ():
        rec = json.loads(p.read_text(encoding="utf-8"))
        yield {"source": "archive", "site": rec["site"], "written_at": rec["written_at"],
               "attempts": billed_attempts(rec["response"], rec["requested_model"])}


def table_calls(curated: Path, before: str | None) -> Iterator[dict]:
    for name, site in TABLES.items():
        path = curated / f"{name}.jsonl"
        for r in jsonl.read(path) if path.exists() else ():
            if r.get("usage") and (before is None or r["written_at"] < before):
                usage = {k: r["usage"].get(k, 0) or 0 for k in TOKENS}
                yield {"source": "rows", "site": site, "written_at": r["written_at"],
                       "attempts": [(r.get("model") or r["requested_model"], usage)]}


def tally(responses_dir: Path, curated_dir: Path, price_for) -> dict:
    """Calls, tokens and dollars by (source, site, model), plus totals."""
    archived = list(archived_calls(responses_dir))
    start = min((c["written_at"] for c in archived), default=None)
    groups: dict[tuple, dict] = defaultdict(lambda: {"calls": 0, **dict.fromkeys(TOKENS, 0), "usd": 0.0})
    unpriced: set[str] = set()
    for c in [*table_calls(curated_dir, start), *archived]:
        for i, (model, usage) in enumerate(c["attempts"]):
            g = groups[(c["source"], c["site"], model)]
            g["calls"] += i == 0
            for k in TOKENS:
                g[k] += usage[k]
            price = price_for(model)
            if price is None:
                unpriced.add(model)
            else:
                g["usd"] += price.cost(**usage)
    rows = [{"source": s, "site": site, "model": m, **g} for (s, site, m), g in sorted(groups.items())]
    return {"archive_starts": start, "rows": rows, "unpriced": sorted(unpriced),
            "total_usd": sum(r["usd"] for r in rows),
            "estimated_usd": sum(r["usd"] for r in rows if r["source"] == "rows")}
