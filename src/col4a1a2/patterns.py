"""Regexes that classify text, defined once.

These are classifiers, versioned with the code that uses them and deliberately
not config: they are immune to TOML string-literal hazards here, and a change
to one is a change in behaviour that belongs in a commit. They used to be
copied between the fetch command and the dev triage tool, and the copies had
drifted -- the dev copy lacked eight clinical terms -- so the tool built to
measure the deployed keyword rule could not score it.

Two of them change the assembled paper text when edited (`HGVS_LIKE` and
`GENE_IN_CELL`, through supplement row filtering and the figure gate), and so
change the resume key: an edit re-extracts every affected paper at full price.
"""

from __future__ import annotations

import re

# A gene symbol as a word, for titles and abstracts.
GENE_RE = re.compile(r"\bCOL4A[12]\b", re.I)

# A gene symbol anywhere in a table cell, word boundary or not. Deliberately
# looser than GENE_RE and kept that way: supplement row filtering uses it, so
# tightening it would change which rows reach the prompt.
GENE_IN_CELL = re.compile(r"COL4A[12]", re.I)

# Anything that looks like HGVS. Used by the figure cost gate and by supplement
# row filtering.
HGVS_LIKE = re.compile(r"\bc\.\d+|\bp\.\(?[A-Z][a-z]{2}\d+|\bp\.\(?[A-Z]\d+|\b[A-Z][a-z]{2}\d+[A-Z][a-z]{2}\b")

# Clinical vocabulary for the keyword triage rule, the fallback when the model
# gate is unavailable.
CLINICAL_RE = re.compile(
    r"variant|mutation|pathogenic|de novo|heterozygous|c\.\d|p\.\(?[A-Z][a-z]{2}\d|Gould|HANAC|porencephal|"
    r"schizencephal|h(a)?emorrhag|small[- ]vessel|leukoencephalopath|PADMAL|cataract|anterior segment|"
    r"Axenfeld|tortuosity|hematuria|haematuria|nephropath|cramps|myopath|cerebral palsy|stroke|proband|famil",
    re.I)
