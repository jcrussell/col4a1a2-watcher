#!/usr/bin/env python
"""Check that a Pydantic schema is accepted by the structured-outputs grammar.

    uv run col4a1a2-dev probe col4a1a2.extract.schema:PaperExtraction

Structured outputs compile the schema into a grammar with a size budget, and
exceeding it fails the whole request with `400 Schema is too complex` rather
than degrading. Run this before committing a schema change. One tiny call,
so the cost is negligible. Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import importlib
import sys
import time


import anthropic

PROBE = "PMID 1. Title: test paper. Abstract: no variants are reported."


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or ":" not in argv[0]:
        print(__doc__)
        return 2
    module, name = argv[0].rsplit(":", 1)
    model_cls = getattr(importlib.import_module(module), name)

    from col4a1a2.extract.llm import schema_shape

    sh = schema_shape(model_cls)
    print(f"{name}: {sh['properties']} properties, {sh['bytes']} bytes, {sh['anyOf']} anyOf, "
          f"{sh['objects']} objects, {sh['enums']} enums, {sh['arrays']} arrays")

    from col4a1a2.cli import make_anthropic
    from col4a1a2.config import load_settings

    st = load_settings()
    client = make_anthropic(st)
    t0 = time.time()
    try:
        resp = client.beta.messages.parse(
            model=st.extract_model,
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
