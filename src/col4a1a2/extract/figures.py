"""Reading variant labels out of figure images, and rendering them as text.

Why transcribe rather than pass images into the extraction call: `verify.ground`
string-matches every evidence quote against exactly the string sent to the
model. An image has no string, so a fact read straight from one could never be
grounded. Transcribing first keeps the whole grounding architecture intact and
leaves the transcription on disk as an auditable artefact, clearly labelled as
machine-generated.

The output is structured rather than prose for two reasons. Given room, the
model will *infer* which panel of a schematic belongs to which gene, which
would silently misassign every variant in it; the schema makes panel
labels something it reports or leaves empty rather than reasons about. And
because the rendered text is what quotes are matched against, it must not
depend on the model's prose style.
"""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field

from col4a1a2.digest import sha256_bytes
from col4a1a2.extract.cache import KeyedCache
from col4a1a2.sources.fulltext import FigureRef, rows_to_text, text_deficit

log = logging.getLogger("col4a1a2")

FIGURE_PROMPT_VERSION = "f1"
MAX_FIGURES_PER_PAPER = 3
MAX_FIGURE_TOKENS = 4000

MEDIA_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "gif": "image/gif", "webp": "image/webp",
}

SYSTEM = """You transcribe scientific figures for a COL4A1/COL4A2 variant database. You are an instrument, not an interpreter.

Rules:
- Transcribe only what is printed in the image. Copy variant labels, exon numbers, panel letters and axis labels exactly as they appear, including punctuation and case.
- Never infer. If a panel is not labelled with a gene name, leave the gene out; do not deduce it from context, layout or which gene has more variants. Mis-assigning a panel silently misattributes every variant in it.
- If any text is too small, blurry or overlapping to read with confidence, set legible to false and say which parts in notes. A partial honest transcription is far better than a complete guess.
- Colour-coded or legend-keyed metadata is unreliable to read. Record it in notes with an explicit caveat rather than as an item.
- Set contains_variant_labels to false for imaging, histology, photographs, flow charts, survival curves and bar charts with no variant nomenclature."""


class TranscriptionFailed(RuntimeError):
    """The vision call returned no usable transcription."""


class FigureTranscription(BaseModel):
    figure_label: str = Field(description="The figure label as printed, e.g. 'Figure 4'; empty if absent")
    panel_labels: list[str] = Field(default_factory=list, description="Panel labels exactly as printed, e.g. ['A', 'B']")
    items: list[str] = Field(default_factory=list, description="Every printed label, verbatim, one per entry; prefix with the panel where a panel is labelled, e.g. 'A | exon 3 | c.164G>A'")
    legible: bool = Field(description="False if any part could not be read with confidence")
    contains_variant_labels: bool = Field(description="True only if the image prints variant nomenclature")
    notes: str = Field(default="", description="Uncertainty, illegible regions, colour-keyed metadata and its caveats")


def media_type_for(ext: str) -> str | None:
    return MEDIA_TYPES.get(ext.lower())


def select_figures(
    figures: list[FigureRef],
    text: str,
    *,
    max_figures: int = MAX_FIGURES_PER_PAPER,
    force: bool = False,
) -> tuple[list[FigureRef], dict]:
    """Which figures to transcribe, and why.

    Gated per paper first: if the text already names variants, reading its
    figures buys nothing and would inject model-transcribed evidence into a
    record that does not need it. Within a deficit paper, figures cited from a
    paragraph that discusses variants are preferred, since caption keywords
    over-select badly: imaging captions routinely say "in a patient with a
    COL4A1 variant", and some publishers emit no caption title at all.
    """
    gate = text_deficit(text)
    gate["forced"] = force
    if not force and not gate["deficit"]:
        gate["selected_reason"] = "text already names variants"
        return [], gate

    cited = [f for f in figures if f.cited_near_variant_text]
    titled = [
        f for f in figures
        if f not in cited and f.caption_title
        and any(k in f.caption_title.lower() for k in ("variant", "mutation", "genetic", "pedigree", "domain", "spectrum"))
    ]
    chosen = (cited + titled)[:max_figures]
    gate["selected_reason"] = "cited in variant context" if cited else ("caption title" if titled else "none matched")
    gate["n_cited"] = len(cited)
    gate["n_titled"] = len(titled)
    return chosen, gate


def transcribe_figure(
    client,
    image: bytes,
    media_type: str,
    fig: FigureRef,
    *,
    model: str,
    effort: str,
    max_tokens: int = MAX_FIGURE_TOKENS,
    image_sha256: str | None = None,
    archive=None,
    refresh: bool = False,
) -> tuple[FigureTranscription, dict, dict]:
    """One vision call. The caption is supplied as text so the model need not guess.

    Returns the transcription, its usage, and the provenance its cached row carries.
    """
    from col4a1a2.extract.llm import call, system_block

    caption = fig.caption_title or fig.caption
    prompt = (
        f"Figure label as the article gives it: {fig.label or 'unknown'}\n"
        f"Caption as the article gives it: {caption or '(none)'}\n\n"
        "Transcribe this figure."
    )
    reply = call(
        client, site="figure", model=model, effort=effort, max_tokens=max_tokens,
        system=system_block(SYSTEM),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(image).decode()}},
            {"type": "text", "text": prompt},
        ]}],
        output_format=FigureTranscription, archive=archive, refresh=refresh,
        request={"prompt_version": FIGURE_PROMPT_VERSION, "fig_id": fig.fig_id,
                 "image_sha256": image_sha256 or sha256_bytes(image)},
    )
    # Thinking is always on for this model and counts against max_tokens, so a
    # dense figure can spend the whole budget before emitting a text block, and a
    # safety decline emits none either; `require` checks the stop reason first.
    return reply.require(TranscriptionFailed), reply.usage_row(), reply.provenance()


def transcription_to_text(fig: FigureRef, t: FigureTranscription) -> str:
    """Render a transcription deterministically.

    The header names the figure and states plainly that what follows is machine
    output, and it sits *inside* the region so a quote that accidentally includes
    it still attributes to the figure.
    """
    head = f"=== FIGURE TRANSCRIPTION (MODEL-GENERATED, NOT AUTHOR TEXT): {transcript_label(fig, t)} ==="
    lines = [head]
    caption = fig.caption_title or fig.caption
    if caption:
        lines.append(f"Caption: {caption}")
    if t.panel_labels:
        lines.append(f"Panels as printed: {', '.join(t.panel_labels)}")
    if t.items:
        lines.append(rows_to_text("", ([i] for i in t.items)))
    if not t.legible:
        lines.append("NOTE: the model reported that part of this figure could not be read reliably.")
    if t.notes:
        lines.append(f"Transcription notes: {t.notes}")
    return "\n".join(lines)


def transcript_label(fig: FigureRef, t: FigureTranscription) -> str:
    return t.figure_label or fig.label or fig.fig_id or "figure"


class FigureCache(KeyedCache):
    """Transcriptions keyed by image content, prompt version and requested model.

    Keyed on the image sha256 rather than its filename, so a publisher replacing
    a figure invalidates correctly. Lives outside the snapshot so new snapshots
    and re-fetches reuse it, the same reasoning as the triage decision log.
    """

    KEY = ("image_sha256", "figure_prompt_version", "requested_model")
    WHAT = "figure cache"

    @staticmethod
    def digest(image: bytes) -> str:
        return sha256_bytes(image)

    def get(self, image_sha256: str, requested_model: str, *, refresh: bool = False) -> dict | None:
        return self.lookup((image_sha256, FIGURE_PROMPT_VERSION, requested_model), refresh=refresh)

    def put(self, *, pmcid: str, fig: FigureRef, image_sha256: str,
            transcription: FigureTranscription, usage: dict, provenance: dict) -> dict:
        return self.store({
            "pmcid": pmcid,
            "fig_id": fig.fig_id,
            "label": fig.label,
            "image_sha256": image_sha256,
            "figure_prompt_version": FIGURE_PROMPT_VERSION,
            **provenance,
            "transcription": transcription.model_dump(),
            "usage": usage,
        })
