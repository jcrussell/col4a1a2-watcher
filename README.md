# col4a1a2

A literature watcher for the collagen IV genes **COL4A1** and **COL4A2** (Gould syndrome, HANAC, PADMAL and related phenotypes).

It finds papers, triages them on title and abstract, has Claude read each kept paper and extract per-variant carrier evidence into a structured record, verifies every quote against the source text, has a second call try to refute each record, and queues what needs a human. The goal is an open evidence table: nobody publishes an open, per-variant account of **published COL4A1/COL4A2 carriers**, with proband and family counts, de novo status, organ-system phenotypes and functional work, each fact tied to a PMID and a verbatim quote.

**Status.** Fetch, triage, extraction, grounding, adversarial review and the review queue run over the whole open-access corpus, and `publish` builds the evidence and literature tables and a static site from tracked files (CI deploys them). The monthly report and the human spot-check that would let us state an accuracy figure do not exist yet. See `docs/roadmap.md`.

## Setup

```bash
uv sync
export ANTHROPIC_API_KEY=...      # needed by extract, challenge and the triage gate
git config core.hooksPath .githooks   # runs `col4a1a2 check` and the tests before each commit
```

## Commands

```bash
# Search Europe PMC and PubTator, triage on abstracts, download kept full texts
uv run col4a1a2 fetch-papers --date-from 2026-04-01 --date-to 2026-10-29 --oa-only

# Inspect a snapshot: triage flags and keep/skip per paper
uv run col4a1a2 list --snapshot data/raw/2026-10-31

# Count input tokens per paper without extracting
uv run col4a1a2 extract --snapshot data/raw/2026-10-31 --count-only

# Extract (skips papers already done at the current prompt version, model and text)
uv run col4a1a2 extract --snapshot data/raw/2026-10-31 --budget-usd 20

# Adversarially review the extracted records, then emit the spot-check queue
uv run col4a1a2 challenge --snapshot data/raw/2026-10-31
uv run col4a1a2 review --snapshot data/raw/2026-10-31 --tsv queue.tsv

# Print records; summarise the denominator; check tracked data integrity
uv run col4a1a2 show --snapshot data/raw/2026-10-31

# Build the release (confident records) into _site/; CI does this on every push to main
uv run col4a1a2 publish
uv run col4a1a2 ledger
uv run col4a1a2 check
```

Add `--no-llm-triage` to fetch without an API key, using the keyword fallback.

```bash
uv run pytest                                              # offline, no key needed
uv run col4a1a2-dev --help     # probe, triage, citations, export, fixtures
```

## Documentation

| Page | Contents |
|---|---|
| `AGENTS.md` | Working agreements, hard constraints, and decisions already made with their reasons |
| `docs/architecture.md` | Pipeline stages, modules, data lifecycle |
| `docs/methods.md` | How a paper becomes records: search, triage, extraction, grounding, review, deduplication, output format |
| `docs/codebook.md` | Every extraction field and how to spot-check it |
| `docs/sources.md` | Data sources, rate limits, licences, and what is deliberately unused |
| `docs/measurements.md` | Measured numbers behind the design choices, with dates |
| `docs/roadmap.md` | Status, milestones, known gaps |
| `docs/references.md` | Background reading |

## Data and licences

- Full texts and supplementary files come from Europe PMC and PubMed Central open-access articles, are stored locally only, and are never redistributed. Test fixtures are limited to CC BY articles and supplement files we generate ourselves, attributed in `tests/fixtures/README.md`.
- Extracted records carry a PMID, a prepared citation and short verbatim quotes.
- The evidence and literature tables are released CC BY 4.0. Code is Apache-2.0.
- Every non-Anthropic request goes through one rate-limited, cached, serial client (`src/col4a1a2/http.py`).

**Research use only.** Records are machine-extracted and spot-checked at a stated rate. Nothing here is clinical advice.
