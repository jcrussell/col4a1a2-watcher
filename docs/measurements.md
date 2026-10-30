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

Triage costs $0.0011 per paper, so the full bootstrap gate is about $2.60. Extraction of a triaged bootstrap corpus lands near $70 synchronous or half that through the Batches API.

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
