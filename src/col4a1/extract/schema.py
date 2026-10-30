"""Structured-output schema for per-paper variant evidence extraction.

The shape is deliberately flat: structured outputs compile the schema into a
grammar, and a first draft with 12 nullable unions plus two extra nested
objects was rejected by the API with "Schema is too complex". Optional strings
therefore use "" for "not stated"; the three segregation counts and
n_total_carriers keep null, because 0 and unknown mean different things there.
See tests/test_prompt.py for the guard that keeps the shape within budget.
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

class PaperType(str, Enum):
    case_report = "case_report"
    case_series = "case_series"
    cohort = "cohort"
    review = "review"
    functional = "functional"
    other = "other"
class Gene(str, Enum):
    COL4A1 = "COL4A1"
    COL4A2 = "COL4A2"
class PhenotypeStatus(str, Enum):
    present = "present"
    absent_on_exam = "absent_on_exam"
    not_assessed = "not_assessed"
class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

class InheritanceCounts(BaseModel):
    de_novo_confirmed: int = Field(description="Probands whose parents were both tested negative")
    de_novo_assumed: int = Field(description="Probands called de novo without described parental testing")
    inherited: int = Field(description="Probands with a carrier parent")
    unknown: int = Field(description="Probands with no inheritance information")

class Segregation(BaseModel):
    affected_carriers: int | None = Field(default=None, description="Relatives who carry the variant and are affected, excluding probands; null if not stated")
    unaffected_carriers: int | None = Field(default=None, description="Relatives who carry the variant and are reported unaffected; null if not stated")
    non_carrier_affected: int | None = Field(default=None, description="Affected relatives shown not to carry the variant; null if not stated")

class Phenotypes(BaseModel):
    cerebrovascular: PhenotypeStatus = Field(description="Porencephaly, intracerebral or intraventricular haemorrhage, schizencephaly, stroke, perinatal brain injury")
    small_vessel: PhenotypeStatus = Field(description="Leukoencephalopathy, microbleeds, lacunes, PADMAL, white-matter disease on imaging")
    renal_hanac: PhenotypeStatus = Field(description="Hematuria, cysts, nephropathy, HANAC features")
    ocular: PhenotypeStatus = Field(description="Cataract, anterior segment dysgenesis, Axenfeld-Rieger, retinal arterial tortuosity")
    muscle: PhenotypeStatus = Field(description="Cramps, elevated CK, myopathy")
    other: PhenotypeStatus = Field(description="Any other organ-system finding; describe in details")
    details: str = Field(default="", description="Free-text summary of the phenotype(s) as reported, including age at onset if stated")

class FunctionalAssay(BaseModel):
    assay_type: str = Field(description="e.g. collagen IV secretion, intracellular retention, ER stress, splicing minigene, mouse model")
    result: str = Field(description="What the assay showed for this variant")
    evidence_quote: str = Field(description="Verbatim sentence from the paper supporting the result")

class VariantEvidence(BaseModel):
    gene: Gene
    as_written: str = Field(description="The variant exactly as written in the paper (first occurrence)")
    hgvs_c: str = Field(default="", description="Coding HGVS if given or unambiguously derivable, e.g. c.1537G>A; empty if not")
    hgvs_p: str = Field(default="", description="Protein HGVS if given, e.g. p.Gly513Ser; empty if not")
    transcript_if_stated: str = Field(default="", description="Reference transcript if the paper states it, e.g. NM_001845.6")
    genome_build_if_stated: str = Field(default="", description="GRCh37/hg19 or GRCh38/hg38 if stated")
    variant_type: str = Field(description="missense_glycine, missense_other, splice, nonsense, frameshift, inframe_indel, intronic, utr, deletion_cnv, other")
    n_probands: int = Field(description="Number of unrelated probands reported with this variant in THIS paper")
    n_families: int = Field(description="Number of families reported with this variant in THIS paper")
    n_total_carriers: int | None = Field(default=None, description="All reported carriers including relatives; null if not stated")
    inheritance: InheritanceCounts = Field(description="Proband counts by inheritance mode; must sum to n_probands")
    segregation: Segregation
    phenotypes: Phenotypes
    functional_assays: list[FunctionalAssay] = Field(default_factory=list)
    reported_classification: str = Field(default="", description="The authors' own classification (pathogenic, likely pathogenic, VUS) if stated")
    same_family_as_prior_report: bool = Field(description="True if the paper indicates this family or proband was reported before")
    prior_report_pmid: str = Field(default="", description="PMID of the prior report if cited")
    prior_report_reason: str = Field(default="", description="Why you think it is the same family")
    evidence_quotes: list[str] = Field(description="2-5 verbatim sentences from the paper that support the counts and phenotypes above")
    confidence: Confidence = Field(description="Your confidence that the counts and phenotype statuses are correct")
    notes: str = Field(default="", description="Ambiguities, e.g. numbering convention unclear, table only, abstract only")

class PaperExtraction(BaseModel):
    pmid: str
    pmcid: str = ""
    paper_type: PaperType
    input_was_abstract_only: bool = Field(description="True if only the title and abstract were available")
    has_variant_level_data: bool = Field(description="True if the paper reports specific COL4A1/COL4A2 variants in specific individuals")
    variants: list[VariantEvidence] = Field(default_factory=list)
    extraction_notes: str = Field(default="", description="Anything the reviewer should know: tables not parsed, supplementary data referenced, numbering issues")


def json_schema() -> dict:
    return PaperExtraction.model_json_schema()
