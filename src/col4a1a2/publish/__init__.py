"""Build the public release from tracked files alone: three tables and a static site.

    uv run col4a1a2 publish [--out _site] [--all]

Inputs are `data/curated/` and the code, nothing under `data/raw/`, so CI can
build the release from a checkout. The output is deterministic: rows are
sorted, the release version is the package's (from the git tag), and "data as
of" is the date of the newest `written_at` in the data, never the clock, so the
same commit always builds the same bytes.

By default the evidence table holds only confident records: upheld by the
checked adversarial review, every quote found in the text, nothing flagging
them for a person (`extract.rows.is_confident`). `--all` releases every current
record with the status columns that explain each one; it is for people
inspecting the data, and CI never passes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from col4a1a2 import __repository__, __version__, jsonl, normalize, schemas
from col4a1a2.config import Settings
from col4a1a2.extract import rows as rows_mod
from col4a1a2.extract.challenge import record_key
from col4a1a2.extract.schema import PHENOTYPE_SYSTEMS, VARIANT_TYPES
from col4a1a2.ledger import STATUS_MEANINGS, fold_rows, paper_status
from col4a1a2.publish import variants as variants_mod

LICENSE = {"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/",
           "title": "Creative Commons Attribution 4.0, except the excerpt columns"}
# Columns that carry the papers' own words. They are quoted so each record can
# be checked against its source, and they stay under the article's copyright:
# we cannot license someone else's sentence, so CC BY covers everything else.
EXCERPT_COLUMNS = ("evidence_quotes", "reported_classification")
# Every page is templates/layout.html around one page template; the nav, the
# footer, the styles and the script wrapper are written once, here and there.
TEMPLATES = Path(__file__).with_name("templates")
JS = Path(__file__).with_name("js")
CSS = Path(__file__).with_name("site.css")
DESCRIPTION = ("Published COL4A1 and COL4A2 variant carriers, machine-extracted from the literature with every "
               "fact tied to a paper and a verbatim quote.")
EVIDENCE_COLUMNS = list(schemas.EvidenceRow.model_fields)
LITERATURE_COLUMNS = list(schemas.LiteratureRow.model_fields)
VARIANT_COLUMNS = list(schemas.VariantRow.model_fields)


@dataclass
class Release:
    evidence: list[dict]
    variants: list[dict]
    literature: list[dict]
    data_as_of: str
    include_all: bool
    n_extracted: int
    n_spot_checked: int
    verdicts: dict[str, int]

    def selection(self) -> str:
        if self.include_all:
            return (f"All {self.n_extracted} extracted records, including those the adversarial review "
                    "disputed, could not verify, or flagged for a person; see the status columns.")
        return (f"{len(self.evidence)} of {self.n_extracted} extracted records, those an adversarial review upheld, "
                "with every quote found in the paper and nothing flagged for a person.")

    def unkeyed(self) -> int:
        return sum(1 for e in self.evidence if not variants_mod.group_key(e))

    def variants_note(self) -> str:
        return (f"{len(self.variants)} variants from {len(self.evidence) - self.unkeyed()} records; the other "
                f"{self.unkeyed()}, mostly copy-number changes, have no variant key and are in the evidence table "
                f"only. {variants_mod.RESIDUAL_RISK}")

    def notice(self) -> str:
        checked = (f"{self.n_spot_checked} records spot-checked by a person" if self.n_spot_checked
                   else "not yet spot-checked by a person")
        return ("Machine-extracted by Claude from published papers and "
                f"{checked}. For research use only; not clinical advice. "
                "Every record cites its paper; check the paper before relying on it.")


def _spot_checked(st: Settings) -> int:
    """People's verdicts on records, once they exist.

    The gold files have no schema yet, so none may be counted: claiming a rate
    from files nothing validates is exactly the claim the notice must not make.
    """
    gold = list((st.curated_dir / "gold").glob("*.jsonl"))
    if gold:
        raise RuntimeError("data/curated/gold/ has files but no schema; define one in schemas.py "
                           "before publish may state a spot-check rate")
    return 0


def gather(st: Settings, *, include_all: bool = False) -> Release:
    ledger_rows = jsonl.read_tolerant(st.curated_dir / "ledger.jsonl", what="ledger")
    fold = fold_rows(ledger_rows)
    extractions = rows_mod.latest_extractions(st.extractions, st.extract_model)
    challenges = rows_mod.current_challenges(
        jsonl.read_tolerant(st.curated_dir / "challenges.jsonl", what="challenges"), st.extract_model)
    answers = normalize.load_answers(st.normalized)

    evidence: list[dict] = []
    verdicts: dict[str, int] = {}
    n_extracted = 0
    for r in extractions:
        meta = fold.get(r["pmid"], {})
        challenge = rows_mod.challenge_for(r, challenges)
        verdict_by_key = {v.get("record_key"): v for v in (challenge or {}).get("challenge", {}).get("verdicts", [])}
        g = r.get("grounding") or {}
        ungrounded = {u[0] for u in g.get("ungrounded") or []}
        for i, v in enumerate(r["extraction"].get("variants") or []):
            n_extracted += 1
            key = record_key(r["pmid"], i, v)
            verdict, flags = rows_mod.record_review(key, v, g, challenge)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if not include_all and not rows_mod.is_confident(verdict, flags):
                continue
            row = {f: v.get(f) for f in EVIDENCE_COLUMNS if f in v}
            row.update({
                "record_key": key, "pmid": r["pmid"], "pmcid": r.get("pmcid"), "doi": meta.get("doi"),
                "paper_type": r["extraction"].get("paper_type"),
                "citation": meta.get("citation"), "year": (meta.get("first_pub_date") or "")[:4],
                "quotes_grounded": v["as_written"] not in ungrounded,
                "figure_sourced": v["as_written"] in (g.get("figure_sourced") or []),
                "reviewer_verdict": verdict,
                "reviewer_problem": ((verdict_by_key.get(key) or {}).get("problem")
                                     if verdict == "disputed" else None),
                "review_flags": ", ".join(flags),
                "extracted_by": r.get("model"), "prompt_version": r["prompt_version"],
                "extracted_at": r["written_at"],
                **normalize.link(v, answers),
            })
            evidence.append(_blank_strings(row, schemas.EvidenceRow))
    # Along the protein, as the Variants table runs; records with no variant key last.
    def along(e: dict) -> tuple:
        pos = variants_mod.protein_position(e["protein_key"] or e["variant_key"]) if e["variant_key"] else None
        return (e["gene"], pos is None, pos or 0, e["variant_key"], e["as_written"].lower(), e["pmid"], e["record_key"])
    evidence.sort(key=along)

    released: dict[str, int] = {}
    for e in evidence:
        released[e["pmid"]] = released.get(e["pmid"], 0) + 1
    extracted = {r["pmid"] for r in extractions}
    literature = [_literature_row(ident, r, extracted, released) for ident, r in fold.items()]
    literature.sort(key=lambda r: (r["first_pub_date"], r["identity"]), reverse=True)

    # Every input that can change a released row dates the release: a commit
    # that only adds challenges changes which records are confident.
    stamps = [r.get("written_at", "") for r in ledger_rows] + [r["written_at"] for r in extractions] \
        + [c.get("written_at", "") for c in challenges.values()] + [a["written_at"] for a in answers.values()]
    return Release(evidence=evidence, variants=variants_mod.roll_up(evidence), literature=literature, data_as_of=max(stamps, default="")[:10],
                   include_all=include_all, n_extracted=n_extracted,
                   n_spot_checked=_spot_checked(st), verdicts=verdicts)


def _blank_strings(row: dict, model) -> dict:
    """Plain-string columns read empty rather than null, so CSV and JSONL agree."""
    for name, f in model.model_fields.items():
        if f.annotation is str and row.get(name) is None:
            row[name] = ""
    return row


def _literature_row(ident: str, r: dict, extracted: set[str], released: dict[str, int]) -> dict:
    triage = r.get("triage") or {}
    pmid = r.get("pmid") or ""
    return _blank_strings({
        "pmid": pmid, "identity": ident, "doi": r.get("doi"), "title": r.get("title"),
        "journal": r.get("journal"), "first_pub_date": r.get("first_pub_date"),
        "citation": r.get("citation"), "open_access": r.get("is_open_access"),
        "found_by": ", ".join(sorted(r.get("found_by") or [])),
        "gate_kept": triage.get("keep") if triage else None,
        "gate_reason": triage.get("llm_reason"),
        "status": paper_status(r, extracted=pmid in extracted),
        "n_records": released.get(pmid, 0),
    }, schemas.LiteratureRow)


def _table_schema(model) -> dict:
    """Frictionless Table Schema from a row model, so column docs come from the code."""
    kinds = {"str": "string", "int": "integer", "bool": "boolean", "list": "string"}
    fields = []
    for name, f in model.model_fields.items():
        ann = str(f.annotation)
        kind = next((v for k, v in kinds.items() if ann.startswith(k) or f"<class '{k}'>" in ann), "string")
        fields.append({"name": name, "type": kind, "description": f.description or ""})
    return {"fields": fields}


def datapackage(rel: Release) -> dict:
    def resource(name: str, model, n: int) -> dict:
        return {"name": name, "path": f"{name}.csv", "format": "csv", "mediatype": "text/csv",
                "encoding": "utf-8", "description": f"{model.__doc__.strip()} {n} rows. {rel.notice()}",
                "schema": _table_schema(model),
                "alternates": [f"{name}.jsonl"]}

    return {
        "profile": "tabular-data-package",
        "name": "col4a1a2-carrier-evidence",
        "title": "COL4A1/COL4A2 published carrier evidence",
        "version": __version__,
        "data_as_of": rel.data_as_of,
        "description": f"{rel.notice()} {rel.selection()}",
        "licenses": [{**LICENSE, "description": licence_notice()}],
        "sources": [{"title": "Europe PMC", "path": "https://europepmc.org"},
                    {"title": "PubTator 3", "path": "https://www.ncbi.nlm.nih.gov/research/pubtator3/"},
                    {"title": "Ensembl VEP, for variant keys and GRCh38 positions (cite as "
                              "https://www.ensembl.org/info/about/publications.html asks)",
                     "path": "https://www.ensembl.org"}],
        "resources": [resource("evidence", schemas.EvidenceRow, len(rel.evidence)),
                      {**resource("variants", schemas.VariantRow, len(rel.variants)),
                       "description": f"{schemas.VariantRow.__doc__.strip()} {rel.variants_note()} {rel.notice()}"},
                      resource("literature", schemas.LiteratureRow, len(rel.literature))],
    }


def licence_notice() -> str:
    """What CC BY covers and what it cannot: one wording for every output."""
    return ("Our data are released under CC BY 4.0; quotes from the papers stay under the original articles' "
            "copyright, not CC BY.")


def readme(rel: Release) -> str:
    """The notice for anyone holding the tables without the site."""
    return "\n".join([
        f"COL4A1/COL4A2 published carrier evidence, version {__version__}, data as of {rel.data_as_of}",
        "",
        rel.notice(),
        "",
        "evidence.csv / evidence.jsonl: " + rel.selection(),
        "variants.csv / variants.jsonl: " + rel.variants_note(),
        "literature.csv / literature.jsonl: every paper considered and what became of it.",
        "datapackage.json describes every column.",
        "",
        "Licence: " + licence_notice() + " " + LICENSE["path"],
        "",
    ])


def _embed(obj) -> str:
    """JSON safe inside a <script> element."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def nav(active: str) -> str:
    """The top bar, shared by every page; `active` is the page: "about", "data" or "downloads"."""
    from html import escape

    def link(href: str, label: str, key: str) -> str:
        current = ' aria-current="page"' if key == active else ""
        return f'<a href="{href}"{current}>{label}</a>'

    repo = (f'<a href="{escape(__repository__)}" rel="noopener">GitHub</a>' if __repository__ else "")
    return ('<header class="topnav"><div class="inner">'
            '<a class="brand" href="index.html"><span class="gene">COL4A1</span>/<span class="gene">COL4A2</span>'
            '<span class="rest"> carrier evidence</span></a><nav aria-label="Site">'
            + link("index.html", "About", "about") + link("data.html", "Data", "data")
            + link("downloads.html", "Downloads", "downloads") + repo
            + '<button type="button" class="theme" id="theme-toggle" aria-label="Switch theme">'
            '<svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
            '<svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/>'
            '<path d="M12 1.5v2.5M12 20v2.5M1.5 12H4M20 12h2.5M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/></svg>'
            "</button>"
            + "</nav></div></header>")


def footer(rel: Release) -> str:
    """The foot of every page: the version and the licence."""
    from html import escape

    licence = escape(licence_notice()).replace("CC BY 4.0", f'<a href="{LICENSE["path"]}">CC BY 4.0</a>', 1)
    return (f'  <footer>\n    <p>{licence}</p>\n    <p class="version">Version {escape(__version__)}, data as of {escape(rel.data_as_of)}</p>\n'
            "  </footer>\n")


def render(page: str, rel: Release, *, title: str, heading: str, fill: dict[str, str] | None = None,
           scripts: tuple[str, ...] = (), payload: dict | None = None, main_class: str = "") -> str:
    """One page: templates/<page>.html inside the shared layout.

    Every page opens the same way, with its heading and the notice, and ends
    with the shared footer. `heading` is HTML; `fill` replaces the page's own
    placeholders. Page scripts are plain files
    under js/, concatenated inside one function scope with the payload as `D`.
    The payload goes in last, so no placeholder inside extracted text is ever
    substituted.
    """
    from html import escape

    body = (TEMPLATES / f"{page}.html").read_text(encoding="utf-8")
    for key, value in (fill or {}).items():
        body = body.replace(f"@@{key}@@", value)
    script = ""
    if scripts:
        script = ('<script type="application/json" id="data-json">@@DATA@@</script>\n<script>\n(function () {\n'
                  '  "use strict";\n  var D = JSON.parse(document.getElementById("data-json").textContent);\n'
                  + "".join((JS / f"{n}.js").read_text(encoding="utf-8") for n in scripts) + "})();\n</script>\n")
    html = ((TEMPLATES / "layout.html").read_text(encoding="utf-8")
            .replace("@@TITLE@@", escape(title)).replace("@@DESCRIPTION@@", escape(DESCRIPTION))
            .replace("@@CSS@@", CSS.read_text(encoding="utf-8")).replace("@@NAV@@", nav(page))
            .replace(' class="@@CLASS@@"', f' class="{main_class}"' if main_class else "")
            .replace("@@HEADING@@", heading).replace("@@NOTICE@@", escape(rel.notice()))
            .replace("@@FOOTER@@", footer(rel)).replace("@@SCRIPT@@", script).replace("@@MAIN@@", body))
    return html.replace("@@DATA@@", _embed(payload)) if scripts else html


def steps(rel: Release) -> str:
    """How the release was made, in order, with this release's own numbers."""
    from html import escape

    kept = sum(1 for r in rel.literature if r["gate_kept"])
    read = sum(1 for r in rel.literature if r["status"] == "extracted")
    survive = (f"all {rel.n_extracted} records are shown here with the review's verdicts" if rel.include_all else
               f"{len(rel.evidence)} of {rel.n_extracted} records survive and are published")
    items = [
        f"<strong>Find.</strong> Europe PMC and PubTator are searched for papers naming either gene; "
        f"{len(rel.literature):,} so far, each recorded whether read or not.",
        f"<strong>Judge.</strong> Claude reads each title and abstract and keeps {kept:,} papers likely to report "
        f"carriers.",
        f"<strong>Read.</strong> Claude reads the {read:,} with open full text, supplements and figures included, "
        f"and records each variant with quotes that must appear verbatim in the paper.",
        f"<strong>Challenge.</strong> A second pass tries to refute each record against the same text; {survive}.",
        f"<strong>Group.</strong> Ensembl VEP normalizes each variant, and records are grouped into "
        f"{len(rel.variants)} variants with proband counts as ranges.",
    ]
    more = (f'<p>Methods: <a href="{escape(__repository__)}/blob/main/docs/methods.md">docs/methods.md</a>.</p>'
            if __repository__ else "")
    return '<ol class="steps">' + "".join(f"<li>{i}</li>" for i in items) + "</ol>" + more


def home(rel: Release) -> str:
    """The About page: what this is, the protein map, and how it was made."""
    keep = ("variant_id", "gene", "protein_position", "n_papers", "probands_min", "probands_max", "variant_types")
    payload = {
        "variants": [{k: v[k] for k in keep} for v in rel.variants],
        "proteins": variants_mod.PROTEINS,
        "counts": {"variants": len(rel.variants), "papers": len({e["pmid"] for e in rel.evidence}),
                   "considered": len(rel.literature),
                   "watch": sum(1 for r in rel.literature if r["status"] == "kept_not_read")},
    }
    return render("about", rel, title="COL4A1/COL4A2 carrier evidence",
                  heading='<span class="gene">COL4A1</span> and <span class="gene">COL4A2</span> carriers in the '
                          'published literature', fill={"STEPS": steps(rel)},
                  scripts=("common", "map"), payload=payload)


def downloads_page(rel: Release, data: Path) -> str:
    from col4a1a2.publish.downloads import body

    return render("downloads", rel, title="Downloads: COL4A1/COL4A2 carrier evidence", heading="Downloads",
                  fill={"BODY": body(rel, data)}, main_class="doc")


def site(rel: Release) -> str:
    """The Data page: the three tables."""
    keep = ("pmid", "title", "journal", "first_pub_date", "status", "gate_reason", "n_records")
    payload = {
        "include_all": rel.include_all, "n_extracted": rel.n_extracted, "verdicts": rel.verdicts,
        "evidence": rel.evidence,
        "variants": rel.variants, "variants_note": rel.variants_note(),
        # Hover text and column guides come from the row models and vocabularies,
        # the same descriptions datapackage.json carries, so the page cannot drift.
        "columns": {name: {f: info.description or "" for f, info in model.model_fields.items()}
                    for name, model in (("variants", schemas.VariantRow), ("evidence", schemas.EvidenceRow),
                                        ("literature", schemas.LiteratureRow))},
        "glossary": {"phenotype_systems": PHENOTYPE_SYSTEMS, "variant_types": VARIANT_TYPES},
        "statuses": STATUS_MEANINGS,
        "literature": [{k: r[k] for k in keep} for r in rel.literature],
    }
    lede = ("One row per variant per paper, with the review's verdict." if rel.include_all
            else "One row per variant per paper that passed review.")
    return render("data", rel, title="Data: COL4A1/COL4A2 carrier evidence", heading="Data",
                  fill={"EVIDENCE_LEDE": lede}, scripts=("common", "tables"),
                  payload=payload)


def write(rel: Release, out: Path) -> list[Path]:
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows, cols in (("evidence", rel.evidence, EVIDENCE_COLUMNS),
                             ("variants", rel.variants, VARIANT_COLUMNS),
                             ("literature", rel.literature, LITERATURE_COLUMNS)):
        jsonl.write(data / f"{name}.jsonl", rows)
        jsonl.write_table(data / f"{name}.csv", cols, rows)
        written += [data / f"{name}.jsonl", data / f"{name}.csv"]
    schemas.write_document(data / "datapackage.json", datapackage(rel))
    # The tables cannot carry the notice themselves without breaking every CSV
    # parser, so it travels beside them in the same directory.
    jsonl.replace_atomically(data / "README.txt", readme(rel))
    jsonl.replace_atomically(out / "index.html", home(rel))
    jsonl.replace_atomically(out / "data.html", site(rel))
    # After the data files, whose sizes it states.
    jsonl.replace_atomically(out / "downloads.html", downloads_page(rel, data))
    return written + [data / "datapackage.json", data / "README.txt",
                      out / "index.html", out / "data.html", out / "downloads.html"]
