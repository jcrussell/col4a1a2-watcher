"""JATS XML -> plain text suitable for an LLM, with tables preserved as rows."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from lxml import etree

from col4a1a2.patterns import HGVS_LIKE

XLINK = "{http://www.w3.org/1999/xlink}href"

DROP_TAGS = {"ref-list", "fn-group", "ack", "glossary", "app-group", "back", "author-notes", "license"}
SKIP_SECTION_TITLES = re.compile(r"^(references|acknowledg|author contributions|funding|conflict|supplementary)", re.IGNORECASE)

# Variant vocabulary used to decide whether a figure is cited in a variant context.
VARIANT_CONTEXT = re.compile(
    r"\b(variant|variants|mutation|mutations|de novo|missense|nonsense|frameshift|splice|"
    r"glycine|exon|pedigree|segregat\w*|genotype|Sanger|chromatogram|domain)\b", re.IGNORECASE)



@dataclass(frozen=True)
class TextRegion:
    """A labelled span of the assembled prompt text, in raw (pre-normalisation) offsets.

    Grounding uses these to say *where* a quote was found: author body text, a
    deterministically parsed supplement, or a model-generated figure
    transcription. Provenance is therefore computed from the match rather than
    self-reported by the model, which is what makes it worth anything.
    """

    kind: str          # title | abstract | body | supplement | figure_transcript
    label: str         # "" for body regions; "eTable 2a (…)" / "Figure 4" otherwise
    start: int
    end: int

    @property
    def is_model_generated(self) -> bool:
        return self.kind == "figure_transcript"


@dataclass(frozen=True)
class FigureRef:
    fig_id: str
    label: str
    caption: str
    caption_title: str
    graphic_hrefs: list[str]              # content-type="image" first, thumbnails last
    cited_near_variant_text: bool


@dataclass(frozen=True)
class SupplementRef:
    supp_id: str
    label: str
    caption: str
    hrefs: list[str]


@dataclass
class PaperText:
    pmid: str | None
    pmcid: str | None
    title: str
    abstract: str
    body: str
    n_tables: int
    # Pre-rendered text supplied by the caller: (label, text) per item. This module
    # owns the prompt layout; parsing and transcription live elsewhere.
    supplements: list[tuple[str, str]] = field(default_factory=list)
    figures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_full_text(self) -> bool:
        """More than front matter. Some publishers let PMC serve only the title
        and abstract as XML, which must not pass for a full text."""
        return bool(self.body.strip() or self.supplements or self.figures)

    def _assemble(self) -> tuple[str, list[TextRegion]]:
        chunks: list[tuple[str, str, str]] = [("title", "", f"TITLE: {self.title}".strip())]
        if self.abstract:
            chunks.append(("abstract", "", f"ABSTRACT:\n{self.abstract}"))
        if self.body:
            chunks.append(("body", "", f"BODY:\n{self.body}"))
        for label, text in self.supplements:
            chunks.append(("supplement", label, text))
        for label, text in self.figures:
            chunks.append(("figure_transcript", label, text))

        out: list[str] = []
        regions: list[TextRegion] = []
        pos = 0
        for kind, label, chunk in chunks:
            if out:
                pos += 2  # the "\n\n" join separator
            regions.append(TextRegion(kind=kind, label=label, start=pos, end=pos + len(chunk)))
            out.append(chunk)
            pos += len(chunk)
        return "\n\n".join(out), regions

    def as_prompt_text(self) -> str:
        """The exact string sent to the model. With `supplements` and `figures`
        both empty it is title, abstract and body alone, so token measurements
        of the article text stay comparable whether or not extras are attached."""
        return self._assemble()[0]

    def regions(self) -> list[TextRegion]:
        return self._assemble()[1]


def _clean(s: str) -> str:
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _remove_keep_tail(el) -> None:
    """Remove an element but keep its tail text (lxml drops it otherwise)."""
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _text_of(el) -> str:
    """Text of an element, dropping bibliographic xrefs and keeping inline text."""
    for xref in el.xpath(".//xref[@ref-type='bibr']"):
        _remove_keep_tail(xref)
    return _clean("".join(el.itertext()))


def rows_to_text(head: str, rows: Iterable[Sequence[str]]) -> str:
    """Render a table as `[TABLE] <head>` plus pipe-joined rows.

    The single implementation of the table format, shared by JATS tables and by
    parsed supplement spreadsheets so both look identical to the model.
    """
    lines: list[str] = []
    if head:
        lines.append(f"[TABLE] {head}")
    for cells in rows:
        cleaned = [_clean(c) for c in cells]
        if any(cleaned):
            lines.append(" | ".join(cleaned))
    return "\n".join(lines)


def _table_to_text(tw) -> str:
    label = tw.find("label")
    caption = tw.find("caption")
    head = " ".join(x for x in [_text_of(label) if label is not None else "", _text_of(caption) if caption is not None else ""] if x)
    rows = (["".join(td.itertext()) for td in tr.xpath("./th|./td")] for tr in tw.xpath(".//tr"))
    return rows_to_text(head, rows)


def _parse(xml: bytes):
    """JATS as published is often malformed and occasionally huge; recover and allow both."""
    return etree.fromstring(xml, etree.XMLParser(recover=True, huge_tree=True))


def jats_to_text(xml: bytes) -> PaperText:
    root = _parse(xml)
    if root is None:
        raise ValueError("could not parse XML")

    def first(xp: str) -> str | None:
        r = root.xpath(xp)
        return _clean("".join(r[0].itertext())) if r else None

    pmid = first("//article-meta/article-id[@pub-id-type='pmid']")
    pmcid = first("//article-meta/article-id[@pub-id-type='pmcid']")
    if pmcid and not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    title = first("//article-meta//article-title") or ""
    abstract_els = root.xpath("//article-meta/abstract")
    abstract = "\n\n".join(_text_of(a) for a in abstract_els)

    body_parts: list[str] = []
    n_tables = 0
    body = root.find("body")
    if body is not None:
        for drop in body.xpath(".//*[self::ref-list or self::fn-group]"):
            _remove_keep_tail(drop)
        # Tables: render separately, then remove so paragraphs don't duplicate cell text.
        for tw in body.xpath(".//table-wrap"):
            n_tables += 1
            body_parts.append(_table_to_text(tw))
            _remove_keep_tail(tw)
        for sec in body.xpath("./sec") or [body]:
            body_parts.append(_section_text(sec, level=1))
    # Tables that live in <floats-group> or <back> are still data.
    for tw in root.xpath("//floats-group//table-wrap"):
        n_tables += 1
        body_parts.append(_table_to_text(tw))

    return PaperText(pmid=pmid, pmcid=pmcid, title=title, abstract=abstract, body=_clean("\n\n".join(p for p in body_parts if p)), n_tables=n_tables)


def _section_text(sec, level: int) -> str:
    title_el = sec.find("title")
    title = _text_of(title_el) if title_el is not None else ""
    if title and SKIP_SECTION_TITLES.match(title):
        return ""
    parts = []
    if title:
        parts.append(("#" * min(level, 4)) + " " + title)
    for child in sec:
        tag = etree.QName(child).localname if isinstance(child.tag, str) else ""
        if tag == "title":
            continue
        if tag == "sec":
            parts.append(_section_text(child, level + 1))
        elif tag in DROP_TAGS:
            continue
        else:
            t = _text_of(child)
            if t:
                parts.append(t)
    return "\n\n".join(p for p in parts if p)


def _graphic_hrefs(fig) -> list[str]:
    """Graphic hrefs for a figure, full-size renditions first.

    PMC emits two <graphic> children per figure, distinguished by
    content-type="image" versus "thumb". Ordering by that attribute is exact,
    where guessing from the file extension is not.
    """
    scored: list[tuple[int, str]] = []
    for g in fig.iter("graphic"):
        href = g.get(XLINK)
        if not href:
            continue
        ctype = (g.get("content-type") or "").lower()
        rank = 0 if ctype == "image" else (2 if ctype == "thumb" else 1)
        scored.append((rank, href))
    seen: set[str] = set()
    out: list[str] = []
    for _, href in sorted(scored, key=lambda x: x[0]):
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out


def jats_inventory(xml: bytes) -> tuple[list[FigureRef], list[SupplementRef]]:
    """Figures and supplementary files declared by the article, from its own XML.

    Parses a fresh tree, because `jats_to_text` mutates the one it is given.
    `cited_near_variant_text` asks whether the paragraph that cites a figure
    talks about variants. Measured on the committed fixtures, that selects the
    right figure where caption keywords do not: MRI captions routinely mention
    "a patient with a COL4A1 variant", and some publishers emit no caption title
    at all.
    """
    root = _parse(xml)
    if root is None:
        return [], []

    # Which figure ids are cited from a paragraph that discusses variants.
    variant_cited: set[str] = set()
    for xref in root.xpath("//xref[@ref-type='fig']"):
        rid = xref.get("rid") or ""
        if not rid:
            continue
        # Only a paragraph-level ancestor counts. Climbing as far as <sec> or
        # <body> would match nearly any Results section, since the vocabulary
        # includes words like "exon" and "variant", and would mark figures as
        # variant-cited on no real evidence.
        container = xref.getparent()
        while container is not None and etree.QName(container).localname not in ("p", "td", "th"):
            if etree.QName(container).localname in ("sec", "body", "article"):
                container = None
                break
            container = container.getparent()
        if container is None:
            continue
        if VARIANT_CONTEXT.search(_clean("".join(container.itertext()))):
            variant_cited.update(rid.split())

    figures: list[FigureRef] = []
    for fig in root.iter("fig"):
        fig_id = fig.get("id") or ""
        label_el = fig.find("label")
        caption_el = fig.find("caption")
        title_el = caption_el.find("title") if caption_el is not None else None
        figures.append(FigureRef(
            fig_id=fig_id,
            label=_text_of(label_el) if label_el is not None else "",
            caption=_text_of(caption_el) if caption_el is not None else "",
            caption_title=_text_of(title_el) if title_el is not None else "",
            graphic_hrefs=_graphic_hrefs(fig),
            cited_near_variant_text=bool(fig_id) and fig_id in variant_cited,
        ))

    supplements: list[SupplementRef] = []
    for sm in root.iter("supplementary-material"):
        label_el = sm.find("label")
        caption_el = sm.find("caption")
        hrefs = [h for h in (el.get(XLINK) for el in sm.iter()) if h]
        supplements.append(SupplementRef(
            supp_id=sm.get("id") or "",
            label=_text_of(label_el) if label_el is not None else "",
            caption=_text_of(caption_el) if caption_el is not None else "",
            hrefs=list(dict.fromkeys(hrefs)),
        ))
    return figures, supplements


def text_deficit(text: str) -> dict:
    """Does this paper's text look like it is missing the variants it claims?

    The cost gate for figure vision. A paper whose text names no HGVS-like token
    while claiming a cohort has its variants somewhere we cannot read. Measured
    on the committed fixtures: the 44-patient cohort scores zero HGVS tokens,
    the prenatal case report scores two. Running vision only on the deficit
    cases keeps the rest of the table free of model-transcribed evidence.
    """
    n_hgvs = len(set(HGVS_LIKE.findall(text)))
    claimed = [int(m) for m in re.findall(r"\b(\d{1,4})\s+(?:patients|probands|individuals|families|cases)\b", text, re.IGNORECASE)]
    largest_claim = max(claimed) if claimed else 0
    points_elsewhere = bool(re.search(
        r"(eTable|Table S\d|Supplementary Table|supplemental Table|are (?:shown|listed|depicted|included) in (?:Figure|Fig\.?)\s*\d)",
        text, re.IGNORECASE))
    return {
        "n_hgvs": n_hgvs,
        "largest_claim": largest_claim,
        "points_elsewhere": points_elsewhere,
        "deficit": n_hgvs == 0 or (largest_claim >= 5 and n_hgvs < max(2, largest_claim // 10)),
    }
