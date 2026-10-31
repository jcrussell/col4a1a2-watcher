from pathlib import Path

from col4a1a2.sources.fulltext import jats_to_text

FIX = Path(__file__).parent / "fixtures"


def test_case_report_parses():
    pt = jats_to_text((FIX / "PMC13693400.xml").read_bytes())
    assert pt.pmcid == "PMC13693400"
    assert "COL4A1" in pt.title
    assert len(pt.abstract) > 200
    assert len(pt.body) > 5000
    assert "<" not in pt.body and "</" not in pt.abstract
    assert pt.n_tables >= 1
    assert "[TABLE]" in pt.body
    assert "References" not in pt.body.split("\n")[0]


def test_cohort_paper_parses():
    pt = jats_to_text((FIX / "PMC13148870.xml").read_bytes())
    assert pt.pmcid == "PMC13148870"
    assert len(pt.body) > 10000
    text = pt.as_prompt_text()
    assert text.startswith("TITLE:")
    assert "ABSTRACT:" in text and "BODY:" in text


def test_bibliographic_xrefs_removed():
    xml = b"""<article><front><article-meta><article-title>T</article-title></article-meta></front>
    <body><sec><title>Intro</title><p>Known fact<xref ref-type="bibr" rid="r1">1</xref>. Next.</p></sec>
    <sec><title>References</title><p>should be dropped</p></sec></body></article>"""
    pt = jats_to_text(xml)
    assert "Known fact. Next." in pt.body
    assert "should be dropped" not in pt.body
