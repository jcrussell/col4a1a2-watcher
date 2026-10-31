# Test fixtures

Two open-access articles, stored as the JATS XML that Europe PMC serves, so the
JATS-to-text tests run offline against real-world markup rather than a synthetic
approximation. Both are **CC BY 4.0**, which permits redistribution with
attribution; their licence statements are inside the XML.

| File | Article | Licence |
|---|---|---|
| `PMC13148870.xml` | Stratification of Phenotypes in Childhood-Onset COL4A1/COL4A2-Related Disorders. PMID 42099677, PMC13148870. Published by Wolters Kluwer Health on behalf of the American Academy of Neurology. | CC BY 4.0 |
| `PMC13693400.xml` | Prenatal Diagnosis of Recurrent Fetal Intracranial Hemorrhage and a Maternally Inherited COL4A1 Variant. PMID 42921483, PMC13693400. Clinical Case Reports, John Wiley & Sons Ltd. | CC BY 4.0 |

## Generated fixtures

`supplement_variants.{xlsx,docx,csv,pdf}` are **ours**, written by
`col4a1a2-dev fixtures`, so the supplement parsers can be tested offline
without redistributing any publisher file. Regenerate them with:

```
uv run col4a1a2-dev fixtures
```

Supplementary files downloaded from publishers must never become fixtures: they
carry the article's licence, vary per paper, and can hold third-party or
identifiable material.

Do not add a fixture whose licence does not permit redistribution. Working full
texts live in `data/raw/`, which is untracked for exactly this reason. If a test
needs text from a paper under a more restrictive licence, excerpt only what the
test asserts on, or synthesise equivalent markup.
