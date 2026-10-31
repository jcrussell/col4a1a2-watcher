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


def latest_extractions(path: Path, model: str) -> list[dict]:
    """One current row per PMID, the newest by `written_at`.

    A paper re-extracted after its text grew, or re-run with `--no-llm-cache`,
    has more than one row; readers want the newest current result.
    """
    by_pmid: dict[str, dict] = {}
    for r in sorted(jsonl.read(path), key=lambda r: r.get("written_at", "")):
        if is_current_extraction(r, model):
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
