"""Deterministic checks and derivations over extracted records.

Pure functions, no network, no model. Two honest caveats measured over the 177
records in the 2026-10-30 batch, both of which shrank this module's ambitions:

**The consistency checks fire zero times.** Every one of them -- inheritance
sums, families against probands, carriers against probands, a phenotype marked
present with no detail, a prior-report flag with no reason -- passes on all 177
records. They are kept because they are free and will eventually catch
something, not because they are catching anything now. They do *not* reduce
what a human or an adversarial reviewer has to look at.

**Deriving `variant_type` is worse than asking the model, not better.** 63 of
177 records carry no `hgvs_p`, and `hgvs_c` alone cannot separate missense from
nonsense from synonymous: `c.164G>A` is any of the three without the transcript.
A correct pure function returns `other` for 95 records where the model returns
it for 36. So this is a *cross-check on the subset where the protein change is
unambiguous*, never the primary, and the model's value remains what gets
published. Predicate order matters: testing the glycine pattern first fires
`missense_glycine` on seven records the model classified correctly, because
`p.(Gly423Trpfs*19)` is a frameshift and `p.(Gly2=)` is synonymous.
"""

from __future__ import annotations

from col4a1a2.extract.schema import PHENOTYPE_SYSTEMS

import re
from dataclasses import dataclass

# Ordered most-specific first. Order is the whole correctness story here.
_FRAMESHIFT = re.compile(r"fs(\*|Ter|\d|$)", re.I)
_SYNONYMOUS = re.compile(r"=\)?\s*$")
_NONSENSE = re.compile(r"(Ter\d*|\*\d*)\s*\)?\s*$", re.I)
_INDEL = re.compile(r"(del|dup|ins|delins)", re.I)
_GLYCINE = re.compile(r"^p\.\(?Gly\d+(?!\d)(?!.*fs)", re.I)
_MISSENSE = re.compile(r"^p\.\(?[A-Z][a-z]{2}\d+[A-Z][a-z]{2}\)?$")
_SPLICE_C = re.compile(r"c\.\d+[+-]\d+")
_UTR_C = re.compile(r"c\.[*-]\d+")


@dataclass(frozen=True)
class Finding:
    """One deterministic problem with one record."""

    pmid: str
    as_written: str
    check: str
    detail: str


def derive_variant_type(hgvs_p: str, hgvs_c: str) -> str | None:
    """Classify from nomenclature, or None when the inputs cannot decide.

    Returning None is the common case and the point: it means "no opinion",
    not "other". Only a non-None result is worth comparing with the model.
    """
    p = (hgvs_p or "").strip()
    c = (hgvs_c or "").strip()

    if p:
        # Frameshift before glycine: p.(Gly423Trpfs*19) is a frameshift.
        if _FRAMESHIFT.search(p):
            return "frameshift"
        # Synonymous before glycine: p.(Gly2=) changes nothing.
        if _SYNONYMOUS.search(p):
            return "other"
        if _NONSENSE.search(p):
            return "nonsense"
        if _INDEL.search(p):
            return "inframe_indel"
        if _GLYCINE.match(p) and _MISSENSE.match(p):
            return "missense_glycine"
        if _MISSENSE.match(p):
            return "missense_other"
        return None

    # Without a protein change, only the structural cases are decidable.
    if c:
        if _SPLICE_C.search(c):
            return "splice"
        if _UTR_C.search(c):
            return "utr"
        if _INDEL.search(c):
            return None  # del/dup may be in-frame or frameshift; cannot tell
    return None




def check_record(pmid: str, v: dict) -> list[Finding]:
    """Deterministic consistency checks on one variant record."""
    out: list[Finding] = []
    w = v.get("as_written", "")

    def add(check: str, detail: str) -> None:
        out.append(Finding(pmid=pmid, as_written=w, check=check, detail=detail))

    inh_fields = ("inh_de_novo_confirmed", "inh_de_novo_assumed", "inh_inherited", "inh_unknown")
    total = sum(int(v.get(k) or 0) for k in inh_fields)
    if total != v.get("n_probands"):
        add("inheritance_sum", f"inheritance sums to {total}, n_probands is {v.get('n_probands')}")

    probands, families = v.get("n_probands"), v.get("n_families")
    if isinstance(probands, int) and isinstance(families, int):
        if families > probands:
            add("families_gt_probands", f"{families} families for {probands} probands")
        # The corpus's worst record is 174 probands in 174 families for a
        # population-cohort variant. Equality is normal for small numbers and
        # implausible in bulk, so flag it by magnitude rather than by ratio.
        elif families == probands >= 20:
            add("families_equal_probands_at_scale",
                f"{probands} probands recorded as {families} distinct families")

    tested = v.get("n_tested")
    if isinstance(tested, int) and tested > 0 and isinstance(probands, int) and probands > tested:
        add("probands_gt_tested", f"{probands} probands out of {tested} tested")
    # The error this field exists for: carriers found by screening a large
    # cohort are not that many independent families.
    if isinstance(tested, int) and tested >= 1000 and isinstance(families, int) and families >= 20:
        add("many_families_from_a_cohort",
            f"{families} families claimed from a {tested}-person screen")

    carriers = v.get("n_total_carriers")
    if isinstance(carriers, int) and isinstance(probands, int) and carriers < probands:
        add("carriers_lt_probands", f"{carriers} total carriers below {probands} probands")

    systems = [t.strip() for t in (v.get("phenotype_systems") or "").split(",") if t.strip()]
    if systems and not (v.get("phenotype_details") or "").strip():
        add("present_without_details", "a phenotype system is listed with no details")
    unknown = [t for t in systems if t not in PHENOTYPE_SYSTEMS]
    if unknown:
        # A token list buys a property back from the grammar budget at the cost
        # of free-text drift, so the vocabulary is checked here instead.
        add("unknown_phenotype_system", f"not in the vocabulary: {', '.join(unknown)}")

    if v.get("same_family_as_prior_report") and not (v.get("prior_report_reason") or "").strip():
        add("prior_report_without_reason", "flagged as a prior report with no reason given")

    derived = derive_variant_type(v.get("hgvs_p", ""), v.get("hgvs_c", ""))
    if derived is not None and derived != v.get("variant_type"):
        add("variant_type_disagreement",
            f"nomenclature implies {derived}, record says {v.get('variant_type')}")

    return out


def check_extraction(extraction: dict) -> list[Finding]:
    pmid = extraction.get("pmid", "")
    return [f for v in extraction.get("variants") or [] for f in check_record(pmid, v)]
