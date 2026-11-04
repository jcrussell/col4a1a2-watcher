"""The downloads page: what each file holds, how its rows were chosen, and every column.

Built server-side, with no script: it is documentation, and it has to read
correctly wherever the files are mirrored. Column meanings come from the row
models, the same text `datapackage.json` carries; the prose here explains what
the models cannot, such as what one row stands for and what to read before
using a file.
"""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path

from col4a1a2 import __repository__, __version__, schemas
from col4a1a2.ledger import STATUS_MEANINGS
from col4a1a2.normalize import REFERENCE
from col4a1a2.publish.variants import SCREEN_MIN


def _size(path: Path) -> str:
    n = path.stat().st_size
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def _type(annotation) -> str:
    text = str(annotation)
    kind = ("list" if text.startswith("list") else "integer" if "int" in text
            else "true/false" if "bool" in text else "text")
    return kind + (", may be empty" if "None" in text else "")


def _columns(model) -> str:
    rows = "".join(f"<tr><td>{escape(name)}</td><td>{escape(_type(f.annotation))}</td>"
                   f"<td>{escape(f.description or '')}</td></tr>" for name, f in model.model_fields.items())
    return (f'<details class="help"><summary>All {len(model.model_fields)} columns</summary>'
            f'<div class="wrap"><table class="cols"><thead><tr><th>Column</th><th>Type</th><th>Meaning</th>'
            f"</tr></thead><tbody>{rows}</tbody></table></div></details>")


def _files(data: Path, name: str) -> str:
    return ('<div class="files">'
            f'<a class="btn file" href="data/{name}.csv" download>{name}.csv <small>{_size(data / f"{name}.csv")}, '
            'for spreadsheets</small></a>'
            f'<a class="btn file" href="data/{name}.jsonl" download>{name}.jsonl '
            f'<small>{_size(data / f"{name}.jsonl")}, for programs</small></a></div>')


def _facts(items: list[tuple[str, str]]) -> str:
    return '<dl class="facts">' + "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in items) + "</dl>"


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _section(id_: str, heading: str, main: str, aside: str, after: str = "") -> str:
    """One section: the explanation at the reading measure, with the parts to scan or act on
    (counts, files, the release citation) beside it on a wide screen; `after` spans both
    columns. Code goes in `main`, since a line of it would not fit the aside."""
    return (f'<section class="split" id="{id_}"><h2>{heading}</h2><div class="split-main">{main}</div>'
            f'<aside class="split-aside">{aside}</aside>{after}</section>')


def body(rel, data: Path) -> str:
    """The page's content; `data` is the written data directory, for file sizes."""
    tx = " / ".join(r.transcript for r in REFERENCE.values())
    np_ = " / ".join(r.protein for r in REFERENCE.values())
    status_counts = Counter(r["status"] for r in rel.literature)
    multi = sum(1 for v in rel.variants if v["n_papers"] > 1)
    spot = ("No record has been checked by a person yet, so extraction accuracy is unmeasured."
            if not rel.n_spot_checked else f"{rel.n_spot_checked} records have been checked by a person.")
    parts = []

    parts.append(_section(
        "which", "Which file do I want?",
        '<dl class="which">'
        '<dt><a href="#variants">variants</a></dt><dd>How much has been published about a variant? Start here.</dd>'
        '<dt><a href="#evidence">evidence</a></dt><dd>What exactly did a paper say?</dd>'
        '<dt><a href="#literature">literature</a></dt><dd>Which papers were looked at, read or not?</dd></dl>',
        '<p>Each comes as CSV and as JSON Lines with the same rows; see <a href="#formats">formats</a>.</p>'))

    parts.append(_section(
        "variants", "variants: one row per variant",
        f"<p>Records are normalized with Ensembl VEP and grouped by protein change ({escape(np_)}), or by coding "
        f"change ({escape(tx)}) when there is none. <code>coding_keys</code> lists every nucleotide change in a "
        f"group.</p>"
        "<p><strong>Counts are ranges.</strong> The same family is often reported more than once and papers carry "
        "no patient identifiers, so <code>probands_min</code> is the largest single paper's count and "
        "<code>probands_max</code> the sum over papers, leaving out families a paper says were reported before.</p>"
        "<h3>Read before using</h3>"
        + _list([(f"{rel.unkeyed()} records without a variant key, mostly copy-number changes, are in the "
                  "evidence table only."),
                 "Reviews are listed in <code>pointer_pmids</code> and add no counts.",
                 (f"Carriers from screens of {SCREEN_MIN:,} or more people are in <code>screened_carriers</code>, "
                  "not counted as probands."),
                 "A cohort reporting only totals can overlap earlier case reports invisibly; no range covers that."]),
        _facts([("Rows", f"{len(rel.variants)}, {multi} reported in more than one paper"), ("Key", "variant_id")])
        + _files(data, "variants"),
        _columns(schemas.VariantRow)))

    parts.append(_section(
        "evidence", "evidence: one row per variant per paper",
        "<p>What one paper says about one variant, with the sentences that say it. Claude extracted each record "
        "from the full text, supplements and figures, or from the abstract where no full text is open; a second pass then tried to refute it against the same text. "
        f"This file holds {escape(rel.selection())}</p>"
        "<h3>Read before using</h3>"
        + _list([escape(spot) + " Check the quotes against the paper before relying on a record.",
                 "<code>figure_sourced</code> marks a record read from a machine transcription of a figure.",
                 ("<code>abstract_only</code> marks a record read from the title and abstract alone, because "
                  "the full text is not open access; its counts and phenotypes are what the abstract states."),
                 ("<code>same_family_as_prior_report</code> means the paper says these patients were published "
                  "before, so their counts may repeat another row's.")]),
        _facts([("Rows", f"{len(rel.evidence)}, from {len({e['pmid'] for e in rel.evidence})} papers"),
                ("Key", "record_key")])
        + _files(data, "evidence"),
        _columns(schemas.EvidenceRow)))

    status_items = [f"<code>{escape(s)}</code> ({status_counts.get(s, 0)}): {escape(m)}"
                    for s, m in STATUS_MEANINGS.items()]
    parts.append(_section(
        "literature", "literature: every paper considered",
        "<p>Every paper found, read or not, so a missing variant can be told apart from an unread paper. Relevant "
        "papers with no open full text are <code>kept_not_read</code>: the watch list.</p>"
        "<h3>Statuses</h3>" + _list(status_items),
        _facts([("Rows", f"{len(rel.literature):,}"), ("Key", "identity: the PMID, else the DOI, else a title hash")])
        + _files(data, "literature"),
        _columns(schemas.LiteratureRow)))

    parts.append(_section(
        "formats", "Formats",
        "<pre>import pandas as pd\n"
        "variants = pd.read_json(\"variants.jsonl\", lines=True)\n"
        "evidence = pd.read_csv(\"evidence.csv\")</pre>"
        "<pre>variants &lt;- jsonlite::stream_in(file(\"variants.jsonl\"))\n"
        "evidence &lt;- read.csv(\"evidence.csv\")</pre>",
        _list(["<strong>CSV</strong> opens in any spreadsheet. List columns are joined with <code> | </code>.",
               "<strong>JSON Lines</strong> is one JSON object per line, for programs; lists stay lists.",
               ('<a href="data/datapackage.json"><strong>datapackage.json</strong></a> describes every file, '
                'column and type as a <a href="https://specs.frictionlessdata.io/data-package/">Frictionless Data '
                "Package</a>."),
               ('<a href="data/README.txt"><strong>README.txt</strong></a> carries the notice and licence with the '
                "files.")])))

    repo = f' (<a href="{escape(__repository__)}">GitHub</a>)' if __repository__ else ""
    parts.append(_section(
        "citing", "Citing",
        "<p>The facts are the papers': cite them from each row's <code>citation</code>, <code>pmid</code> and "
        "<code>doi</code>.</p>"
        "<p>Variant keys come from Ensembl VEP; cite it as "
        '<a href="https://www.ensembl.org/info/about/publications.html">Ensembl asks</a>.</p>',
        "<p>Cite this release as \u201cCOL4A1/COL4A2 published carrier evidence, version "
        f"{escape(__version__)}\u201d{repo}.</p>"))
    return "\n".join(parts)
