# Measurements

Numbers behind the design choices, each with the date it was taken. Re-measure before quoting any of these as current. Scripts that produce them are in `tools/`.

## Corpus size (2026-10-29, Europe PMC and NCBI)

| Quantity | Value |
|---|---|
| Papers naming COL4A1 or COL4A2 anywhere | 11,938 |
| Gene named in title or abstract, since 2015 | 1,218 |
| Of those, open-access full text | 601 |
| Gene in title or abstract, last 12 months | 210 |
| PubTator papers with gene and a variant mention | 1,795 |
| ClinVar records, both genes | 5,174 |

So the bootstrap corpus is roughly two thousand papers and the monthly increment is under twenty.

## Triage (2026-10-29 window, 20 labelled papers)

Five of the twenty genuinely report COL4A1/COL4A2 variants in named individuals. Labels are in `data/curated/triage_labels.json`. Reproduce with:

```
uv run python tools/triage_eval.py --snapshot data/raw/2026-10-29
```

| Gate | Kept | Recall | Precision |
|---|---|---|---|
| Old rule, as deployed | 20 | 1.00 | 0.25 |
| Patient words outnumber method words in abstract | 6 | 1.00 | 0.83 |
| Patient words at least equal method words | 8 | 1.00 | 0.62 |
| **Haiku on title and abstract (adopted)** | **8** | **1.00** | **0.62** |
| Gene named in the title | 3 | 0.60 | 1.00 |
| PubTator variant linked to the gene | 1 | 0.20 | 1.00 |
| Reference transcript present in body | 4 | 0.80 | 1.00 |
| Three or more HGVS strings near a gene mention | 5 | 0.40 | 0.40 |

The keyword rule's higher precision came from excluding three genuinely ambiguous papers that the model keeps on purpose: a research-network paper whose patient data is not yet published, an exome cohort that turns out to have been pre-screened negative for these genes, and a systematic review carrying only aggregate counts. None is decidable from the abstract. Recall is what matters, so the model won.

The two perfect-precision signals have recall too low to gate on, so they act as promoters. PubTator's low recall is explained by its annotating only the title and abstract for most papers.

On the full window the adopted gate keeps 8 of 47 candidates, against 20 under the old rule.

## Extraction (2026-10-29, 20 papers, Opus at high effort)

| Quantity | Value |
|---|---|
| Papers extracted | 20 of 20 |
| Papers with variant-level data | 3 |
| Variant records produced | 11 |
| Quotes matching the source text | 33 of 33 |
| Records flagged for review | 0 |
| Mean input tokens per paper | 15,093 |
| Cost, synchronous | $1.60 |
| Cost per paper | $0.080 |

Paper types assigned: 7 functional, 5 cohort, 3 other, 2 review, 2 case report, 1 case series.

Triage costs **$0.000103** per paper, so the whole gate is about 8 cents. (This document said $0.0011 for some weeks, a clean 10x, by applying Haiku 4.5 prices to a Haiku 5.5 model. Measured directly over all 742 decisions: 515,607 input and 50,427 output tokens, $0.077 in total.) Extraction of a triaged bootstrap corpus lands near $70 synchronous or half that through the Batches API.

A character-count estimate made before measuring put the mean at 10.7k tokens, about 30% low. Do not estimate tokens from characters for this corpus.

## Structured-output schema limit (2026-10-29)

The first extraction schema was rejected with `400 Schema is too complex`. It carried twelve nullable unions and two more levels of nested object. Flattening optional strings to the empty string, collapsing an inheritance list into a fixed four-count object, and inlining a nested prior-report object brought it inside the budget, accepted in four seconds. `tools/probe_schema.py` checks a candidate against the live API; `tests/test_prompt.py` guards the shape offline.

## Citation density between papers reporting the same variant (2026-10-30)

For each recurrent variant, every paper mentioning it was retrieved and its bibliography checked against the earlier papers in the same set.

| Variant | Papers | Later-cites-earlier pairs |
|---|---|---|
| COL4A1 p.Gly773Arg | 13 | 22% |
| COL4A1 p.Gly1580Arg | 3 | 33% |
| COL4A2 p.Gly1389Arg | 2 | 0% |

Bibliographies in that set ran from 17 to 111 references. Citation links are therefore weak in both directions: absence says almost nothing, and presence makes a paper one candidate among dozens. Some of the sparsity is legitimate, since independent cohorts finding the same recurrent variant have no reason to cite each other, so this bounds the signal rather than impugning the literature.

Separately, a variant-string search over full text returns a small candidate set per variant, between one and thirteen papers here, which is what makes per-variant deduplication tractable.

## Text shape, relevant to the output format

No extracted quote or free-text field in the 2026-10-29 records contained a tab or a newline; about half contained a comma. Longest quote was 316 characters. Hence tab-separated output, with the writer still rejecting tabs and newlines rather than assuming.

## Supplements and figures, 100-paper batch (2026-10-30)

A historical window back to 2015, open access only, triaged then capped at 100 papers.

| | |
|---|---|
| Candidates triaged | 742 |
| Kept by the gate | 124 |
| Papers taken (cap) | 100 |
| Extracted without failure | 100 |
| Papers yielding variant records | 71 |
| Variant records | 177 |
| Quotes matching their source | 524 of 525 |
| Records flagged for review | 10 |
| Extraction cost | $9.30, about $0.09 per paper |

Quote provenance, computed from where each quote matched: 472 author body, 11 abstract, 39 figure transcription, 1 parsed supplement. Every one of the 39 figure-sourced records was flagged, and none claimed high confidence.

The supplement and figure stages added **39 variant records that the body text alone did not contain**, taking the batch from 138 to 177. Thirty-eight of those come from one paper, the 44-patient cohort that previously produced nothing: it went from 0 records to 38, each marked as transcribed from a figure. Its figure transcription correctly declined to say which panel was COL4A1 and which was COL4A2, since the figure does not label them; an earlier free-prose probe of the same image had guessed. Gene assignment for those records therefore comes from the body text and remains the weakest part of them.

Cost of the enrichment itself was negligible: the figure gate fired on 8 of 100 papers, producing 9 transcriptions for well under a dollar, and supplements added about 43,000 characters across the batch against 2.9 million overall.

### What limits it

The Europe PMC bundle endpoint is the bottleneck, not the parsing or the vision. It builds its ZIP per request and, measured over these runs, **succeeded for roughly one request in five**; the rest timed out or stalled mid-transfer. Consequences, all handled rather than worked around:

- Bundles are stored outside any snapshot and reused by every later run, so each run accumulates successes instead of re-attempting from scratch. The store grew from 19 to 29 bundles across two passes of the same 100 papers.
- The endpoint has its own rate bucket at 0.25/s. At the host's normal 2/s it returns HTTP 500.
- Downloads stream under a byte cap and a total wall-clock deadline, and get exactly one attempt. A per-read timeout does not catch a server that dribbles bytes, and retrying an optional enrichment let a handful of bad papers dominate the wall clock of a whole run.
- The same cohort paper yielded 38 records on a run where its bundle arrived and 0 on a run where it timed out. Nothing about the parsing changed; only the download did.

## Corpus size, corrected (2026-10-30)

> **Superseded in part.** Every open-access count below is a floor, not a
> measurement: the PubTator metadata lookup was resolving 2% of what it asked
> for, so most of the union pool was never checked for open-access status at
> all. See *The lookup that hid three quarters of the corpus* below.

Planning documents carried a "2,200-paper bootstrap" for months. That came from
the rev-5 full-text query, which was replaced by the title-or-abstract query
long before the batch ran. Measured against the live query:

| | |
|---|---|
| Papers addressed by the query | 1,468 |
| Of those, open access | 645 (603 since 2015, 42 before) |
| Candidates seen, union with PubTator, before filters | 2,850 |
| Of those, open access with a PMCID | 742 |
| Kept by the triage gate | 124 |
| Already extracted | 100 |

So the bootstrap is **roughly 30 papers and $3 from done**, not $200. The
manifest compounded the error by reporting `candidates_before_limit` as 742,
computed after the open-access filter had reassigned its own input, understating
the candidate pool roughly fourfold.

The binding constraint is wall clock, not money: the supplement endpoint runs at
0.25 requests per second and succeeds for roughly one request in five, so each
pass over the remaining papers is a 25-minute problem that no spend cap touches.

## The processing ledger (2026-10-30)

What was invisible before it existed, now recorded per candidate:

| Disposition | Candidates |
|---|---|
| Still in play | 100 |
| Cut by `--limit` | 642, of which **24 were papers the gate wanted** |
| Dropped by the open-access filter | 2,108 |
| Europe PMC hits carrying no PMID | 141 |

Two of those four rows were later found to be wrong, in opposite directions:
`--limit` is no longer a disposition at all, and most of the open-access row was
never looked up. Both are measured below.

3,396 identities over 3,396 events, 2.4 MB tracked, after two fixes: a run no
longer re-appends a row whose state is unchanged, and `rank`/`limit_at` were
removed because they describe a run rather than a paper, so every candidate row
was being rewritten whenever `--limit` changed. Before both, the file reached
11 MB over a handful of runs; a repeat run now appends zero events.

## Adversarial review, first run (2026-10-30)

Three papers, 50 records, $0.55. Not a precision estimate: `data/curated/gold/`
is still empty, so these verdicts are advisory.

| Paper | Records | Upheld | Disputed | Unverifiable | Fabricated | Grounded misses |
|---|---|---|---|---|---|---|
| 35711275 | 11 | 11 | 0 | 0 | 0 | 13 |
| 42099677 | 38 | 24 | 0 | 14 | 0 | 0 |
| 36300346 | 1 | 0 | 0 | 1 | 0 | 0 |

Three things worth recording from it. The reviewer found two real problems in
the figure-transcribed cohort that nothing else had: a variant listed under an
exon out of order, implying a transcription misread, and one COL4A2 variant
named in the text but absent from the transcription. It correctly diagnosed the
174-probands-in-174-families record as a biobank cohort rather than ascertained
families, but filed it as unverifiable rather than disputed, so the
deterministic magnitude check catches that one more decisively. And most of the
13 "missed" variants in 35711275 are common SNPs from a GWAS section that
should not be records at all, which the reviewer itself said in its notes, so
`missed_variants` needs a relevance filter before the count can be read as
recall.

## Adversarial review over the corpus (2026-10-30)

42 papers, 144 records, $3.11 in total.

| | |
|---|---|
| Upheld | 109 |
| Disputed, with a grounded refuting quote | 2 |
| Unverifiable | 33 |
| **Fabricated** (dispute attempted, quote not in the text) | **0** |
| Incomplete verdict lists | 0 |
| Grounded missed-variant reports | 115 |
| Invented missed-variant reports | 0 |

Two things this establishes and one it demolishes.

The reviewer does not invent. Across every dispute attempt the refuting quote
grounded, and not one of the 115 missed-variant strings failed to appear in the
paper. It also never returned a short verdict list for a long record set.

It finds real problems nothing else does. On the figure-transcribed cohort it
spotted a variant listed under an exon out of order, implying a transcription
misread, and noticed that the text names nine distinct COL4A2 variants where
only eight were transcribed.

**But `missed_variants` is nearly worthless as a recall signal as it stands.**
All 115 ground, and almost none should be records: they are common association
SNPs, mouse alleles, positive controls in assays, and variants cited from other
papers. The reviewer said so itself, accurately, in its per-paper notes — which
are the valuable output, not the count. Read naively the number looks like a
catastrophic recall failure and is instead an artefact of asking for every
variant designation in the text. A relevance filter is required before this can
be called recall.

Cost per paper came out at $0.07, against $0.11 estimated.

## The lookup that hid three quarters of the corpus (2026-10-31)

Europe PMC does not index a `PMID` field. `cli.py` had been looking up
PubTator-discovered PMIDs with `query=PMID:x OR PMID:y` since the first commit.

| | |
|---|---|
| Unique PMIDs sent through the old query | 2,075 |
| Resolved | **41** (2.0%) |
| `PMID:11985779` → `hitCount` | **0** |
| `EXT_ID:11985779` → `hitCount` | **1**, PMC102755, open access |
| Random 100 of the 2,034 unresolved, re-queried with `EXT_ID` | **97 resolved** |
| Of those 100: carrying a PMCID | 92 |
| Of those 100: flagged open access | 91 |
| Two 50-PMID chunks, resolved against requested | 47/50 and 50/50 |

The code beneath the lookup then built a `PaperMeta` by hand for every miss,
with `is_open_access=False` and `pmcid=None`, `--oa-only` dropped those, and the
ledger recorded them as `oa_filter` -- not open access, a claim nothing had
checked. So roughly **1,850 open-access papers with full text in PMC** sat in
the ledger labelled closed, and they are the PubTator pool: gene *and* variant
tagged, the highest-prior set in the project and the one least examined.

**Why it was silent for ten commits.** The fallback's job is to invent metadata,
and it cannot tell "absent from Europe PMC" from "asked wrong". Nothing compared
requested against resolved. That comparison, per chunk and in the manifest
(`pmid_lookup_requested`, `pmid_lookup_resolved`), is the durable fix; `EXT_ID`
is the one-line part.

`EXT_ID` also matches PMC-sourced records, one of which came back
`source=PMC, pmid=null`, so records without a PMID are dropped rather than keyed
wrongly.

## Run-scoped facts stored as paper state (2026-10-31)

`dropped_at="limit"` was a disposition. The `2026-10-30-idem` run -- a
`--limit 100` idempotence check that started at 19:01, two minutes after the
real sweep finished -- stamped it onto 739 papers that had already been triaged,
fetched, and in 36 cases extracted and challenged.

| Ledger reported | Actually |
|---|---|
| `limit` = 652 | 8 |
| `in_play` = 159 | ~803 |
| -- | 644 rows reading `stage=fetched, dropped_at=limit`, which cannot happen |

`rank` and `limit_at` had already been removed from the ledger for being
run-scoped; this was the last of the class. `--limit` now records no
disposition, the per-run count goes in the manifest as
`candidates_cut_by_limit`, and the backlog is derived instead:
`col4a1a2 ledger` reports `kept_not_extracted`, which found PMID 36360323 --
triage-kept, 63,613 characters of full text fetched, sitting in a snapshot that
was never extracted. One paper of 133, known to the ledger, read by nothing.

## What the query can and cannot see (2026-10-31)

Both genes are covered at every stage -- Europe PMC query, both PubTator
queries, NCBI gene IDs 1282 and 1284, the triage prompt, and the extraction
schema's two-member `Gene` enum -- and COL4A2 is a third of the output, not a
token presence: **65 of 206 records**, against 141 for COL4A1. Of 802 snapshot
papers, 85 name COL4A1 in the title, 33 name COL4A2 and 13 name both.

The symbol query's blind spot, measured against the live index:

| Query | Hits | Beyond the symbols | Open access |
|---|---|---|---|
| Gene symbols only | 1,469 | -- | -- |
| Disease names as phrases | 63 | **16** (15 new to us) | 3 |
| Protein names ("collagen type IV alpha 1", …) | 159 | 63 (1 new and relevant) | 12 |
| Adopted: symbols + disease names | 1,485 | 16 | 3 |

The disease-name clause earns its place on content, not count: those 16 include
a 2026 anaesthetic case report on a child with Gould syndrome, 2026 retinal
imaging in HANAC, a 2015 HANAC case, the 2019 HANAC series and the 2009 PADMAL
MRI series. The protein-name clause does not: 63 papers for one relevant hit
already held, the rest collagen-IV cell biology.

The acronyms must be phrases. A bare `HANAC` matches plant NAC
transcription-factor papers and a 1950 method for determining cell counts.

What still cannot be reached this way is a paper that names neither the symbol
nor a disease name in its title or abstract and mentions the gene only in the
body. PubTator is the mitigation, since it tags gene concepts by synonym across
the full text -- which is also why the enrichment lookup silently dropping 98%
of PubTator PMIDs mattered so much.
