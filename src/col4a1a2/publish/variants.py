"""The per-variant table: evidence records grouped by variant, with counts as ranges.

The same family is often reported in several papers, and papers carry no
patient identifiers, so summing probands across papers overstates the
evidence, and only ever in that direction. Nothing is merged here. Each count
is a range (`docs/methods.md`, Deduplication):

- `*_min` assumes every overlap that could exist does: the largest single
  paper's count, as if every other report re-described its patients.
- `*_max` assumes papers are independent, except records whose paper says the
  family was reported before. It is never below the minimum.

Three kinds of record do not count as clinical probands. Reviews are pointers:
their PMIDs are listed and they contribute no counts, since they are the
papers most likely to recycle published patients. Carriers found by screening
a population of at least `SCREEN_MIN` people go in `screened_carriers`, since
a biobank carrier is not a patient. Records with no variant key (structural
variants, mostly) cannot be grouped and appear only in the evidence table.

Inheritance is counted in papers, not probands: "three papers report a
confirmed de novo case" survives overlap; a summed proband count does not.

Records are grouped on the protein change when there is one, so a paper giving
only `p.Gly1239Arg` joins papers giving the coding change. A protein group can
hold different coding changes with the same consequence; `coding_keys` lists
them rather than hiding it.

The residual risk, stated wherever the table is published: a large cohort
reporting totals without per-patient detail can overlap earlier case reports
invisibly, and consortia reuse cohorts.
"""

from __future__ import annotations

from collections import defaultdict

# Below this, `n_tested` describes a clinical series; at or above it, a
# population screen. 187 of 435 probands came from 5 such records in the first
# full run (`docs/measurements.md`).
SCREEN_MIN = 1000

RESIDUAL_RISK = ("Counts are ranges, not merges: probands_min assumes every possible overlap between papers is "
                 "real, probands_max that papers are independent. A large cohort reporting totals without "
                 "per-patient detail can still overlap earlier case reports invisibly, and consortia reuse cohorts.")


def group_key(e: dict) -> str:
    return e.get("protein_key") or e.get("variant_key") or ""


def _range(records: list[dict], field: str) -> tuple[int, int]:
    per_paper: dict[str, int] = defaultdict(int)
    for r in records:
        per_paper[r["pmid"]] += r[field]
    low = max(per_paper.values(), default=0)
    independent = sum(r[field] for r in records if not r["same_family_as_prior_report"])
    return low, max(independent, low)


def _papers(records: list[dict], field: str) -> int:
    return len({r["pmid"] for r in records if r[field]})


def roll_up(evidence: list[dict]) -> list[dict]:
    """One row per variant key, sorted by gene and key."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in evidence:
        if group_key(e):
            groups[(e["gene"], group_key(e))].append(e)
    out = []
    for (gene, key), recs in sorted(groups.items()):
        primary = [r for r in recs if r["paper_type"] != "review"]
        clinical = [r for r in primary if r["n_tested"] < SCREEN_MIN]
        probands, families = _range(clinical, "n_probands"), _range(clinical, "n_families")
        years = sorted(r["year"] for r in recs if r["year"])
        out.append({
            "variant_id": key, "gene": gene,
            "grouped_by": "protein" if recs[0].get("protein_key") else "coding",
            "coding_keys": sorted({r["variant_key"] for r in recs if r["key_level"] == "coding"}),
            "grch38": sorted({r["grch38"] for r in recs if r["grch38"]}),
            "variant_types": sorted({r["variant_type"] for r in recs if r["variant_type"]}),
            "n_records": len(recs), "n_papers": len({r["pmid"] for r in recs}),
            "n_primary_papers": len({r["pmid"] for r in primary}),
            "probands_min": probands[0], "probands_max": probands[1],
            "families_min": families[0], "families_max": families[1],
            "screened_carriers": sum(r["n_probands"] for r in primary if r["n_tested"] >= SCREEN_MIN),
            "papers_de_novo_confirmed": _papers(primary, "inh_de_novo_confirmed"),
            "papers_de_novo_assumed": _papers(primary, "inh_de_novo_assumed"),
            "papers_inherited": _papers(primary, "inh_inherited"),
            "records_prior_report": sum(1 for r in primary if r["same_family_as_prior_report"]),
            "papers_functional": _papers(primary, "functional_evidence"),
            "phenotype_systems": ", ".join(sorted({p.strip() for r in recs
                                                   for p in (r["phenotype_systems"] or "").split(",") if p.strip()})),
            "first_year": years[0] if years else "", "last_year": years[-1] if years else "",
            "pmids": sorted({r["pmid"] for r in recs}),
            "pointer_pmids": sorted({r["pmid"] for r in recs if r["paper_type"] == "review"}),
            "normalization_flags": ", ".join(sorted({f.strip() for r in recs
                                                     for f in r["normalization_flags"].split(",") if f.strip()})),
        })
    return out
