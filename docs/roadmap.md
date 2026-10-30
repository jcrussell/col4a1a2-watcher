# Roadmap

## What this is for

Nobody publishes an open, per-variant table of **published COL4A1/COL4A2 carriers**: how many probands and families carry each variant, whether it arose de novo, how it segregated, which organ systems were affected, and what functional work exists, each fact tied to a PMID and a verbatim quote.

That table is the asset. The watcher is how it stays current, and Claude reading the papers is what makes building it feasible without a human transcribing several thousand articles.

The audience is a patient foundation following these genes, clinicians and researchers classifying variants, and any future expert panel that would otherwise start from nothing.

## Status

Milestone 1 is complete except for the human spot-check.

- Rate-limited, cached HTTP client; Europe PMC, PubTator 3 and NCBI sources; JATS to text.
- Haiku triage gate, cached by prompt version, with a keyword fallback for offline runs.
- Extraction schema, versioned prompt, structured-output client, quote grounding.
- `fetch-papers`, `list`, `extract`, `show`. Tests run offline.
- One window extracted end to end. Numbers in `docs/measurements.md`.

## Milestones

**M1. Twenty papers end to end.** Fetch, triage, extract and ground a recent window; measure tokens and cost; have a human read the outputs against the papers to seed the gold set; iterate the prompt once. *Remaining: the spot-check, and the first gold records in `data/curated/gold/`.*

**M2. Bootstrap.** Corpus list with no date window, back to roughly 2000. Triage, then extraction through the Batches API at half price. Then HGVS normalisation via VEP, `build`, and the first evidence tables. Grow the gold set to thirty papers and report measured precision and recall.

**M3. Watcher.** ClinVar diff, `report`, `watch` as a single scheduled command, snapshot freshness keys, idempotence tests. Runs monthly after the ClinVar release.

**M4. Publish.** Static tables and report, README carrying the measured accuracy and the known limits, outreach to the Gould Syndrome Foundation and the Gould lab with the first report.

## Known gaps

- **Supplementary files are not read.** This is currently the largest loss. In the first window the single richest paper, a 44-patient cohort, passed triage, was read in full, and yielded nothing, because every variant sits in supplementary tables. Fixing this is worth more than any further triage tuning.
- **Deduplication is designed but not built.** See `docs/methods.md`.
- Non-open-access papers yield title and abstract only.
- Reviews contribute pointers, never counts, by design.

## Later, optional

An ACMG evidence-gap view consuming the table: which criteria a given variant already has published support for, and what is missing. This is a consumer of the table, not a classification engine, and it only makes sense once the table exists and its accuracy is known.
