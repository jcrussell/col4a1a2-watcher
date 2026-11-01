"""HGVS normalization against canned Ensembl answers, offline.

The canned answers reproduce what VEP said on release 116 for the shapes that
matter: a substitution, a rejected reference base, protein-only input with the
right and the wrong residue, an insertion VEP rewrites as a duplication, and
bare coordinates whose build only the reference base reveals.
"""

import json

import pytest

from col4a1a2 import jsonl, normalize
from col4a1a2.http import TooManyFailures


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.ok = status < 400
        self.body = json.dumps(payload).encode()

    def json(self):
        return json.loads(self.body)


def _vep(chrom, start, end, alleles, hgvsc, hgvsp, terms, tid="NM_001845.6"):
    return 200, [{"seq_region_name": chrom, "start": start, "end": end, "allele_string": alleles,
                  "colocated_variants": [{"id": "rs1", "clin_sig": ["pathogenic"]}],
                  "transcript_consequences": [{"transcript_id": tid, "hgvsc": hgvsc, "hgvsp": hgvsp,
                                               "consequence_terms": terms, "sift_score": 0.0}]}]


ANSWERS = {
    "vep/human/hgvs/NM_001845.6%3Ac.1420G%3EA": _vep("13", 110192875, 110192875, "G/A", "NM_001845.6:c.1420G>A",
                                                     "NP_001836.3:p.Gly474Arg", ["missense_variant"]),
    "vep/human/hgvs/NM_001845.6%3Ac.1420C%3EA": (400, {"error": "Unable to parse HGVS notation "
        "'NM_001845.6:c.1420C>A':  : Reference allele extracted from NM_001845.6:110192875-110192875 (G) "
        "does not match reference allele given by HGVS notation NM_001845.6:c.1420C>A (C)"}),
    "vep/human/hgvs/NM_001845.6%3Ac.4611_4612insG": _vep("13", 110161221, 110161220, "-/G", "NM_001845.6:c.4615dup",
                                                         "NP_001836.3:p.Glu1539GlyfsTer8", ["frameshift_variant"]),
    "vep/human/hgvs/NP_001836.3%3Ap.Gly474Arg": (400, {"error": "Unable to parse HGVS notation "
        "'NP_001836.3:p.Gly474Arg':  : Could not uniquely determine nucleotide change from NP_001836.3:p.Gly474Arg"}),
    "vep/human/hgvs/NP_001836.3%3Ap.Ala474Arg": (400, {"error": "Unable to parse HGVS notation "
        "'NP_001836.3:p.Ala474Arg':  : Sequence translated from reference (GGA -> G) does not match input sequence (A)"}),
    "sequence/region/human/13:110804862..110804862:1?GRCh38": (200, {"seq": "T"}),
    "sequence/region/human/13:110804862..110804862:1?GRCh37": (200, {"seq": "C"}),
    "map/human/GRCh37/13:110804862..110804862:1/GRCh38": (200, {"mappings": [
        {"mapped": {"start": 110152515, "end": 110152515}, "original": {"start": 110804862}}]}),
    "vep/human/region/13:110152515-110152515:1/T": _vep("13", 110152515, 110152515, "C/T",
                                                        "NM_001845.6:c.4756-29G>A", "", ["intron_variant"]),
    "info/software": (200, {"release": 116}),
}


class FakeClient:
    def __init__(self, answers=ANSWERS, down=False):
        self.answers = answers
        self.down = down
        self.urls: list[str] = []
        self.ttls: list = []
        self.network_requests = 0
        self.cache_hits = 0

    def get(self, url, params=None, headers=None, ttl=None, **kw):
        if self.down:
            raise TooManyFailures(f"GET {url}: HTTP 503")
        path = url.removeprefix("https://rest.ensembl.org/")
        if "coord_system_version" in (params or {}):
            path += "?" + params["coord_system_version"]
        self.urls.append(path)
        self.ttls.append(ttl)
        self.network_requests += 1
        assert "AlphaMissense" not in (params or {}) and "REVEL" not in (params or {})
        status, payload = self.answers[path]
        return FakeResponse(status, payload)


def _rec(**kw):
    v = {"gene": "COL4A1", "as_written": "", "hgvs_c": "", "hgvs_p": "", "transcript_if_stated": "",
         "variant_type": "missense_glycine"}
    v.update(kw)
    return v


def _answers(*records, client=None):
    """What `normalize` would store for these records: asked lazily, as the command asks."""
    client = client or FakeClient()
    answers: dict = {}
    normalize.normalize_records(list(records), answers, lambda q: normalize.ask(client, q, 116))
    return answers


@pytest.mark.parametrize("raw,want", [
    ("p.(Gly474Arg)", "p.Gly474Arg"), ("p.G1229D", "p.Gly1229Asp"), ("p.(G1118D)", "p.Gly1118Asp"),
    ("NP_001836.3:p.Gly474Arg", "p.Gly474Arg"), ("p.T1537fs", "p.Thr1537fs"), ("p.R538*", "p.Arg538Ter"),
    ("", None), ("Gly474Arg", None),
])
def test_protein_changes_are_spelled_one_way(raw, want):
    assert normalize.clean_p(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("c.1420G>A", "c.1420G>A"), ("NM_001845.5:c.1420G>A", "c.1420G>A"), ("c.(1420G>A)", "c.1420G>A"),
    ("c. 1420 G>A", "c.1420G>A"), ("1420G>A", None), ("", None),
])
def test_coding_changes_lose_their_transcript(raw, want):
    assert normalize.clean_c(raw) == want


def test_queries_go_to_the_reference_transcript_never_the_papers():
    """VEP serves only current versions: `.5` is a 400 and unversioned a 500."""
    for stated in ("NM_001845.5", "NM_001845", "NM_0018456.6", ""):
        q = normalize.query_for(_rec(hgvs_c="c.1420G>A", transcript_if_stated=stated))
        assert q["notation"] == "NM_001845.6:c.1420G>A" and q["transcript_id"] == "NM_001845.6"
    q = normalize.query_for(_rec(gene="COL4A2", hgvs_c="c.2267G>A"))
    assert q["notation"] == "NM_001846.4:c.2267G>A"


def test_a_substitution_keys_on_vep_and_stores_nothing_third_party():
    v = _rec(hgvs_c="c.1420G>A", hgvs_p="p.(Gly474Arg)", transcript_if_stated="NM_001845.6")
    answers = _answers(v)
    row = next(iter(answers.values()))
    assert "colocated" not in json.dumps(row) and "sift" not in json.dumps(row)
    assert row["ensembl_release"] == 116
    out = normalize.link(v, answers)
    assert out == {"variant_key": "NM_001845.6:c.1420G>A", "key_level": "coding",
                   "protein_key": "NP_001836.3:p.Gly474Arg", "grch38": "13:110192875:G/A",
                   "normalization_flags": ""}


def test_two_spellings_of_one_insertion_share_a_key():
    a = _rec(hgvs_c="c.4611_4612insG", hgvs_p="p.T1537fs")
    out = normalize.link(a, _answers(a))
    assert out["variant_key"] == "NM_001845.6:c.4615dup"
    assert out["grch38"] == "13:110161221-110161220:-/G"
    assert out["normalization_flags"] == ""  # a frameshift's protein is not compared


def test_a_wrong_reference_base_is_flagged_not_keyed():
    v = _rec(hgvs_c="c.1420C>A")
    out = normalize.link(v, _answers(v))
    assert out["variant_key"] == "" and out["key_level"] == "none"
    assert out["normalization_flags"] == "reference-mismatch"


def test_a_protein_the_coding_change_does_not_translate_to_is_flagged():
    v = _rec(hgvs_c="c.1420G>A", hgvs_p="p.Gly475Arg")
    assert normalize.link(v, _answers(v))["normalization_flags"] == "c-p-mismatch"


def test_protein_only_input_is_keyed_at_protein_level_once_its_residue_checks():
    good = _rec(hgvs_p="p.G474R")
    out = normalize.link(good, _answers(good))
    assert out["key_level"] == "protein" and out["variant_key"] == "NP_001836.3:p.Gly474Arg"
    bad = _rec(hgvs_p="p.Ala474Arg")
    out = normalize.link(bad, _answers(bad))
    assert out["key_level"] == "none" and out["normalization_flags"] == "reference-mismatch"


def test_bare_coordinates_take_the_build_their_reference_base_matches():
    v = _rec(as_written="13: 110804862: C: T", variant_type="intronic")
    client = FakeClient()
    answers = _answers(v, client=client)
    row = next(iter(answers.values()))
    assert row["build_inferred"] == "GRCh37"
    assert "map/human/GRCh37/13:110804862..110804862:1/GRCh38" in client.urls
    out = normalize.link(v, answers)
    assert out["grch38"] == "13:110152515:C/T" and out["variant_key"] == "NM_001845.6:c.4756-29G>A"
    assert out["normalization_flags"] == "build-inferred-grch37"


def test_coordinates_win_over_a_protein_change_they_can_pin_down():
    """A protein change cannot say which nucleotide changed; the coordinates can."""
    q = normalize.query_for(_rec(as_written="13: 110804862: C: T", hgvs_p="p.Gly474Arg"))
    assert q["kind"] == "genomic"
    assert normalize.query_for(_rec(as_written="13: 110804862: C: T", hgvs_c="c.1420G>A"))["kind"] == "hgvs_c"


def test_a_base_both_builds_share_gives_no_key():
    answers = {**ANSWERS, "sequence/region/human/13:110866346..110866346:1?GRCh38": (200, {"seq": "G"}),
               "sequence/region/human/13:110866346..110866346:1?GRCh37": (200, {"seq": "G"})}
    v = _rec(as_written="13:110866346 G/A")
    out = normalize.link(v, _answers(v, client=FakeClient(answers)))
    assert out["variant_key"] == "" and out["normalization_flags"] == "build-ambiguous"


def test_ambiguous_coordinates_fall_back_to_the_protein_change():
    answers = {**ANSWERS, "sequence/region/human/13:110866346..110866346:1?GRCh38": (200, {"seq": "G"}),
               "sequence/region/human/13:110866346..110866346:1?GRCh37": (200, {"seq": "G"})}
    v = _rec(as_written="13:110866346 G/A", hgvs_p="p.Gly474Arg")
    out = normalize.link(v, _answers(v, client=FakeClient(answers)))
    assert out["key_level"] == "protein" and out["variant_key"] == "NP_001836.3:p.Gly474Arg"
    assert out["normalization_flags"] == "build-ambiguous"


def test_coordinates_that_translate_to_another_protein_are_flagged():
    answers = {**ANSWERS, "vep/human/region/13:110152515-110152515:1/T": _vep(
        "13", 110152515, 110152515, "C/T", "NM_001845.6:c.4756G>A", "NP_001836.3:p.Gly1586Arg", ["missense_variant"])}
    v = _rec(as_written="13: 110804862: C: T", hgvs_p="p.Gly474Arg")
    out = normalize.link(v, _answers(v, client=FakeClient(answers)))
    assert out["key_level"] == "coding" and "c-p-mismatch" in out["normalization_flags"]


def test_a_transcript_with_other_numbering_is_flagged_and_a_known_equivalent_is_not():
    answers = _answers(_rec(hgvs_c="c.1420G>A"))
    for stated, flagged in (("NM_001303110.2", True), ("NM_00185", True), ("ENST00000375820", False),
                            ("NM_001845.4", False), ("", False)):
        flags = normalize.link(_rec(hgvs_c="c.1420G>A", transcript_if_stated=stated), answers)["normalization_flags"]
        assert ("transcript-other" in flags) is flagged, stated


def test_structural_variants_are_not_forced_into_hgvs():
    v = _rec(as_written="Exon 30-31 2.4 kb del", variant_type="deletion_cnv")
    assert normalize.query_for(v) is None
    assert normalize.link(v, {}) == {"variant_key": "", "key_level": "none", "protein_key": "", "grch38": "",
                                     "normalization_flags": ""}


def test_an_unasked_query_reads_not_normalized():
    assert normalize.link(_rec(hgvs_c="c.1420G>A"), {})["normalization_flags"] == "not-normalized"


def test_an_outage_stores_nothing_so_the_next_run_asks_again():
    q = normalize.query_for(_rec(hgvs_c="c.1420G>A"))
    assert normalize.ask(FakeClient(down=True), q, 116) is None


def test_a_refresh_bypasses_the_http_cache():
    """A cached answer must never be stamped with a newer release."""
    client = FakeClient()
    normalize.ask(client, normalize.query_for(_rec(hgvs_c="c.1420G>A")), 116, ttl=0)
    assert client.ttls == [0]


def test_rows_validate_and_the_newest_answer_wins(tmp_path):
    path = tmp_path / "normalized.jsonl"
    v = _rec(hgvs_c="c.1420G>A")
    q = normalize.query_for(v)
    old = normalize.ask(FakeClient(), q, 115)
    new = {**normalize.ask(FakeClient(), q, 116), "written_at": "9999-01-01T00:00:00+00:00"}
    for row in (new, old):  # file order must not matter
        jsonl.append(path, row)
    assert normalize.load_answers(path)[normalize.query_key(q)]["ensembl_release"] == 116


def test_a_fallback_is_asked_only_when_the_better_query_fails():
    asked = []

    def ask_one(q):
        asked.append(q["kind"])
        return normalize.ask(FakeClient(), q, 116)

    good = _rec(hgvs_c="c.1420G>A", hgvs_p="p.Gly474Arg")
    answers: dict = {}
    assert normalize.normalize_records([good], answers, ask_one) == (1, 1)
    assert asked == ["hgvs_c"], "a resolving coding change costs no protein query"
    bad = _rec(hgvs_c="c.1420C>A", hgvs_p="p.Gly474Arg")
    normalize.normalize_records([bad], answers, ask_one)
    assert asked == ["hgvs_c", "hgvs_c", "hgvs_p"]
    assert normalize.link(bad, answers)["key_level"] == "protein"
    assert normalize.normalize_records([good, bad], answers, ask_one) == (0, 0), "nothing asked twice"
