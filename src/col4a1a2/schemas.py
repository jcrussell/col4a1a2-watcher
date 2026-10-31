"""Row schemas for every JSON Lines file this project writes.

Why this exists. Everything downstream of a stage reads that stage's JSONL.
Without a declared schema, a writer that said `n_variant` where a reader wanted
`n_variants` would produce a file that parsed cleanly, folded cleanly, and
quietly lost the field. The ledger is the project's denominator; it cannot be
the place where a typo hides.

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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from col4a1a2.extract.schema import PaperExtraction
from col4a1a2.ledger import DROPPED_AT, STAGES


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
    """Token usage on a challenge row; `effort` here is optional and superseded
    by the row's top-level `effort`, which every row carries.
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
    # Closed vocabularies, so an excluded value such as `limit` is rejected.
    dropped_at: Literal[DROPPED_AT] | None = None
    stage: Literal[STAGES] | None = None
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
    requested_model: str | None = None
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


class Provenance(Row):
    """Who answered, what we asked for, how hard, and when.

    `model` is what the API reports served the request, which can differ from
    `requested_model` under server-side fallbacks. Caches and resume key on
    `requested_model`, so a fallback-served answer still counts as done for the
    run that asked for it, and a second model's rows sit alongside the first's.
    """

    model: str
    requested_model: str
    effort: str | None
    written_at: str


class TriageDecisionRow(Provenance):
    pmid: str
    triage_prompt_version: str
    read_full_text: bool
    reason: str
    usage: Usage


class FigureTranscriptRow(Provenance):
    pmcid: str
    fig_id: str
    label: str
    image_sha256: str
    figure_prompt_version: str
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
    # A failure row may have no served model: the call never returned.
    model: str | None = None
    requested_model: str
    effort: str | None = None
    written_at: str
    # The snapshot whose papers.jsonl holds the text this row was extracted
    # from; `challenge` reads it there, since the text itself is never tracked.
    snapshot: str
    prompt_version: str
    # Without it a paper cannot be told apart from the same paper whose
    # supplements have since arrived.
    text_sha256: str
    schema_version: str | None = None
    stop_reason: str | None = None
    usage: Usage | None = None
    grounding: Grounding | None = None
    # Permissive by type, strict by validator. A prompt bump that reshapes the
    # schema leaves older rows in an older shape until they are re-extracted,
    # and validating those against today's model would fail. So the current
    # prompt version is checked against `PaperExtraction` and older ones are
    # accepted as they stand.
    extraction: dict | None = None
    error: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def _check_current_extraction(self):
        from col4a1a2.extract.prompt import PROMPT_VERSION

        if self.extraction is not None and self.prompt_version == PROMPT_VERSION:
            PaperExtraction.model_validate(self.extraction)
        return self


class ChallengeRow(Provenance):
    pmid: str
    text_sha256: str
    records_digest: str
    challenge_prompt_version: str
    extract_prompt_version: str
    usage: ChallengeUsage
    challenge: Loose | None = None
    report: Loose | None = None
    error: str | None = None
    detail: str | None = None


class ManifestRow(Row):
    """One document per snapshot directory; `jq -s` over
    `data/raw/*/manifest.json` is a table of every sweep.
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
    # Superseded name, accepted only so older manifests validate; nothing writes
    # it. Its values were counted after the open-access filter had narrowed its
    # own input, so they understate the pool roughly fourfold.
    candidates_before_limit: int | None = None
    candidates_cut_by_limit: int | None = None
    europepmc_hits_without_pmid: int | None = None
    pmid_lookup_requested: int | None = None
    pubtator_export_requested: int | None = None
    pubtator_export_resolved: int | None = None
    text_affecting: dict[str, float] | None = None
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


class ApiResponse(Row):
    """One archived Claude response, written by `extract/llm.py` before parsing.

    `request` summarises the call (prompt version, input hash, pmid) in place
    of the request text, which the snapshot already holds. `response` is the
    API's message as returned, thinking included.
    """

    digest: str
    written_at: str
    site: Literal["extract", "triage", "figure", "challenge"]
    requested_model: str
    effort: str | None
    max_tokens: int
    betas: list[str]
    request: Loose
    response: Loose


# Files named `<digest>.<stamp>.response.json`, one per call, matched by suffix.
RESPONSE_SUFFIX = ".response.json"

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
    "triage.jsonl": TriageDecisionRow,
    "figures.jsonl": FigureTranscriptRow,
    "challenges.jsonl": ChallengeRow,
    "papers.jsonl": PaperRow,
    "extractions.jsonl": ExtractionRow,
    "review.jsonl": ReviewRow,
    "manifest.json": ManifestRow,
    "labels.json": TriageLabels,
}


def model_for(path) -> type[BaseModel] | None:
    from pathlib import Path

    name = Path(path).name
    if name.endswith(RESPONSE_SUFFIX):
        return ApiResponse
    return MODELS.get(name)


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

    from col4a1a2.jsonl import _replace_atomically

    validate(path, obj)
    _replace_atomically(Path(path), json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
