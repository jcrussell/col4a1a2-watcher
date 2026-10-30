"""JATS XML -> plain text suitable for an LLM, with tables preserved as rows."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

DROP_TAGS = {"ref-list", "fn-group", "ack", "glossary", "app-group", "back", "author-notes", "license"}
SKIP_SECTION_TITLES = re.compile(r"^(references|acknowledg|author contributions|funding|conflict|supplementary)", re.I)


@dataclass
class PaperText:
    pmid: str | None
    pmcid: str | None
    title: str
    abstract: str
    body: str
    n_tables: int

    def as_prompt_text(self) -> str:
        parts = [f"TITLE: {self.title}".strip()]
        if self.abstract:
            parts.append(f"ABSTRACT:\n{self.abstract}")
        if self.body:
            parts.append(f"BODY:\n{self.body}")
        return "\n\n".join(parts)


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


def _table_to_text(tw) -> str:
    lines: list[str] = []
    label = tw.find("label")
    caption = tw.find("caption")
    head = " ".join(x for x in [_text_of(label) if label is not None else "", _text_of(caption) if caption is not None else ""] if x)
    if head:
        lines.append(f"[TABLE] {head}")
    for tr in tw.xpath(".//tr"):
        cells = [_clean("".join(td.itertext())) for td in tr.xpath("./th|./td")]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def jats_to_text(xml: bytes) -> PaperText:
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(xml, parser)
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
