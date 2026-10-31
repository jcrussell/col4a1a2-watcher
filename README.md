# col4a1a2

A literature watcher for the collagen IV genes **COL4A1** and **COL4A2** (Gould syndrome, HANAC, PADMAL and related phenotypes).

It finds new papers each month, has Claude read each one and extract per-variant carrier evidence into a structured record, verifies every quote against the source text, and rolls the results into an open evidence table and a report. The table is the point: nobody publishes an open, per-variant account of **published COL4A1/COL4A2 carriers**, with proband and family counts, de novo status, segregation, organ-system phenotypes and functional work, each fact tied to a PMID and a verbatim quote.

**Status: milestone 1.** Fetch, triage, extract and quote-grounding work end to end on a recent window. The evidence table itself is not built yet. See `docs/roadmap.md`.

## Setup

```bash
uv sync
export ANTHROPIC_API_KEY=...      # needed by `extract` and by the triage gate
```

## Commands

```bash
# Search Europe PMC and PubTator, triage on abstracts, download kept full texts
uv run col4a1a2 fetch-papers --date-from 2026-04-01 --date-to 2026-10-29 --oa-only

# Inspect a snapshot: triage flags and keep/skip per paper
uv run col4a1a2 list --snapshot data/raw/2026-10-29

# Count input tokens per paper without extracting
uv run col4a1a2 extract --snapshot data/raw/2026-10-29 --count-only

# Extract (skips papers already done at the current prompt version)
uv run col4a1a2 extract --snapshot data/raw/2026-10-29

# Print the extracted records
uv run col4a1a2 show --snapshot data/raw/2026-10-29
```

Add `--no-llm-triage` to fetch without an API key, using the keyword fallback.

```bash
uv run pytest                                              # offline, no key needed
uv run col4a1a2-dev triage --snapshot data/raw/2026-10-29
```

## Documentation

| Page | Contents |
|---|---|
| `AGENTS.md` | Working agreements, hard constraints, and decisions already made with their reasons |
| `docs/architecture.md` | Pipeline stages, modules, data lifecycle |
| `docs/methods.md` | How a paper becomes table rows: search, triage, extraction, grounding, deduplication, output format |
| `docs/codebook.md` | Every extraction field and how to spot-check it |
| `docs/sources.md` | Data sources, rate limits, licences, and what is deliberately unused |
| `docs/measurements.md` | Measured numbers behind the design choices, with dates |
| `docs/roadmap.md` | Status, milestones, known gaps |
| `docs/references.md` | Background reading |

## Data and licences

- Full texts come from Europe PMC and PubMed Central open-access articles, are stored locally only, and are never redistributed. Test fixtures are limited to CC BY articles, attributed in `tests/fixtures/README.md`.
- Extracted records carry a PMID, a prepared citation and short verbatim quotes.
- The evidence table will be released CC BY 4.0. Code is Apache-2.0.
- Every non-Anthropic request goes through one rate-limited, cached, serial client (`src/col4a1a2/http.py`).

**Research use only.** Records are machine-extracted and spot-checked at a stated rate. Nothing here is clinical advice.
