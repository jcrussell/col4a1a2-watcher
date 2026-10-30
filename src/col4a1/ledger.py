"""A durable record of every paper the project has considered.

The published table is a numerator. Without a denominator nobody can tell
"absent because nobody looked" from "looked and found nothing", and today most
of what we process leaves no trace: of 742 candidates in the last batch only
100 got a snapshot row, 642 had nothing but a bare triage decision, and 24 of
those were papers the gate wanted that `--limit` discarded. Three classes
vanished completely -- the 141 Europe PMC hits with no PMID, anything cut by
`--max-search`, and everything the open-access filter dropped before triage
ever ran.

Design choices and why:

**Append-only, folded on read.** Every JSONL in this project works that way.
Three commands read-modify-rewriting one unlocked file loses rows on interleave,
and `write_jsonl` truncates on open, so a crash would destroy the file rather
than appending a bad line.

**Folded by `written_at`, not by position.** The file is tracked, and
`.gitattributes` gives it a union merge driver so an end-of-file conflict
resolves by concatenation. But git's union driver orders lines by which side is
"ours", so a position-based last-wins fold would give different state depending
on merge direction. The existing caches fold by position safely only because
their values are immutable per key; these mutate.

**Identity has a fallback.** The class this exists for has no PMID, so "one row
per PMID" would be structurally unable to record it. Falls back to DOI, then to
a hash of the title, with `identity_kind` saying which.

**No article text.** This file is tracked, and redistributing article content is
forbidden, so identity and metadata only -- no abstracts, no body, no quotes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("col4a1")

LEDGER_VERSION = 1

# Where a candidate left the pipeline. Empty means it is still in play.
DROPPED_AT = ("", "no_pmid", "oa_filter", "limit")

# Furthest point reached. Reporting only: a paper whose text later grows is
# still `extracted` yet needs re-extracting, so selection must key on the text
# hash and never on this.
STAGES = ("candidate", "triaged", "fetched", "extracted", "challenged")

_STAGE_RANK = {s: i for i, s in enumerate(STAGES)}


def identify(pmid: str | None, doi: str | None, title: str | None) -> tuple[str, str]:
    """A stable key for a candidate, and which kind of key it is."""
    if pmid:
        return str(pmid), "pmid"
    if doi:
        return str(doi).lower(), "doi"
    norm = " ".join((title or "").split()).casefold()
    if norm:
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16], "title_sha256"
    return "", "unknown"


@dataclass
class Ledger:
    path: Path
    rows: list[dict] = field(default_factory=list)
    appended: int = 0

    def __post_init__(self) -> None:
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    self.rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # A tracked file can be hand-edited or merge-resolved badly.
                    # One bad line must not break every command that reads it.
                    log.warning("ledger: skipping malformed line in %s", self.path)

    # ---------------------------------------------------------------- writing

    def record(self, **fields) -> dict:
        """Append one event. Callers pass only what they know."""
        identity = fields.get("identity")
        if not identity:
            raise ValueError("a ledger row needs an identity")
        row = {"identity": identity, "ledger_version": LEDGER_VERSION,
               "written_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        row.update({k: v for k, v in fields.items() if v is not None})
        for banned in ("text", "abstract", "body", "evidence_quotes"):
            if banned in row:
                raise ValueError(f"ledger rows must not carry {banned!r}: the file is tracked")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.rows.append(row)
        self.appended += 1
        return row

    def record_candidate(self, *, pmid, doi, title, journal, first_pub_date,
                         is_open_access, has_pmcid, found_by, dropped_at="",
                         snapshot=None, rank=None, limit_at=None, citation=None) -> dict:
        identity, kind = identify(pmid, doi, title)
        if not identity:
            return {}
        return self.record(
            identity=identity, identity_kind=kind, pmid=pmid, doi=doi,
            title=(title or "")[:300] or None, journal=journal,
            first_pub_date=first_pub_date, citation=citation,
            is_open_access=bool(is_open_access), has_pmcid=bool(has_pmcid),
            found_by=sorted(found_by) if found_by else None,
            dropped_at=dropped_at, stage="candidate" if not dropped_at else "candidate",
            snapshot=snapshot, rank=rank, limit_at=limit_at,
        )

    # ---------------------------------------------------------------- reading

    def fold(self) -> dict[str, dict]:
        """Current state per identity: merge events in `written_at` order.

        Order-independent by construction, so a union merge in git cannot change
        the result. Later events overwrite the fields they carry and leave the
        rest, and `stage` only ever advances.
        """
        out: dict[str, dict] = {}
        for row in sorted(self.rows, key=lambda r: (r.get("written_at", ""), r.get("identity", ""))):
            ident = row.get("identity")
            if not ident:
                continue
            cur = out.setdefault(ident, {})
            stage_new, stage_old = row.get("stage"), cur.get("stage")
            cur.update({k: v for k, v in row.items() if k not in ("stage", "written_at")})
            cur["written_at"] = row.get("written_at")
            if stage_new and _STAGE_RANK.get(stage_new, -1) >= _STAGE_RANK.get(stage_old or "", -1):
                cur["stage"] = stage_new
            elif stage_old:
                cur["stage"] = stage_old
        return out

    def summary(self) -> dict:
        state = self.fold()
        by_stage: dict[str, int] = {}
        by_drop: dict[str, int] = {}
        for r in state.values():
            by_stage[r.get("stage", "candidate")] = by_stage.get(r.get("stage", "candidate"), 0) + 1
            d = r.get("dropped_at") or ""
            by_drop[d or "in_play"] = by_drop.get(d or "in_play", 0) + 1
        return {
            "identities": len(state),
            "events": len(self.rows),
            "by_stage": by_stage,
            "by_disposition": by_drop,
            "kept_by_gate": sum(1 for r in state.values() if (r.get("triage") or {}).get("keep")),
            "with_records": sum(1 for r in state.values() if (r.get("n_variants") or 0) > 0),
            "needs_review": sum(1 for r in state.values() if r.get("needs_review")),
            "wedged": sum(1 for r in state.values() if (r.get("attempts") or 0) >= 3),
        }

    def needs_extraction(self, identity: str, text_sha256: str, prompt_version: str,
                         *, max_attempts: int = 3) -> bool:
        """Freshness, not stage.

        A paper extracted last month whose bundle arrives this month has grown
        new text and needs re-extracting, yet its stage is already `extracted` --
        the same bug already fixed once inside `cmd_extract`, one layer up. And a
        paper that fails deterministically must stop being first in the queue
        forever, so attempts are counted.
        """
        r = self.fold().get(identity)
        if r is None:
            return True
        if (r.get("attempts") or 0) >= max_attempts:
            return False
        return not (r.get("text_sha256") == text_sha256
                    and r.get("prompt_version") == prompt_version
                    and not r.get("last_error"))
