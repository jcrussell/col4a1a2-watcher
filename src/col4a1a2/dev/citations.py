"""Measure how often papers reporting the same variant cite each other.

    uv run col4a1a2-dev citations "p.Gly773Arg" "G773R"

Each argument is an alternative spelling of one variant. The script finds every
paper whose full text mentions any of them, orders them by publication date, and
checks each paper's bibliography against the earlier papers in the set.

Why it matters: if later papers reliably cited the earlier reports of a variant,
the citation graph would be a deduplication instrument. Measured, they do not,
so it is only useful for resolving an explicit prior-report claim. Network only,
through the rate-limited client; no model calls.
"""

from __future__ import annotations

import sys

from col4a1a2.cli import make_client
from col4a1a2.config import load_settings
from col4a1a2.sources import europepmc


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    spellings = argv
    if not spellings:
        print(__doc__)
        return 2

    client = make_client(load_settings())
    terms = " OR ".join(f'"{s}"' for s in spellings)
    query = f"(COL4A1 OR COL4A2) AND ({terms})"

    papers = [m for m in europepmc.search(client, query, page_size=100, max_results=60) if m.pmid]
    papers.sort(key=lambda m: m.first_pub_date or "")
    print(f"{query}\n{len(papers)} papers\n")
    if not papers:
        return 0

    pmids = [m.pmid for m in papers]
    refs = {m.pmid: {r["pmid"] for r in europepmc.references(client, m.pmid) if r["pmid"]} for m in papers}

    hit_pairs = 0
    for i, m in enumerate(papers):
        earlier = set(pmids[:i])
        cited = earlier & refs[m.pmid]
        hit_pairs += len(cited)
        kind = ",".join((m.extra or {}).get("pubType") or [])[:28]
        print(f"  {m.first_pub_date or '????-??-??'} {m.pmid:>9} "
              f"refs={len(refs[m.pmid]):>4} cites {len(cited)}/{len(earlier)} earlier "
              f"[{kind}] {(m.title or '')[:44]}")

    total_pairs = sum(range(len(papers)))
    if total_pairs:
        print(f"\nlater-cites-earlier pairs: {hit_pairs}/{total_pairs} = {hit_pairs / total_pairs:.0%}")
    print(f"network={client.network_requests} cache={client.cache_hits} "
          f"slept={client.limiter.slept_total:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
