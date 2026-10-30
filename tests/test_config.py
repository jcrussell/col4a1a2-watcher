"""Configuration loading, and the rate-key hazard that motivates most of it."""

import tomllib
from pathlib import Path

import pytest

from col4a1.config import EXPECTED_RATE_KEYS, ConfigError, Prices, load_settings

CONFIG = Path(__file__).resolve().parents[1] / "config" / "col4a1.toml"


def test_committed_config_loads():
    st = load_settings(CONFIG)
    assert st.contact_email and "@" in st.contact_email
    assert "COL4A1" in st.europepmc_query and "TITLE:" in st.europepmc_query
    assert st.pubtator_gene_ids == {"1282", "1284"}
    assert len(st.pubtator_queries) == 2


def test_every_rate_key_is_quoted_and_numeric():
    """The sharpest regression in the migration.

    TOML reads a bare dotted key as a nested table, so `www.ebi.ac.uk = 2`
    becomes {"www": {"ebi": {"ac": {"uk": 2}}}}. The rate limiter would then
    match no host, fall everything back to 1/s, and take the supplement
    endpoint from 0.25/s to the rate that returns HTTP 500 -- silently, and
    against a host we are being deliberately polite to.
    """
    st = load_settings(CONFIG)
    assert set(st.rates) == EXPECTED_RATE_KEYS
    for key, value in st.rates.items():
        assert isinstance(value, float), f"{key} must be numeric, got {value!r}"
        assert 0 < value <= 10, f"{key} rate {value} is implausible"
    # Compare numerically: TOML distinguishes 2 from 0.25 where YAML did not.
    assert st.rates["europepmc:supplementaryFiles"] == pytest.approx(0.25)
    assert st.rates["www.ebi.ac.uk"] == pytest.approx(2.0)


def test_unquoted_host_key_is_rejected_not_silently_accepted(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        'contact_email = "a@b.c"\nuser_agent = "ua"\n'
        '[europepmc]\nquery = "q"\n[pubtator]\n'
        "[rates]\nwww.ebi.ac.uk = 2\n",
        encoding="utf-8",
    )
    # Confirm the hazard is real at the TOML level ...
    with bad.open("rb") as f:
        assert tomllib.load(f)["rates"] == {"www": {"ebi": {"ac": {"uk": 2}}}}
    # ... and that we refuse it rather than running at 1/s against every host.
    with pytest.raises(ConfigError, match="Quote every host key"):
        load_settings(bad)


def test_clinical_regex_is_code_not_config():
    """One definition only. It is a classifier, versioned with its code, and
    immune to TOML's string-literal escaping rules there."""
    from col4a1.cli import CLINICAL_DEFAULT

    with CONFIG.open("rb") as f:
        raw = tomllib.load(f)
    assert "triage" not in raw, "the regex must not be duplicated into config"
    assert r"c\.\d" in CLINICAL_DEFAULT


def test_paths_derive_from_the_cache_dir(tmp_path):
    st = load_settings(CONFIG, cache_dir=tmp_path / "c")
    assert st.cache_dir == tmp_path / "c"
    assert st.http_cache == tmp_path / "c" / "http.sqlite"
    assert st.bundle_store == tmp_path / "c" / "bundles"
    # curated paths are independent of the cache override
    assert st.triage_decisions.name == "triage_decisions.jsonl"
    assert st.figure_transcripts.name == "figure_transcripts.jsonl"


def test_missing_config_is_an_error_not_a_silent_default(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml")


def test_prices_cover_every_configured_model_and_compute_cost():
    st = load_settings(CONFIG)
    for model in (st.extract_model, st.triage_model, st.figure_model):
        assert st.price_for(model) is not None, f"no price for {model}"
    # The measured triage spend: 515,607 in / 50,427 out over 742 decisions was
    # $0.077 total. Docs quoted 10x that for months by using Haiku 4.5 prices.
    haiku = st.price_for("claude-haiku-5-5")
    assert haiku.cost(input=515_607, output=50_427) == pytest.approx(0.0768, abs=0.001)


def test_text_affecting_settings_are_reported_for_the_manifest():
    """Each of these changes the assembled text, hence the resume key, so the
    manifest must record them or a re-extraction is untraceable."""
    st = load_settings(CONFIG)
    keys = st.text_affecting()
    assert set(keys) >= {"supplements_max_chars_per_paper", "figures_max_per_paper"}
    assert all(isinstance(v, int) for v in keys.values())


def test_prices_cost_is_linear_and_counts_cache_separately():
    p = Prices(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
    assert p.cost(input=1_000_000) == pytest.approx(4.0)
    assert p.cost(output=1_000_000) == pytest.approx(20.0)
    assert p.cost(cache_read=1_000_000) == pytest.approx(0.2)
