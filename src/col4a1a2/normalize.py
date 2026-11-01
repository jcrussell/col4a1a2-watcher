"""HGVS normalization: one canonical key per variant, from Ensembl VEP.

    uv run col4a1a2 normalize [--refresh]

Papers write one variant many ways: on transcript versions VEP no longer
serves, with one-letter amino acids, as an insertion VEP calls a duplication,
or as bare coordinates on an unstated build. Grouping records by variant needs
one spelling, and this module gets it from VEP on the reference transcript.

Two halves, deliberately apart:

- `query_for` and `ask` turn a record into a request and VEP's answer into a
  row of `data/curated/normalized.jsonl`. Rows are keyed by the request, so a
  variant reported in five papers is asked once, and the file is tracked so
  `publish` builds offline.
- `link` joins a record to its row and computes the published columns. It is
  pure, so `publish` and the command's summary share it.

What normalization can tell us is also a check on the extraction. A
coding-DNA change VEP rejects because the reference base is wrong, or one
whose translation is not the protein change the paper states, is flagged:
wrong transcript, off-by-one numbering and typos all land there. Nothing is
corrected silently, and a record that cannot be resolved has no key rather
than a guessed one.

Structural variants (copy-number changes, rearrangements) are not forced into
HGVS: they have key level `none` until they get an interval model.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from col4a1a2 import jsonl
from col4a1a2.digest import sha256_json
from col4a1a2.patterns import ACCESSION, GENOMIC_AS_WRITTEN, VEP_PROTEIN_NOT_UNIQUE, VEP_REFERENCE_MISMATCH
from col4a1a2.sources import ensembl


@dataclass(frozen=True)
class Reference:
    transcript: str
    protein: str
    # Accessions whose coding numbering is the reference's: the unversioned
    # accession, and the Ensembl transcript VEP maps onto it. Verified on
    # release 116 (`docs/measurements.md`).
    same_numbering: frozenset[str]


REFERENCE = {
    "COL4A1": Reference("NM_001845.6", "NP_001836.3", frozenset({"NM_001845", "ENST00000375820"})),
    "COL4A2": Reference("NM_001846.4", "NP_001837.2", frozenset({"NM_001846", "ENST00000360467"})),
}

AA3 = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu", "G": "Gly",
       "H": "His", "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe", "P": "Pro", "S": "Ser",
       "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "*": "Ter", "X": "Ter"}
_PREFIX = re.compile(r"^[A-Za-z]{2,4}_?\d+(?:\.\d+)?\s*:\s*")
_ONE_LETTER = re.compile(r"^p\.([ACDEFGHIKLMNPQRSTVWY*X])(\d+)(.*)$")
_SUBSTITUTION = re.compile(r"^p\.[A-Z][a-z]{2}\d+(?:[A-Z][a-z]{2}|=)$")


def clean_c(s: str | None) -> str | None:
    """Coding-DNA change without its transcript, spaces or brackets; None if not one."""
    s = _PREFIX.sub("", (s or "").strip()).replace(" ", "")
    s = re.sub(r"^c\.\((.*)\)$", r"c.\1", s)
    return s if s.startswith("c.") and len(s) > 2 else None


def clean_p(s: str | None) -> str | None:
    """Protein change in three-letter form without brackets or prefix; None if not one."""
    s = _PREFIX.sub("", (s or "").strip()).replace(" ", "")
    s = re.sub(r"^p\.\((.*)\)$", r"p.\1", s)
    if not s.startswith("p.") or len(s) <= 2:
        return None
    m = _ONE_LETTER.match(s)
    if m:
        ref, pos, rest = m.groups()
        if len(rest) == 1 and rest.upper() in AA3:
            rest = AA3[rest.upper()]
        s = f"p.{AA3[ref]}{pos}{rest}"
    return s.replace("*", "Ter")


def queries_for(v: dict) -> list[dict]:
    """Every request that could normalize a record, most precise first.

    A coding change, then genomic coordinates (which fix the nucleotide change
    a protein change cannot), then a protein change. `link` keys the record on
    the first that resolves, so coordinates on an ambiguous build fall back to
    the protein change the paper also gives.
    """
    ref = REFERENCE.get(v.get("gene", ""))
    if ref is None:
        return []
    out = []
    c = clean_c(v.get("hgvs_c"))
    if c:
        out.append({"kind": "hgvs_c", "gene": v["gene"], "notation": f"{ref.transcript}:{c}",
                    "transcript_id": ref.transcript})
    m = GENOMIC_AS_WRITTEN.match((v.get("as_written") or "").strip())
    if m:
        chrom, pos, r, a = m.groups()
        out.append({"kind": "genomic", "gene": v["gene"],
                    "notation": f"{chrom}:{pos.replace(',', '')}:{r.upper()}:{a.upper()}",
                    "transcript_id": ref.transcript})
    p = clean_p(v.get("hgvs_p"))
    if p and _SUBSTITUTION.match(p):
        out.append({"kind": "hgvs_p", "gene": v["gene"], "notation": f"{ref.protein}:{p}",
                    "transcript_id": ref.transcript})
    return out


def query_for(v: dict) -> dict | None:
    """The most precise request for a record, or None if nothing can be asked."""
    qs = queries_for(v)
    return qs[0] if qs else None


def query_key(q: dict) -> str:
    return sha256_json(q)


def ask(client, q: dict, release: int | None, *, ttl: float | None = None) -> dict | None:
    """Ask Ensembl one query; the row to store, or None if Ensembl was unavailable.

    `ttl=0` bypasses the HTTP cache, for a refresh.
    """
    build = None
    if q["kind"] == "genomic":
        chrom, pos, r, a = q["notation"].split(":")
        bases = {asm: ensembl.reference_base(client, chrom, int(pos), asm, ttl=ttl) for asm in ("GRCh38", "GRCh37")}
        if None in bases.values():
            return None
        matches = [asm for asm, b in bases.items() if b == r]
        if len(matches) != 1:
            build = "ambiguous" if matches else "neither"
            ans = ensembl.VepAnswer(status="rejected", error=f"reference {r} matches {build} build "
                                                             f"(GRCh38 {bases['GRCh38']}, GRCh37 {bases['GRCh37']})")
        else:
            build = matches[0]
            pos38 = int(pos) if build == "GRCh38" else ensembl.grch37_to_grch38(client, chrom, int(pos), ttl=ttl)
            if pos38 is None:
                return None
            ans = ensembl.vep_region(client, chrom, pos38, a, q["transcript_id"], ttl=ttl)
    else:
        ans = ensembl.vep_hgvs(client, q["notation"], q["transcript_id"], ttl=ttl)
    if ans.status == "unavailable":
        return None
    return {"query_key": query_key(q), **q, "ensembl_release": release, "status": ans.status,
            "error": ans.error, "build_inferred": build, "chrom": ans.chrom, "start": ans.start,
            "end": ans.end, "allele_string": ans.allele_string, "hgvs_c": ans.hgvs_c,
            "hgvs_p": ans.hgvs_p, "consequence": ans.consequence,
            "written_at": dt.datetime.now(dt.UTC).isoformat()}


def normalize_records(records: list[dict], answers: dict[str, dict], ask_one) -> tuple[int, int]:
    """Ask, per record, only what its earlier candidates leave unresolved.

    `ask_one(q)` returns the stored row or None when Ensembl was unavailable;
    `answers` is updated in place. Returns (asked, answered). A record with a
    coding change that resolves never costs a protein query, so the fallback
    adds requests only where it can change a key.
    """
    asked: dict[str, dict | None] = {}
    for v in records:
        for q in queries_for(v):
            k = query_key(q)
            if k not in answers and k not in asked:
                asked[k] = ask_one(q)
                if asked[k] is not None:
                    answers[k] = asked[k]
            if resolve(v, q, answers.get(k))[0]:
                break
    return len(asked), sum(1 for r in asked.values() if r is not None)


def load_answers(path: Path) -> dict[str, dict]:
    """The newest answer per query key."""
    out: dict[str, dict] = {}
    for r in sorted(jsonl.read(path) if path.exists() else [], key=lambda r: r["written_at"]):
        out[r["query_key"]] = r
    return out


def _bare_p(s: str) -> str:
    return clean_p(s) or ""


def resolve(v: dict, q: dict, a: dict | None) -> tuple[dict | None, list[str]]:
    """One candidate's columns, or None if it gives no key, and the flags it raises."""
    if a is None:
        return None, ["not-normalized"]
    if a["status"] != "ok":
        if q["kind"] == "hgvs_p" and VEP_PROTEIN_NOT_UNIQUE.search(a["error"]):
            return {"variant_key": q["notation"], "key_level": "protein", "protein_key": q["notation"],
                    "grch38": ""}, []
        if a.get("build_inferred") == "ambiguous":
            return None, ["build-ambiguous"]
        return None, ["reference-mismatch" if VEP_REFERENCE_MISMATCH.search(a["error"])
                      or a.get("build_inferred") == "neither" else "vep-rejected"]
    flags = []
    protein = a["hgvs_p"] or ""
    start, end = a["start"], a["end"]
    grch38 = f"{a['chrom']}:{start}:{a['allele_string']}" if start == end else \
        f"{a['chrom']}:{start}-{end}:{a['allele_string']}"
    if q["kind"] == "hgvs_p":
        cols = {"variant_key": q["notation"], "key_level": "protein", "protein_key": q["notation"], "grch38": grch38}
    else:
        cols = {"variant_key": a["hgvs_c"], "key_level": "coding", "protein_key": protein, "grch38": grch38}
        paper_p = clean_p(v.get("hgvs_p"))
        if paper_p and _SUBSTITUTION.match(paper_p) and protein and _bare_p(protein) != paper_p:
            flags.append("c-p-mismatch")
    if a.get("build_inferred") == "GRCh37":
        flags.append("build-inferred-grch37")
    return cols, flags


def link(v: dict, answers: dict[str, dict]) -> dict:
    """A record's published normalization columns, from the first candidate query that resolves.

    Flags from candidates tried before it are kept: coordinates on an ambiguous
    build still say so when the protein change supplied the key.
    """
    out = {"variant_key": "", "key_level": "none", "protein_key": "", "grch38": "", "normalization_flags": ""}
    flags: list[str] = []
    ref = REFERENCE.get(v.get("gene", ""))
    stated = ACCESSION.search(v.get("transcript_if_stated") or "")
    if ref and (v.get("transcript_if_stated") or "").strip() and (
            not stated or (stated.group(1) != ref.transcript.split(".")[0] and stated.group(1) not in ref.same_numbering)):
        flags.append("transcript-other")
    for q in queries_for(v):
        cols, raised = resolve(v, q, answers.get(query_key(q)))
        flags += [f for f in raised if f not in flags]
        if cols:
            out.update(cols)
            break
    out["normalization_flags"] = ", ".join(flags)
    return out
