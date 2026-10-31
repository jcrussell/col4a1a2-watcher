"""System prompt and user-content assembly for extraction."""

from __future__ import annotations

from dataclasses import dataclass, field

from col4a1a2.sources.fulltext import TextRegion
from pathlib import Path

PROMPT_VERSION = "v1"
_PROMPT_DIR = Path(__file__).parent / "prompts"


def system_prompt(version: str = PROMPT_VERSION) -> str:
    return (_PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


@dataclass
class PaperInput:
    pmid: str
    pmcid: str | None
    title: str
    text: str
    abstract_only: bool
    pubtator_hints: list[dict] = field(default_factory=list)
    journal: str | None = None
    pub_date: str | None = None
    # Labelled spans of `text`, used by grounding to attribute each quote to
    # author body text, a parsed supplement, or a model figure transcription.
    regions: list[TextRegion] = field(default_factory=list)


def user_content(p: PaperInput) -> str:
    hints = ""
    if p.pubtator_hints:
        lines = [f"- {h['type']}: {h['text']}" + (f" [{h['identifier']}]" if h.get("identifier") else "") for h in p.pubtator_hints[:60]]
        hints = "\n\nAUTOMATED ENTITY MENTIONS (PubTator 3; hints only, may be wrong):\n" + "\n".join(lines)
    header = (
        f"PMID: {p.pmid}\nPMCID: {p.pmcid or 'none'}\nJOURNAL: {p.journal or 'unknown'}\nDATE: {p.pub_date or 'unknown'}\n"
        f"INPUT TYPE: {'TITLE AND ABSTRACT ONLY' if p.abstract_only else 'FULL TEXT'}"
    )
    return f"{header}{hints}\n\n=== PAPER TEXT START ===\n{p.text}\n=== PAPER TEXT END ===\n\nExtract the PaperExtraction record for this paper."
