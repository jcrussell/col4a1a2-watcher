#!/usr/bin/env python
"""Score triage rules against hand-labelled papers.

    uv run col4a1a2-dev triage --snapshot data/raw/2026-10-29

Needs a fetched snapshot and `data/curated/labels.json`. Keyword rules
are scored offline from the snapshot. Pass --llm to also score the live Haiku
gate, which costs about a tenth of a cent per paper and needs ANTHROPIC_API_KEY.

Recall is the figure that matters: a false keep costs one full-text read, a
false skip loses the evidence permanently.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from col4a1a2.config import REPO

PERSON = re.compile(
    r"\b(proband|probands|patient|patients|family|families|kindred|pedigree|"
    r"case report|fetus|sibling|siblings|carrier|carriers)\b", re.I)
METHOD = re.compile(
    r"\b(expression|expressed|mRNA|transcriptom\w*|proteom\w*|single[- ]cell|scRNA|"
    r"RNA-seq|knockdown|knockout|overexpress\w*|GWAS|genome[- ]wide association|"
    r"biomarker|molecular docking|immunohistochem\w*|mouse|mice|zebrafish|rat|"
    r"iPSC|cell line|in vitro|differentially expressed|eQTL|summary statistics)\b", re.I)
CLINICAL = re.compile(
    r"\b(variant|mutation|pathogenic|de novo|heterozygous|Gould|HANAC|porencephal\w*|"
    r"schizencephal\w*|h(?:a)?emorrhag\w*|leukoencephalopath\w*|PADMAL|cataract|"
    r"tortuosity|h(?:a)?ematuria|nephropath\w*|myopath\w*|stroke)\b", re.I)
GENE = re.compile(r"COL4A[12]", re.I)
TRANSCRIPT = re.compile(r"NM_001845|NM_001846")
HGVS = re.compile(r"\bc\.\d+[+-]?\d*[ACGT_>a-z]|\bp\.(?:[A-Z][a-z]{2}\d+|[A-Z]\d+)")


def hgvs_near_gene(body: str, window: int = 300) -> int:
    return sum(
        1 for m in GENE.finditer(body)
        if HGVS.search(body[max(0, m.start() - window): m.end() + window])
    )


def llm_decisions() -> dict[str, bool]:
    """Recorded Haiku decisions, from the tracked log rather than one snapshot.

    The log covers every candidate ever triaged, while a snapshot only holds the
    papers that window kept, so the log is the authoritative record.
    """
    path = REPO / "data/curated/triage.jsonl"
    out: dict[str, bool] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["pmid"]] = bool(r["read_full_text"])
    return out


def load(snapshot: Path) -> tuple[dict, dict]:
    papers = {
        r["pmid"]: r
        for r in (json.loads(l) for l in (snapshot / "papers.jsonl").read_text().splitlines() if l.strip())
    }
    labels = json.loads((REPO / "data/curated/labels.json").read_text())["labels"]
    return papers, labels


def signals(row: dict) -> dict:
    title = row.get("title") or ""
    abstract = row.get("abstract") or ""
    ta = f"{title}\n{abstract}"
    body = row.get("text") or ""
    tr = row.get("triage", {})
    return {
        "n_person": len(PERSON.findall(ta)),
        "n_method": len(METHOD.findall(ta)),
        "gene_in_title": bool(GENE.search(title)),
        "clinical": bool(CLINICAL.search(ta)),
        "gene_mention": bool(GENE.search(ta)),
        "pt_linked": bool(tr.get("pubtator_gene_linked_variant")),
        "pt_search": bool(tr.get("pubtator_search_hit")),
        "transcript": bool(TRANSCRIPT.search(body)),
        "hgvs_near": hgvs_near_gene(body),
        "llm_keep": tr.get("llm_keep"),
        "keyword_keep": bool(tr.get("keyword_keep", tr.get("keep"))),
    }


def score(name: str, keep_fn, sig: dict, truth: dict) -> None:
    """keep_fn(pmid, signals) -> bool."""
    tp = fp = fn = 0
    missed = []
    for pmid, s in sig.items():
        pred, actual = bool(keep_fn(pmid, s)), truth[pmid]
        if pred and actual:
            tp += 1
        elif pred:
            fp += 1
        elif actual:
            fn += 1
            missed.append(pmid)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    tail = f"  missed={','.join(missed)}" if missed else ""
    print(f"{name:<48} keep={tp + fp:>3}  TP={tp} FP={fp} FN={fn}  prec={prec:.2f} rec={rec:.2f}{tail}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="col4a1a2-dev triage")
    ap.add_argument("--snapshot", default="data/raw/2026-10-29")
    ap.add_argument("--borderline-is-yes", action="store_true",
                    help="count the 'borderline' label as a true positive")
    ap.add_argument("--llm", action="store_true", help="also call Haiku live (costs money)")
    a = ap.parse_args(argv)

    snapshot = Path(a.snapshot)
    if not snapshot.is_absolute():
        snapshot = REPO / snapshot
    papers, labels = load(snapshot)

    known = {p: labels[p] for p in papers if p in labels}
    if not known:
        print(f"no labelled papers in {snapshot}", file=sys.stderr)
        return 2
    truth = {
        p: (lab["label"] == "yes" or (a.borderline_is_yes and lab["label"] == "borderline"))
        for p, lab in known.items()
    }
    recorded = llm_decisions()
    sig = {p: {**signals(papers[p]), "llm_keep": recorded.get(p)} for p in known}

    print(f"{len(known)} labelled papers, {sum(truth.values())} positive "
          f"(borderline counted as {'yes' if a.borderline_is_yes else 'no'})\n")

    score("old rule as deployed (recorded)", lambda _p, s: s["keyword_keep"], sig, truth)
    score("  one disjunct: gene mention and clinical keyword",
          lambda _p, s: s["gene_mention"] and s["clinical"], sig, truth)
    score("gene named in title", lambda _p, s: s["gene_in_title"], sig, truth)
    score("patient words > method words", lambda _p, s: s["n_person"] > s["n_method"], sig, truth)
    score("patient words >= method words", lambda _p, s: s["n_person"] >= s["n_method"], sig, truth)
    score("PubTator variant linked to gene", lambda _p, s: s["pt_linked"], sig, truth)
    score("reference transcript in body", lambda _p, s: s["transcript"], sig, truth)
    score("3+ HGVS strings near a gene mention", lambda _p, s: s["hgvs_near"] >= 3, sig, truth)
    if any(s["llm_keep"] is not None for s in sig.values()):
        score("Haiku gate (recorded decisions)", lambda _p, s: s["llm_keep"], sig, truth)
        score("adopted: Haiku or PubTator-linked variant",
              lambda _p, s: bool(s["llm_keep"]) or s["pt_linked"], sig, truth)
    else:
        print("\n(no recorded Haiku decisions; run fetch-papers with a key, or pass --llm)")

    if a.llm:
        import anthropic
        from col4a1a2.extract.triage import triage_paper
        print("\nlive Haiku calls:")
        client = anthropic.Anthropic()
        live, tin, tout = {}, 0, 0
        for p in known:
            d, u = triage_paper(client, papers[p].get("title") or "", papers[p].get("abstract"))
            live[p] = d.read_full_text
            tin += u["input"]
            tout += u["output"]
            mark = "" if d.read_full_text == truth[p] else "  <-- disagrees with label"
            print(f"  {p} {'READ' if d.read_full_text else 'skip':>4}  {d.reason[:66]}{mark}")
        score("live Haiku gate", lambda p, _s: live[p], sig, truth)
        print(f"tokens in={tin} out={tout}  cost=${(tin * 1.0 + tout * 5.0) / 1e6:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
