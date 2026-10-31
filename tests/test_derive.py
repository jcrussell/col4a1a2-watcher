"""Deterministic checks and the variant_type cross-check.

The predicate order is the whole correctness story: an earlier draft tested the
glycine pattern first and so flagged seven records the model had classified
correctly, because p.(Gly423Trpfs*19) is a frameshift and p.(Gly2=) is
synonymous. Those seven are pinned below.
"""

import json
from pathlib import Path

import pytest

from col4a1a2.extract.derive import check_extraction, check_record, derive_variant_type


@pytest.mark.parametrize("hgvs_p,hgvs_c,expected", [
    # The seven that a glycine-first rule order got wrong.
    ("p.(Gly423Trpfs*19)", "c.1265dupC", "frameshift"),
    ("p.(Gly1426Argfs*30)", "c.4275dup", "frameshift"),
    ("p.(Gly835Aspfs*19)", "c.2502del", "frameshift"),
    ("p.(Gly2=)", "c.6G>T", "other"),
    ("p.Gly1176del", "", "inframe_indel"),
    ("p.(Thr1313ArgfsTer38)", "c.3938_3939delCA", "frameshift"),
    ("p.(Pro1224=)", "c.3672G>A", "other"),
    # Straightforward cases.
    ("p.Gly513Ser", "c.1537G>A", "missense_glycine"),
    ("p.Thr1498Met", "c.4493C>T", "missense_other"),
    ("p.Arg1141Ter", "c.3421C>T", "nonsense"),
    ("p.Gln1283*", "", "nonsense"),
    # Decidable from the coding change alone.
    ("", "c.1234+5G>A", "splice"),
    ("", "c.-12G>A", "utr"),
    ("", "c.*44G>A", "utr"),
])
def test_derivation(hgvs_p, hgvs_c, expected):
    assert derive_variant_type(hgvs_p, hgvs_c) == expected


@pytest.mark.parametrize("hgvs_p,hgvs_c", [
    ("", ""),
    ("", "c.164G>A"),          # missense, nonsense or synonymous: undecidable
    ("", "c.2502del"),         # in-frame or frameshift: undecidable
    ("p.?", ""),
])
def test_no_opinion_is_none_not_other(hgvs_p, hgvs_c):
    """None means 'cannot decide'. Returning 'other' would manufacture a
    disagreement with the model on 59 of 177 real records."""
    assert derive_variant_type(hgvs_p, hgvs_c) is None


def _rec(**kw):
    base = dict(as_written="c.1A>T", hgvs_c="c.1A>T", hgvs_p="", variant_type="other",
                n_probands=1, n_families=1, n_total_carriers=None, n_tested=0,
                inh_de_novo_confirmed=0, inh_de_novo_assumed=0, inh_inherited=1, inh_unknown=0,
                phenotype_systems="", phenotype_details="",
                same_family_as_prior_report=False, prior_report_reason="")
    base.update(kw)
    return base


def test_each_check_fires_on_a_synthetic_failure():
    fired = lambda r: {f.check for f in check_record("1", r)}
    assert "inheritance_sum" in fired(_rec(n_probands=3))
    assert "families_gt_probands" in fired(_rec(n_probands=1, n_families=2))
    assert "carriers_lt_probands" in fired(_rec(n_total_carriers=0))
    assert "present_without_details" in fired(_rec(phenotype_systems="ocular"))
    assert "unknown_phenotype_system" in fired(
        _rec(phenotype_systems="kidney", phenotype_details="nephropathy"))
    # The two checks the denominator makes possible.
    assert "probands_gt_tested" in fired(_rec(n_probands=5, n_tested=3, inh_inherited=5))
    assert "many_families_from_a_cohort" in fired(
        _rec(n_probands=40, n_families=40, n_tested=500000, inh_inherited=40))
    assert "prior_report_without_reason" in fired(_rec(same_family_as_prior_report=True))
    assert "variant_type_disagreement" in fired(_rec(hgvs_p="p.Gly513Ser", variant_type="nonsense"))
    assert "cnv_gain_typed_as_deletion" in fired(_rec(
        hgvs_c="", variant_type="deletion_cnv",
        as_written="41.7 Mb duplication of 13q (partial trisomy) including COL4A1"))
    # A deletion described as a deletion is fine, and so is a small c.dup,
    # which is a frameshift and never typed deletion_cnv in the first place.
    assert "cnv_gain_typed_as_deletion" not in fired(_rec(
        hgvs_c="", variant_type="deletion_cnv", as_written="1.9 Mb deletion of 13q34"))


def test_clean_record_produces_nothing():
    assert check_record("1", _rec()) == []


def test_families_equal_probands_fires_only_at_scale():
    """The corpus's worst record: 174 individuals from a population cohort
    recorded as 174 distinct families. Equality is normal for small numbers,
    so flag by magnitude."""
    small = {f.check for f in check_record("1", _rec(n_probands=2, n_families=2,
                                                     inheritance={"unknown": 2, "inherited": 0,
                                                                  "de_novo_confirmed": 0, "de_novo_assumed": 0}))}
    assert "families_equal_probands_at_scale" not in small
    big = {f.check for f in check_record("1", _rec(n_probands=174, n_families=174, n_total_carriers=174,
                                                   inheritance={"unknown": 174, "inherited": 0,
                                                                "de_novo_confirmed": 0, "de_novo_assumed": 0}))}
    assert "families_equal_probands_at_scale" in big


def test_checks_are_quiet_on_the_real_corpus():
    """Measured: exactly two findings over 177 records, and both are real.

    Guards against a future check that floods the review queue. If this fails,
    the new check needs its false-positive rate measured before it ships.
    """
    snap = Path(__file__).resolve().parents[1] / "data/raw/2026-10-30-batch100/extractions.jsonl"
    if not snap.exists():
        pytest.skip("batch snapshot not present")
    rows = {}
    for line in snap.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if not r.get("error"):
            rows[r["pmid"]] = r
    # The snapshot was written by prompt v2, which nested inheritance,
    # segregation and phenotypes. The checks read the v3 flat fields, so the
    # rows are adapted here rather than teaching production code two shapes.
    # The point of the test survives: the checks must stay quiet on 177 real
    # records, and the one real error must still fire.
    def to_v3(v: dict) -> dict:
        inh = v.get("inheritance") or {}
        ph = v.get("phenotypes") or {}
        present = [k for k, val in ph.items() if val == "present" and k != "details"]
        return {**v,
                "inh_de_novo_confirmed": inh.get("de_novo_confirmed", 0),
                "inh_de_novo_assumed": inh.get("de_novo_assumed", 0),
                "inh_inherited": inh.get("inherited", 0),
                "inh_unknown": inh.get("unknown", 0),
                "phenotype_systems": ",".join(present),
                "phenotype_details": ph.get("details", ""),
                "n_tested": v.get("n_tested", 0)}

    for r in rows.values():
        r["extraction"]["variants"] = [to_v3(v) for v in r["extraction"]["variants"]]
    findings = [f for r in rows.values() for f in check_extraction(r["extraction"])]
    n_records = sum(len(r["extraction"]["variants"]) for r in rows.values())
    assert n_records == 177
    assert len(findings) <= 5, f"{len(findings)} findings over {n_records} records: {findings}"
    assert any(f.check == "families_equal_probands_at_scale" for f in findings)
