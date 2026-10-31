"""Supplementary tables: bytes in, rows out.

Deliberately pure. No network, no JATS, no model calls, so the whole module is
testable offline against files the tests generate themselves.

Row filtering is what makes this affordable. A measured supplement held 1,512
cell strings across 11 sheets covering 859 genes, of which exactly two mentioned
COL4A1 or COL4A2; appending it whole would spend ~3,600 input tokens to convey
two relevant rows. Filtering is therefore on by default, but it is never silent:
a truncation marker goes into the rendered text and the kept/total counts are
recorded, because a quietly truncated cohort table would make the model
confidently undercount, which is a correctness bug rather than a cost one.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field

from col4a1a2.sources.fulltext import HGVS_LIKE, rows_to_text

log = logging.getLogger("col4a1a2")

GENE_RE = re.compile(r"COL4A[12]", re.I)
# Variant notation that HGVS_LIKE alone misses: bare protein shorthand and
# three-letter forms without a c./p. prefix.
EXTRA_VARIANT_RE = re.compile(r"\b[A-Z]\d{2,5}[A-Z]\b|\bGly\d+[A-Z][a-z]{2}\b|\bexon\s*\d+\b", re.I)

HEADER_ROWS = 3        # multi-row headers are the norm in supplement tables
KEEP_ALL_BELOW = 60    # small sheets are cheaper to include whole than to reason about
MAX_ROWS_PER_SHEET = 200
MAX_CHARS_PER_PAPER = 24_000
MAX_PDF_PAGES = 30
MIN_PDF_CHARS = 200    # below this a PDF is probably scanned

SUPPORTED = {"xlsx", "csv", "tsv", "txt", "docx", "pdf"}


class UnsupportedSupplement(Exception):
    pass


@dataclass
class Sheet:
    name: str
    rows: list[list[str]] = field(default_factory=list)
    rows_total: int = 0
    truncated: bool = False

    def summary(self) -> dict:
        return {"name": self.name, "rows_total": self.rows_total,
                "rows_kept": len(self.rows), "truncated": self.truncated}


def is_relevant(cells: list[str]) -> bool:
    joined = " ".join(cells)
    return bool(GENE_RE.search(joined) or HGVS_LIKE.search(joined) or EXTRA_VARIANT_RE.search(joined))


def filter_rows(
    sheet: Sheet,
    *,
    header_rows: int = HEADER_ROWS,
    keep_all_below: int = KEEP_ALL_BELOW,
    max_rows: int = MAX_ROWS_PER_SHEET,
) -> Sheet:
    """Keep the header, then only rows that mention our genes or a variant.

    Small sheets pass through whole: cheaper than risking the filter dropping the
    answer. Everything dropped is counted, never silently discarded.
    """
    total = len(sheet.rows)
    if total <= keep_all_below:
        return Sheet(name=sheet.name, rows=sheet.rows[:max_rows], rows_total=total,
                     truncated=total > max_rows)
    head = sheet.rows[:header_rows]
    body = [r for r in sheet.rows[header_rows:] if is_relevant(r)]
    truncated = len(body) > max_rows
    return Sheet(name=sheet.name, rows=head + body[:max_rows], rows_total=total, truncated=truncated)


def _xlsx(data: bytes) -> list[Sheet]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        out: list[Sheet] = []
        for ws in wb.worksheets:
            rows: list[list[str]] = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if any(c.strip() for c in cells):
                    rows.append(cells)
            out.append(Sheet(name=ws.title, rows=rows, rows_total=len(rows)))
        return out
    finally:
        wb.close()


def _delimited(data: bytes, ext: str) -> list[Sheet]:
    text = data.decode("utf-8", errors="replace")
    delim = "\t" if ext in ("tsv", "txt") and "\t" in text[:4000] else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    return [Sheet(name="", rows=rows, rows_total=len(rows))]


def _docx(data: bytes) -> list[Sheet]:
    """Tables from a Word document. Textboxes, headers and embedded sheets are
    not reachable through python-docx and are therefore not covered."""
    import docx

    doc = docx.Document(io.BytesIO(data))
    out: list[Sheet] = []
    for i, table in enumerate(doc.tables, start=1):
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if any(c.strip() for c in cells):
                rows.append(cells)
        if rows:
            out.append(Sheet(name=f"table {i}", rows=rows, rows_total=len(rows)))
    if not out:
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        if paras:
            out.append(Sheet(name="text", rows=[[p] for p in paras], rows_total=len(paras)))
    return out


def _pdf(data: bytes, *, max_pages: int = MAX_PDF_PAGES) -> list[Sheet]:
    """Tables and text from a PDF. A scanned PDF yields almost nothing; that is
    detected by the caller and recorded rather than guessed at. No OCR."""
    import pdfplumber

    out: list[Sheet] = []
    chars = 0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pno, page in enumerate(pdf.pages[:max_pages], start=1):
            found_on_page = False
            for tno, table in enumerate(page.extract_tables() or [], start=1):
                rows = [["" if c is None else str(c) for c in row] for row in table]
                rows = [r for r in rows if any(c.strip() for c in r)]
                if rows:
                    out.append(Sheet(name=f"page {pno} table {tno}", rows=rows, rows_total=len(rows)))
                    chars += sum(len(c) for r in rows for c in r)
                    found_on_page = True
            # Fall back to this page's text only when this page had no table.
            # Tracking it per page matters: a PDF with a table on page 1 and
            # text-only tables later would otherwise lose everything after page 1.
            if not found_on_page:
                t = page.extract_text() or ""
                chars += len(t)
                if t.strip():
                    lines = [[ln] for ln in t.splitlines() if ln.strip()]
                    out.append(Sheet(name=f"page {pno} text", rows=lines, rows_total=len(lines)))
    if chars < MIN_PDF_CHARS:
        raise UnsupportedSupplement(f"pdf yielded only {chars} characters; probably scanned (needs_ocr)")
    return out


def parse(data: bytes, ext: str) -> list[Sheet]:
    """Parse a supplement into sheets. Raises UnsupportedSupplement, never crashes."""
    e = ext.lower()
    try:
        if e == "xlsx":
            return _xlsx(data)
        if e in ("csv", "tsv", "txt"):
            return _delimited(data, e)
        if e == "docx":
            return _docx(data)
        if e == "pdf":
            return _pdf(data)
    except UnsupportedSupplement:
        raise
    except Exception as e2:
        raise UnsupportedSupplement(f"{ext} parse failed: {type(e2).__name__}: {e2}") from e2
    if e in ("xls", "doc"):
        raise UnsupportedSupplement(f"legacy {e} format not supported")
    raise UnsupportedSupplement(f"unsupported extension {ext!r}")


def filter_sheets(sheets: list[Sheet], *, max_total_rows: int = 400) -> list[Sheet]:
    """Filter a whole file's sheets, deciding when the small-sheet bypass applies.

    `filter_rows` lets a small sheet through whole so a single short variant
    table is never gutted. That is right for a one-table supplement and wrong for
    a large workbook, where it waves through hundreds of rows of gene-panel
    listings; measured on a real supplement, the bypass cost about 8,000 input
    tokens to convey a handful of relevant rows.

    The decision is made on **total rows**, not sheet count: `_pdf` emits one
    sheet per page and `_docx` one per table, so a sheet-count gate would strip
    the bypass from any multi-page PDF and cut each page's few lines down.
    """
    return filter_sheets_by_rows(sheets, max_total_rows=max_total_rows)


def sheets_to_text(
    label: str,
    member: str,
    sheets: list[Sheet],
    *,
    max_chars: int = MAX_CHARS_PER_PAPER,
) -> tuple[str, int]:
    """Render filtered sheets in the same table format the article body uses.

    Returns the text and **how many sheets it actually rendered**, so the caller
    records what reached the prompt rather than what it hoped to send. The header
    sits inside the region so a quote that accidentally includes it still
    attributes to the supplement.
    """
    name = label or member
    out = [f"=== SUPPLEMENTARY FILE: {name} (parsed from {member}) ==="]
    used = len(out[0])
    rendered = 0
    for s in sheets:
        if not s.rows:
            continue
        # Always give the table a head, so it carries the same "[TABLE]" marker
        # body tables do; an unnamed sheet would otherwise render as bare rows.
        head = s.name or name
        if s.rows_total > len(s.rows) or s.truncated:
            head = f"{head} [kept {len(s.rows)} of {s.rows_total} rows]".strip()
        chunk = rows_to_text(head, s.rows)
        if used + len(chunk) > max_chars:
            out.append(f"[TRUNCATED: character cap reached; "
                       f"{len(sheets) - rendered} of {len(sheets)} sheets in this file were not included]")
            break
        out.append(chunk)
        used += len(chunk)
        rendered += 1
    return "\n".join(out), rendered


def filter_sheets_by_rows(sheets: list[Sheet], *, max_total_rows: int = 400) -> list[Sheet]:
    """Decide the small-sheet bypass from total row count, not sheet count.

    `_pdf` emits one Sheet per page and `_docx` one per table, so counting
    sheets would strip the bypass from any PDF over a few pages and gut each
    page's handful of lines. Total rows is what actually costs tokens.
    """
    total = sum(sh.rows_total for sh in sheets)
    bypass = 0 if total > max_total_rows else KEEP_ALL_BELOW
    return [filter_rows(sh, keep_all_below=bypass) for sh in sheets]
