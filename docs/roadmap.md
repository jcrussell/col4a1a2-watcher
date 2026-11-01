# Roadmap

## What this is for

Nobody publishes an open, per-variant table of **published COL4A1/COL4A2 carriers**: how many probands and families carry each variant, whether it arose de novo, how it segregated, which organ systems were affected, and what functional work exists, each fact tied to a PMID and a verbatim quote.

That table is the asset. The watcher is how it stays current, and Claude reading the papers is what makes building it feasible without a human transcribing several thousand articles.

The audience is a patient foundation following these genes, clinicians and researchers classifying variants, and any future expert panel that would otherwise start from nothing.

## Status

In place:

- Rate-limited, cached HTTP client; time-sliced Europe PMC discovery, PubTator 3 and NCBI sources; JATS to text; supplementary files parsed and figures transcribed.
- Haiku triage gate, cached by prompt version and model, with a keyword fallback for offline runs.
- Extraction (prompt v1), quote grounding with computed provenance, deterministic checks, adversarial review (prompt c1), and the spot-check queue.
- Every raw Claude response archived; schema-validated files throughout; the ledger as denominator; `col4a1a2 check` in the pre-commit hook. Tests run offline.
- The open-access corpus triaged, extracted and challenged; closed-access candidates triaged on title and abstract. Numbers in `docs/measurements.md`.

- `publish`: confident evidence and the literature table as CSV, JSON Lines and a datapackage, plus a static site, built and deployed by CI from tracked files.

- `normalize`: every extracted variant keyed on the reference transcript through Ensembl VEP, with the disagreements between a paper's coding and protein changes flagged.

Not yet: the human spot-check, the per-variant roll-up, and `report`.

## Milestones

**M1. End to end on one window.** Done, except the human spot-check that seeds the gold set in `data/curated/gold/`.

**M2. Bootstrap.** The whole back catalogue (discovery starts at 2000 by default), triaged and extracted: done for open access. Remaining: a per-variant roll-up over the normalized keys; a gold set of thirty papers and measured precision and recall.

**M3. Watcher.** ClinVar diff, `report`, `watch` as a single scheduled command, snapshot freshness keys, idempotence tests. Runs monthly after the ClinVar release, with extraction through the Batches API at half price.

**M4. Publish.** Static tables and report, README carrying the measured accuracy and the known limits, outreach to the Gould Syndrome Foundation and the Gould lab with the first report.

## Known gaps

- **Closed-access papers yield title and abstract only**, and more of the relevant literature is closed than open: the gate keeps about a third of closed-access candidates against about 9% of open-access ones. Those papers are in the ledger as triaged and kept, not extracted.
- **Some supplements and figures stay out of reach**: supplements the publisher hosts and PMC does not, bundles the Europe PMC endpoint fails to deliver (most requests; successes are kept and reused), scanned PDFs (detected and recorded, never guessed at), and TIFF figures, which the vision API does not accept. Figures are transcribed only for papers whose text does not already name its variants.
- **No precision estimate exists**, for extraction or for review, because `data/curated/gold/` is empty. `col4a1a2 review` emits the queue; filling it is human work and the one thing blocking a stated accuracy figure.
- **`missed_variants` needs a relevance filter** before its count can be read as recall: common SNPs from association sections ground correctly but should never be records.
- **`variant_type` has no class for a copy-number gain**; `derive.py` flags gains typed `deletion_cnv`, and the fix waits for a prompt revision that is worth re-extracting for.
- **Deduplication is designed but not built.** See `docs/methods.md`.
- Reviews contribute pointers, never counts, by design.

## Later, optional

An ACMG evidence-gap view consuming the table: which criteria a given variant already has published support for, and what is missing. This is a consumer of the table, not a classification engine, and it only makes sense once the table exists and its accuracy is known.
