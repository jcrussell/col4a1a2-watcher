"""`cmd_extract` end to end against a fake client: resume, models, archive."""

import argparse
from pathlib import Path

from col4a1a2 import cli, jsonl
from col4a1a2.config import load_settings
from col4a1a2.extract.schema import PaperExtraction, PaperType
from col4a1a2.ledger import Ledger
from conftest import make_paper_row

CONFIG = Path(__file__).resolve().parents[1] / "config" / "col4a1a2.toml"


def _setup(tmp_path, monkeypatch, client):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(cli, "make_anthropic", lambda _st: client)
    st = load_settings(CONFIG, repo=tmp_path)
    snap = st.snapshot_root / "s1"
    snap.mkdir(parents=True)
    jsonl.write(snap / "papers.jsonl", [make_paper_row("1", text="TITLE: T", triage={"keep": True})])
    return st, snap


def _args(st, snap, **over):
    a = dict(settings=st, snapshot=str(snap), all=False, pmid=None, limit=None, model=None,
             effort=None, count_only=False, budget_usd=None, no_llm_cache=False)
    a.update(over)
    return argparse.Namespace(**a)


def _ex():
    return PaperExtraction(pmid="1", paper_type=PaperType.case_report,
                           input_was_abstract_only=False, has_variant_level_data=False)


def test_a_second_model_extracts_alongside_and_leaves_the_ledger_alone(tmp_path, monkeypatch, fake_anthropic):
    """`--model X` used to find the configured model's rows and skip every
    paper, because nothing in the resume key named a model."""
    client = fake_anthropic(default_output=_ex())
    st, snap = _setup(tmp_path, monkeypatch, client)
    assert cli.cmd_extract(_args(st, snap)) == 0
    assert cli.cmd_extract(_args(st, snap)) == 0
    assert len(client.calls) == 1, "the configured model resumes"

    assert cli.cmd_extract(_args(st, snap, model="other-model")) == 0
    assert len(client.calls) == 2, "a second model must run, not skip"
    rows = jsonl.read(snap / "extractions.jsonl")
    assert [r["requested_model"] for r in rows] == [st.extract_model, "other-model"]
    assert [r["pmid"] for r in cli.latest_extractions(snap / "extractions.jsonl", st)] == ["1"]
    assert cli.latest_extractions(snap / "extractions.jsonl", st)[0]["requested_model"] == st.extract_model
    events = [e for e in Ledger(st.curated_dir / "ledger.jsonl").rows if e.get("stage") == "extracted"]
    assert [e["requested_model"] for e in events] == [st.extract_model], \
        "the ledger tracks the configured model only"


def test_no_llm_cache_calls_again_and_the_new_row_is_current(tmp_path, monkeypatch, fake_anthropic):
    client = fake_anthropic(default_output=_ex())
    st, snap = _setup(tmp_path, monkeypatch, client)
    cli.cmd_extract(_args(st, snap))
    cli.cmd_extract(_args(st, snap, no_llm_cache=True))
    assert len(client.calls) == 2
    rows = jsonl.read(snap / "extractions.jsonl")
    current = cli.latest_extractions(snap / "extractions.jsonl", st)
    assert current[0]["written_at"] == max(r["written_at"] for r in rows)
    assert len(list(st.responses_dir.glob("*/*.response.json"))) == 2


def test_an_archived_answer_is_replayed_and_not_billed(tmp_path, monkeypatch, fake_anthropic, capsys):
    """Losing the extraction row but keeping the archive costs nothing to redo."""
    client = fake_anthropic(default_output=_ex())
    st, snap = _setup(tmp_path, monkeypatch, client)
    cli.cmd_extract(_args(st, snap))
    (snap / "extractions.jsonl").unlink()
    (st.curated_dir / "ledger.jsonl").unlink()
    capsys.readouterr()
    cli.cmd_extract(_args(st, snap))
    assert len(client.calls) == 1, "replayed from the archive"
    assert "$0.00 spent" in capsys.readouterr().out
