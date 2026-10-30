# Working agreements

Instructions for anyone, human or agent, working in this repository.

## Tooling

- **Use `uv`, never bare `pip` or `venv`.** `uv sync` to install, `uv add` to add a dependency, `uv run col4a1 ...` to run the CLI, `uv run pytest` for tests. `pyproject.toml` and `uv.lock` are both tracked; commit lockfile changes with the dependency change that caused them.
- Python is pinned in `.python-version`.
- `uv` lives in `~/.local/bin` on the development machine, so `export PATH="$HOME/.local/bin:$PATH"` may be needed.

## Hard constraints

1. **Everything we produce is structured data.** Intermediate and published artefacts are JSON Lines, TSV or typed records, never prose or Markdown that something downstream has to re-parse. A stage that emits a pile of Markdown for the next stage to read is wrong, however convenient. (This rule is about *our* outputs. Input formats are not restricted: spreadsheets, Word documents, PDFs and figure images are all fair game, and we parse them. What stays off the critical path is a *human* retyping any of it; human effort is reserved for spot-checking.)
2. **Never pull fast.** Every request to a non-Anthropic host goes through `src/col4a1/http.py`, which enforces per-host rate limits, issues requests serially, backs off on 429 and 5xx, caches to disk, and sends a User-Agent naming the project and a contact address. Do not add a second HTTP client, and do not call `httpx` or `requests` directly from a source module.
3. **Respect upstream licences and terms.** See `docs/sources.md`. In particular: full texts are stored locally and never redistributed; AlphaGenome and AlphaMissense are out of scope, because their terms forbid training other models on their output and we would gain little.
4. **No clinical claims.** Every output says, in its own text, that it is machine-extracted, spot-checked at a stated rate, for research use, and not clinical advice.

## Conventions

- **Versioned prompts and schemas.** `PROMPT_VERSION` in `extract/prompt.py`, `SCHEMA_VERSION` in `extract/schema.py`, `TRIAGE_PROMPT_VERSION` in `extract/triage.py`. Bump one only when the thing it names actually changes, and only when the version is load-bearing. Extraction results are keyed by prompt version, so bumping it re-runs every paper; a bump therefore costs real money. Do not bump a version to mark an edit that no stored record depends on.
- **Structured outputs have a grammar budget.** The extraction schema is deliberately flat. A draft with twelve nullable unions and two more nested objects was rejected by the API with "Schema is too complex". `tests/test_prompt.py` guards the shape; `tools/probe_schema.py` checks a candidate schema against the live API before you commit to it.
- **Snapshots are immutable and dated.** `data/raw/<YYYY-MM-DD>/` holds what a fetch found, with a `manifest.json` recording the queries, the window, counts and request totals. `build` must be offline, deterministic, and derived from one snapshot.
- **Tests run offline.** Every test works with no network and no API key, against fixtures. Keep it that way; measurement scripts that need the network live in `tools/` and are never invoked by the test suite.
- **Quotes are grounded, not trusted.** Every extracted fact carries a verbatim quote that `extract/verify.py` string-matches against the source. An unmatched quote flags the record for human review. Do not relax this into fuzzy matching.
- **Provenance is computed, never self-reported.** Grounding records *which region* of the input a quote matched, so a fact from a parsed supplementary table can be told apart from one read out of a figure image by a model. Never add a schema field asking the model where it got something: a model-declared source is worthless as an audit signal, and the schema is at its grammar budget anyway.
- **Figures are transcribed before extraction, never passed as images to it.** An image has no string for grounding to match, so a fact read straight from one could never be verified. `extract/figures.py` turns a figure into text first, clearly labelled as machine-generated.
- **Claude's own spend is explicit.** Triage uses Haiku on title and abstract only; extraction uses Opus on full text. Decisions and extractions are both cached so that re-running costs nothing. Before adding a model call to a loop, work out what it costs over the full corpus and say so.

## Decisions already made, with reasons

Do not re-propose these without new evidence.

- **No severity predictor.** The original idea, a LoRA-adapted protein language model predicting clinical severity, does not survive contact with the data: intrafamilial variability is wide, penetrance is incomplete, and there are only a few hundred variants with phenotype data. Two adversarial reviews rejected it.
- **No general ACMG classification engine.** Franklin, VarSome and InterVar already do this. A machine-only engine double-counts computational and hotspot evidence and cannot fire the criteria that matter most here. The evidence table may later feed such a tool, which is a different thing.
- **No AlphaGenome or AlphaMissense features.** Terms forbid training other models on the outputs, the regulatory site that matters most for one disease mechanism here is not modelled, and nothing in the current scope needs them.

The current direction, and why, is in `docs/roadmap.md`.
