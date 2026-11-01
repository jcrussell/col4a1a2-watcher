"""Regexes that classify text, defined once.

These are classifiers, versioned with the code that uses them and deliberately
not config: they are immune to TOML string-literal hazards here, and a change
to one is a change in behaviour that belongs in a commit. The fetch command
and the dev triage tool share these definitions, so the tool that measures the
deployed keyword rule scores exactly that rule.

Two of them change the assembled paper text when edited (`HGVS_LIKE` and
`GENE_IN_CELL`, through supplement row filtering and the figure gate), and so
change the resume key: an edit re-extracts every affected paper at full price.
"""

from __future__ import annotations

import re

# A gene symbol as a word, for titles and abstracts.
GENE_RE = re.compile(r"\bCOL4A[12]\b", re.IGNORECASE)

# A gene symbol anywhere in a table cell, word boundary or not. Deliberately
# looser than GENE_RE and kept that way: supplement row filtering uses it, so
# tightening it would change which rows reach the prompt.
GENE_IN_CELL = re.compile(r"COL4A[12]", re.IGNORECASE)

# Anything that looks like HGVS. Used by the figure cost gate and by supplement
# row filtering.
HGVS_LIKE = re.compile(r"\bc\.\d+|\bp\.\(?[A-Z][a-z]{2}\d+|\bp\.\(?[A-Z]\d+|\b[A-Z][a-z]{2}\d+[A-Z][a-z]{2}\b")

# Clinical vocabulary for the keyword triage rule, the fallback when the model
# gate is unavailable.
CLINICAL_RE = re.compile(
    r"variant|mutation|pathogenic|de novo|heterozygous|c\.\d|p\.\(?[A-Z][a-z]{2}\d|Gould|HANAC|porencephal|"
    r"schizencephal|h(a)?emorrhag|small[- ]vessel|leukoencephalopath|PADMAL|cataract|anterior segment|"
    r"Axenfeld|tortuosity|hematuria|haematuria|nephropath|cramps|myopath|cerebral palsy|stroke|proband|famil",
    re.IGNORECASE)

# A variant written as bare genomic coordinates on chromosome 13 with no
# build, e.g. "13: 110804862: C: T" or "chr13:110804862 C>T". `normalize`
# infers the build from the reference base.
GENOMIC_AS_WRITTEN = re.compile(r"^(?:chr)?(13)[\s:]+(\d[\d,]*)[\s:]+([ACGT])[\s:>/]+([ACGT])$", re.IGNORECASE)

# A RefSeq or Ensembl accession with an optional version, as papers state them.
ACCESSION = re.compile(r"(N[MPRC]_\d+|ENS[TP]\d+)(?:\.(\d+))?")

# VEP's 400 for protein input. A correct reference residue still cannot be
# placed on the genome when several codons fit, which VEP reports as not
# unique; a wrong residue gets a translation mismatch. Measured on release 116.
VEP_PROTEIN_NOT_UNIQUE = re.compile(r"Could not uniquely determine nucleotide change")
VEP_REFERENCE_MISMATCH = re.compile(r"does not match (?:reference allele|input sequence)")
