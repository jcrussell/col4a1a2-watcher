#!/usr/bin/env python
"""Export JSON Schema for every artefact this project writes.

    uv run col4a1a2-dev export

`schemas/` is documentation for anyone consuming the published data without
this codebase: one file per artefact plus an index naming the format. The
committed output is checked against the models by `tests/test_schemas.py`, so a
model change with no re-export is a test failure rather than stale
documentation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from col4a1a2.config import REPO

from col4a1a2 import schemas

OUT = REPO / "schemas"


def build() -> dict[str, dict]:
    """Return {artefact filename: (schema path, schema document)} as a dict of
    plain data, so the test can compare without writing anything."""
    files: dict[str, dict] = {}
    index: dict[str, dict] = {}
    for name, model in sorted(schemas.MODELS.items()):
        stem = name.rsplit(".", 1)[0]
        is_rows = name.endswith(".jsonl")
        doc = model.model_json_schema(by_alias=True)
        doc["title"] = model.__name__
        first_line = (model.__doc__ or "").strip().split("\n")[0]
        doc["description"] = (
            f"{'One row of' if is_rows else 'The whole document'} {name}. {first_line}".strip()
        )
        files[f"{stem}.schema.json"] = doc
        index[name] = {"schema": f"schemas/{stem}.schema.json",
                       "model": model.__name__,
                       "format": "jsonl" if is_rows else "json"}
    files["index.json"] = index
    return files


def main(argv: list[str] | None = None) -> int:
    OUT.mkdir(exist_ok=True)
    files = build()
    for rel, doc in files.items():
        (OUT / rel).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(files)} files to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
