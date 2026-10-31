"""NCBI E-utilities: fallback full text from PubMed Central."""

from __future__ import annotations

from col4a1a2.http import PoliteClient

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def pmc_full_text_xml(client: PoliteClient, pmcid: str, email: str) -> bytes | None:
    pmc_num = pmcid.replace("PMC", "")
    resp = client.get(
        f"{EUTILS}/efetch.fcgi",
        params={"db": "pmc", "id": pmc_num, "rettype": "xml", "tool": "col4a1a2-watcher", "email": email},
        ttl=None,
    )
    if resp.ok and b"<article" in resp.body:
        return resp.body
    return None
