"""Quote grounding: every evidence quote must appear verbatim in the source text."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from col4a1a2.extract.schema import Confidence, PaperExtraction
from col4a1a2.sources.fulltext import TextRegion

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x00A0: " "}


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DASHES).translate(_QUOTES)
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


def find_quote(quote: str, text_norm: str) -> int | None:
    """Offset of a quote in normalised text, or None. Same tolerance as before."""
    q = normalise(quote)
    if not q:
        return None
    i = text_norm.find(q)
    if i >= 0:
        return i
    # Tolerate a trailing citation marker or punctuation difference.
    q2 = re.sub(r"[\s.;:,]+$", "", q)
    if not q2:
        return None
    i = text_norm.find(q2)
    return i if i >= 0 else None


def quote_in_text(quote: str, text_norm: str) -> bool:
    return find_quote(quote, text_norm) is not None


# Joins normalised regions. Not whitespace, so it survives whitespace collapsing,
# and it cannot occur inside a quote, so no quote can match across a seam.
_SEP = "\x00"


def _normalise_regions(source_text: str, regions: Sequence[TextRegion]) -> tuple[str, list[tuple[int, int, TextRegion]]]:
    """Normalise each region separately and record its bounds in the joined string."""
    parts: list[str] = []
    bounds: list[tuple[int, int, TextRegion]] = []
    pos = 0
    for r in regions:
        n = normalise(source_text[r.start : r.end])
        if parts:
            pos += len(_SEP)
        bounds.append((pos, pos + len(n), r))
        parts.append(n)
        pos += len(n)
    return _SEP.join(parts), bounds


def _region_at(offset: int, bounds: Sequence[tuple[int, int, TextRegion]]) -> TextRegion | None:
    for start, end, r in bounds:
        if start <= offset < end:
            return r
    return None


@dataclass
class GroundingReport:
    pmid: str
    total_quotes: int = 0
    grounded_quotes: int = 0
    ungrounded: list[tuple[str, str]] = field(default_factory=list)  # (variant as_written, quote)
    low_confidence_variants: list[str] = field(default_factory=list)
    # Provenance, computed from where each quote matched rather than self-reported.
    quote_regions: list[tuple[int, str, str]] = field(default_factory=list)  # (variant index, as_written, "kind:label")
    region_counts: dict[str, int] = field(default_factory=dict)              # kind -> grounded quote count
    figure_sourced: list[str] = field(default_factory=list)                   # variants with ANY figure-transcription support
    model_transcribed_only: list[str] = field(default_factory=list)          # ... and no other support at all
    overconfident_transcribed: list[str] = field(default_factory=list)        # figure-sourced yet claiming high confidence

    @property
    def needs_review(self) -> bool:
        return bool(self.ungrounded) or bool(self.low_confidence_variants) or bool(self.figure_sourced)

    @property
    def grounded_fraction(self) -> float:
        return 1.0 if self.total_quotes == 0 else self.grounded_quotes / self.total_quotes


def ground(
    extraction: PaperExtraction,
    source_text: str,
    regions: Sequence[TextRegion] = (),
) -> GroundingReport:
    """String-match every evidence quote against the text that was sent to the model.

    With `regions`, also records which region each quote matched, so a fact taken
    from a deterministically parsed supplement can be told apart from one taken
    from a model-generated figure transcription. Passing no regions reproduces
    the original behaviour exactly.

    Note that region matching is marginally stricter: a quote spanning the seam
    between two regions no longer grounds. That is correct, since such a quote is
    not verbatim in either source.
    """
    if regions:
        text_norm, bounds = _normalise_regions(source_text, regions)
    else:
        text_norm, bounds = normalise(source_text), []

    rep = GroundingReport(pmid=extraction.pmid)
    for idx, v in enumerate(extraction.variants):
        # Functional evidence is now one sentence rather than a list of assay
        # objects, and it is the model's own prose rather than a quote, so it is
        # not grounded. Only `evidence_quotes` claims to be verbatim.
        quotes = list(v.evidence_quotes)
        kinds: set[str] = set()
        for q in quotes:
            rep.total_quotes += 1
            at = find_quote(q, text_norm)
            if at is None:
                rep.ungrounded.append((v.as_written, q))
                continue
            rep.grounded_quotes += 1
            region = _region_at(at, bounds)
            if region is not None:
                kinds.add(region.kind)
                tag = f"{region.kind}:{region.label}" if region.label else region.kind
                rep.quote_regions.append((idx, v.as_written, tag))
                rep.region_counts[region.kind] = rep.region_counts.get(region.kind, 0) + 1
        # Only meaningful when something grounded: a variant with no grounded quote
        # at all is already a louder, different problem.
        # "Any figure support" is the flag the published provenance column wants.
        # Exclusivity is too strict to be useful on its own: a model can pair a
        # figure quote with a generic body sentence ("variants are shown in
        # Figure 4") and escape it, which is exactly what happened on the first
        # cohort paper where all 38 records came from a figure.
        if "figure_transcript" in kinds:
            rep.figure_sourced.append(v.as_written)
            # The prompt asks for less than high confidence on figure evidence,
            # but self-policing is not a guarantee. We know which variants these
            # are, so check rather than trust.
            if v.confidence == Confidence.high:
                rep.overconfident_transcribed.append(v.as_written)
        if kinds == {"figure_transcript"}:
            rep.model_transcribed_only.append(v.as_written)
        if v.confidence == Confidence.low:
            rep.low_confidence_variants.append(v.as_written)
    return rep
