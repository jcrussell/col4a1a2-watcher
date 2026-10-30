#!/usr/bin/env python
"""Check that a Pydantic schema is accepted by the structured-outputs grammar.

    uv run python tools/probe_schema.py col4a1.extract.schema:PaperExtraction

Structured outputs compile the schema into a grammar with a size budget, and
exceeding it fails the whole request with `400 Schema is too complex` rather
than degrading. Run this before committing a schema change. One tiny call,
so the cost is negligible. Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import anthropic  # noqa: E402

PROBE = "PMID 1. Title: test paper. Abstract: no variants are reported."


def main() -> int:
    if len(sys.argv) != 2 or ":" not in sys.argv[1]:
        print(__doc__)
        return 2
    module, name = sys.argv[1].rsplit(":", 1)
    model_cls = getattr(importlib.import_module(module), name)

    schema = json.dumps(model_cls.model_json_schema())
    print(f"{name}: {len(schema)} bytes, {schema.count('anyOf')} anyOf, "
          f"{schema.count(chr(34) + 'object' + chr(34))} objects, "
          f"{schema.count(chr(34) + 'enum' + chr(34))} enums, "
          f"{schema.count(chr(34) + 'array' + chr(34))} arrays")

    client = anthropic.Anthropic()
    t0 = time.time()
    try:
        resp = client.beta.messages.parse(
            model="claude-opus-5-5",
            max_tokens=2000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": PROBE}],
            output_format=model_cls,
        )
    except anthropic.BadRequestError as e:
        print(f"REJECTED after {time.time() - t0:.0f}s: {e.message}")
        print("\nFlatten the schema: replace nullable unions with sentinel values, collapse\n"
              "lists of small objects into fixed fields, and inline single-use nested objects.")
        return 1
    print(f"ACCEPTED in {time.time() - t0:.0f}s (stop_reason={resp.stop_reason}, "
          f"{resp.usage.input_tokens} in / {resp.usage.output_tokens} out)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
