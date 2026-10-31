"""Row schemas for every JSON Lines file this project writes.

Why this exists. Everything downstream of a stage reads that stage's JSONL, and
until now the only declared schema was the extraction payload's. The field names
of the ledger, the discovery log, the snapshot rows and the caches lived in the
code that wrote them and nowhere else, so a writer that said `n_variant` where a
reader wanted `n_variants` would have produced a file that parsed cleanly,
folded cleanly, and quietly lost the field. The ledger is the project's
denominator; it cannot be the place where a typo hides.

Three rules hold here:

- **Every file has a model, found by filename.** `MODELS` maps a basename to the
  model for one of its rows, and `jsonl.write`/`jsonl.append` look it up. A new
  JSONL file without an entry is a test failure, not a silent exception.
- **Top level is strict, upstream shapes are not.** Our own field names are
  `extra="forbid"`, so an unknown key raises. Objects whose shape belongs to
  someone else -- PubTator's annotation records, a parsed spreadsheet's sheets --
  are permissive, because tightening them would mean breaking a run whenever an
  upstream API adds a field.
- **Fields are optional by default and documented by measurement.** The ledger
  writes only what a caller knows: of 3,717 events, `identity` appears in all of
  them, `triage` in 811, `n_variants` in 144. A model that required those fields
  would reject the file it describes.

The models are deliberately not versioned. `SCHEMA_VERSION` in
`extract/schema.py` gates stored extraction results and costs money to bump;
these describe the files as they are today, and the test suite checks them
against the real ones.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from col4a1a2.extract.schema import PaperExtraction


class Row(BaseModel):
    """Our own rows: an unknown field is a mistake, not an extension."""

    model_config = ConfigDict(extra="forbid")


class Loose(BaseModel):
    """Shapes owned by an upstream API or a parsed document."""

    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------------ fragments

class Usage(Row):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


class ChallengeUsage(Usage):
    """The challenge writer records the request's effort level alongside the
    token counts. Misfiled -- effort is a request parameter, not usage -- but
    149 rows on disk say so, and a model describes the file it has rather than
    the file it wishes it had.
    """

    effort: str | None = None


class TriageVerdict(Row):
    """Both forms of the triage verdict.

    `papers.jsonl` stores all eleven fields; the ledger stores the four that
    describe the decision rather than the signals behind it. One model with
    optional fields covers both, and forbids a fifth spelling of either.
    """

    keep: bool | None = None
    keyword_keep: bool | None = None
    llm_keep: bool | None = None
    llm_reason: str | None = None
    triage_prompt_version: str | None = None
    title_abstract_mention: bool | None = None
    clinical_keyword: bool | None = None
    pubtator_gene_variant: bool | None = None
    pubtator_gene_linked_variant: bool | None = None
    pubtator_linked_hgvs: list[str] = Field(default_factory=list)
    pubtator_search_hit: bool | None = None


class ChallengeSummary(Row):
    """The counts the ledger keeps from a challenge; the verdicts stay in
    `challenges.jsonl`, since the ledger carries no article text or quotes."""

    n_records: int | None = None
    n_upheld: int | None = None
    n_disputed: int | None = None
    n_unverifiable: int | None = None
    n_fabricated: int | None = None
    incomplete: bool | None = None


class SupplementBundle(Row):
    status: int = 0           # HTTP status of the bundle fetch; 0 = not attempted
    bytes: int = 0
    members: int = 0
    n_unmatched_members: int = 0
    skip_reason: str = ""


class SupplementMeta(Row):
    member: str
    ext: str = ""
    bytes: int = 0
    label: str = ""
    sheets: list[Loose] = Field(default_factory=list)
    skip_reason: str = ""
    sheets_dropped_at_cap: int | None = None


class FigureMeta(Row):
    fig_id: str = ""
    label: str = ""
    member: str = ""
    selected: bool = False
    select_reason: str = ""
    transcribed: bool = False
    skip_reason: str = ""
    image_sha256: str = ""
    legible: bool | None = None
    contains_variant_labels: bool | None = None
    figure_prompt_version: str | None = None


class FigureGate(Row):
    n_hgvs: int | None = None
    largest_claim: int | None = None
    points_elsewhere: bool | None = None
    deficit: bool | None = None
    forced: bool | None = None
    selected_reason: str | None = None
    n_cited: int | None = None
    n_titled: int | None = None


class Region(Row):
    """Where a span of the assembled text came from, which is what lets
    grounding tell a figure transcript from the author's own words."""

    kind: str
    label: str
    start: int
    end: int


class Grounding(Row):
    total: int
    grounded: int
    ungrounded: list[Any] = Field(default_factory=list)
    low_confidence: list[Any] = Field(default_factory=list)
    region_counts: dict[str, int] = Field(default_factory=dict)
    quote_regions: list[Any] = Field(default_factory=list)
    figure_sourced: list[str] = Field(default_factory=list)
    model_transcribed_only: list[str] = Field(default_factory=list)
    overconfident_transcribed: list[str] = Field(default_factory=list)
    needs_review: bool = False


# ----------------------------------------------------------------- file rows

class LedgerEvent(Row):
    """One event about one candidate. Append-only; the fold is by `written_at`.

    No article text, no abstract, no quotes: the file is tracked and
    redistributing article content is forbidden. `Ledger.record` rejects those
    keys at runtime and `extra="forbid"` here rejects anything unforeseen.
    """

    identity: str
    ledger_version: int
    written_at: str
    identity_kind: str | None = None
    pmid: str | None = None
    doi: str | None = None
    title: str | None = None
    journal: str | None = None
    first_pub_date: str | None = None
    citation: str | None = None
    is_open_access: bool | None = None
    has_pmcid: bool | None = None
    found_by: list[str] | None = None
    dropped_at: str | None = None
    stage: str | None = None
    snapshot: str | None = None
    triage: TriageVerdict | None = None
    challenge: ChallengeSummary | None = None
    text_sha256: str | None = None
    n_chars: int | None = None
    abstract_only: bool | None = None
    fulltext_source: str | None = None
    bundle_status: int | None = None
    n_supplements: int | None = None
    n_figures: int | None = None
    prompt_version: str | None = None
    n_variants: int | None = None
    n_grounded: int | None = None
    n_ungrounded: int | None = None
    n_figure_sourced: int | None = None
    needs_review: bool | None = None
    attempts: int | None = None
    last_error: str | None = None


class DiscoveryRow(Row):
    query_hash: str
    slice: str
    date_from: str
    date_to: str
    hits_available: int | None = None
    hits_fetched: int
    complete: bool
    completed_at: str


class TriageDecisionRow(Row):
    pmid: str
    triage_prompt_version: str
    model: str
    read_full_text: bool
    reason: str
    usage: Usage


class FigureTranscriptRow(Row):
    pmcid: str
    fig_id: str
    label: str
    image_sha256: str
    figure_prompt_version: str
    model: str
    transcription: Loose
    usage: Usage


class PaperRow(Row):
    """A snapshot's record of one paper, including the assembled text that
    extraction reads and the hash that resume keys on."""

    pmid: str
    pmcid: str | None = None
    doi: str | None = None
    title: str = ""
    journal: str | None = None
    first_pub_date: str | None = None
    pub_year: str | None = None
    author_string: str | None = None
    authors: list[str] = Field(default_factory=list)
    affiliation: str | None = None
    abstract: str | None = None
    citation: str = ""
    is_open_access: bool = False
    source: str = ""
    abstract_only: bool = False
    n_tables: int = 0
    n_chars: int = 0
    text: str = ""
    text_sha256: str = ""
    regions: list[Region] = Field(default_factory=list)
    fulltext_source: str | None = None
    supplement_bundle: SupplementBundle = Field(default_factory=SupplementBundle)
    supplements: list[SupplementMeta] = Field(default_factory=list)
    figures: list[FigureMeta] = Field(default_factory=list)
    figure_gate: FigureGate = Field(default_factory=FigureGate)
    n_chars_supplements: int = 0
    n_chars_figures: int = 0
    pubtator_hints: list[Loose] = Field(default_factory=list)
    triage: TriageVerdict = Field(default_factory=TriageVerdict)


class ExtractionRow(Row):
    """Either a result or a recorded failure.

    A failure row is a log, not a result, and `cmd_extract` excludes it from the
    resume set so a transient refusal is retried. Both shapes live in the same
    file, so `extraction` and `grounding` are optional and `error` is the
    discriminator.
    """

    pmid: str
    pmcid: str | None = None
    model: str | None = None
    prompt_version: str
    # Optional only for history: the 20 rows in the 2026-10-29 snapshot were
    # written before resume keyed on the text hash. Every row written since
    # carries it, and without it a paper cannot be told apart from the same
    # paper whose supplements have since arrived.
    text_sha256: str | None = None
    schema_version: str | None = None
    stop_reason: str | None = None
    usage: Usage | None = None
    grounding: Grounding | None = None
    extraction: PaperExtraction | None = None
    error: str | None = None
    detail: str | None = None


class ChallengeRow(Row):
    pmid: str
    text_sha256: str
    records_digest: str
    challenge_prompt_version: str
    extract_prompt_version: str
    model: str
    usage: ChallengeUsage
    challenge: Loose | None = None
    report: Loose | None = None
    error: str | None = None
    detail: str | None = None


class ManifestRow(Row):
    """One row per fetch run. As JSONL rather than one JSON object per
    directory, `cat data/raw/*/manifest.jsonl` is a table of every sweep.
    """

    snapshot: str | None = None
    fetched_at: str
    date_from: str | None = None
    date_to: str | None = None
    limit: int | None = None
    oa_only: bool = False
    europepmc_query: str = ""
    pubtator_queries: list[str] = Field(default_factory=list)
    papers: int = 0
    full_text: int = 0
    abstract_only: int = 0
    triage_keep: int = 0
    triage_keep_before_limit: int = 0
    candidates_triaged: int | None = None
    candidates_before_filters: int | None = None
    # Superseded name, kept so the five manifests written before the rename
    # still validate: it was computed after the open-access filter had
    # reassigned its own input, so it understated the pool roughly fourfold.
    candidates_before_limit: int | None = None
    candidates_cut_by_limit: int | None = None
    europepmc_hits_without_pmid: int | None = None
    pmid_lookup_requested: int | None = None
    pmid_lookup_resolved: int | None = None
    ledger_events: int | None = None
    discovery_slices: int | None = None
    discovery_slices_sealed: int | None = None
    supplement_files_parsed: int | None = None
    figures_transcribed: int | None = None
    figure_vision_calls: int | None = None
    figure_vision_tokens: dict[str, int] | None = None
    figure_cache_hits: int | None = None
    network_requests: int = 0
    cache_hits: int = 0
    rate_limiter_slept_s: float = 0.0


class TriageLabel(Row):
    """`borderline` is a real class, not a hedge: it marks a paper where the
    gate's decision is defensible either way, so a disagreement there is not
    counted as an error."""

    label: str                 # yes | no | borderline
    note: str = ""


class TriageLabels(Row):
    """Hand-assigned ground truth for the triage gate: one document, written
    once by a person, so JSON rather than JSONL. 20 papers, 5 yes, 14 no, 1
    borderline, assigned against the snapshot named here."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    about: str = Field(alias="_about")
    snapshot: str | None = None
    labels: dict[str, TriageLabel]


class ReviewRow(Row):
    """One flagged record for a human to check.

    `CURATOR_VERDICT` and `CURATOR_NOTE` are the only fields a person fills, and
    they are what `data/curated/gold/` would be built from. Nothing in the
    pipeline depends on them being filled; while they are empty, no output may
    claim a precision estimate.
    """

    pmid: str
    record_key: str
    citation: str = ""
    gene: str = ""
    as_written: str = ""
    hgvs_c: str = ""
    hgvs_p: str = ""
    n_probands: int | None = None
    n_families: int | None = None
    confidence: str = ""
    flagged_because: str = ""
    verdict: str = ""
    problem: str = ""
    refuting_quote: str = ""
    proposed_n_probands: int | str = ""
    quote_1: str = ""
    CURATOR_VERDICT: str = ""
    CURATOR_NOTE: str = ""


# Basename -> model. For a `.jsonl` file the model describes one row and
# `jsonl.write`/`jsonl.append` consult this on every write; for a `.json` file
# it describes the whole document and `write_document` does. Adding a file
# without adding it here is caught by the test suite.
#
# Which format a file gets is not a style choice: records that accumulate over
# runs are JSONL, because appending is safe under interleaving and a fold makes
# the file order-independent, while a single document written once per directory
# -- the snapshot manifest, the hand-assigned triage labels -- is JSON, which
# diffs legibly and loses nothing, since `jq -s` assembles the cross-run table.
MODELS: dict[str, type[BaseModel]] = {
    "ledger.jsonl": LedgerEvent,
    "discovery.jsonl": DiscoveryRow,
    "triage_decisions.jsonl": TriageDecisionRow,
    "figure_transcripts.jsonl": FigureTranscriptRow,
    "challenges.jsonl": ChallengeRow,
    "papers.jsonl": PaperRow,
    "extractions.jsonl": ExtractionRow,
    "review_queue.jsonl": ReviewRow,
    "manifest.json": ManifestRow,
    "triage_labels.json": TriageLabels,
}


def model_for(path) -> type[BaseModel] | None:
    from pathlib import Path

    return MODELS.get(Path(path).name)


def validate(path, row) -> None:
    """Raise if `row` does not conform to the schema for `path`'s file."""
    model = model_for(path)
    if model is None:
        return
    model.model_validate(row)


def write_document(path, obj) -> None:
    """Validate and write a whole-document JSON file, indented.

    The JSONL writers in `jsonl.py` cover files of records; this covers the two
    files that are one document each.
    """
    import json
    from pathlib import Path

    validate(path, obj)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
