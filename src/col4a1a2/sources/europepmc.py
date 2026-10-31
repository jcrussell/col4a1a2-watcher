"""Europe PMC REST: search, abstracts, open-access full text (JATS XML)."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field

from col4a1a2.http import PoliteClient

log = logging.getLogger(__name__)

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


@dataclass
class PaperMeta:
    pmid: str | None
    pmcid: str | None
    doi: str | None
    title: str
    journal: str | None
    pub_year: str | None
    first_pub_date: str | None
    is_open_access: bool
    abstract: str | None
    # Citation + dedup fields. `author_string` is Europe PMC's own rendering
    # ("Morel A, Guey S."), kept verbatim so a citation never has to be rebuilt
    # from parts. `authors` and `affiliation` feed the author/centre-overlap
    # signal used when deciding whether two papers describe the same family.
    author_string: str | None = None
    authors: list[str] = field(default_factory=list)
    affiliation: str | None = None
    source: str = "europepmc"
    # Where the metadata came from, which is not the same as where the paper was
    # found. "europepmc" means a search returned this record and its
    # `is_open_access`/`pmcid` are facts; "pubtator_only" means the Europe PMC
    # lookup did not resolve the PMID and everything below the title was filled
    # in by hand, so absent OA and PMCID mean "not retrieved", not "not open
    # access". Conflating the two cost this project 2,034 papers: see
    # `lookup_pmids`.
    metadata_source: str = "europepmc"
    extra: dict = field(default_factory=dict)


_TAG = re.compile(r"<[^>]+>")


def _plain(s: str | None) -> str | None:
    return _TAG.sub("", html.unescape(s)) if s else s


def _to_meta(r: dict) -> PaperMeta:
    return PaperMeta(
        pmid=r.get("pmid"),
        pmcid=r.get("pmcid"),
        doi=r.get("doi"),
        title=_plain(r.get("title", "")) or "",
        journal=r.get("journalTitle") or (r.get("journalInfo") or {}).get("journal", {}).get("title"),
        pub_year=r.get("pubYear"),
        first_pub_date=r.get("firstPublicationDate"),
        is_open_access=(r.get("isOpenAccess") == "Y"),
        abstract=_plain(r.get("abstractText")),
        author_string=r.get("authorString"),
        authors=[
            " ".join(x for x in (a.get("lastName"), a.get("initials")) if x) or a.get("fullName") or ""
            for a in ((r.get("authorList") or {}).get("author") or [])
        ],
        affiliation=r.get("affiliation"),
        extra={"pubType": r.get("pubTypeList", {}).get("pubType") if isinstance(r.get("pubTypeList"), dict) else None},
    )


def search(
    client: PoliteClient,
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    page_size: int = 100,
    max_results: int | None = None,
    ttl: float | None = 7 * 86400,
) -> list[PaperMeta]:
    """Run a Europe PMC search with cursor pagination. Dates are YYYY-MM-DD."""
    q = query
    if date_from or date_to:
        q = f"({q}) AND FIRST_PDATE:[{date_from or '1900-01-01'} TO {date_to or '2100-01-01'}]"
    cursor = "*"
    out: list[PaperMeta] = []
    while True:
        resp = client.get(
            f"{BASE}/search",
            params={
                "query": q,
                "format": "json",
                "resultType": "core",
                "pageSize": str(page_size),
                "cursorMark": cursor,
                "sort": "P_PDATE_D desc",
            },
            ttl=ttl,
        )
        data = resp.require_ok("Europe PMC search").json()
        results = data.get("resultList", {}).get("result", [])
        out.extend(_to_meta(r) for r in results)
        if max_results is not None and len(out) >= max_results:
            return out[:max_results]
        next_cursor = data.get("nextCursorMark")
        if not results or not next_cursor or next_cursor == cursor:
            return out
        cursor = next_cursor


def lookup_pmids(client: PoliteClient, pmids: list[str], *, chunk: int = 50,
                 min_resolution: float = 0.5) -> dict[str, PaperMeta]:
    """Europe PMC metadata for a list of PMIDs, keyed by PMID.

    **`EXT_ID`, never `PMID`.** Europe PMC does not index a `PMID` field, so a
    `PMID:11985779` query returns hitCount 0 while `EXT_ID:11985779` returns the
    paper. This module asked the wrong question for the whole bootstrap: of
    2,075 unique PMIDs sent through the old query, 41 resolved. The caller then
    filled in the misses by hand with `is_open_access=False`, the open-access
    filter dropped them, and the ledger recorded 2,034 papers as closed access
    without ever having looked. Re-querying a random 100 of them with `EXT_ID`
    resolved 97, of which 92 had a PMCID and 91 were flagged open access.

    Two details. `EXT_ID` matches PMC-sourced records as well as MED ones -- one
    response came back `source=PMC, pmid=null` -- so records without a PMID are
    dropped rather than keyed wrongly. And the resolution rate is checked per
    chunk and logged: the old failure was silent precisely because nothing
    compared what was asked for against what came back.
    """
    out: dict[str, PaperMeta] = {}
    for i in range(0, len(pmids), chunk):
        batch = pmids[i : i + chunk]
        query = " OR ".join(f"EXT_ID:{p}" for p in batch)
        found = 0
        for m in search(client, query, page_size=100):
            if not m.pmid:
                continue  # a PMC-keyed duplicate of a record we want by PMID
            out.setdefault(m.pmid, m)
            found += 1
        if batch and found < len(batch) * min_resolution:
            log.warning("Europe PMC PMID lookup resolved only %d of %d; "
                        "check the query field", found, len(batch))
    log.info("Europe PMC PMID lookup: %d of %d resolved", len(out), len(pmids))
    return out


def hit_count(client: PoliteClient, query: str, ttl: float | None = 86400) -> int:
    resp = client.get(
        f"{BASE}/search",
        params={"query": query, "format": "json", "pageSize": "1"},
        ttl=ttl,
    )
    return int(resp.require_ok("Europe PMC hit count").json().get("hitCount", 0))


def full_text_xml(client: PoliteClient, pmcid: str) -> bytes | None:
    """Return JATS XML for an open-access article, or None if unavailable."""
    resp = client.get(f"{BASE}/{pmcid}/fullTextXML", ttl=None)
    if resp.ok and resp.body.strip():
        return resp.body
    log.info("no Europe PMC full text for %s (HTTP %d)", pmcid, resp.status)
    return None


def references(client: PoliteClient, pmid: str, *, page_size: int = 1000,
               ttl: float | None = 30 * 86400) -> list[dict]:
    """PMIDs a paper cites, for checking a claimed prior report against the bibliography.

    A citation is necessary but not sufficient evidence of patient overlap:
    cohort papers cite prior literature for discussion as well. Use it to prune
    the candidate set, not to decide.
    """
    out: list[dict] = []
    page = 1
    while True:
        resp = client.get(
            f"{BASE}/MED/{pmid}/references",
            params={"format": "json", "pageSize": str(page_size), "page": str(page)},
            ttl=ttl,
        )
        # Raises rather than returning what it has: a partial bibliography
        # would read as "this paper does not cite that one".
        data = resp.require_ok(f"Europe PMC references for {pmid}").json()
        refs = (data.get("referenceList") or {}).get("reference") or []
        for r in refs:
            out.append({
                "pmid": str(r["id"]) if r.get("source") == "MED" and r.get("id") else None,
                "doi": r.get("doi"),
                "title": _plain(r.get("title")),
                "authors": r.get("authorString"),
                "year": r.get("pubYear"),
            })
        if len(refs) < page_size:
            return out
        page += 1


def citation(m: PaperMeta) -> str:
    """A one-line human citation for a table row.

    Every fact in the published table is attributable to a paper, so each row
    carries both this string and the structured identifiers it was built from.
    Format: "Morel A, et al. J Med Genet. 2026. PMID:42438399. doi:10.1/xyz".
    """
    authors = (m.author_string or "").strip()
    if authors.count(",") >= 1:
        authors = authors.split(",")[0].strip().rstrip(".") + ", et al."
    parts = [
        authors or None,
        m.journal,
        m.pub_year or ((m.first_pub_date or "")[:4] or None),
        f"PMID:{m.pmid}" if m.pmid else None,
        f"doi:{m.doi}" if m.doi else None,
    ]
    return ". ".join(x.strip().rstrip(".") for x in parts if x)
