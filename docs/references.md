# References

Background reading. PDFs are not tracked in this repository, because at least one
is not redistributable; fetch them from the DOIs below.

## Methods context

- Cheng J, Novati G, Pan J, et al. **Accurate proteome-wide missense variant effect prediction with AlphaMissense.** Science, 2023. doi:[10.1126/science.adg7492](https://doi.org/10.1126/science.adg7492) — read for how a proteome-wide missense predictor is calibrated and reported. Not used as a feature source; see `docs/sources.md`.
- **AlphaGenome Atlas** preprint — read for the regulatory-variant scoring approach. Also not used; its terms forbid training other models on the outputs.

## Domain background

- Gould syndrome and the COL4A1/COL4A2 phenotype spectrum: cerebrovascular disease, HANAC, PADMAL, ocular anterior segment dysgenesis, and the wide intrafamilial variability that makes severity prediction unsupportable. Entry point: GeneReviews on COL4A1/COL4A2-related disorders.
- ClinGen Sequence Variant Interpretation working group recommendations on calibrating computational and segregation evidence, which is the framework any downstream use of the evidence table should follow.

## Standards used

- HGVS nomenclature, for variant naming in the extraction schema.
- Reference transcripts NM_001845.6 (COL4A1) and NM_001846.4 (COL4A2) on GRCh38.
