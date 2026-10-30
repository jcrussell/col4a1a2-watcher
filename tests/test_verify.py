from col4a1.extract.schema import (
    Confidence, Gene, InheritanceCounts, PaperExtraction, PaperType, PhenotypeStatus, Phenotypes, Segregation,
    VariantEvidence,
)
from col4a1.extract.verify import ground, normalise, quote_in_text

TEXT = "The proband carried the c.1537G>A (p.Gly513Ser) variant — inherited from her mother. Renal ultrasound was normal."


def _variant(quotes, conf=Confidence.high):
    return VariantEvidence(
        gene=Gene.COL4A1, as_written="c.1537G>A", variant_type="missense_glycine",
        n_probands=1, n_families=1,
        inheritance=InheritanceCounts(de_novo_confirmed=0, de_novo_assumed=0, inherited=1, unknown=0),
        segregation=Segregation(), same_family_as_prior_report=False,
        phenotypes=Phenotypes(cerebrovascular=PhenotypeStatus.present, small_vessel=PhenotypeStatus.not_assessed,
                              renal_hanac=PhenotypeStatus.absent_on_exam, ocular=PhenotypeStatus.not_assessed,
                              muscle=PhenotypeStatus.not_assessed, other=PhenotypeStatus.not_assessed),
        evidence_quotes=quotes, confidence=conf,
    )


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
