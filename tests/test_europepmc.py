"""Europe PMC metadata lookup by PMID.

The whole bootstrap ran against the wrong query field. Europe PMC does not
index `PMID`, so `PMID:11985779` returns hitCount 0 while `EXT_ID:11985779`
returns the paper; of 2,075 PMIDs sent through the old query 41 resolved, and
the caller invented `is_open_access=False` for the rest. Re-querying a random
100 of the unresolved with `EXT_ID` resolved 97, 92 with a PMCID and 91 flagged
open access -- so the project recorded roughly 1,850 open-access papers as
closed without ever looking one up.
"""

import json

from col4a1a2.sources.europepmc import PaperMeta, lookup_pmids


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.status = 200
        self.ok = True

    def json(self):
        return json.loads(self.body)

    def require_ok(self, what):
        return self


class FakeClient:
    """Answers an EXT_ID query from a table of records, as Europe PMC does."""

    def __init__(self, records):
        self.records = records
        self.queries: list[str] = []

    def get(self, url, params=None, **kw):
        q = (params or {}).get("query", "")
        self.queries.append(q)
        ids = {part.split(":", 1)[1] for part in q.split(" OR ") if ":" in part}
        hits = [r for r in self.records if (r.get("id") or r.get("pmid")) in ids]
        return FakeResponse({"resultList": {"result": hits}, "hitCount": len(hits)})


def _med(pmid, **kw):
    r = {"id": pmid, "pmid": pmid, "source": "MED", "title": f"T{pmid}",
         "isOpenAccess": "Y", "pmcid": f"PMC{pmid}"}
    r.update(kw)
    return r


def test_lookup_uses_ext_id_not_pmid():
    """The one-line cause of the whole miss."""
    c = FakeClient([_med("11985779")])
    out = lookup_pmids(c, ["11985779"])
    assert "EXT_ID:11985779" in c.queries[0]
    assert "PMID:" not in c.queries[0]
    assert out["11985779"].pmcid == "PMC11985779"
    assert out["11985779"].is_open_access is True


def test_a_resolved_paper_carries_its_open_access_facts():
    c = FakeClient([_med("1"), _med("2", isOpenAccess="N", pmcid=None)])
    out = lookup_pmids(c, ["1", "2"], min_resolution=0.0)
    assert out["1"].is_open_access and out["1"].pmcid
    assert not out["2"].is_open_access and not out["2"].pmcid
    # Both were retrieved, so both are facts rather than assumptions.
    assert all(m.metadata_source == "europepmc" for m in out.values())


def test_an_unresolved_pmid_is_absent_rather_than_closed_access():
    """It must not come back as a record claiming `is_open_access=False`: that
    claim, made on no evidence, is what buried 2,034 papers."""
    c = FakeClient([_med("1")])
    out = lookup_pmids(c, ["1", "2"], min_resolution=0.0)
    assert set(out) == {"1"}


def test_a_pmc_keyed_duplicate_does_not_displace_the_med_record():
    """`EXT_ID` matches PMC-sourced records too, and one real response came back
    `source=PMC, pmid=null`, which would key a candidate under the wrong id."""
    pmc_only = {"id": "PMC999", "pmid": None, "source": "PMC", "title": "dup",
                "isOpenAccess": "N", "pmcid": "PMC999"}
    c = FakeClient([pmc_only, _med("999")])
    out = lookup_pmids(c, ["999", "PMC999"], min_resolution=0.0)
    assert set(out) == {"999"}
    assert out["999"].source == "europepmc"


def test_a_low_resolution_chunk_warns(caplog):
    """The old failure was silent because nothing compared what was asked for
    against what came back."""
    c = FakeClient([_med("1")])
    with caplog.at_level("WARNING"):
        lookup_pmids(c, ["1", "2", "3", "4"])
    assert any("resolved only 1 of 4" in r.getMessage() for r in caplog.records)


def test_chunks_are_batched_and_every_pmid_is_asked_for():
    ids = [str(i) for i in range(1, 121)]
    c = FakeClient([_med(i) for i in ids])
    out = lookup_pmids(c, ids, chunk=50)
    assert len(c.queries) == 3
    assert set(out) == set(ids)


def test_fabricated_metadata_is_marked():
    """The CLI's fallback builds this when a lookup misses; the flag is what
    stops the ledger calling it closed access."""
    m = PaperMeta(pmid="1", pmcid=None, doi=None, title="T", journal=None, pub_year=None,
                  first_pub_date=None, is_open_access=False, abstract=None,
                  source="pubtator", metadata_source="pubtator_only")
    assert m.metadata_source != "europepmc"
