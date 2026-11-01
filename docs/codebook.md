# Codebook: per-paper variant evidence extraction

This defines every field Claude fills in `PaperExtraction` (see `src/col4a1a2/extract/schema.py`) and how a human spot-checker should judge it. The system prompt (`src/col4a1a2/extract/prompts/v1.md`) is the operational version of these rules; keep the two in sync. A change to the prompt itself means bumping `PROMPT_VERSION`, which re-extracts every paper.

## What the schema is for

Three questions, and nothing else:

1. **Is this paper new and relevant?** `paper_type`, `has_variant_level_data`.
2. **What variants exist?** gene, the variant as published, HGVS, the transcript, the variant class.
3. **How many people are known to carry each?** probands, families, total carriers, and the denominator they came out of.

Fields that serve interpretation rather than these three are deliberately absent: co-segregation counts, per-assay functional structure, and a present / absent-on-exam / not-assessed phenotype status. Measured over 206 records they were rarely filled (segregation 12%, 11% and 2% of the time; structured functional data in 16 records), and the structured-output grammar has room only for the fields above. See the module docstring of `schema.py` and `docs/measurements.md`.

## Unit of extraction

One record per paper. Inside it, one `VariantEvidence` per distinct COL4A1/COL4A2 variant reported in specific individuals **in this paper**. Variants cited from earlier publications without new individuals are not records. Nor are **common polymorphisms studied for association** -- an rsID reported with allele frequencies, odds ratios or p-values across a cohort. They are not disease variants, and their carrier counts are not probands.

## Counting

| Field | Rule |
|---|---|
| `n_probands` | Unrelated index cases with this variant in this paper. One per family. If probands and relatives are not distinguished, count families and say so in `notes`. |
| `n_families` | Kindreds with this variant in this paper. **0 when carriers were found by screening a cohort and no family was studied** -- `n_probands` then carries the count. A roll-up must not sum families across such records. |
| `n_total_carriers` | All reported carriers including relatives; `null` if not stated. |
| `n_tested` | **The denominator**: everyone the study screened or sequenced -- cohort, biobank or panel size. `0` means not stated, never "nobody tested". A variant in 174 of half a million biobank participants and in 174 clinic patients are different facts, and this field is the only thing that tells them apart. |
| `same_family_as_prior_report` | `true` when the paper says the family or proband was reported before, with the reasoning in `prior_report_reason`. This is the main double-counting risk when rolling up across papers. |

## Inheritance (proband counts; the four sum to `n_probands`)

| Field | Rule |
|---|---|
| `inh_de_novo_confirmed` | Both parents tested negative; parentage not disputed. If the paper says parentage was not confirmed, or says only "possibly" de novo, it is `assumed`. |
| `inh_de_novo_assumed` | Called de novo without parental testing described. |
| `inh_inherited` | A parent carries the variant. |
| `inh_unknown` | Anything else. |

## Phenotypes

`phenotype_systems` is a comma-separated list of organ systems **reported as affected in at least one carrier of this variant**, from exactly this vocabulary. `derive.py` flags any token outside it.

| Token | Includes |
|---|---|
| `cerebrovascular` | porencephaly, intracerebral/intraventricular haemorrhage, schizencephaly, stroke, hemiparesis from perinatal injury, fetal intracranial haemorrhage |
| `small_vessel` | leukoencephalopathy, white-matter hyperintensities, microbleeds, lacunes, PADMAL, dilated perivascular spaces |
| `renal_hanac` | hematuria, renal cysts, nephropathy, reduced kidney function attributed to the variant, HANAC features |
| `ocular` | cataract, anterior segment dysgenesis, Axenfeld-Rieger, retinal arterial tortuosity, glaucoma, microphthalmia |
| `muscle` | cramps, elevated CK, myopathy |
| `other` | anything else, described in `phenotype_details` |

A system examined and found normal is **not** listed, and nothing records that it was examined. That is a deliberate loss: a normal renal ultrasound is real evidence, and the record cannot show it.

`phenotype_details` is the phenotype as reported, including age at onset if stated.

## Other awareness fields

| Field | Rule |
|---|---|
| `reported_classification` | The authors' own call, verbatim. Never the model's. |
| `functional_evidence` | One-sentence summary of functional work on this variant in this paper; empty if none. The model's own words, so it is **not** grounded. |

## Variant naming

`as_written` is copied verbatim. `hgvs_c` / `hgvs_p` only when given or unambiguous; never converted between numbering systems by the model. `transcript_if_stated` matters because a `c.` number means nothing without it. Normalization to NM_001845.6 / NM_001846.4 and GRCh38 happens afterwards in `normalize`, through Ensembl VEP, never in extraction; see `docs/methods.md`.

`variant_type`: `missense_glycine` (substitution of a Gly), `missense_other`, `splice`, `nonsense`, `frameshift`, `inframe_indel`, `intronic`, `utr`, `deletion_cnv`, `other`. The vocabulary has no class for a copy-number gain, so a duplication lands in `deletion_cnv`; `derive.py` flags it.

## Evidence quotes and grounding

Two to five verbatim sentences or table rows per variant. `verify.py` string-matches each quote (after normalising whitespace, dashes and quote marks) against the source text. Any unmatched quote or any `confidence=low` variant puts the record on the spot-check queue. Only `evidence_quotes` claims to be verbatim; nothing else is grounded.

## Confidence

`low` when counts come from an abstract only, from an ambiguous table, or when overlap with earlier reports is suspected; `medium` for clear reports with some inference; `high` when counts are stated explicitly.

## Spot-check protocol (human)

For each queued record, open the paper and check, in order: (1) every variant in the paper has a record and no record is invented; (2) `n_probands`, `n_families` and `n_tested` -- remembering that affected relatives in one family are **one** proband, however many were enrolled; (3) inheritance; (4) `phenotype_systems`; (5) quotes are real. Record agree/disagree per item as JSONL in `data/curated/gold/`. Precision and recall are computed from those files, and until they exist no output may state either.
