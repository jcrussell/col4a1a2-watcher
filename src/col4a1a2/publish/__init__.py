"""Build the public release from tracked files alone: two tables and a static site.

    uv run col4a1a2 publish [--out _site] [--all]

Inputs are `data/curated/` and the code, nothing under `data/raw/`, so CI can
build the release from a checkout. The output is deterministic: rows are
sorted, and the version is the date of the newest `written_at` in the data,
never the clock, so the same commit always builds the same bytes.

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

from col4a1a2 import jsonl, normalize, schemas
from col4a1a2.config import Settings
from col4a1a2.extract import rows as rows_mod
from col4a1a2.extract.challenge import record_key
from col4a1a2.ledger import fold_rows, paper_status

LICENSE = {"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/",
           "title": "Creative Commons Attribution 4.0, except the excerpt columns"}
# Columns that carry the papers' own words. They are quoted so each record can
# be checked against its source, and they stay under the article's copyright:
# we cannot license someone else's sentence, so CC BY covers everything else.
EXCERPT_COLUMNS = ("evidence_quotes", "reported_classification")
TEMPLATE = Path(__file__).with_name("index.html")
EVIDENCE_COLUMNS = list(schemas.EvidenceRow.model_fields)
LITERATURE_COLUMNS = list(schemas.LiteratureRow.model_fields)


@dataclass
class Release:
    evidence: list[dict]
    literature: list[dict]
    version: str
    include_all: bool
    n_extracted: int
    n_spot_checked: int
    verdicts: dict[str, int]

    def selection(self) -> str:
        if self.include_all:
            return (f"All {self.n_extracted} extracted records, including those the adversarial review "
                    "disputed, could not verify, or flagged for a person; see the status columns.")
        return (f"{len(self.evidence)} of {self.n_extracted} extracted records: those an adversarial review "
                "upheld against the paper's text, with every quote found verbatim and nothing flagged for a person.")

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
    evidence.sort(key=lambda e: (e["gene"], e["as_written"].lower(), e["pmid"], e["record_key"]))

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
    return Release(evidence=evidence, literature=literature, version=max(stamps, default="")[:10],
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
        "version": rel.version,
        "description": f"{rel.notice()} {rel.selection()}",
        "licenses": [{**LICENSE, "description": licence_notice()}],
        "sources": [{"title": "Europe PMC", "path": "https://europepmc.org"},
                    {"title": "PubTator 3", "path": "https://www.ncbi.nlm.nih.gov/research/pubtator3/"},
                    {"title": "Ensembl VEP, for variant keys and GRCh38 positions (cite as "
                              "https://www.ensembl.org/info/about/publications.html asks)",
                     "path": "https://www.ensembl.org"}],
        "resources": [resource("evidence", schemas.EvidenceRow, len(rel.evidence)),
                      resource("literature", schemas.LiteratureRow, len(rel.literature))],
    }


def licence_notice() -> str:
    """What CC BY covers and what it cannot: one wording for every output."""
    cols = " and ".join(EXCERPT_COLUMNS)
    return (f"Our own fields are released under CC BY 4.0 ({LICENSE['path']}). "
            f"The {cols} columns are short verbatim excerpts from the cited papers, "
            "included so each record can be checked against its source; they remain under "
            "the copyright and licence of the original article and are not covered by CC BY. "
            "Titles and journal names are bibliographic metadata as indexed by Europe PMC.")


def readme(rel: Release) -> str:
    """The notice for anyone holding the tables without the site."""
    return "\n".join([
        "COL4A1/COL4A2 published carrier evidence, version " + rel.version,
        "",
        rel.notice(),
        "",
        "evidence.csv / evidence.jsonl: " + rel.selection(),
        "literature.csv / literature.jsonl: every paper considered and what became of it.",
        "datapackage.json describes every column.",
        "",
        "Licence: " + licence_notice(),
        "",
    ])


def _embed(obj) -> str:
    """JSON safe inside a <script> element."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def site(rel: Release) -> str:
    keep = ("pmid", "title", "journal", "first_pub_date", "status", "gate_reason", "n_records")
    payload = {
        "version": rel.version, "notice": rel.notice(), "selection": rel.selection(),
        "include_all": rel.include_all, "n_extracted": rel.n_extracted, "verdicts": rel.verdicts,
        "evidence": rel.evidence,
        "literature": [{k: r[k] for k in keep} for r in rel.literature],
    }
    from html import escape

    # The data goes in last, so no placeholder inside extracted text is ever
    # substituted.
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("@@NOTICE@@", escape(rel.notice()))
            .replace("@@SELECTION@@", escape(rel.selection()))
            .replace("@@VERSION@@", escape(rel.version))
            .replace("@@LICENCE@@", escape(licence_notice()).replace(
                LICENSE["path"], f'<a href="{LICENSE["path"]}">{LICENSE["path"]}</a>'))
            .replace("@@DATA@@", _embed(payload)))


def write(rel: Release, out: Path) -> list[Path]:
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows, cols in (("evidence", rel.evidence, EVIDENCE_COLUMNS),
                             ("literature", rel.literature, LITERATURE_COLUMNS)):
        jsonl.write(data / f"{name}.jsonl", rows)
        jsonl.write_table(data / f"{name}.csv", cols, rows)
        written += [data / f"{name}.jsonl", data / f"{name}.csv"]
    schemas.write_document(data / "datapackage.json", datapackage(rel))
    # The tables cannot carry the notice themselves without breaking every CSV
    # parser, so it travels beside them in the same directory.
    jsonl.replace_atomically(data / "README.txt", readme(rel))
    jsonl.replace_atomically(out / "index.html", site(rel))
    return written + [data / "datapackage.json", data / "README.txt", out / "index.html"]
