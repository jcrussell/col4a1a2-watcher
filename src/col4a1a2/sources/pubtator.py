"""PubTator 3 API: entity-aware search and BioC annotations."""

from __future__ import annotations

from col4a1a2.http import PoliteClient

BASE = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"

ANNOTATION_TYPES = {"Gene", "Variant", "Mutation", "Disease", "Species"}


def search(
    client: PoliteClient,
    text: str,
    *,
    page_size: int = 100,
    max_results: int | None = None,
    ttl: float | None = 7 * 86400,
) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        resp = client.get(
            f"{BASE}/search/",
            params={"text": text, "page_size": str(page_size), "page": str(page)},
            ttl=ttl,
        )
        if not resp.ok:
            raise RuntimeError(f"PubTator search failed: HTTP {resp.status}")
        data = resp.json()
        results = data.get("results", [])
        out.extend(results)
        if max_results is not None and len(out) >= max_results:
            return out[:max_results]
        total_pages = int(data.get("total_pages", 1))
        if not results or page >= total_pages:
            return out
        page += 1


def export_biocjson(client: PoliteClient, pmids: list[str], chunk: int = 100) -> dict[str, dict]:
    """Return {pmid: BioC document} for the given PMIDs."""
    docs: dict[str, dict] = {}
    for i in range(0, len(pmids), chunk):
        batch = pmids[i : i + chunk]
        resp = client.get(
            f"{BASE}/publications/export/biocjson",
            params={"pmids": ",".join(batch)},
            ttl=None,
        )
        if not resp.ok:
            continue
        data = resp.json()
        items = data.get("PubTator3", data) if isinstance(data, dict) else data
        for doc in items:
            pmid = str(doc.get("pmid") or doc.get("id") or "")
            if pmid:
                docs[pmid] = doc
    return docs


def annotations(doc: dict) -> list[dict]:
    """Flatten a BioC document to unique (type, text, identifier) mentions."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for passage in doc.get("passages", []):
        for a in passage.get("annotations", []):
            infons = a.get("infons", {})
            t = infons.get("type")
            if t not in ANNOTATION_TYPES:
                continue
            key = (t, a.get("text"), infons.get("identifier"))
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": t, "text": a.get("text"), "identifier": infons.get("identifier")})
    return out


VARIANT_TYPES = {"Variant", "Mutation", "ProteinMutation", "DNAMutation"}


def gene_linked_variants(doc: dict, gene_ids: set[str] | set[int]) -> list[dict]:
    """Variant mentions that PubTator normalised to one of `gene_ids`.

    PubTator's tmVar annotations carry `CorrespondingGene`, surfaced as the
    `gene_ids` infon. A variant mention tied to COL4A1 (1282) or COL4A2 (1284)
    is near-proof that the paper discusses a variant *in* that gene, rather
    than merely mentioning the gene somewhere. Measured on the 2026-10-29
    snapshot: precision 1.00, recall 0.20 (PubTator usually annotates the
    title/abstract only), so this is a promoter, never a gate.
    """
    want = {str(g) for g in gene_ids}
    out: list[dict] = []
    seen: set[tuple] = set()
    for passage in doc.get("passages", []):
        for a in passage.get("annotations", []):
            inf = a.get("infons", {})
            if inf.get("type") not in VARIANT_TYPES:
                continue
            ids = inf.get("gene_ids") or ([inf["gene_id"]] if inf.get("gene_id") else [])
            if not {str(g) for g in ids} & want:
                continue
            key = (a.get("text"), inf.get("hgvs"))
            if key in seen:
                continue
            seen.add(key)
            out.append({"text": a.get("text"), "hgvs": inf.get("hgvs"),
                        "gene_id": str(inf.get("gene_id") or ""), "rsid": inf.get("rsid")})
    return out
