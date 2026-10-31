# Codebook: per-paper variant evidence extraction

This defines every field Claude fills in `PaperExtraction` (see `src/col4a1a2/extract/schema.py`) and how a human spot-checker should judge it. The system prompt (`src/col4a1a2/extract/prompts/v1.md`) is the operational version of these rules; keep the two in sync and bump `PROMPT_VERSION` when either changes.

## Unit of extraction

One record per paper. Inside it, one `VariantEvidence` per distinct COL4A1/COL4A2 variant reported in specific individuals **in this paper**. Variants cited from earlier publications without new individuals are not records.

## Counting

| Field | Rule |
|---|---|
| `n_probands` | Unrelated index cases with this variant in this paper. One per family. If probands and relatives are not distinguished, count families and say so in `notes`. |
| `n_families` | Kindreds with this variant in this paper. |
| `n_total_carriers` | All reported carriers including relatives, only if stated. |
| `same_family_as_prior_report` | `true` when the paper says the family or proband was reported before; the cited PMID goes in `prior_report_pmid` and the reasoning in `prior_report_reason`. This is the main double-counting risk when rolling up across papers. |

## Inheritance (proband counts per mode; the four sum to `n_probands`)

| Mode | Rule |
|---|---|
| `de_novo_confirmed` | Both parents tested negative; parentage not disputed. |
| `de_novo_assumed` | Called de novo without parental testing described. |
| `inherited` | A parent carries the variant. |
| `unknown` | Anything else. |

## Segregation (relatives only, probands excluded)

`affected_carriers`, `unaffected_carriers`, `non_carrier_affected`; each is null when the paper gives no number. Informative meioses, if stated, go in `notes`.

## Phenotype statuses

Three states, per organ system, for the carriers of this variant in this paper:

- `present`: reported in at least one carrier.
- `absent_on_exam`: the paper states the examination or imaging was done and normal.
- `not_assessed`: not mentioned, or not stated to have been examined. This is the default and will be the most common value.

| Category | Includes |
|---|---|
| `cerebrovascular` | porencephaly, intracerebral/intraventricular haemorrhage, schizencephaly, stroke, hemiparesis from perinatal injury, fetal intracranial haemorrhage |
| `small_vessel` | leukoencephalopathy, white-matter hyperintensities, microbleeds, lacunes, PADMAL, dilated perivascular spaces |
| `renal_hanac` | hematuria, renal cysts, nephropathy, reduced kidney function attributed to the variant, HANAC features |
| `ocular` | cataract, anterior segment dysgenesis, Axenfeld-Rieger, retinal arterial tortuosity, glaucoma, microphthalmia |
| `muscle` | cramps, elevated CK, myopathy |
| `other` | anything else, described in `details` |

## Variant naming

`as_written` is copied verbatim. `hgvs_c` / `hgvs_p` only when given or unambiguous; never converted between numbering systems by the model. Normalisation to NM_001845.6 / NM_001846.4 and GRCh38 happens later via Ensembl VEP, not in extraction.

`variant_type`: `missense_glycine` (substitution of a Gly), `missense_other`, `splice`, `nonsense`, `frameshift`, `inframe_indel`, `intronic`, `utr`, `deletion_cnv`, `other`.

## Evidence quotes and grounding

Two to five verbatim sentences or table rows per variant. `verify.py` string-matches each quote (after normalising whitespace, dashes and quote marks) against the source text. Any unmatched quote or any `confidence=low` variant puts the record on the spot-check queue.

## Confidence

`low` when counts come from an abstract only, from an ambiguous table, or when overlap with earlier reports is suspected; `medium` for clear reports with some inference; `high` when counts and statuses are stated explicitly.

## Spot-check protocol (human)

For each queued record, open the paper and check, in order: (1) every variant in the paper has a record and no record is invented; (2) `n_probands` and `n_families`; (3) inheritance mode; (4) each phenotype status, especially `absent_on_exam` versus `not_assessed`; (5) quotes are real. Record agree/disagree per item in `data/curated/gold/<pmid>.json` with the corrected values. Precision and recall are computed from these files.
