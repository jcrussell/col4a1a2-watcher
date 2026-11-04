# Measurements

Numbers behind the design choices, each with the date it was taken. Re-measure before quoting any of these as current. The tools that produce them are verbs of `uv run col4a1a2-dev`.

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

Five of the twenty genuinely report COL4A1/COL4A2 variants in named individuals. Labels are in `data/curated/labels.json`. Reproduce with:

```
uv run col4a1a2-dev triage --snapshot data/raw/2026-10-29
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

The first extraction schema was rejected with `400 Schema is too complex`. It carried twelve nullable unions and two more levels of nested object. Flattening optional strings to the empty string, collapsing an inheritance list into a fixed four-count object, and inlining a nested prior-report object brought it inside the budget, accepted in four seconds. `col4a1a2-dev probe` checks a candidate against the live API; `tests/test_prompt.py` guards the shape offline.

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

## The structured-output grammar ceiling is structural (2026-10-31)

Adding five fields to the draft-v2 extraction schema was rejected. Three probes with
`col4a1a2-dev probe`, each taking two to three minutes because the server compiles
the grammar before it refuses:

| Shape | Properties | anyOf | Bytes | Result |
|---|---|---|---|---|
| draft v2, as used for the first 206 records | 45 | 4 | ~7,900 | accepted |
| draft v2 + 5 fields | 50 | 5 | 9,069 | **rejected** |
| draft v2 + 5 fields, nullable replaced by a sentinel | 50 | 4 | 9,032 | **rejected** |
| as above, minus two dead fields, descriptions cut by 1,300 bytes | 48 | 4 | 7,733 | **rejected** |
| draft v3, scoped to the project's three questions | **30** | 1 | 5,190 | **accepted in 72s** |

So the ceiling is between 45 and 48 properties for this nesting, union count is
not the binding constraint, and prose descriptions barely matter. Only removing
properties helps. `tests/test_prompt.py` now bounds properties at 40.

## gnomAD is blind where this project looks (2026-10-31)

ClinVar's COL4A1 variants, as reported by the gnomAD API, against their
presence in gnomAD r4 exomes and genomes:

| ClinVar significance | Variants | Absent from gnomAD |
|---|---|---|
| Pathogenic | 144 | **134 (93%)** |
| Likely pathogenic | 248 | **220 (89%)** |
| Pathogenic/Likely pathogenic | 28 | 21 (75%) |
| Uncertain significance | 1,112 | 471 (42%) |

COL4A1 constraint: pLI 1, o/e LoF 0.18. Nine in ten pathogenic variants have
never been observed in a population reference, so allele frequency is negative
evidence only -- "too common to be causal" -- and says nothing about the
variants clinicians are actually asked about.

## How ClinVar gets its data, for these genes (2026-10-31)

60 pathogenic COL4A1/COL4A2 records via E-utilities, full provenance for 12:

| | |
|---|---|
| Variants with exactly one submission | 56 of 60 |
| Review status "criteria provided, single submitter" | 53 of 60 |
| Reviewed by an expert panel | **0** |
| `MethodType` of the 12 assertions | clinical testing 10, research 2, literature only 0 |
| PubMed citations across the 12 assertions | 25 |

Submitters are diagnostic laboratories reporting their own patients: Labcorp
(formerly Invitae), GeneDx, PreventionGenetics, and hospital laboratories in
Seattle, Cardiff, Kiel and London. ClinVar is a set of single-lab verdicts, not
a synthesis of the published evidence, and no expert panel has curated either
gene. The two datasets are disjoint in both directions: many lab-submitted
variants were never published, and published variants no lab submitted are
absent. Small sample; widen before quoting in a report.

## The expanded corpus (2026-10-31 sweep)

| | Before the lookup fix | After |
|---|---|---|
| PubTator PMIDs resolved by Europe PMC | 41 of 2,075 | 1,769 of 1,809 (97.8%) |
| Open-access candidates triaged | 811 | 2,371 |
| Kept by the gate | 133 (16.4%) | 209 (8.8%) |

The recovered PubTator population keeps at roughly 5%, a third of the Europe
PMC rate, so the lookup fix added about 76 papers worth extracting rather than
the ~300 projected from the old rate.

LOVD was evaluated and rejected; see `AGENTS.md`. No LOVD data is stored.

## First full run on prompt v1, then numbered draft v4 (2026-10-31)

All 209 open-access papers the gate kept, extracted on draft v4 and challenged on draft c2 -- released as v1 and c1, see below.

| | |
|---|---|
| Extraction | 209 papers, 0 failures, **$17.58** ($0.084/paper; the pilot's $0.153 over-sampled big papers) |
| Challenge (c1, then c2) | 209 papers, 0 failures, **$13.89** |
| Papers with records | 101; 17 reviews, 13 functional and 10 "other" correctly yield none |
| Records | 233 |
| Distinct variants (crude key, before VEP) | **219** -- COL4A1 157, COL4A2 62 |
| Variants reported in more than one paper | **12** |
| Probands | 435, of which **187 (43%) come from 5 records in studies screening at least 1,000 people** |
| `n_tested` stated | 214 of 233 |
| Inheritance | de novo 45, inherited 46, unknown 344 |
| Quotes grounded | 653 / 655 |
| Reviewer verdicts | upheld 184, disputed 6, unverifiable 43; **fabricated 0 / 233** |
| Missed-variant reports | 286 grounded, **1 invented** -- the first invention on that side |
| Deterministic findings | 2 variant_type disagreements, 2 CNV-direction flags |

The 43% is why `n_tested` exists: without it, "435 known carriers" would hide
that nearly half were found by screening a population, one variant alone
contributing 174 from UK Biobank.

All six disputes are substantive. Three share a cause in our vocabulary, not
the model: `variant_type` has `deletion_cnv` and no class for a copy-number
gain, so a 41.7 Mb duplication had nowhere to go. `derive.py` now flags a gain
typed as a deletion -- 2 of the 3, no false positives -- and the vocabulary
fix waits for the next prompt revision, since any bump re-extracts every
paper.

## The papers we can see but cannot read (2026-10-31)

The triage-only pass ran the gate over the 768 candidates that are not open
access. It kept **265 (34.5%)**, against 209 of 2,371 open-access candidates
(8.8%). **More of the relevant literature is closed than open.** Closed
clinical journals -- Neurology, Stroke, Annals of Neurology -- are where case
reports are published, so the open-access corpus is less than half of what
the watcher should be aware of. These 265 are in the ledger as triaged and
kept, so the denominator is honest; none has been extracted.

## Prompt versions squashed to v1 (2026-10-31)

Nothing had been published and every in-scope paper had been extracted on the latest draft, so the drafts were retired and their numbering reset: extraction prompt draft v4 became **v1**, challenge prompt draft c2 became **c1**. Draft numbers elsewhere in this file refer to the retired drafts, which survive in git history.

| | before | after |
|---|---|---|
| Extraction rows in `data/raw/*/extractions.jsonl` | 395 across v1-v4 | 212, all v1 |
| Challenge rows in `data/curated/challenges.jsonl` | 361 (149 c1/v2, 212 c2/v4) | 212, all c1/v1 |
| Ledger events | 11,356 | 11,356 |

The older rows could not simply be relabelled: 20 rows were already called `v1`, and resume matches on the version string, so leaving them would have made pre-gate extractions count as current. They were removed, and the 16 papers that had only an older extraction -- all with zero records, all off-topic, none in current discovery -- now read `fetched`.

Ledger events were kept, not deleted. A first attempt dropped them, and that erased 4 papers from the denominator and the gate decision on 127 more, because for those papers a backfilled challenge event was the only place the metadata and triage verdict were written down. Each retired extraction or challenge event instead lost the fields that described the retired run (`prompt_version`, record counts, grounding counts, `challenge`) and kept the rest, with its stage set to `fetched`, which remains true. After the rewrite the folded ledger matches the old one except for those 16 papers moving from extracted or challenged back to fetched.

## Provenance backfilled, responses archived from here on (2026-10-31)

Every model-derived row now records the model that served it, the model requested, the effort and when it was written, and caches key on the requested model. Before this, triage and figure rows recorded a module constant rather than the model called: the config's `triage_model`, `triage_effort`, `figure_model` and `figure_effort` were never read. No row named the wrong model, because the constants matched the config, but changing the config would have made every new row misreport.

The existing rows were backfilled once, under `COL4A1A2_ALLOW_REWRITE`, from the best evidence available:

| Table | Rows | `written_at` from | Matched |
|---|---|---|---|
| `triage.jsonl` | 3,276 | the earliest ledger event carrying the same verdict reason | 3,276 exactly |
| `figures.jsonl` | 92 | the fetch time of the earliest snapshot that used the image | 92 |
| `challenges.jsonl` | 212 | the paper's challenged ledger events, in order | 212 |
| `extractions.jsonl` (2026-10-31) | 210 | the extracted ledger event at the same text hash | 210 |

These timestamps are when the ledger recorded the result, within seconds of the call, not the call itself. `requested_model` is the recorded model in every case; `effort` is the config value each ran under (extraction high, triage low, figures low) or, for challenges, the effort they already recorded.

Raw API responses were not kept before this date. The archive under `data/responses/` starts with the next paid call.

Two resume bugs surfaced while doing it. A paper whose text grew after it was challenged (a supplement arriving) read as already extracted at the new hash, because resume consulted the merged ledger fold, where a later `fetched` event overwrites the hash but not the stage. And `extract --model X` would have skipped every paper, because no key named a model. Both are fixed and tested.

## Cache writes went unrecorded on three call sites (fixed 2026-10-31)

Triage, figure and challenge rows kept `input`, `output` and `cache_read` and dropped `cache_write`, although all three send a cached system prompt; the spend total in `cmd_challenge` dropped it too, so `--budget-usd` stopped late. Extraction always recorded all four. The challenge cost reported above ($13.89) is therefore a slight underestimate: each call writes at most one short system prompt to the cache, so the error is bounded by that prompt's size at the cache-write rate, and it cannot be corrected after the fact because no raw responses were archived before today. All four counts are recorded from now on, through one `Reply.usage_row()`.

## Extraction results tracked (2026-10-31)

The 210 extraction rows moved from `data/raw/2026-10-31/extractions.jsonl` (untracked) to `data/curated/extractions.jsonl` (tracked), each gaining a `snapshot` field naming where its source text lives; the rows are otherwise byte-identical. This lets `publish` build from the repository alone, in CI. The rows carry short cited quotes and model output, never article text.

## First normalization run, Ensembl release 116 (2026-11-01)

`normalize` over the 233 current records: 204 distinct queries (165 coding-DNA, 33 protein-only, 6 bare coordinates), all answered, in 218 requests and 11 minutes. VEP takes about 3 s per answer and the client asks serially, so the 5/s budget is never approached. A second run made no requests.

| | Records |
|---|---|
| Keyed on coding-DNA HGVS on the reference transcript | 182 |
| Keyed at protein level (protein change only, reference residue verified) | 35 |
| No key | 16: 13 structural, `p.Gly1176del` (protein-only deletions are not sent), a fusion, an unspecified "mutation", and one bare coordinate whose base is G on both builds |
| Distinct keys | 203 |

What VEP probing settled first: only the current RefSeq version resolves (`NM_001845.5` is a 400, unversioned `NM_001845` a 500), so the reference transcript is always sent and the stated one recorded; `NM_001303110.2` and `ENST00000375820` number COL4A1 as `NM_001845.6` does, and `ENST00000360467` COL4A2 as `NM_001846.4`; the protein accessions are `NP_001836.3` and `NP_001837.2`.

**The extraction is internally consistent.** Of 116 coding records whose paper also gives a protein substitution, VEP's translation agrees with the paper in all 116, and VEP rejected no coding change for a wrong reference base. The check costs nothing and stays, but on this corpus it has found nothing. Flags raised: `build-inferred-grch37` 5 (all bare coordinates from one paper), `transcript-other` 4 (`NM_001303110.2`, two typos `NM_00185` and `NM_0018456.6`, and an `ENSP` accession), `build-ambiguous` 1.

VEP rewrote 4 coding changes into canonical form: `c.4611_4612insG` to `c.4615dup`, `c.1265dupC` to `c.1265dup`, `c.1812_1815delGCCTinsCCC` to `c.1812_1815delinsCCC`, `c.3326-7dupT` to `c.3326-7dup`.

**Grouping.** On the coding-DNA key, 12 variants appear in more than one paper, the same 12 as the crude spelling key measured earlier. Grouping at protein level, which joins protein-only records to coding ones, finds 16: p.Gly888Arg and p.Gly1239Arg (three papers each), p.Pro352Leu, p.Arg1063Ter and COL4A2 p.Gly800Arg had been split by spelling. A protein-level group can join different coding changes with the same protein consequence, so a roll-up must list the coding keys under it rather than merge them.

**Revised the same day: candidates in order of precision.** A record can give more than one description of its variant, and `normalize` now tries them in order, coding change, then bare coordinates, then protein change, keying on the first that resolves and asking about the next only when one fails. Coordinates had been tried after a protein change, so 4 records carrying both were keyed on the protein when their coordinates fix the nucleotide change; 2 others have coordinates whose reference base is the same on both builds and now fall back to their protein change, with `build-ambiguous` kept as a flag. The coordinate path also gained the protein cross-check. Result over the 233 records: coding 186 (from 182), protein 31 (from 35), none 16, distinct keys 202; flags `build-inferred-grch37` 9, `build-ambiguous` 3, `transcript-other` 4. The change cost 27 requests.

## First per-variant table (2026-11-01)

Built by `publish` from the 167 confident records: 160 carry a variant key and group into **148 variants** (COL4A1 102, COL4A2 46; 130 grouped on the protein change, 18 on the coding change, mostly UTR and splice). The 7 unkeyed records are structural variants and stay in the evidence table only.

| | Variants |
|---|---|
| Reported in more than one paper | 10 |
| Proband range wider than a single number | 9 |
| More than one coding change under one protein change | 1 (p.Gly888Arg: `c.2662G>A` and `c.2662G>C`) |
| With population-screen carriers | 2 (5 carriers; the 174-carrier biobank record is not in the confident set) |
| With a paper reporting a confirmed de novo case | 25 |

Summed over variants, probands run from 150 (every possible overlap real) to 161 (papers independent). The range is narrow because few variants recur in this release, not because overlap has been resolved: most variants appear in one paper, where the range is a single number by construction. The widest are p.Gly1239Arg and p.Gly888Arg, 1 to 3 probands across three papers each.

## Abstract-only extraction of closed-access papers (2026-11-03)

The gate had kept 214 papers with no open full text. 48 have no abstract either, only a title, and were not extracted. The other 166 went through the unchanged pipeline (prompt v1, challenge c1) on title and abstract alone: a seeded sample of 40 first, then the remaining 126.

| | Sample of 40 | All 166 |
|---|---|---|
| Papers yielding records | 21 | 63 |
| Records | 24 | 75 |
| Quotes not matching the abstract | 0 | 0 |
| **Confident** (upheld, unflagged) | **14** | **46** |
| Upheld but low confidence | 8 | 23 |
| Unverifiable | 2 | 5 |
| Disputed, with a grounded quote | 0 | 1 |
| Cost, extraction and challenge | $1.16 | $4.28, about $0.026 per paper |

The sample ran high: it projected 55 to 60 confident records and the whole set gave 46, 0.28 per paper. The low-confidence records are what they should be: abstracts that say "a novel COL4A1 mutation" without naming it, and large 13q deletions or duplications.

Effect on the release: evidence rows 167 to 213, variants 148 to 180. Of the 41 variant keys the abstract records carry, 6 were already in the table from full texts.

Abstract-only is computed, not taken from the model: the release's `abstract_only` column reads the ledger's `fetched` event for the exact text extracted. The model's own `input_was_abstract_only` disagrees with it on 4 papers read in full.

The fetch that built the snapshot also found 86 open-access papers with no extraction at their current text: 51 never extracted, and 35 whose text had grown (165 supplementary files parsed, 44 figures transcribed from bundles that arrived this time). Europe PMC's full-text endpoint returned HTTP 500 on every retry for 42 articles during this fetch; one of them had aborted the first attempt, until a full text that keeps failing became a fallback to NCBI rather than an exception.

## The open-access backlog, and front matter that passed for full text (2026-11-04)

The 2026-11-03 fetch left 86 kept open-access papers with no extraction at their current text: 51 never extracted and 35 whose text had grown, mostly from parsed supplements. `extract` and `challenge` ran over the snapshot with budgets of $12 and $6. Plain `extract` also took the 48 kept papers that have a title and no abstract, which the abstract run had left out.

| | Full text | Title only |
|---|---|---|
| Papers | 86 | 48 |
| Papers yielding records | 40 | 3 |
| Records | 120 | 3 |
| Cost, extraction | $6.08 | $0.38 |

The challenge ($4.41 for all 134) upheld 113 records, disputed none and could not verify 10. It also reported 127 grounded "missed" variants, nearly all association SNPs, mouse alleles and polymorphisms the extractor was right to leave out, so that count is still not a recall figure. The meter matched `col4a1a2 spend` to the cent on both runs.

Effect on the release: evidence rows 213 to 252, variants 180 to 211. Re-extraction gives records new keys; matched on paper, gene and HGVS, 39 variants are new and one was lost: 36786861 COL4A1 c.*31G>T, upheld and grounded, now rated low confidence by the model. Other changes in re-extracted papers are mostly counts read differently (31922066 `n_tested` 18 to 179) and, in 34531397, protein changes written with one-letter codes.

**Front matter passed for full text.** For 33 papers PMC returns XML with a title and abstract and no body, 27 of them saying in a comment that the publisher does not allow the full text to be downloaded as XML. Fetch counted any XML as full text, so these were extracted under a FULL TEXT label and 7 released rows showed `abstract_only` false. Fetch now requires a body, supplement or figure (`PaperText.is_full_text`), and records a `fetched` event when a text's classification changes even if the text does not. A re-fetch at a budget of $0 corrected the 33 papers without changing any text, so no extraction was redone.

**A title is not evidence.** The three title-only records ground, because their quotes are the title, and only the model's own low confidence kept them out of the release. Fetch now records `has_abstract`, and `publish` flags `title_only` on every record from a paper with neither full text nor abstract, so none can be confident. The re-fetch wrote `has_abstract` for all 3,139 papers once: 121 have none.

Known and not fixed here: a somatic COL4A1-VEGFD fusion in myofibroma (33830670) is counted as 2 probands, and a duplication of both genes (31719132) is filed under COL4A1 alone. Both are extraction-prompt questions.
