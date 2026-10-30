# Data sources, rates and licences

Every host below is reached through `src/col4a1/http.py`. Rates are configured in `config/queries.yaml`; anything not listed defaults to one request per second.

## In use

| Source | What we take | Rate | Terms |
|---|---|---|---|
| Europe PMC REST | Search results, abstracts, author lists and affiliations, open-access full text as JATS XML, reference lists | 2/s | Metadata is open. Article licences vary per paper and are stated in the XML. |
| PubTator 3 (NCBI) | Entity-aware search, BioC annotations, variant mentions linked to a gene | 2/s | Public NCBI resource. |
| NCBI E-utilities | Full-text fallback, ClinVar record counts | 2/s | Public. Requests carry a contact address, as NCBI asks. |
| Anthropic API | Triage on title and abstract, extraction on full text | n/a | Outputs belong to the account holder. |

## Planned

| Source | What for | Rate |
|---|---|---|
| ClinVar bulk files (NCBI FTP) | Monthly diff of variant classifications | 1/s, bulk file not per-variant queries |
| Ensembl VEP REST | Normalising HGVS to reference transcripts and GRCh38 | 5/s |
| gnomAD GraphQL | Allele frequency, which sets the prior for whether two reports of a variant are the same family | 0.15/s |

Reference transcripts are NM_001845.6 for COL4A1 and NM_001846.4 for COL4A2.

## Deliberately not used

- **AlphaGenome and the AlphaGenome Atlas.** The terms forbid using outputs to train other machine-learning models and bar clinical decision-making. The regulatory site most relevant to one disease mechanism in these genes is not modelled. Nothing in scope needs it.
- **AlphaMissense.** Licensed CC BY-NC-SA 4.0, which would constrain redistribution of anything derived from it, and the current scope makes no use of a missense pathogenicity score.
- Hosts unreachable from the development sandbox and not needed: Zenodo, HuggingFace, DECIPHER, MGI, UCSC downloads, AlphaFold, RCSB, GTEx.

## Licensing of our own output

- **Full texts are stored locally and never redistributed.** This is why `data/raw/` is untracked and why test fixtures are limited to papers under a licence that permits redistribution, with attribution recorded in `tests/fixtures/README.md`.
- **Extracted records** carry a PMID, a prepared citation and short verbatim quotes. Attribution per row is both a licence obligation and the thing that makes the table checkable.
- **The evidence table** will be released CC BY 4.0. Annotations obtained from other services are not redistributed with it; only identifiers and coordinates.
- **Code** is Apache-2.0.
- Research use only. Machine-extracted, spot-checked at a stated rate, not clinical advice.
