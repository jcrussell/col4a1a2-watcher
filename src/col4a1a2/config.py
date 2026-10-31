"""Configuration: one TOML file, resolved once into a frozen Settings object.

Read with stdlib `tomllib`, so there is no dependency to install. Precedence is
CLI flags over the file over the defaults below.

Two hazards this module exists to contain:

1. **TOML reads a bare dotted key as a nested table.** `www.ebi.ac.uk = 2`
   parses to `{"www": {"ebi": {"ac": {"uk": 2}}}}`, which would make the rate
   limiter match no host and fall every host back to 1/s -- including the
   supplement endpoint, which answers HTTP 500 at that rate. `rates` is
   therefore validated to be flat, with a test asserting the exact key set.
2. **A settings object nobody passes is no better than a constant.** Every
   command takes `Settings`, including `extract`, `show` and `list`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# The defaults live beside the code they govern; config can override each one.
from col4a1a2.extract.figures import MAX_FIGURE_TOKENS, MAX_FIGURES_PER_PAPER
from col4a1a2.sources.supplements import BUNDLE_DEADLINE_S, BUNDLE_RATE_KEY
from col4a1a2.sources.tabular import KEEP_ALL_BELOW, MAX_CHARS_PER_PAPER, MAX_ROWS_PER_SHEET

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "config" / "col4a1a2.toml"

# Hosts we are willing to talk to at a rate other than the 1/s default. A key
# here that is not a hostname (the supplement bucket) is a deliberate synthetic
# rate key, passed explicitly by its caller.
EXPECTED_RATE_KEYS = {
    "www.ebi.ac.uk",
    BUNDLE_RATE_KEY,
    "eutils.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "ftp.ncbi.nlm.nih.gov",
    "rest.ensembl.org",
    "gnomad.broadinstitute.org",
}


class ConfigError(RuntimeError):
    """The config file is present but unusable."""


@dataclass(frozen=True)
class Prices:
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0

    def cost(self, *, input: int = 0, output: int = 0, cache_read: int = 0, cache_write: int = 0) -> float:
        return (input * self.input + output * self.output
                + cache_read * self.cache_read + cache_write * self.cache_write) / 1e6


@dataclass(frozen=True)
class Settings:
    config_path: Path
    contact_email: str
    user_agent: str

    europepmc_query: str
    europepmc_page_size: int
    pubtator_queries: tuple[str, ...]
    pubtator_gene_ids: frozenset[str]
    pubtator_page_size: int

    rates: dict[str, float]

    cache_dir: Path
    snapshot_root: Path
    curated_dir: Path
    # Raw API responses we paid for. Outside cache_dir on purpose: eviction
    # must never delete one.
    responses_dir: Path

    anthropic_max_retries: int
    anthropic_timeout_s: float

    extract_model: str
    extract_effort: str
    triage_model: str
    triage_effort: str
    figure_model: str
    figure_effort: str

    prices: dict[str, Prices] = field(default_factory=dict)

    figures_max_per_paper: int = MAX_FIGURES_PER_PAPER
    figures_max_tokens: int = MAX_FIGURE_TOKENS

    # These three alter the assembled paper text, so they are echoed into the
    # snapshot manifest; a change here re-extracts the affected papers and the
    # manifest is the only place that would explain why.
    supplements_max_chars_per_paper: int = MAX_CHARS_PER_PAPER
    supplements_max_rows_per_sheet: int = MAX_ROWS_PER_SHEET
    supplements_keep_all_below_rows: int = KEEP_ALL_BELOW
    bundle_deadline_s: float = BUNDLE_DEADLINE_S

    # Derived paths
    @property
    def http_cache(self) -> Path:
        return self.cache_dir / "http.sqlite"

    @property
    def bundle_store(self) -> Path:
        return self.cache_dir / "bundles"

    @property
    def lock_file(self) -> Path:
        """Held by any command that writes, so two runs never interleave."""
        return self.curated_dir.parent / ".lock"

    @property
    def triage_cache(self) -> Path:
        return self.curated_dir / "triage.jsonl"

    @property
    def extractions(self) -> Path:
        """Extraction results: tracked, so `publish` can build from the repo alone."""
        return self.curated_dir / "extractions.jsonl"

    @property
    def figure_cache(self) -> Path:
        return self.curated_dir / "figures.jsonl"

    def price_for(self, model: str) -> Prices | None:
        return self.prices.get(model)

    def text_affecting(self) -> dict:
        """The settings that change the assembled text, for the manifest."""
        return {
            "supplements_max_chars_per_paper": self.supplements_max_chars_per_paper,
            "supplements_max_rows_per_sheet": self.supplements_max_rows_per_sheet,
            "supplements_keep_all_below_rows": self.supplements_keep_all_below_rows,
            "figures_max_per_paper": self.figures_max_per_paper,
            "figures_max_tokens": self.figures_max_tokens,
            # Which bundles arrive in time decides which supplements reach the text.
            "bundle_deadline_s": self.bundle_deadline_s,
        }


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _check_rates(raw: object, path: Path) -> dict[str, float]:
    """Reject the nested-table shape a bare dotted key would produce."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [rates] must be a table of host -> requests/second")
    bad = {k: v for k, v in raw.items() if not isinstance(v, (int, float)) or isinstance(v, bool)}
    if bad:
        raise ConfigError(
            f"{path}: [rates] keys must map to numbers, got {sorted(bad)}. "
            "A nested value here almost always means an unquoted host key: TOML reads "
            '`www.ebi.ac.uk = 2` as a nested table. Quote every host key.'
        )
    return {k: float(v) for k, v in raw.items()}


def load_settings(
    config_path: Path | str | None = None,
    *,
    cache_dir: Path | str | None = None,
    repo: Path | None = None,
) -> Settings:
    root = repo or REPO
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as f:
        raw = tomllib.load(f)

    paths = raw.get("paths", {})
    ant = raw.get("anthropic", {})
    models = raw.get("models", {})
    figs = raw.get("figures", {})
    sup = raw.get("supplements", {})
    epmc = raw.get("europepmc", {})
    pt = raw.get("pubtator", {})

    prices = {}
    for model, spec in (raw.get("prices") or {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: [prices] entry for {model!r} must be a table")
        prices[model] = Prices(
            input=float(spec["input"]), output=float(spec["output"]),
            cache_read=float(spec.get("cache_read", 0.0)),
            cache_write=float(spec.get("cache_write", 0.0)),
        )

    return Settings(
        config_path=path,
        contact_email=raw["contact_email"],
        user_agent=raw["user_agent"],
        europepmc_query=epmc["query"],
        europepmc_page_size=int(epmc.get("page_size", 100)),
        pubtator_queries=tuple(pt.get("queries", ())),
        pubtator_gene_ids=frozenset(str(g) for g in pt.get("gene_ids", ())),
        pubtator_page_size=int(pt.get("page_size", 100)),
        rates=_check_rates(raw.get("rates", {}), path),
        cache_dir=_resolve(root, str(cache_dir)) if cache_dir else _resolve(root, paths.get("cache_dir", "data/cache")),
        snapshot_root=_resolve(root, paths.get("snapshot_root", "data/raw")),
        curated_dir=_resolve(root, paths.get("curated_dir", "data/curated")),
        responses_dir=_resolve(root, paths.get("responses_dir", "data/responses")),
        anthropic_max_retries=int(ant.get("max_retries", 8)),
        anthropic_timeout_s=float(ant.get("timeout_s", 120.0)),
        extract_model=models.get("extract_model", "claude-opus-5-5"),
        extract_effort=models.get("extract_effort", "high"),
        triage_model=models.get("triage_model", "claude-haiku-5-5"),
        triage_effort=models.get("triage_effort", "low"),
        figure_model=models.get("figure_model", "claude-opus-5-5"),
        figure_effort=models.get("figure_effort", "low"),
        prices=prices,
        figures_max_per_paper=int(figs.get("max_per_paper", MAX_FIGURES_PER_PAPER)),
        figures_max_tokens=int(figs.get("max_tokens", MAX_FIGURE_TOKENS)),
        supplements_max_chars_per_paper=int(sup.get("max_chars_per_paper", MAX_CHARS_PER_PAPER)),
        supplements_max_rows_per_sheet=int(sup.get("max_rows_per_sheet", MAX_ROWS_PER_SHEET)),
        supplements_keep_all_below_rows=int(sup.get("keep_all_below_rows", KEEP_ALL_BELOW)),
        bundle_deadline_s=float(sup.get("bundle_deadline_s", BUNDLE_DEADLINE_S)),
    )
