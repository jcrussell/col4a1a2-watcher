"""Upstream failures must be loud: a failed page never reads as an empty one."""

import logging

import pytest

from col4a1a2.http import HttpError, Response, TooManyFailures
from col4a1a2.sources import europepmc, pubtator


class Client:
    def __init__(self, status, body=b"{}"):
        self.status, self.body = status, body

    def get(self, url, params=None, ttl=None):
        return Response(url=url, status=self.status, headers={}, body=self.body,
                        from_cache=False, fetched_at=0.0)


def test_a_failed_pubtator_chunk_is_logged_not_swallowed(caplog):
    with caplog.at_level(logging.WARNING, logger="col4a1a2"):
        docs = pubtator.export_biocjson(Client(503), ["1", "2", "3"])
    assert docs == {}
    assert "PubTator export failed for 3 PMIDs" in caplog.text


@pytest.mark.parametrize("call", [
    lambda c: europepmc.hit_count(c, "q"),
    lambda c: europepmc.references(c, "1"),
    lambda c: europepmc.search(c, "q"),
    lambda c: pubtator.search(c, "q"),
])
def test_a_failed_response_raises(call):
    """Otherwise `hit_count` parses whatever came back and `references` returns
    a partial bibliography, which reads as "does not cite"."""
    with pytest.raises(HttpError, match="HTTP 500"):
        call(Client(500))



class Hosts:
    """Europe PMC keeps failing; NCBI answers with `ncbi`, or fails too if it is None."""

    def __init__(self, ncbi):
        self.ncbi = ncbi

    def get(self, url, params=None, ttl=None):
        if "ebi.ac.uk" in url or self.ncbi is None:
            raise TooManyFailures(f"GET {url}: HTTP 500")
        return Response(url=url, status=200, headers={}, body=self.ncbi, from_cache=False, fetched_at=0.0)


def test_a_failing_full_text_source_falls_back_and_is_named():
    from col4a1a2.cli import _full_text

    assert _full_text(Hosts(b"<article/>"), "PMC1", "x@example.org") == (b"<article/>", "ncbi", ["europepmc"])
    assert _full_text(Hosts(b"no such article"), "PMC1", "x@example.org") == (None, None, ["europepmc"]), \
        "a failure is not an absence, even when the other source has nothing"
    assert _full_text(Hosts(None), "PMC1", "x@example.org") == (None, None, ["europepmc", "ncbi"])
