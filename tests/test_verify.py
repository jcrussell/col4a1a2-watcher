from col4a1a2.extract.schema import (
    Confidence, PaperExtraction, PaperType, VariantEvidence,
)
from col4a1a2.extract.verify import ground, normalise, quote_in_text
from conftest import make_variant

TEXT = "The proband carried the c.1537G>A (p.Gly513Ser) variant — inherited from her mother. Renal ultrasound was normal."


def _variant(quotes, conf=Confidence.high):
    return VariantEvidence.model_validate(make_variant(
        as_written="c.1537G>A", inh_inherited=1, inh_unknown=0, phenotype_systems="cerebrovascular",
        phenotype_details="porencephaly", evidence_quotes=quotes, confidence=conf.value))


def test_normalise_unifies_dashes_quotes_whitespace():
    assert normalise("a – b  “c”") == normalise("a - b \"c\"")


def test_quote_grounding_exact_and_trailing_punct():
    t = normalise(TEXT)
    assert quote_in_text("Renal ultrasound was normal", t)
    assert quote_in_text("Renal ultrasound was normal.", t)
    assert quote_in_text("inherited from her mother - inherited", t) is False


def test_ground_report_flags_ungrounded_and_low_confidence():
    ex = PaperExtraction(pmid="1", paper_type=PaperType.case_report, input_was_abstract_only=False,
                         has_variant_level_data=True,
                         variants=[_variant(["Renal ultrasound was normal.", "This sentence is invented."], Confidence.low)])
    rep = ground(ex, TEXT)
    assert rep.total_quotes == 2 and rep.grounded_quotes == 1
    assert rep.ungrounded == [("c.1537G>A", "This sentence is invented.")]
    assert rep.low_confidence_variants == ["c.1537G>A"]
    assert rep.needs_review


def test_schema_roundtrip_and_json_schema():
    ex = PaperExtraction(pmid="2", paper_type=PaperType.review, input_was_abstract_only=True, has_variant_level_data=False)
    assert PaperExtraction.model_validate_json(ex.model_dump_json()) == ex
    schema = PaperExtraction.model_json_schema()
    assert "variants" in schema["properties"]


def test_region_attribution_separates_supplement_from_figure_evidence():
    from col4a1a2.sources.fulltext import PaperText

    pt = PaperText(pmid="1", pmcid="PMC1", title="T", abstract="", body="The proband carried c.1A>T.",
                   n_tables=0,
                   supplements=[("eTable 2", "=== SUPPLEMENTARY FILE: eTable 2 ===\nP1 | COL4A1 | c.2G>C")],
                   figures=[("Figure 4", "=== FIGURE TRANSCRIPTION (MODEL-GENERATED, NOT AUTHOR TEXT): Figure 4 ===\nexon 3 | c.3T>A")])
    text, regions = pt.as_prompt_text(), pt.regions()

    def extraction(quote):
        return PaperExtraction(pmid="1", paper_type=PaperType.cohort, input_was_abstract_only=False,
                               has_variant_level_data=True, variants=[_variant([quote])])

    body = ground(extraction("The proband carried c.1A>T."), text, regions)
    assert body.region_counts == {"body": 1} and body.model_transcribed_only == []

    supp = ground(extraction("P1 | COL4A1 | c.2G>C"), text, regions)
    assert supp.region_counts == {"supplement": 1}
    assert supp.model_transcribed_only == [], "a deterministic parse is verifiable; no review needed"

    fig = ground(extraction("exon 3 | c.3T>A"), text, regions)
    assert fig.region_counts == {"figure_transcript": 1}
    assert fig.figure_sourced == ["c.1537G>A"]
    assert fig.model_transcribed_only == ["c.1537G>A"] and fig.needs_review
    assert fig.quote_regions[0][2] == "figure_transcript:Figure 4"


def test_a_body_quote_does_not_launder_figure_sourced_evidence():
    """Pairing a figure quote with a generic body sentence must not hide the
    figure provenance, or figure-derived records pass as body text."""
    from col4a1a2.sources.fulltext import PaperText

    pt = PaperText(pmid="1", pmcid=None, title="T", abstract="",
                   body="Variant positions are depicted in Figure 4.", n_tables=0,
                   figures=[("Figure 4", "=== FIGURE TRANSCRIPTION (MODEL-GENERATED, NOT AUTHOR TEXT): Figure 4 ===\nexon 3 | c.3T>A")])
    text, regions = pt.as_prompt_text(), pt.regions()
    ex = PaperExtraction(pmid="1", paper_type=PaperType.cohort, input_was_abstract_only=False,
                         has_variant_level_data=True,
                         variants=[_variant(["Variant positions are depicted in Figure 4.", "exon 3 | c.3T>A"])])
    rep = ground(ex, text, regions)
    assert rep.figure_sourced == ["c.1537G>A"], "any figure support must be flagged"
    assert rep.model_transcribed_only == [], "it is not figure-only; it also has a body quote"
    assert rep.overconfident_transcribed == ["c.1537G>A"], "high confidence on figure evidence is checked"
    assert rep.needs_review


def test_quote_spanning_two_regions_does_not_ground():
    from col4a1a2.sources.fulltext import PaperText

    pt = PaperText(pmid="1", pmcid=None, title="T", abstract="", body="ends here", n_tables=0,
                   supplements=[("eTable", "starts there")])
    text, regions = pt.as_prompt_text(), pt.regions()
    ex = PaperExtraction(pmid="1", paper_type=PaperType.cohort, input_was_abstract_only=False,
                         has_variant_level_data=True, variants=[_variant(["ends here starts there"])])
    assert ground(ex, text, regions).grounded_quotes == 0
    # Without regions the same quote still matches, so the stricter behaviour is opt-in.
    assert ground(ex, text).grounded_quotes == 1


def test_as_prompt_text_is_unchanged_when_nothing_is_appended():
    from col4a1a2.sources.fulltext import PaperText

    pt = PaperText(pmid="1", pmcid=None, title="T", abstract="A", body="B", n_tables=0)
    assert pt.as_prompt_text() == "TITLE: T\n\nABSTRACT:\nA\n\nBODY:\nB"
    assert [r.kind for r in pt.regions()] == ["title", "abstract", "body"]
