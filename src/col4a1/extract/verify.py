"""Quote grounding: every evidence quote must appear verbatim in the source text."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from col4a1.extract.schema import Confidence, PaperExtraction

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x00A0: " "}


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DASHES).translate(_QUOTES)
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


def quote_in_text(quote: str, text_norm: str) -> bool:
    q = normalise(quote)
    if not q:
        return False
    if q in text_norm:
        return True
    # Tolerate a trailing citation marker or punctuation difference.
    q2 = re.sub(r"[\s.;:,]+$", "", q)
    return bool(q2) and q2 in text_norm


@dataclass
class GroundingReport:
    pmid: str
    total_quotes: int = 0
    grounded_quotes: int = 0
    ungrounded: list[tuple[str, str]] = field(default_factory=list)  # (variant as_written, quote)
    low_confidence_variants: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.ungrounded) or bool(self.low_confidence_variants)

    @property
    def grounded_fraction(self) -> float:
        return 1.0 if self.total_quotes == 0 else self.grounded_quotes / self.total_quotes


def ground(extraction: PaperExtraction, source_text: str) -> GroundingReport:
    text_norm = normalise(source_text)
    rep = GroundingReport(pmid=extraction.pmid)
    for v in extraction.variants:
        quotes = list(v.evidence_quotes) + [fa.evidence_quote for fa in v.functional_assays]
        for q in quotes:
            rep.total_quotes += 1
            if quote_in_text(q, text_norm):
                rep.grounded_quotes += 1
            else:
                rep.ungrounded.append((v.as_written, q))
        if v.confidence == Confidence.low:
            rep.low_confidence_variants.append(v.as_written)
    return rep
