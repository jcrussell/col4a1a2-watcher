"""Figure selection, the cost gate, rendering and the transcript cache.
The vision call itself is never exercised offline."""

from pathlib import Path

from col4a1a2.extract.figures import (
    FIGURE_PROMPT_VERSION,
    FigureCache,
    FigureTranscription,
    media_type_for,
    select_figures,
    transcription_to_text,
)
from col4a1a2.sources.fulltext import FigureRef, jats_inventory, jats_to_text, text_deficit

FIX = Path(__file__).parent / "fixtures"


def _fig(fig_id, label, title="", cited=False, href="f.jpg"):
    return FigureRef(fig_id, label, "", title, [href], cited)


def test_gate_fires_on_the_cohort_paper_and_not_on_the_case_report():
    """The cohort paper claims 44 patients and names no variant; the case report
    names its variant. Vision should run only on the first."""
    cohort = jats_to_text((FIX / "PMC13148870.xml").read_bytes()).as_prompt_text()
    case = jats_to_text((FIX / "PMC13693400.xml").read_bytes()).as_prompt_text()
    d1, d2 = text_deficit(cohort), text_deficit(case)
    assert d1["deficit"] and d1["n_hgvs"] == 0 and d1["largest_claim"] >= 40
    assert not d2["deficit"] and d2["n_hgvs"] > 0


def test_selection_picks_only_the_variant_figure_on_the_real_fixture():
    xml = (FIX / "PMC13148870.xml").read_bytes()
    figs, _ = jats_inventory(xml)
    text = jats_to_text(xml).as_prompt_text()
    chosen, gate = select_figures(figs, text)
    assert [f.label for f in chosen] == ["Figure 4"]
    assert gate["selected_reason"] == "cited in variant context"


def test_no_figures_selected_when_the_text_already_names_variants():
    xml = (FIX / "PMC13693400.xml").read_bytes()
    figs, _ = jats_inventory(xml)
    chosen, gate = select_figures(figs, jats_to_text(xml).as_prompt_text())
    assert chosen == []
    assert gate["selected_reason"] == "text already names variants"


def test_force_overrides_the_gate():
    xml = (FIX / "PMC13693400.xml").read_bytes()
    figs, _ = jats_inventory(xml)
    chosen, gate = select_figures(figs, jats_to_text(xml).as_prompt_text(), force=True)
    assert chosen and gate["forced"]


def test_selection_honours_the_cap():
    figs = [_fig(f"F{i}", f"Figure {i}", cited=True) for i in range(6)]
    chosen, _ = select_figures(figs, "no variants here", max_figures=3)
    assert len(chosen) == 3


def test_caption_title_is_a_fallback_when_nothing_is_cited():
    figs = [_fig("F1", "Figure 1", title="Neuroimaging"), _fig("F2", "Figure 2", title="Variant spectrum")]
    chosen, gate = select_figures(figs, "no variants here")
    assert [f.label for f in chosen] == ["Figure 2"]
    assert gate["selected_reason"] == "caption title"


def test_rendered_transcription_is_labelled_as_machine_output():
    t = FigureTranscription(figure_label="Figure 4", panel_labels=["A", "B"],
                            items=["A | exon 3 | c.164G>A", "B | exon 26 | c.1820G>T"],
                            legible=True, contains_variant_labels=True, notes="colour key not read")
    text = transcription_to_text(_fig("F4", "Figure 4", "Genetic Variants"), t)
    assert text.startswith("=== FIGURE TRANSCRIPTION (MODEL-GENERATED, NOT AUTHOR TEXT): Figure 4 ===")
    assert "c.164G>A" in text and "Panels as printed: A, B" in text
    assert "colour key not read" in text


def test_illegible_transcription_says_so_in_the_text():
    t = FigureTranscription(figure_label="Figure 4", items=["c.1A>T"], legible=False,
                            contains_variant_labels=True)
    assert "could not be read reliably" in transcription_to_text(_fig("F4", "Figure 4"), t)


def test_media_types():
    assert media_type_for("JPG") == "image/jpeg"
    assert media_type_for("webp") == "image/webp"
    assert media_type_for("tif") is None, "tif is not an accepted image type"


def test_cache_keys_on_image_content_and_prompt_version(tmp_path):
    c = FigureCache(tmp_path / "ft.jsonl")
    t = FigureTranscription(figure_label="Figure 4", items=["c.1A>T"], legible=True,
                            contains_variant_labels=True)
    digest = c.digest(b"imagebytes")
    assert c.get(digest) is None
    c.put(pmcid="PMC1", fig=_fig("F4", "Figure 4"), image_sha256=digest, transcription=t,
          usage={"input": 300, "output": 100})

    reread = FigureCache(tmp_path / "ft.jsonl")
    got = reread.get(digest)
    assert got is not None and got["figure_prompt_version"] == FIGURE_PROMPT_VERSION
    assert FigureTranscription.model_validate(got["transcription"]).items == ["c.1A>T"]
    # Different image bytes must miss, so a replaced figure is re-read.
    assert reread.get(c.digest(b"otherbytes")) is None
