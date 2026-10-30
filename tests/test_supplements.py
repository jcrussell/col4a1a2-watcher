"""Bundle safety and member classification. No network: a fake client serves bytes."""

import io
import time
import zipfile

import pytest

from col4a1.http import DownloadTimeout, Response, ResponseTooLarge
from col4a1.sources import supplements as sup
from col4a1.sources.fulltext import FigureRef, SupplementRef, jats_inventory


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeClient:
    """Stands in for PoliteClient. Dependency injection is the house style here."""

    def __init__(self, body: bytes, status: int = 200, stall: bool = False):
        self.body = body
        self.status = status
        self.stall = stall
        self.calls: list[str] = []

    def download(self, url, *, max_bytes, deadline_s=120.0, params=None, rate_key=None, read_timeout=None, max_attempts=None):
        self.calls.append("GET")
        if self.stall:
            raise DownloadTimeout(f"GET {url}: still receiving after {deadline_s:.0f}s")
        if self.status >= 400:
            return Response(url, self.status, {}, b"", False, time.time())
        if len(self.body) > max_bytes:
            raise ResponseTooLarge(f"GET {url}: over {max_bytes} bytes")
        return Response(url, self.status, {}, self.body, False, time.time())


def test_safe_member_name_rejects_escapes():
    assert sup.safe_member_name("fig1.jpg") == "fig1.jpg"
    assert sup.safe_member_name("PMC1/fig1.jpg") == "fig1.jpg"
    for bad in ("../../etc/passwd", "/etc/passwd", "a/../../b.txt", "C:/win.ini", "", "dir/"):
        assert sup.safe_member_name(bad) is None, bad


def test_fetch_bundle_reads_members():
    body = _zip({"PMC1/f1.jpg": b"\xff\xd8jpeg", "PMC1/data.xlsx": b"xlsxbytes"})
    b = sup.fetch_bundle(FakeClient(body), "PMC1")
    assert b.ok and len(b.members) == 2
    assert {m.name for m in b.members} == {"f1.jpg", "data.xlsx"}
    assert b.find("PMC1/f1.jpg").data == b"\xff\xd8jpeg"


def test_fetch_bundle_abandons_an_oversize_body_mid_stream():
    b = sup.fetch_bundle(FakeClient(b"x" * 5000), "PMC1", max_bundle_bytes=1000)
    assert not b.ok and "too large" in b.skip_reason
    assert b.members == []


def test_fetch_bundle_gives_up_on_a_stalled_download():
    """One real Europe PMC bundle sends ~3 MB and then never finishes. Without a
    total deadline the whole run blocks on it forever."""
    b = sup.fetch_bundle(FakeClient(b"", stall=True), "PMC1")
    assert not b.ok and "stalled" in b.skip_reason


def test_fetch_bundle_rejects_oversize_member_without_reading_it():
    body = _zip({"big.xlsx": b"x" * 5000, "ok.csv": b"a,b\n"})
    b = sup.fetch_bundle(FakeClient(body), "PMC1", max_member_bytes=1000)
    big = next(m for m in b.members if m.name == "big.xlsx")
    assert big.data is None and "too large" in big.skip_reason
    assert b.find("ok.csv").data == b"a,b\n"


def test_fetch_bundle_survives_non_zip_and_errors():
    assert sup.fetch_bundle(FakeClient(b"<html>404</html>"), "PMC1").skip_reason == "not a zip archive"
    b = sup.fetch_bundle(FakeClient(b"", status=404), "PMC1")
    assert not b.ok and b.skip_reason == "HTTP 404"


def test_members_are_ordered_deterministically():
    body = _zip({"z.csv": b"1", "a.csv": b"2", "m.xlsx": b"3"})
    names = [m.name for m in sup.fetch_bundle(FakeClient(body), "PMC1").members]
    assert names == sorted(names)


def test_classify_separates_figures_from_supplements():
    body = _zip({"art-f4.jpg": b"img", "art-f4.gif": b"thumb", "etable2.xlsx": b"x", "movie.avi": b"v"})
    b = sup.fetch_bundle(FakeClient(body), "PMC1")
    figs = [FigureRef("F4", "Figure 4", "", "Genetic Variants", ["art-f4.jpg", "art-f4.gif"], True)]
    sups = [SupplementRef("S1", "eTable 2", "", ["etable2.xlsx"])]
    sup.classify(b, figs, sups)
    kinds = {m.name: m.kind for m in b.members}
    assert kinds["art-f4.jpg"] == "figure" and kinds["art-f4.gif"] == "figure"
    assert kinds["etable2.xlsx"] == "supplement"
    assert kinds["movie.avi"] == "unmatched"
    assert b.summary()["n_unmatched_members"] == 1


def test_classify_keeps_data_files_whose_href_did_not_resolve():
    """Some publishers emit extension-less hrefs, so an unresolved data file is
    still a supplement candidate rather than something to drop silently."""
    b = sup.fetch_bundle(FakeClient(_zip({"mmc1.docx": b"d"})), "PMC1")
    sup.classify(b, [], [])
    assert b.members[0].kind == "supplement"


def test_write_members_only_writes_readable_ones(tmp_path):
    body = _zip({"a.csv": b"a,b\n", "big.xlsx": b"x" * 5000})
    b = sup.fetch_bundle(FakeClient(body), "PMC1", max_member_bytes=1000)
    written = sup.write_members(b, tmp_path / "PMC1")
    assert [p.name for p in written] == ["a.csv"]
    assert (tmp_path / "PMC1" / "a.csv").read_bytes() == b"a,b\n"


def test_inventory_on_real_fixture_finds_only_the_variant_figure(fixtures):
    """The committed CC BY cohort paper: five figures, none supplementary, and
    exactly one cited from a paragraph that talks about variants."""
    figs, sups = jats_inventory((fixtures / "PMC13148870.xml").read_bytes())
    assert len(figs) == 5 and sups == []
    cited = [f.label for f in figs if f.cited_near_variant_text]
    assert cited == ["Figure 4"]
    f4 = next(f for f in figs if f.label == "Figure 4")
    assert f4.caption_title == "Genetic Variants"
    # Full-size rendition first, thumbnail last, from the content-type attribute.
    assert f4.graphic_hrefs[0].endswith(".jpg") and f4.graphic_hrefs[-1].endswith(".gif")


def test_inventory_parses_supplementary_material():
    xml = b"""<article><body><sec><p>Variants are listed in
      <xref ref-type="fig" rid="F1">Figure 1</xref>.</p>
      <fig id="F1"><label>Figure 1</label><caption><title>Variant spectrum</title></caption>
        <graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="f1.jpg" content-type="image"/></fig>
      <supplementary-material id="S1"><label>eTable 2</label>
        <media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="etable2.xlsx"/></supplementary-material>
      </sec></body></article>"""
    figs, sups = jats_inventory(xml)
    assert [f.fig_id for f in figs] == ["F1"] and figs[0].cited_near_variant_text
    assert [s.label for s in sups] == ["eTable 2"] and sups[0].hrefs == ["etable2.xlsx"]


@pytest.fixture
def fixtures():
    from pathlib import Path

    return Path(__file__).parent / "fixtures"


def test_summary_reflects_classification():
    """The unmatched count is only meaningful after classify() has run."""
    body = _zip({"f1.jpg": b"i", "movie.avi": b"v"})
    b = sup.fetch_bundle(FakeClient(body), "PMC1")
    assert b.summary()["n_unmatched_members"] == 2
    sup.classify(b, [FigureRef("F1", "Figure 1", "", "", ["f1.jpg"], True)], [])
    assert b.summary()["n_unmatched_members"] == 1


def test_cached_bundle_is_reused_instead_of_refetched(tmp_path):
    """The endpoint is throttled to one request every ten seconds, so a rerun
    must read the members a previous run left on disk."""
    body = _zip({"f1.jpg": b"img", "etable2.xlsx": b"x"})
    dest = tmp_path / "PMC1"
    first = sup.fetch_bundle(FakeClient(body), "PMC1")
    sup.write_members(first, dest)

    again = sup.load_cached(dest)
    assert again is not None and again.ok
    assert {m.name for m in again.members} == {"f1.jpg", "etable2.xlsx"}
    assert again.find("etable2.xlsx").data == b"x"
    assert sup.load_cached(tmp_path / "PMC_absent") is None


def test_marker_is_not_mistaken_for_a_member(tmp_path):
    dest = tmp_path / "PMC1"
    sup.write_members(sup.fetch_bundle(FakeClient(_zip({"a.csv": b"1"})), "PMC1"), dest)
    assert (dest / sup.MARKER).is_file()
    assert [m.name for m in sup.load_cached(dest).members] == ["a.csv"]


def test_enrich_parses_supplements_end_to_end(tmp_path, fixtures):
    """Exercises cli._enrich with a fake client, which is what would have caught
    an unbound local in the supplement branch before it killed a 100-paper run."""
    import io
    import zipfile

    from col4a1 import cli
    from col4a1.sources.fulltext import jats_to_text

    xlsx = (fixtures / "supplement_variants.xlsx").read_bytes()
    jats = b"""<article><front><article-meta>
        <article-id pub-id-type="pmcid">PMC9</article-id>
        <article-title>Cohort</article-title></article-meta></front>
      <body><sec><p>Variants are listed in the supplementary table.</p>
      <supplementary-material id="S1"><label>eTable 2</label>
        <media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="etable2.xlsx"/>
      </supplementary-material></sec></body></article>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("etable2.xlsx", xlsx)

    pt = jats_to_text(jats)
    e = cli._enrich(FakeClient(buf.getvalue()), pt, jats, "PMC9", tmp_path / "PMC9",
                    want_supplements=True, want_figures=False, force_figures=False)
    assert len(e.supplements) == 1
    label, text = e.supplements[0]
    assert label == "eTable 2"
    assert "COL4A1" in text and "c.1537G>A" in text
    assert e.supplement_meta[0]["label"] == "eTable 2"
    assert e.supplement_meta[0]["sheets"], "sheet metadata must record what was rendered"


def test_enrich_reports_an_unsupported_supplement_without_raising(tmp_path):
    import io
    import zipfile

    from col4a1 import cli
    from col4a1.sources.fulltext import jats_to_text

    jats = b"""<article><front><article-meta><article-title>T</article-title></article-meta></front>
      <body><sec><p>x</p><supplementary-material id="S1"><label>Movie 1</label>
        <media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="movie.avi"/>
      </supplementary-material></sec></body></article>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("movie.avi", b"video")
    e = cli._enrich(FakeClient(buf.getvalue()), jats_to_text(jats), jats, "PMC9", tmp_path / "PMC9",
                    want_supplements=True, want_figures=False, force_figures=False)
    assert e.supplements == []
    assert "unsupported" in e.supplement_meta[0]["skip_reason"]
