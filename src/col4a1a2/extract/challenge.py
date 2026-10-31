"""Adversarial peer review of extracted records.

Grounding proves a quote appears verbatim in what the model was shown. It cannot
say whether the quote supports the claim, whether proband counts are right, or
whether the same family is counted twice. This module asks a second call to try
to refute each record, and makes its answers checkable.

Design, and the reasons, because several are counter-intuitive:

**Every judgement carries a verbatim quote.** A `disputed` verdict must cite the
sentence that contradicts the record, and that sentence is string-matched against
the exact text the reviewer was shown, using the same grounding machinery as
evidence quotes. An ungrounded refutation is recorded as fabricated rather than
stored as a verdict. This is not a self-reported provenance field of the kind
`AGENTS.md` bans: it is a quote to be matched, the same kind of object as an
`evidence_quote`.

**Be precise about what that buys.** It catches a reviewer inventing a
contradiction. It does *not* catch one quoting a real sentence that does not
actually refute the claim, which is the dominant over-refutation mode for count
disputes. So the measurement is a **fabrication rate**, never "precision", and
the field is named accordingly.

**`missed_variants` is the highest-value output**, because grounding it makes
extraction *recall* computable for the first time: a verbatim variant string
that appears in the text with no record is a real miss, and one that does not
appear is an invention. It is also why zero-record papers are challenged rather
than skipped — a zero-record extraction is either an empty paper or a total
miss, and nothing else distinguishes them.

**No self-reported confidence, and proposals are proposals.** A confidence score
about a judgement is worth less than the grounded quote beside it. Count
corrections are named `proposed_*` because downstream code believes a word like
"corrected", and nothing here is ever written back into `extractions.jsonl`.

**Not a second conversational turn.** Continuing the extraction conversation
would let the paper text be read from cache and cut input cost by nearly half,
but the model would then be reviewing its own prior assistant output, which is
the worst available setup for an adversarial check. A fresh conversation
presenting the records as another analyst's work is far likelier to refute. The
saving is real and is deliberately declined.

**Fails closed.** Triage fails open, because losing a paper is worse than
keeping noise. This has the opposite polarity: a failed challenge must never be
recorded as "nothing to refute".
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from col4a1a2 import jsonl
from col4a1a2.extract.prompt import PROMPT_VERSION
from col4a1a2.extract.verify import find_quote, normalise

log = logging.getLogger("col4a1a2")

CHALLENGE_PROMPT_VERSION = "c1"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CHALLENGE_TOKENS = 32000

# Fields the reviewer needs to judge a record. Dropping the free-text phenotype
# prose roughly halves the records payload; everything a verdict depends on stays.
SLIM_FIELDS = (
    "gene", "as_written", "hgvs_c", "hgvs_p", "transcript_if_stated", "variant_type",
    "n_probands", "n_families", "n_total_carriers", "inheritance", "segregation",
    "reported_classification", "same_family_as_prior_report", "confidence",
    "evidence_quotes", "notes",
)


class Verdict(str, Enum):
    upheld = "upheld"
    disputed = "disputed"
    unverifiable = "unverifiable"


class RecordVerdict(BaseModel):
    record_key: str = Field(description="The record_key given in the input, copied exactly")
    verdict: Verdict = Field(description="upheld if the record is supported; disputed if the text contradicts it; unverifiable if the text does not settle it")
    problem: str = Field(default="", description="What is wrong, in one sentence; empty when upheld")
    refuting_quote: str = Field(default="", description="A sentence copied verbatim from the paper text that contradicts the record. Required for disputed. It will be string-matched, so copy it exactly and do not paraphrase.")
    proposed_n_probands: int | None = Field(default=None, description="What n_probands should be, if the text settles it; null otherwise")
    proposed_n_families: int | None = Field(default=None, description="What n_families should be, if the text settles it; null otherwise")


class PaperChallenge(BaseModel):
    pmid: str
    duplicate_record_keys: list[str] = Field(default_factory=list, description="record_keys that describe the same variant as another record")
    missed_variants: list[str] = Field(default_factory=list, description="Variant designations present verbatim in the paper text with no record. Copy each exactly as the paper writes it; they are string-matched.")
    verdicts: list[RecordVerdict] = Field(default_factory=list, description="Exactly one entry per record given, in any order")
    notes: str = Field(default="", description="Anything a curator should know about this paper's records as a whole")


class ChallengeFailed(RuntimeError):
    """The challenge produced nothing usable. Never treated as 'nothing to refute'."""


SYSTEM = """You are reviewing variant-evidence records that another analyst extracted from a scientific paper about the genes COL4A1 and COL4A2. Your job is to try to refute them.

You are given the paper text and the records. Work only from the text provided.

For each record, return exactly one verdict, copying its record_key exactly:
- "disputed" when the text contradicts the record. You MUST quote the sentence that contradicts it, copied verbatim from the text. The quote is string-matched against the text, so a paraphrase or a reconstruction counts as no quote at all, and a disputed verdict without a matching quote is discarded and counted against you.
- "unverifiable" when the text does not settle the question. Use this freely; it is not a failure.
- "upheld" when the text supports the record as written.

Do not dispute a record because you would have phrased it differently, because a field is empty where the paper is silent, or because you would have chosen a different variant_type. Dispute it when the text says something incompatible with it.

Pay particular attention to:
- Proband and family counts. A cohort of N individuals is not N families. Counts drawn from a figure or a supplementary table are the likeliest to be wrong.
- Whether each evidence quote actually supports the claim attached to it, rather than merely mentioning the variant.
- Whether a gene assignment is printed in the text or was inferred. Records whose details came from a transcribed figure are flagged in the input as such; a figure that does not label its panels cannot establish which gene a variant belongs to.
- The same family appearing as more than one record.

Separately, list in missed_variants any COL4A1 or COL4A2 variant designation that appears verbatim in the paper text and has no record. Copy each exactly as the paper writes it; these are string-matched, and an entry that does not appear in the text is counted as an invention. If the records look complete, return an empty list. If a paper has no records at all, this list is the entire point of reviewing it."""


def record_key(pmid: str, index: int, variant: dict) -> str:
    """A stable join key.

    Not a list position: `latest_extractions` can supersede a paper's records, so
    an index handed to a human could point into a list that has been replaced.
    Not `as_written` alone either: it is free text the model retypes. Both,
    plus a short content hash.
    """
    blob = json.dumps({k: variant.get(k) for k in SLIM_FIELDS}, sort_keys=True, ensure_ascii=False)
    return f"{pmid}:{index}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:8]}"


def slim_records(pmid: str, variants: list[dict], figure_sourced: list[str]) -> list[dict]:
    """The records as the reviewer sees them, with provenance attached.

    Provenance is passed *in*, computed by grounding, rather than asked for.
    """
    fs = set(figure_sourced or ())
    out = []
    for i, v in enumerate(variants):
        rec = {"record_key": record_key(pmid, i, v)}
        rec.update({k: v.get(k) for k in SLIM_FIELDS})
        if v.get("as_written") in fs:
            rec["provenance_note"] = "details came from a model transcription of a figure, not from author text"
        out.append(rec)
    return out


def records_digest(records: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(records, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def effort_for(n_records: int) -> str:
    """Scale thinking with the amount to review.

    Effort is the only thinking control on these models; a token budget is
    rejected. Measured over this corpus the scaling barely engages -- record
    counts are [38, 11, 8, 8, 8, 6, 4, ...], so most papers land on the model's
    own default -- which is why it is three lines and not a centrepiece.
    """
    if n_records > 12:
        return "xhigh"
    if n_records > 3:
        return "high"
    return "medium"


@dataclass
class ChallengeReport:
    """What survived checking. Computed, not reported by the model."""

    pmid: str
    n_records: int = 0
    upheld: list[str] = field(default_factory=list)
    disputed: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)
    fabricated: list[tuple[str, str]] = field(default_factory=list)   # (record_key, ungrounded quote)
    duplicates: list[str] = field(default_factory=list)
    missed_grounded: list[str] = field(default_factory=list)
    missed_invented: list[str] = field(default_factory=list)
    incomplete: bool = False
    notes: str = ""

    @property
    def fabrication_rate(self) -> float:
        """Share of dispute attempts whose refuting quote was not in the text.

        Deliberately not called precision: a real sentence that does not
        actually refute the claim grounds perfectly and is counted as upheld
        here, so this bounds invention only.
        """
        attempts = len(self.disputed) + len(self.fabricated)
        return 0.0 if attempts == 0 else len(self.fabricated) / attempts

    def as_dict(self) -> dict:
        return {
            "pmid": self.pmid, "n_records": self.n_records,
            "n_upheld": len(self.upheld), "n_disputed": len(self.disputed),
            "n_unverifiable": len(self.unverifiable), "n_fabricated": len(self.fabricated),
            "disputed": self.disputed, "fabricated": self.fabricated,
            "duplicates": self.duplicates,
            "missed_grounded": self.missed_grounded, "missed_invented": self.missed_invented,
            "incomplete": self.incomplete,
            "fabrication_rate": round(self.fabrication_rate, 4),
            "notes": self.notes,
        }


def check(challenge: PaperChallenge, records: list[dict], source_text: str) -> ChallengeReport:
    """Verify the reviewer's own output before believing any of it."""
    rep = ChallengeReport(pmid=challenge.pmid, n_records=len(records), notes=challenge.notes)
    text_norm = normalise(source_text)
    keys = [r["record_key"] for r in records]

    returned = {v.record_key for v in challenge.verdicts}
    # A verdict list has no minimum, so a reviewer handed 38 records can return
    # five and still satisfy the schema. That would store a partial audit as a
    # complete one.
    if returned != set(keys) or len(challenge.verdicts) != len(keys):
        rep.incomplete = True

    for v in challenge.verdicts:
        if v.record_key not in keys:
            continue
        if v.verdict is Verdict.disputed:
            if v.refuting_quote and find_quote(v.refuting_quote, text_norm) is not None:
                rep.disputed.append(v.record_key)
            else:
                rep.fabricated.append((v.record_key, v.refuting_quote[:200]))
        elif v.verdict is Verdict.unverifiable:
            rep.unverifiable.append(v.record_key)
        else:
            rep.upheld.append(v.record_key)

    rep.duplicates = [k for k in challenge.duplicate_record_keys if k in keys]

    for m in challenge.missed_variants:
        if m and find_quote(m, text_norm) is not None:
            rep.missed_grounded.append(m)
        else:
            rep.missed_invented.append(m)
    return rep


def challenge_paper(client, *, pmid: str, source_text: str, records: list[dict],
                    model: str, effort: str | None = None,
                    max_tokens: int = MAX_CHALLENGE_TOKENS) -> tuple[PaperChallenge, dict]:
    """One call per paper. Raises ChallengeFailed rather than returning nothing."""
    eff = effort or effort_for(len(records))
    payload = json.dumps(records, ensure_ascii=False, indent=1)
    user = (
        f"PMID: {pmid}\n\n=== PAPER TEXT START ===\n{source_text}\n=== PAPER TEXT END ===\n\n"
        f"=== RECORDS TO REVIEW ({len(records)}) ===\n{payload}\n=== RECORDS END ===\n\n"
        f"Return exactly {len(records)} verdicts, one per record_key above."
    )
    resp = client.beta.messages.parse(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": eff},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=PaperChallenge,
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        raise ChallengeFailed(f"refused: {getattr(resp, 'stop_details', None)}")
    if stop == "max_tokens":
        raise ChallengeFailed("output hit max_tokens before the review was complete")
    if resp.parsed_output is None:
        raise ChallengeFailed(f"no parsed output (stop_reason={stop})")
    usage = {
        "input": resp.usage.input_tokens,
        "output": resp.usage.output_tokens,
        "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        "effort": eff,
    }
    return resp.parsed_output, usage


class ChallengeCache:
    """Keyed on the text *and* the records, plus the prompt version.

    Both halves are load-bearing. The reviewer's quotes are grounded against the
    paper text, so a verdict produced against pre-supplement text must not be
    served for post-supplement text, where that quote may no longer appear. And
    identical text can in principle yield different records, so the records are
    keyed too. (An earlier draft keyed on records alone, justified by a belief
    that extraction was non-deterministic. It is not: all six duplicate
    extraction rows in the corpus differ in their text hash with growing input,
    which is the resume key working correctly.)
    """

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[tuple[str, str, str], dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").split("\n"):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.entries[(r.get("text_sha256", ""), r.get("records_digest", ""),
                              r.get("challenge_prompt_version", ""))] = r
        self.hits = 0
        self.misses = 0

    def get(self, text_sha256: str, records_digest: str) -> dict | None:
        r = self.entries.get((text_sha256, records_digest, CHALLENGE_PROMPT_VERSION))
        if r is not None:
            self.hits += 1
        return r

    def put(self, *, pmid: str, text_sha256: str, records_digest: str, model: str,
            challenge: PaperChallenge, report: ChallengeReport, usage: dict) -> dict:
        row = {
            "pmid": pmid,
            "text_sha256": text_sha256,
            "records_digest": records_digest,
            "challenge_prompt_version": CHALLENGE_PROMPT_VERSION,
            "extract_prompt_version": PROMPT_VERSION,
            "model": model,
            "usage": usage,
            "challenge": challenge.model_dump(mode="json"),
            "report": report.as_dict(),
        }
        self.entries[(text_sha256, records_digest, CHALLENGE_PROMPT_VERSION)] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(jsonl.dumps(row) + "\n")
        self.misses += 1
        return row
