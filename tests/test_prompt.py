from col4a1.extract.prompt import PaperInput, system_prompt, user_content


def test_system_prompt_loads_and_mentions_rules():
    sp = system_prompt()
    assert "not_assessed" in sp and "n_probands" in sp and "verbatim" in sp


def test_user_content_marks_abstract_only_and_hints():
    p = PaperInput(pmid="1", pmcid=None, title="T", text="TITLE: T", abstract_only=True,
                   pubtator_hints=[{"type": "Variant", "text": "p.G513S", "identifier": "x"}])
    uc = user_content(p)
    assert "TITLE AND ABSTRACT ONLY" in uc and "p.G513S" in uc and "=== PAPER TEXT START ===" in uc


def test_schema_stays_within_structured_output_budget():
    """Version 1 was rejected by the API as too complex; keep the shape small."""
    import json
    from col4a1.extract.schema import json_schema
    s = json.dumps(json_schema())
    assert s.count("anyOf") <= 4
    assert s.count('"object"') <= 6


def test_citation_line_is_built_without_double_periods():
    from col4a1.sources.europepmc import PaperMeta, citation

    m = PaperMeta(pmid="42438399", pmcid="PMC1", doi="10.1212/x", title="T", journal="Neurology",
                  pub_year="2026", first_pub_date="2026-07-01", is_open_access=True, abstract=None,
                  author_string="Morel A, Guey S, Hervé D.")
    assert citation(m) == "Morel A, et al. Neurology. 2026. PMID:42438399. doi:10.1212/x"

    solo = PaperMeta(pmid="1", pmcid=None, doi=None, title="T", journal="J Rare Dis", pub_year=None,
                     first_pub_date="2020-01-01", is_open_access=False, abstract=None, author_string="Solo A.")
    assert citation(solo) == "Solo A. J Rare Dis. 2020. PMID:1"

    bare = PaperMeta(pmid=None, pmcid=None, doi=None, title="T", journal=None, pub_year=None,
                     first_pub_date=None, is_open_access=False, abstract=None)
    assert citation(bare) == ""


def test_latest_extractions_supersedes_earlier_rows_and_drops_errors(tmp_path):
    import json

    from col4a1.cli import latest_extractions

    p = tmp_path / "extractions.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"pmid": "1", "text_sha256": "a", "extraction": {"variants": []}},
        {"pmid": "2", "text_sha256": "b", "error": "ExtractionRefused"},
        {"pmid": "1", "text_sha256": "c", "extraction": {"variants": [1, 2]}},
    ]) + "\n")
    rows = latest_extractions(p)
    assert [r["pmid"] for r in rows] == ["1"]
    assert rows[0]["text_sha256"] == "c", "the newer row must win"
