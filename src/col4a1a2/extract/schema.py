"""What we ask Claude to extract from a paper, and nothing more.

The schema is scoped to the three questions this project exists to answer:

1. **Is this paper new and relevant?** -- `paper_type`, `has_variant_level_data`.
2. **What variants exist?** -- gene, the variant as published, HGVS, the
   transcript that makes a `c.` number mean one thing, and the variant class.
3. **How many people are known to carry each?** -- probands, families, total
   carriers, and the denominator they came out of.

Everything else was cut, deliberately, after measuring it over 206 records.
Co-segregation counts were filled 12%, 11% and 2% of the time and are pure
ACMG interpretation evidence, which this project decided not to produce.
Per-assay functional structure was filled in 16 of 206 records; "is there
functional work on this variant" is the awareness that matters and one string
carries it. Phenotype statuses were six enums with a present /
absent_on_exam / not_assessed distinction; the three-way call is deep curation,
so this keeps which systems were affected and loses the ability to record that
an examination was done and normal. That loss is real and intentional.

**The structured-output grammar has a hard ceiling, and it is structural.**
Measured with `col4a1a2-dev probe`: 45 properties accepted, 48 rejected, and
cutting 1,300 bytes of description from the 48-property version did not help.
Only removing properties does. This shape is about 30 properties with two
objects and one nullable union, which leaves room for the next field instead of
spending three minutes a time discovering the wall again.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# Not bumped when the shape changes. `PROMPT_VERSION` is the key that gates
# re-extraction and already moves whenever the shape does, so a second version
# alongside it would be decoration. Bump this only if something ever stores it
# and depends on it.
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


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


# The closed vocabulary for `phenotype_systems`. A token list rather than six
# enum fields: it costs 1 property instead of 7 and stays machine-queryable.
PHENOTYPE_SYSTEMS = (
    "cerebrovascular",   # porencephaly, ICH/IVH, schizencephaly, stroke, perinatal brain injury
    "small_vessel",      # leukoencephalopathy, microbleeds, lacunes, PADMAL
    "renal_hanac",       # hematuria, cysts, nephropathy, HANAC features
    "ocular",            # cataract, anterior segment dysgenesis, retinal arterial tortuosity
    "muscle",            # cramps, raised CK, myopathy
    "other",
)


class VariantEvidence(BaseModel):
    gene: Gene
    as_written: str = Field(description="The variant exactly as written in the paper (first occurrence)")
    hgvs_c: str = Field(default="", description="Coding HGVS if given or derivable, e.g. c.1537G>A; empty if not")
    hgvs_p: str = Field(default="", description="Protein HGVS if given, e.g. p.Gly513Ser; empty if not")
    transcript_if_stated: str = Field(default="", description="Reference transcript if stated, e.g. NM_001845.6")
    variant_type: str = Field(description="missense_glycine, missense_other, splice, nonsense, frameshift, inframe_indel, intronic, utr, deletion_cnv, other")
    n_probands: int = Field(description="Unrelated probands with this variant in THIS paper")
    n_families: int = Field(description="Families with this variant in THIS paper")
    n_total_carriers: int | None = Field(default=None, description="All carriers including relatives; null if not stated")
    n_tested: int = Field(default=0, description="Individuals the study screened in total, the denominator this variant came out of, e.g. cohort or biobank size; 0 if not stated")
    inh_de_novo_confirmed: int = Field(description="Probands with both parents tested negative")
    inh_de_novo_assumed: int = Field(description="Probands called de novo with no parental testing described")
    inh_inherited: int = Field(description="Probands with a carrier parent")
    inh_unknown: int = Field(description="Probands with no inheritance information")
    phenotype_systems: str = Field(default="", description="Comma-separated from exactly: cerebrovascular, small_vessel, renal_hanac, ocular, muscle, other. List only systems reported as affected in a carrier of THIS variant; empty if none reported")
    phenotype_details: str = Field(default="", description="Phenotype as reported, including age at onset if stated")
    functional_evidence: str = Field(default="", description="Functional work on this variant in this paper, one sentence, e.g. reduced collagen IV secretion in transfected cells; empty if none")
    reported_classification: str = Field(default="", description="The authors' own classification verbatim; never your own")
    same_family_as_prior_report: bool = Field(description="True if this family or proband was reported before")
    prior_report_reason: str = Field(default="", description="Why you think it is the same family")
    evidence_quotes: list[str] = Field(description="2-5 verbatim sentences supporting the counts and phenotypes")
    confidence: Confidence = Field(description="Your confidence that the counts are correct")
    notes: str = Field(default="", description="Ambiguities, e.g. numbering unclear, table only, abstract only")


class PaperExtraction(BaseModel):
    pmid: str
    pmcid: str = ""
    paper_type: PaperType
    input_was_abstract_only: bool = Field(description="True if only the title and abstract were available")
    has_variant_level_data: bool = Field(description="True if the paper reports specific COL4A1/COL4A2 variants in specific individuals")
    variants: list[VariantEvidence] = Field(default_factory=list)
    extraction_notes: str = Field(default="", description="Anything the reviewer should know: data referred to but not provided, supplementary rows filtered out, an illegible figure transcription, numbering issues")

