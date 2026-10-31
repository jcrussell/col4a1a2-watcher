"""Which stored rows count as current, defined once.

`extractions.jsonl` and `challenges.jsonl` accumulate rows from retired
prompts, comparison models (`extract --model X`), superseded text and
`--no-llm-cache` re-runs. Every reader needs the same answer to "which of these
is the result", and two copies of one such rule drift apart. So `cli` and
`check` import these and restate nothing.
"""

from __future__ import annotations

from pathlib import Path

from col4a1a2 import jsonl
from col4a1a2.extract.challenge import CHALLENGE_PROMPT_VERSION, records_digest, slim_records
from col4a1a2.extract.prompt import PROMPT_VERSION


def is_current_extraction(row: dict, model: str) -> bool:
    """This prompt, the requested model, and a result rather than a failure."""
    return (not row.get("error") and row.get("prompt_version") == PROMPT_VERSION
            and row.get("requested_model") == model)


def latest_extractions(path: Path, model: str, *, snapshot: str | None = None) -> list[dict]:
    """One current row per PMID, the newest by `written_at`, optionally from one snapshot.

    A paper re-extracted after its text grew, or re-run with `--no-llm-cache`,
    has more than one row; readers want the newest current result.
    """
    by_pmid: dict[str, dict] = {}
    for r in sorted(jsonl.read(path), key=lambda r: r.get("written_at", "")):
        if is_current_extraction(r, model) and (snapshot is None or r.get("snapshot") == snapshot):
            by_pmid[r["pmid"]] = r
    return list(by_pmid.values())


def review_records(row: dict) -> list[dict]:
    """An extraction row's records as the reviewer sees them."""
    return slim_records(row["pmid"], (row.get("extraction") or {}).get("variants") or [],
                        (row.get("grounding") or {}).get("figure_sourced") or [])


def records_digest_for(row: dict) -> str:
    return records_digest(review_records(row))


def is_current_challenge(row: dict, model: str) -> bool:
    return (row.get("challenge_prompt_version") == CHALLENGE_PROMPT_VERSION
            and row.get("requested_model") == model)


def current_challenges(rows: list[dict], model: str) -> dict[tuple[str, str, str], dict]:
    """The newest current challenge per (pmid, text hash, records digest)."""
    out: dict[tuple[str, str, str], dict] = {}
    for r in sorted(rows, key=lambda r: r.get("written_at", "")):
        if is_current_challenge(r, model):
            out[(r["pmid"], r["text_sha256"], r["records_digest"])] = r
    return out


def challenge_for(extraction: dict, challenges: dict[tuple[str, str, str], dict]) -> dict | None:
    """The challenge that reviewed exactly these records on exactly this text.

    Matching on the PMID alone would attach a verdict about superseded records
    to the current ones whenever a paper is re-extracted.
    """
    return challenges.get((extraction["pmid"], extraction["text_sha256"], records_digest_for(extraction)))


def reviewer_verdict(key: str, challenge: dict | None) -> str:
    """What survived checking the reviewer's judgement of one record.

    A dispute counts only if its refuting quote grounds in the text (the
    report's `disputed`); one that does not is `dispute not grounded`. Upheld
    and unverifiable need no grounding, so they come from the verdict itself,
    exactly as `challenge.check` sorts them. A record the reviewer skipped is
    `not reviewed`.
    """
    if not challenge:
        return "not reviewed"
    rep = challenge.get("report") or {}
    if key in (rep.get("disputed") or []):
        return "disputed"
    if any(f[0] == key for f in rep.get("fabricated") or []):
        return "dispute not grounded"
    for v in (challenge.get("challenge") or {}).get("verdicts") or []:
        if v.get("record_key") == key:
            return "unverifiable" if v.get("verdict") == "unverifiable" else (
                "upheld" if v.get("verdict") != "disputed" else "dispute not grounded")
    return "not reviewed"


def review_flags(variant: dict, grounding: dict, verdict: str, *, duplicate: bool = False) -> list[str]:
    """Why a record needs a human, or nothing. The review queue and the release share this."""
    flags = []
    if variant["as_written"] in (grounding.get("figure_sourced") or []):
        flags.append("figure-sourced")
    if variant.get("confidence") == "low":
        flags.append("low-confidence")
    if any(u[0] == variant["as_written"] for u in grounding.get("ungrounded") or []):
        flags.append("ungrounded-quote")
    if verdict == "disputed":
        flags.append("disputed")
    # A record the reviewer says duplicates another in the same paper would
    # count the same carriers twice.
    if duplicate:
        flags.append("duplicate")
    return flags


def record_review(key: str, variant: dict, grounding: dict, challenge: dict | None) -> tuple[str, list[str]]:
    """A record's checked review verdict and its flags, as `review` and `publish` both see them."""
    verdict = reviewer_verdict(key, challenge)
    duplicate = key in (((challenge or {}).get("report") or {}).get("duplicates") or [])
    return verdict, review_flags(variant, grounding, verdict, duplicate=duplicate)


def is_confident(verdict: str, flags: list[str]) -> bool:
    """Upheld by the checked review and nothing flags it: what `publish` releases by default."""
    return verdict == "upheld" and not flags
