"""Ensembl REST: VEP for HGVS normalization, reference bases, GRCh37 to GRCh38 mapping.

Terms (checked 2026-11-01, see `docs/sources.md`): Ensembl "imposes no
restrictions on access to, or use of, the data", with third-party constraints
on some bundled data. Every VEP plugin is off by default, and the restrictive
ones live there (AlphaMissense, REVEL, SpliceAI, ClinPred), so none is ever
requested. A default response still carries co-located variants and SIFT and
PolyPhen; those stay in the HTTP cache, and only coordinates, HGVS and
consequence terms leave this module.

Behavior measured on release 116:

- Only the current RefSeq version resolves. `NM_001845.5` is a 400 ("Could not
  get a Transcript object") and unversioned `NM_001845` is a 500, so callers
  send the reference transcript with its version, never the paper's.
- A wrong reference base is a 400 naming both alleles.
- Protein input with the correct residue is a 400 ("Could not uniquely
  determine nucleotide change"), and with the wrong residue a different 400;
  `patterns.VEP_PROTEIN_*` tell them apart.

A 400 is a cacheable answer about the input. A 5xx after retries is an outage
and is returned as `unavailable`, so the next run asks again. Answers are
cached without expiry; `ttl=0` asks afresh and replaces the cached response,
which a refresh needs so that an old answer is never stamped with a new
release.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote

from col4a1a2.http import PoliteClient, TooManyFailures

log = logging.getLogger("col4a1a2")

BASE = "https://rest.ensembl.org"
HEADERS = {"Accept": "application/json"}


@dataclass
class VepAnswer:
    status: str  # ok | rejected | unavailable
    error: str = ""
    chrom: str = ""
    start: int | None = None
    end: int | None = None
    allele_string: str = ""
    hgvs_c: str = ""
    hgvs_p: str = ""
    consequence: list[str] = field(default_factory=list)


def _answer(resp, transcript_id: str) -> VepAnswer:
    data = resp.json()
    if isinstance(data, dict):
        return VepAnswer(status="rejected", error=str(data.get("error", "")).strip() or f"HTTP {resp.status}")
    if not resp.ok or not data:
        return VepAnswer(status="rejected", error=f"HTTP {resp.status}, empty answer")
    d = data[0]
    tc = next((t for t in d.get("transcript_consequences") or [] if t.get("transcript_id") == transcript_id), None)
    if tc is None:
        return VepAnswer(status="rejected", error=f"no consequence on {transcript_id}")
    return VepAnswer(status="ok", chrom=str(d.get("seq_region_name", "")), start=d.get("start"), end=d.get("end"),
                     allele_string=d.get("allele_string", ""), hgvs_c=tc.get("hgvsc") or "",
                     hgvs_p=tc.get("hgvsp") or "", consequence=list(tc.get("consequence_terms") or []))


def vep_hgvs(client: PoliteClient, notation: str, transcript_id: str, *, ttl: float | None = None) -> VepAnswer:
    """VEP on one HGVS string, reporting the consequence on `transcript_id` only."""
    try:
        resp = client.get(f"{BASE}/vep/human/hgvs/{quote(notation, safe='')}",
                          params={"refseq": "1", "hgvs": "1", "transcript_id": transcript_id},
                          headers=HEADERS, ttl=ttl)
    except TooManyFailures as e:
        return VepAnswer(status="unavailable", error=str(e))
    return _answer(resp, transcript_id)


def vep_region(client: PoliteClient, chrom: str, pos: int, alt: str, transcript_id: str, *,
               ttl: float | None = None) -> VepAnswer:
    """VEP on a GRCh38 single-base substitution, forward strand."""
    try:
        resp = client.get(f"{BASE}/vep/human/region/{chrom}:{pos}-{pos}:1/{alt}",
                          params={"refseq": "1", "hgvs": "1", "transcript_id": transcript_id},
                          headers=HEADERS, ttl=ttl)
    except TooManyFailures as e:
        return VepAnswer(status="unavailable", error=str(e))
    return _answer(resp, transcript_id)


def reference_base(client: PoliteClient, chrom: str, pos: int, assembly: str, *,
                   ttl: float | None = None) -> str | None:
    """The forward-strand base at `chrom:pos` on GRCh37 or GRCh38, or None if unavailable."""
    try:
        resp = client.get(f"{BASE}/sequence/region/human/{chrom}:{pos}..{pos}:1",
                          params={"coord_system_version": assembly}, headers=HEADERS, ttl=ttl)
    except TooManyFailures:
        return None
    return (resp.json().get("seq") or "").upper() or None if resp.ok else None


def grch37_to_grch38(client: PoliteClient, chrom: str, pos: int, *, ttl: float | None = None) -> int | None:
    """One GRCh37 position on GRCh38, or None unless it maps to exactly one base."""
    try:
        resp = client.get(f"{BASE}/map/human/GRCh37/{chrom}:{pos}..{pos}:1/GRCh38", headers=HEADERS, ttl=ttl)
    except TooManyFailures:
        return None
    maps = resp.json().get("mappings") or [] if resp.ok else []
    if len(maps) != 1 or maps[0]["mapped"]["start"] != maps[0]["mapped"]["end"]:
        return None
    return int(maps[0]["mapped"]["start"])


def release(client: PoliteClient) -> int | None:
    """The Ensembl release serving answers, recorded on every row."""
    try:
        resp = client.get(f"{BASE}/info/software", headers=HEADERS, ttl=3600)
    except TooManyFailures:
        return None
    return int(resp.json().get("release")) if resp.ok else None
