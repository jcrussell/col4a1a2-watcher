"""The per-variant table: counts are ranges, reviews are pointers, screens are apart."""

from col4a1a2.publish.variants import SCREEN_MIN, roll_up


def _e(pmid, n=1, fam=1, **kw):
    e = {"pmid": pmid, "gene": "COL4A1", "year": "2020", "paper_type": "case_report",
         "variant_key": "NM_001845.6:c.1420G>A", "protein_key": "NP_001836.3:p.Gly474Arg", "key_level": "coding",
         "grch38": "13:110192875:G/A", "variant_type": "missense_glycine", "n_probands": n, "n_families": fam,
         "n_tested": 0, "inh_de_novo_confirmed": 0, "inh_de_novo_assumed": 0, "inh_inherited": 0,
         "same_family_as_prior_report": False, "functional_evidence": "", "phenotype_systems": "cerebrovascular",
         "normalization_flags": ""}
    e.update(kw)
    return e


def test_counts_are_a_range_from_full_overlap_to_independence():
    [v] = roll_up([_e("1", 3, 2), _e("2", 1, 1), _e("3", 2, 1)])
    assert (v["probands_min"], v["probands_max"]) == (3, 6)
    assert (v["families_min"], v["families_max"]) == (2, 4)
    assert v["n_papers"] == 3 and v["pmids"] == ["1", "2", "3"]


def test_a_family_the_paper_says_was_reported_before_does_not_raise_the_maximum():
    [v] = roll_up([_e("1", 2), _e("2", 1, same_family_as_prior_report=True)])
    assert (v["probands_min"], v["probands_max"]) == (2, 2)
    assert v["records_prior_report"] == 1


def test_the_maximum_is_never_below_the_minimum():
    [v] = roll_up([_e("1", 1), _e("2", 5, same_family_as_prior_report=True)])
    assert (v["probands_min"], v["probands_max"]) == (5, 5)


def test_two_records_in_one_paper_add_up_within_it():
    [v] = roll_up([_e("1", 2), _e("1", 3), _e("2", 4)])
    assert (v["probands_min"], v["probands_max"]) == (5, 9)


def test_reviews_are_pointers_and_count_nothing():
    [v] = roll_up([_e("1", 2), _e("9", 7, paper_type="review", inh_de_novo_confirmed=1)])
    assert (v["probands_min"], v["probands_max"]) == (2, 2)
    assert v["pointer_pmids"] == ["9"] and v["n_papers"] == 2 and v["n_primary_papers"] == 1
    assert v["papers_de_novo_confirmed"] == 0


def test_population_screens_are_counted_apart_from_patients():
    [v] = roll_up([_e("1", 1), _e("2", 174, n_tested=SCREEN_MIN * 400)])
    assert (v["probands_min"], v["probands_max"]) == (1, 1)
    assert v["screened_carriers"] == 174


def test_inheritance_counts_papers_not_probands():
    [v] = roll_up([_e("1", 3, inh_de_novo_confirmed=3), _e("2", 1, inh_de_novo_assumed=1),
                   _e("3", 2, inh_inherited=2)])
    assert (v["papers_de_novo_confirmed"], v["papers_de_novo_assumed"], v["papers_inherited"]) == (1, 1, 1)


def test_protein_groups_list_every_coding_change_and_join_protein_only_records():
    rows = roll_up([_e("1"), _e("2", variant_key="NM_001845.6:c.1420G>C", grch38="13:110192875:G/C"),
                    _e("3", variant_key="NP_001836.3:p.Gly474Arg", key_level="protein", grch38="")])
    [v] = rows
    assert v["grouped_by"] == "protein" and v["variant_id"] == "NP_001836.3:p.Gly474Arg"
    assert v["coding_keys"] == ["NM_001845.6:c.1420G>A", "NM_001845.6:c.1420G>C"]
    assert v["n_papers"] == 3


def test_a_change_with_no_protein_consequence_groups_on_its_coding_key():
    [v] = roll_up([_e("1", variant_key="NM_001845.6:c.*34G>T", protein_key="", variant_type="utr")])
    assert v["grouped_by"] == "coding" and v["variant_id"] == "NM_001845.6:c.*34G>T"


def test_records_without_a_key_stay_out_and_genes_stay_apart():
    rows = roll_up([_e("1", variant_key="", protein_key=""), _e("2"), _e("3", gene="COL4A2")])
    assert [(r["gene"], r["n_papers"]) for r in rows] == [("COL4A1", 1), ("COL4A2", 1)]
