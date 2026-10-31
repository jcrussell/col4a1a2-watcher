from col4a1a2.extract.prompt import PaperInput, system_prompt, user_content


def test_system_prompt_loads_and_mentions_rules():
    sp = system_prompt()
    assert "phenotype_systems" in sp and "n_probands" in sp and "verbatim" in sp
    # The denominator is the field the prompt must argue for, not just name.
    assert "n_tested" in sp and "denominator" in sp


def test_user_content_marks_abstract_only_and_hints():
    p = PaperInput(pmid="1", pmcid=None, title="T", text="TITLE: T", abstract_only=True,
                   pubtator_hints=[{"type": "Variant", "text": "p.G513S", "identifier": "x"}])
    uc = user_content(p)
    assert "TITLE AND ABSTRACT ONLY" in uc and "p.G513S" in uc and "=== PAPER TEXT START ===" in uc


def test_schema_stays_within_structured_output_budget():
    """Version 1 was rejected by the API as too complex; keep the shape small."""
    from col4a1a2.extract.llm import schema_shape
    from col4a1a2.extract.schema import PaperExtraction

    # Measured on the schema as sent, with col4a1a2-dev probe: the goal-scoped
    # schema is 30 properties, 1 anyOf and 2 objects, accepted in 72s. These
    # bounds leave room to add a field without another three-minute probe.
    shape = schema_shape(PaperExtraction)
    assert shape["anyOf"] <= 2
    assert shape["objects"] <= 3
    assert shape["properties"] <= 40, f"{shape['properties']} properties; 48 was rejected by the API"


def test_citation_line_is_built_without_double_periods():
    from col4a1a2.sources.europepmc import PaperMeta, citation

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
