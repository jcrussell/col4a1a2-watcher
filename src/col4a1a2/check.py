"""Integrity checks over our files: the guarantees a database would give.

    uv run col4a1a2 check [--json]

Read-only, offline and deterministic; the pre-commit hook runs it. Every write
is already validated, so this exists for what bypasses the writers -- hand
edits, merges, migrations, a run killed between two files -- and for the
relations no single write can see.

Tracked files (always):
- every line parses and validates; no torn last line;
- keyed caches carry no byte-identical duplicate rows (a re-run with
  `--no-llm-cache` is a same-key row with a newer `written_at`, and allowed);
- foreign keys: triage, challenge and label PMIDs are ledger identities, and
  every paper the ledger calls challenged has a current challenge row;
- append-only: each log extends its `HEAD` version byte for byte, unless
  `COL4A1A2_ALLOW_REWRITE=1` declares a migration, and even then no ledger
  identity and no triage verdict may disappear -- the check that would have
  stopped the first attempt at squashing the prompt versions.

Snapshots under `data/raw/` (when present; a fresh clone has none):
- every snapshot has a valid manifest, and its JSONL files validate;
- the newest current extraction the ledger records exists as a row
  (an index entry pointing at nothing makes resume skip a paper with no
  result: ERROR), and a current row the ledger never heard of is a WARN
  (the harmless orphan left by a crash between data and index);
- each current challenge's `records_digest` matches the records it reviewed;
- each review-queue row points at an existing extraction.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from col4a1a2 import jsonl, schemas
from col4a1a2.config import Settings
from col4a1a2.extract import rows as rows_mod

APPEND_ONLY = ("ledger.jsonl", "triage.jsonl", "figures.jsonl", "challenges.jsonl", "discovery.jsonl")
ALLOW_REWRITE = "COL4A1A2_ALLOW_REWRITE"


@dataclass
class Finding:
    level: str  # ERROR | WARN | INFO
    code: str
    where: str
    message: str

    def line(self) -> str:
        return f"{self.level:5} {self.code}: {self.where}: {self.message}"


class Checker:
    def __init__(self, st: Settings, *, repo: Path, allow_rewrite: bool | None = None):
        self.st = st
        self.repo = repo
        self.allow_rewrite = (os.environ.get(ALLOW_REWRITE) == "1"
                              if allow_rewrite is None else allow_rewrite)
        self.findings: list[Finding] = []

    def add(self, level: str, code: str, where: Path | str, message: str) -> None:
        where = str(where.relative_to(self.repo)) if isinstance(where, Path) and where.is_relative_to(self.repo) else str(where)
        self.findings.append(Finding(level, code, where, message))

    # ------------------------------------------------------------ reading

    def rows(self, path: Path) -> list[dict]:
        """Parse and validate every line, recording problems; return the good rows."""
        if not path.exists():
            return []
        if jsonl.has_torn_tail(path):
            self.add("ERROR", "torn-tail", path, "last line has no newline; a writer died mid-line")
        out, bad = [], 0
        for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                schemas.validate(path, r)
            except (json.JSONDecodeError, ValidationError) as e:
                bad += 1
                if bad <= 3:
                    first = str(e).splitlines()[0] if not isinstance(e, ValidationError) else \
                        f"{e.errors()[0]['loc']}: {e.errors()[0]['msg']}"
                    self.add("ERROR", "invalid-row", f"{path.relative_to(self.repo) if path.is_relative_to(self.repo) else path}:{n}", first)
                continue
            out.append(r)
        if bad > 3:
            self.add("ERROR", "invalid-row", path, f"{bad} invalid rows in all")
        return out

    def document(self, path: Path) -> dict | None:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            schemas.validate(path, d)
            return d
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            self.add("ERROR", "invalid-document", path, str(e).splitlines()[0])
            return None

    # ------------------------------------------------------------ tracked

    def run(self) -> list[Finding]:
        from col4a1a2.ledger import fold_rows

        cur = self.st.curated_dir
        data = {name: self.rows(cur / name) for name in APPEND_ONLY}
        labels = self.document(cur / "labels.json") if (cur / "labels.json").exists() else None

        fold = fold_rows(data["ledger.jsonl"])
        identities = set(fold)

        self.duplicates(cur / "triage.jsonl", data["triage.jsonl"])
        self.duplicates(cur / "figures.jsonl", data["figures.jsonl"])
        self.duplicates(cur / "challenges.jsonl", data["challenges.jsonl"])

        for name in ("triage.jsonl", "challenges.jsonl"):
            orphans = sorted({r["pmid"] for r in data[name]} - identities)
            if orphans:
                self.add("ERROR", "dangling-pmid", cur / name,
                         f"{len(orphans)} PMIDs not in the ledger, e.g. {', '.join(orphans[:5])}")
        if labels:
            orphans = sorted(set(labels.get("labels", {})) - identities)
            if orphans:
                self.add("ERROR", "dangling-pmid", cur / "labels.json",
                         f"{len(orphans)} PMIDs not in the ledger, e.g. {', '.join(orphans[:5])}")

        current_ch = self.current_challenges(data["challenges.jsonl"])
        missing = sorted(i for i, r in fold.items() if r.get("stage") == "challenged" and i not in current_ch)
        if missing:
            self.add("ERROR", "challenged-without-row", cur / "ledger.jsonl",
                     f"{len(missing)} papers the ledger calls challenged have no current challenge row, "
                     f"e.g. {', '.join(missing[:5])}")

        for name in APPEND_ONLY:
            self.append_only(cur / name)

        self.snapshots(data["ledger.jsonl"], current_ch)
        self.responses()
        return self.findings

    def duplicates(self, path: Path, rows: list[dict]) -> None:
        counts = Counter(json.dumps(r, sort_keys=True) for r in rows)
        dup = sum(n - 1 for n in counts.values() if n > 1)
        if dup:
            self.add("WARN", "duplicate-row", path, f"{dup} byte-identical duplicate rows (an accidental re-append?)")

    def current_challenges(self, rows: list[dict]) -> dict[str, dict]:
        """The newest current challenge per PMID, by the shared rule in `extract.rows`."""
        out: dict[str, dict] = {}
        for c in sorted(rows_mod.current_challenges(rows, self.st.extract_model).values(),
                        key=lambda r: r.get("written_at", "")):
            out[c["pmid"]] = c
        return out

    def append_only(self, path: Path) -> None:
        rel = path.relative_to(self.repo).as_posix() if path.is_relative_to(self.repo) else None
        if rel is None or not path.exists():
            return
        try:
            head = subprocess.run(["git", "-C", str(self.repo), "show", f"HEAD:{rel}"],
                                  capture_output=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            return  # not tracked yet, or not a git checkout
        now = path.read_bytes()
        if now.startswith(head):
            return
        if not self.allow_rewrite:
            self.add("ERROR", "rewritten-log", path,
                     f"no longer extends HEAD byte for byte; if this is a deliberate migration, "
                     f"set {ALLOW_REWRITE}=1 and record it in docs/measurements.md")
            return
        self.add("INFO", "rewritten-log", path, f"rewritten under {ALLOW_REWRITE}=1")
        if path.name == "ledger.jsonl":
            self.ledger_migration(path, head)

    def ledger_migration(self, path: Path, head: bytes) -> None:
        """A migration may reshape events; it may not lose a paper or a verdict."""
        from col4a1a2.ledger import fold_rows

        a = fold_rows(jsonl.loads_lines(head.decode("utf-8"), tolerant=True, what="HEAD ledger"))
        b = fold_rows(jsonl.read_tolerant(path))
        gone = sorted(set(a) - set(b))
        if gone:
            self.add("ERROR", "migration-lost-identity", path,
                     f"{len(gone)} papers disappear from the ledger, e.g. {', '.join(gone[:5])}")
        lost = sorted(k for k in set(a) & set(b)
                      if (a[k].get("triage") or {}).get("keep") and not (b[k].get("triage") or {}).get("keep"))
        if lost:
            self.add("ERROR", "migration-lost-verdict", path,
                     f"{len(lost)} papers lose their triage keep verdict, e.g. {', '.join(lost[:5])}")
        moved = Counter((a[k].get("stage"), b[k].get("stage")) for k in set(a) & set(b)
                        if a[k].get("stage") != b[k].get("stage"))
        for (s0, s1), n in sorted(moved.items(), key=lambda kv: str(kv)):
            self.add("INFO", "migration-stage", path, f"{n} papers {s0} -> {s1}")

    # ------------------------------------------------------------ snapshots

    def snapshots(self, ledger_rows: list[dict], current_ch: dict[str, dict]) -> None:
        from col4a1a2.extract.prompt import PROMPT_VERSION
        from col4a1a2.ledger import is_extraction

        root = self.st.snapshot_root
        dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
        if not dirs:
            self.add("INFO", "no-snapshots", root, "skipped: no snapshots on this machine")
            return
        model = self.st.extract_model
        current: dict[tuple[str, str], dict] = {}
        papers_hash: dict[str, set[str]] = {}
        review: list[tuple[Path, dict]] = []
        for d in dirs:
            if not (d / "manifest.json").exists():
                self.add("ERROR", "no-manifest", d, "snapshot has no manifest.json")
            else:
                self.document(d / "manifest.json")
            for p in self.rows(d / "papers.jsonl"):
                papers_hash.setdefault(p["pmid"], set()).add(p.get("text_sha256", ""))
            for r in self.rows(d / "extractions.jsonl"):
                if rows_mod.is_current_extraction(r, model):
                    current[(r["pmid"], r["text_sha256"])] = r
            review += [(d / "review.jsonl", r) for r in self.rows(d / "review.jsonl")]

        newest: dict[str, dict] = {}
        indexed: set[tuple[str, str]] = set()
        for e in sorted(ledger_rows, key=lambda e: e.get("written_at", "")):
            if (is_extraction(e) and e.get("prompt_version") == PROMPT_VERSION
                    and e.get("requested_model") == model):
                newest[e["identity"]] = e
                indexed.add((e["identity"], e["text_sha256"]))
        missing = sorted(i for i, e in newest.items() if (i, e["text_sha256"]) not in current)
        if missing:
            self.add("ERROR", "index-without-data", "data/curated/ledger.jsonl",
                     f"{len(missing)} extractions the ledger records have no row in any snapshot, "
                     f"so resume would skip them: e.g. {', '.join(missing[:5])}")
        orphans = sorted({k[0] for k in current if k not in indexed})
        if orphans:
            self.add("WARN", "data-without-index", root,
                     f"{len(orphans)} current extraction rows have no ledger event, e.g. {', '.join(orphans[:5])}")

        stale = []
        for pmid, c in current_ch.items():
            row = current.get((pmid, c["text_sha256"]))
            if row is None:
                stale.append(pmid)
                continue
            if rows_mod.records_digest_for(row) != c["records_digest"]:
                stale.append(pmid)
        if stale:
            self.add("ERROR", "challenge-of-missing-records", "data/curated/challenges.jsonl",
                     f"{len(stale)} current challenges review records that no longer exist, "
                     f"e.g. {', '.join(sorted(stale)[:5])}")

        have = {pmid for pmid, _ in current}
        bad_review = sorted({r["pmid"] for _, r in review if r["pmid"] not in have})
        if bad_review:
            self.add("ERROR", "review-of-missing-extraction", root,
                     f"{len(bad_review)} review-queue PMIDs have no current extraction, "
                     f"e.g. {', '.join(bad_review[:5])}")

    def responses(self) -> None:
        root = self.st.responses_dir
        files = list(root.glob(f"*/*{schemas.RESPONSE_SUFFIX}")) if root.exists() else []
        for f in files:
            self.document(f)
        size = sum(f.stat().st_size for f in files)
        self.add("INFO", "responses", root, f"{len(files)} archived responses, {size / 1e6:.1f} MB")


def run(st: Settings, *, repo: Path, as_json: bool = False, allow_rewrite: bool | None = None) -> int:
    findings = Checker(st, repo=repo, allow_rewrite=allow_rewrite).run()
    for f in findings:
        print(json.dumps(asdict(f)) if as_json else f.line())
    n_err = sum(f.level == "ERROR" for f in findings)
    n_warn = sum(f.level == "WARN" for f in findings)
    if not as_json:
        print(f"{n_err} errors, {n_warn} warnings")
    return 1 if n_err else 0
