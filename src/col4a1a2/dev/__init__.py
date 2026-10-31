"""Developer and measurement tools: one command, one verb each.

    uv run col4a1a2-dev --help
    uv run col4a1a2-dev probe col4a1a2.extract.schema:PaperExtraction

Separate from `col4a1a2` itself because these measure things rather than run
the pipeline. Several spend money or hit the network, and the test suite never
runs them; `export.build()` is offline and is the one thing a test imports.
Verbs are imported only when invoked, so `--help` needs no API key and loads no
SDK.
"""

from __future__ import annotations

import importlib
import sys

VERBS = ("probe", "triage", "citations", "export", "fixtures")


def _summary(verb: str) -> str:
    doc = importlib.import_module(f"col4a1a2.dev.{verb}").__doc__ or ""
    return doc.strip().split("\n")[0]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: col4a1a2-dev <verb> [args]\n\nverbs:")
        for v in VERBS:
            try:
                line = _summary(v)
            except ImportError as e:  # a verb whose optional dependency is missing
                line = f"(unavailable: {e})"
            print(f"  {v:10} {line}")
        print("\nRun `col4a1a2-dev <verb> --help` or with no arguments for a verb's usage.")
        return 0 if argv else 2
    verb, rest = argv[0], argv[1:]
    if verb not in VERBS:
        print(f"unknown verb {verb!r}; choose from {', '.join(VERBS)}", file=sys.stderr)
        return 2
    return importlib.import_module(f"col4a1a2.dev.{verb}").main(rest)
