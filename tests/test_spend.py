import json

from col4a1a2 import jsonl
from col4a1a2.config import Prices
from col4a1a2.extract.spend import tally

PRICES = {"opus": Prices(input=4, output=20, cache_read=0.2, cache_write=5)}


def _archive(root, written_at, site="extract", model="opus", **usage):
    d = root / written_at[:7]
    d.mkdir(parents=True, exist_ok=True)
    rec = {"digest": "d" + written_at, "written_at": written_at, "site": site, "requested_model": model,
           "response": {"model": model, "stop_reason": "end_turn", "usage": usage}}
    (d / f"d{written_at[:10]}.response.json").write_text(json.dumps(rec))


def _row(curated, written_at, model="opus", **usage):
    jsonl.append(curated / "extractions.jsonl", {"written_at": written_at, "model": model,
                                                 "requested_model": model, "usage": usage}, validate=False)


def test_rows_from_before_the_archive_are_estimated_and_later_ones_are_not_counted_twice(tmp_path):
    responses, curated = tmp_path / "responses", tmp_path / "curated"
    curated.mkdir()
    _row(curated, "2026-10-01T00:00:00+00:00", input=1_000_000)
    _archive(responses, "2026-11-03T00:00:00+00:00", input_tokens=1_000_000, output_tokens=1_000_000)
    _row(curated, "2026-11-03T00:00:00+00:00", input=1_000_000, output=1_000_000)  # the archived call's row

    t = tally(responses, curated, PRICES.get)

    assert t["archive_starts"] == "2026-11-03T00:00:00+00:00"
    assert t["estimated_usd"] == 4
    assert t["total_usd"] == 4 + 24
    assert sum(r["calls"] for r in t["rows"]) == 2


def test_a_model_without_a_price_is_named_and_left_out(tmp_path):
    curated = tmp_path / "curated"
    curated.mkdir()
    _row(curated, "2026-10-01T00:00:00+00:00", model="mystery", input=10)

    t = tally(tmp_path / "responses", curated, PRICES.get)

    assert t["unpriced"] == ["mystery"] and t["total_usd"] == 0 and t["archive_starts"] is None
