# Working agreements

Instructions for anyone, human or agent, working in this repository.

## Tooling

- **Use `uv`, never bare `pip` or `venv`.** `uv sync` to install, `uv add` to add a dependency, `uv run col4a1a2 ...` to run the CLI, `uv run pytest` for tests. `pyproject.toml` and `uv.lock` are both tracked; commit lockfile changes with the dependency change that caused them.
- Python is pinned in `.python-version`.
- `uv` lives in `~/.local/bin` on the development machine, so `export PATH="$HOME/.local/bin:$PATH"` may be needed.

## Hard constraints

1. **Every file we write has a schema, and nothing downstream parses prose.** Row types and document types are declared in `src/col4a1a2/schemas.py`, found by filename; `jsonl.write`/`jsonl.append` and `schemas.write_document` validate before anything reaches disk, so a field name no reader expects raises instead of disappearing into a fold. Records that accumulate across runs are **JSON Lines** — appending is safe under interleaving and a fold makes the file order-independent. A single document written once per directory (the snapshot manifest, the hand-assigned triage labels) is **JSON**, which diffs legibly and loses nothing, since `jq -s` assembles the cross-run table. **TSV and CSV are terminal exports only**: produced for people and other tools, never consumed by a later stage. The one prose file is the release's `README.txt`, which carries the notice beside tables that cannot carry it themselves. Adding a file without a model is a test failure. (This rule is about *our* outputs. Input formats are not restricted: spreadsheets, Word documents, PDFs and figure images are all fair game, and we parse them. What stays off the critical path is a *human* retyping any of it; human effort is reserved for spot-checking.)
2. **Never pull fast.** Every request to a non-Anthropic host goes through `src/col4a1a2/http.py`, which enforces per-host rate limits, issues requests serially, backs off on 429 and 5xx, caches to disk, and sends a User-Agent naming the project and a contact address. Do not add a second HTTP client, and do not call `httpx` or `requests` directly from a source module.
3. **Respect upstream licences and terms.** See `docs/sources.md`. In particular: full texts and supplementary files are stored locally and never redistributed -- `data/raw/` and `data/cache/` are ignored by git for that reason, not just for size. Short verbatim evidence quotes carrying a PMID and a citation *are* the product and are tracked and published; the line is a whole article versus a sentence that supports one fact. AlphaGenome and AlphaMissense are out of scope, because their terms forbid training other models on their output and we would gain little.
4. **No clinical claims.** Every output says, in its own text, that it is machine-extracted, spot-checked at a stated rate, for research use, and not clinical advice.

## Conventions

- **Versioned prompts and schemas.** `PROMPT_VERSION` in `extract/prompt.py`, `SCHEMA_VERSION` in `extract/schema.py`, `TRIAGE_PROMPT_VERSION` in `extract/triage.py`. Bump one only when the thing it names actually changes, and only when the version is load-bearing. Extraction results are keyed by prompt version, so bumping it re-runs every paper; a bump therefore costs real money. Do not bump a version to mark an edit that no stored record depends on.
- **Structured outputs have a grammar budget.** The API rejects a schema past it with "Schema is too complex", and the limit is structural: 45 properties are accepted and 48 rejected, whatever the descriptions weigh. The extraction schema is deliberately flat (30 properties). `tests/test_prompt.py` guards the shape of the schema as sent; `col4a1a2-dev probe` checks a candidate schema against the live API before you commit to it.
- **Snapshots are immutable and dated.** `data/raw/<YYYY-MM-DD>/` holds what a fetch found, with a schema-checked `manifest.json` recording the queries, the window, counts and request totals. Snapshots hold article text, so they are never tracked; everything derived from them that we keep, including extraction results, lives in `data/curated/`.
- **Tests run offline.** Every test works with no network and no API key, against fixtures. Keep it that way; measurement tools that need the network live in `col4a1a2.dev`, run as `uv run col4a1a2-dev <verb>`, and are never invoked by the test suite.
- **Quotes are grounded, not trusted.** Every extracted fact carries a verbatim quote that `extract/verify.py` string-matches against the source. An unmatched quote flags the record for human review. Do not relax this into fuzzy matching.
- **Provenance is computed, never self-reported.** Grounding records *which region* of the input a quote matched, so a fact from a parsed supplementary table can be told apart from one read out of a figure image by a model. Never add a schema field asking the model where it got something: a model-declared source is worthless as an audit signal, and the schema is at its grammar budget anyway.
- **Derive in Python what can be derived; ask the model only for what cannot.** `extract/derive.py` holds the deterministic checks and the `variant_type` cross-check. Be honest about yield: the checks fire on a handful of records in the corpus, and deriving `variant_type` is *worse* than the model where the protein change is absent, so it is a cross-check on an unambiguous subset and never the published value.
- **Everything the model derives gets peer-reviewed, and the review is itself checkable.** `col4a1a2 challenge` refutes extraction records; every judgement carries a verbatim quote that grounding string-matches, so an invented refutation is counted rather than believed. The metric is a fabrication rate, not precision: grounding catches invention, not a true sentence misapplied.
- **One config file, one settings object.** `config/col4a1a2.toml`, read with stdlib `tomllib`. Every host key in `[rates]` must be quoted: TOML reads a bare dotted key as a nested table, which would make the rate limiter miss every host and silently run the supplement endpoint at four times the rate that returns HTTP 500. Security limits, `MAX_TOKENS`, the API beta string and every `*_PROMPT_VERSION` stay in code. Anything that can change the assembled `text` is echoed into the snapshot manifest, because it changes the resume key.
- **Nothing we process goes unrecorded.** `data/curated/ledger.jsonl` is the denominator: every candidate, how it was found, why it was dropped, what came of it. Append-only, folded by `written_at`, no article text. A published table without its denominator cannot distinguish "unexamined" from "examined and empty".
- **Compare what was asked for against what came back.** A fallback that fills in a record for every miss cannot tell "absent upstream" from "asked the wrong question", so a lookup that resolves almost nothing looks like a smaller corpus rather than a bug. Any batch lookup counts requested against resolved, warns when the ratio collapses, and puts both numbers in the snapshot manifest.
- **Invented metadata must never read as a fact.** A record filled in because a lookup missed carries `metadata_source`, and the ledger separates `no_metadata` from `oa_filter`, so a paper nobody looked up is never recorded as closed access.
- **A run's selection is not a paper's fate.** `--limit`, ranks and spend caps decide what one run does; they are never stored as per-paper state, because a later, narrower run would overwrite the dispositions of papers already processed. The backlog is derived instead (`kept_not_extracted`), and per-run counts belong in the manifest.
- **Figures are transcribed before extraction, never passed as images to it.** An image has no string for grounding to match, so a fact read straight from one could never be verified. `extract/figures.py` turns a figure into text first, clearly labelled as machine-generated.
- **Claude's own spend is explicit.** Triage uses Haiku on title and abstract only; extraction uses Opus on full text. Decisions and extractions are both cached so that re-running costs nothing. Before adding a model call to a loop, work out what it costs over the full corpus and say so.

## Integrity

The files are a small database, so they get the few guarantees a database would give. Enable the hook once per clone: `git config core.hooksPath .githooks`.

- **`uv run col4a1a2 check` is the first thing to run after any hand edit, merge or migration.** It validates every row, finds torn lines, duplicate rows and dangling PMIDs, confirms each challenge still matches the records it reviewed, and confirms every extraction the ledger records exists in a snapshot. The pre-commit hook runs it and the tests.
- **Tracked logs are append-only.** `ledger`, `triage`, `figures`, `challenges`, `discovery`, `extractions` and `normalized` must extend their `HEAD` version byte for byte. A deliberate rewrite is a migration: commit it with `COL4A1A2_ALLOW_REWRITE=1` and say in `docs/measurements.md` what changed and why. Even then `check` refuses a ledger rewrite that loses a paper or a triage verdict: an event can be the only record of a paper's metadata or verdict, so deleting events is never a safe way to retire them.
- **Data before index.** Write the result row, then the ledger event. A crash between them leaves a result the ledger does not know about (a warning) rather than a ledger entry pointing at nothing, which would make resume skip a paper that has no result.
- **One writer at a time.** Writing verbs hold `data/.lock`; a second run exits naming the PID that holds it.
- **Writes are atomic or single-line.** `jsonl.write` and `schemas.write_document` write a temp file and rename it; `jsonl.append` writes one line and refuses to append after a torn one. Never open a data file for writing yourself.
- **Every Claude call goes through `extract/llm.py`.** It archives the raw response under `data/responses/` before validating it, so a response that fails to parse can still be read, and an identical request is answered from disk for free. The archive covers calls from 2026-10-31 on; earlier rows have parsed output only.
- **Model-derived rows carry provenance** (`model` as served, `requested_model`, `effort`, `written_at`), and caches and resume key on `requested_model`. "Current" means this prompt version and the configured model; rows from `extract --model X` sit alongside without entering the table, and only the configured model writes extraction events to the ledger. `--no-llm-cache` calls again and appends; the newest row for a key wins.

## Publishing

- **`publish` builds from tracked files only.** `data/curated/`, `config/` and the code; never `data/raw/`. That is what lets CI build the release from a checkout, and a test runs it with no snapshot directory at all.
- **Confident by default.** The release holds records the checked review upheld, with every quote grounded and nothing flagged (`extract.rows.is_confident`). `--all` exists for people inspecting the data; CI never passes it.
- **Deterministic.** Sorted rows, the release version from the git tag, and "data as of" from the data, never the clock, so the same commit builds the same bytes. (An uncommitted working tree adds a date to its development version; CI builds from a clean checkout.)
- **The release is built, never committed, and deployed only from a tag.** `_site/` is gitignored; pushing a `v*` tag runs `.github/workflows/publish.yml`, which builds and deploys it; running that workflow by hand is a dry run that builds and uploads the site without deploying. Tag a release only after `ci.yml` and a dry run are green on GitHub. `ci.yml` runs the tests, `check` and a throwaway build on every push.
- **One version, from the git tag.** `pyproject.toml` declares the version dynamic and hatch-vcs computes it from the latest `v*` tag: tagging `v0.2.0` makes the release `0.2.0`, and an untagged commit gets a development version such as `0.1.dev51+g66ad8dd`. `col4a1a2.__version__` reads it from the package metadata, the User-Agent's `{version}` and the release are filled from that, and CI checks out full history so the tag is visible.
- **Conventional commit subjects; the changelog is generated from them.** `type(scope): description`, with `feat`, `fix`, `perf`, `docs`, `refactor`, `test`, `ci`, `build` or `chore`; `.githooks/commit-msg` enforces it. `cliff.toml` groups the log by type, and after a tag deploys, `publish.yml` creates the GitHub Release with that tag's section as its notes; nothing is committed. A dry run shows the notes the next tag would get in its run summary. Commits before v0.1.0 predate the convention and are listed by subject. `uv run git-cliff` previews it.
- **No spot-check rate without a schema.** `publish` refuses to run while `data/curated/gold/` has files and no model in `schemas.py`.

## Shared code: import, don't restate

Each rule has one home and callers import it. Two copies of a rule drift apart, and the bug then lives in whichever copy a caller happens to use.

| Rule or helper | Home |
|---|---|
| Which extraction or challenge row is current; which challenge reviewed which records; review verdicts, flags, and what counts as confident | `extract/rows.py` |
| Calling Claude: system block, schema as sent and its grammar shape, stop-reason handling, usage, provenance, spend | `extract/llm.py` |
| Caches of model answers (newest `written_at` wins) | `extract/cache.py` |
| Resume and the ledger fold | `ledger.py` (`needs_extraction`, `is_extraction`, `fold_rows`) |
| Variant keys, the reference transcripts, and normalization flags | `normalize.py` |
| Content hashes | `digest.py` |
| Classifier regexes | `patterns.py` |
| Reading, writing and exporting our files | `jsonl.py`, `schemas.write_document` |
| Defaults for tunables | the module that uses them; `config.py` reads and may override them |

## Decisions already made, with reasons

Do not re-propose these without new evidence.

- **No severity predictor.** A protein language model predicting clinical severity does not survive contact with the data: intrafamilial variability is wide, penetrance is incomplete, and there are only a few hundred variants with phenotype data.
- **No general ACMG classification engine.** Franklin, VarSome and InterVar already do this. A machine-only engine double-counts computational and hotspot evidence and cannot fire the criteria that matter most here. The evidence table may later feed such a tool, which is a different thing.
- **No LOVD ingestion.** The shared LOVD instance holds 553 COL4A1 and 360 COL4A2 variant records, and its terms foreclose using them: the variant list view states that "Screenscraping/webscraping (interacting with LOVD using scripts to download data) is strictly prohibited", and the database notice states that contents are the submitters' and curators' intellectual property, that "When no license is listed, no permissions are granted", and that unauthorised "use, copying, storage, or distribution" infringes copyright, citing Directive 96/9/EC on the legal protection of databases. Their robots.txt separately disallows `ClaudeBot` and `Claude-SearchBot` from `/shared/individuals`, which is where patient-level data lives. There is a sanctioned read API, but it grants no licence to store or redistribute what it returns, and the records carry no citations and no classifications anyway (`effect_concluded` is `notClassified` for every record in both genes), so there is little to gain. The only legitimate route is written permission from the curators. Do not scrape it, do not cache it, and do not derive published rows from it.
- **No AlphaGenome or AlphaMissense features.** Terms forbid training other models on the outputs, the regulatory site that matters most for one disease mechanism here is not modelled, and nothing in the current scope needs them.

The current direction, and why, is in `docs/roadmap.md`.
