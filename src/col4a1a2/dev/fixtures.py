"""Generate the synthetic supplement fixtures the offline tests use.

    uv run col4a1a2-dev fixtures

Authored by us, so no publisher bytes are redistributed and the fixture licence
rule in tests/fixtures/README.md does not apply.
"""

from __future__ import annotations

import sys

from col4a1a2.config import REPO

FIX = REPO / "tests" / "fixtures"


def main(argv: list[str] | None = None) -> int:
    FIX.mkdir(parents=True, exist_ok=True)

    ROWS = [["Patient", "Gene", "Transcript", "Coding", "Protein", "Inheritance"],
            ["P1", "COL4A1", "NM_001845.6", "c.1537G>A", "p.Gly513Ser", "de novo"],
            ["P2", "COL4A2", "NM_001846.4", "c.4493C>T", "p.Thr1498Met", "inherited"],
            ["P3", "LAMC1", "NM_002293.3", "c.3716C>A", "p.Ala1239Glu", "unknown"],
            ["P4", "ABCC6", "NM_001171.6", "c.3421C>T", "p.Arg1141Ter", "unknown"]]

    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Variants"
    for r in ROWS: ws.append(r)
    big = wb.create_sheet("AllGenes")
    big.append(ROWS[0])
    for i in range(200):
        big.append([f"X{i}", "SOMEGENE", "NM_000000.1", "", "", ""])
    big.append(["X999", "COL4A1", "NM_001845.6", "c.2317G>A", "p.Gly773Arg", "de novo"])
    wb.save(FIX / "supplement_variants.xlsx")

    (FIX / "supplement_variants.csv").write_text(
        "\n".join(",".join(r) for r in ROWS) + "\n", encoding="utf-8")

    import docx
    d = docx.Document()
    d.add_paragraph("Supplementary Table 1. Variants identified.")
    t = d.add_table(rows=0, cols=len(ROWS[0]))
    for r in ROWS:
        cells = t.add_row().cells
        for c, v in zip(cells, r): c.text = v
    d.save(FIX / "supplement_variants.docx")

    # A minimal PDF with a readable text table, written by hand so we own it.
    lines = ["Supplementary Table 1. Variants identified in this cohort.",
             "Patient Gene Coding Protein Inheritance",
             "P1 COL4A1 c.1537G>A p.Gly513Ser de novo",
             "P2 COL4A2 c.4493C>T p.Thr1498Met inherited",
             "P3 LAMC1 c.3716C>A p.Ala1239Glu unknown",
             "This table lists the collagen IV variants and their inheritance."]
    text_ops = "BT /F1 11 Tf 56 760 Td 14 TL\n" + "".join(
        f"({ln.replace('(', '').replace(')', '')}) Tj T*\n" for ln in lines) + "ET"
    stream = text_ops.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n".encode() + b"%%EOF\n")
    (FIX / "supplement_variants.pdf").write_bytes(bytes(out))
    print("wrote:", sorted(p.name for p in FIX.glob("supplement_variants.*")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
