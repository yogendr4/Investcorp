"""V2 synthesis: the V1 prompt and serialization with an accurate header and one extra rule for fused (lexical + semantic) meeting evidence.

Everything else is reused from V1. The best semantic sentence is already part of each hit's text, so the frozen validator can ground
numbers and dates in it.
"""
from __future__ import annotations

from typing import Any, Optional

from src.baseline.synthesis import SynthesisResult
from src.v1 import synthesis as v1s

_V2_RULE = ("12. In a hybrid-retrieval list, a 'semantic match' sentence was retrieved by meaning, not by shared words: say what that sentence says, "
            "not that the meeting is proven relevant. The order is the fused rank; do not describe it as a date order.\n13. Be concise")
RULES_V2 = v1s.RULES_V1.replace("12. Be concise", _V2_RULE)
_OLD_HEADER = "MEETING EVIDENCE (lexical search, rrf;"
_NEW_HEADER = "MEETING EVIDENCE (hybrid retrieval: lexical BM25 + semantic embedding similarity, reciprocal rank fusion;"


def serialize_evidence(structured, meeting, aggregate, structured_note: Optional[str], meeting_note: Optional[str], fields: tuple[str, ...] = ()) -> tuple[str, bool]:
    text, trimmed = v1s.serialize_evidence(structured, meeting, aggregate, structured_note, meeting_note, fields)
    return text.replace(_OLD_HEADER, _NEW_HEADER), trimmed


def build_prompt(question: str, route: str, entity_lines: list[str], evidence_text: str) -> str:
    return v1s.build_prompt(question, route, entity_lines, evidence_text).replace(v1s.RULES_V1, RULES_V2)


class V2Synthesizer:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def synthesize(self, question: str, route: str, entity_lines: list[str], structured=None, meeting=None, aggregate=None,
                   structured_note: Optional[str] = None, meeting_note: Optional[str] = None, fields: tuple[str, ...] = ()) -> SynthesisResult:
        evidence, trimmed = serialize_evidence(structured, meeting, aggregate, structured_note, meeting_note, fields)
        prompt = build_prompt(question, route, entity_lines, evidence)
        result = self.adapter.run(prompt)
        llm = {"model": result.model, "cli_version": result.cli_version, "exit_code": result.exit_code, "timing": dict(result.timing),
               "error_class": result.error_class.value if result.error_class else None}
        if not result.success:
            return SynthesisResult(False, None, len(prompt), trimmed, llm, {"code": llm["error_class"], "message": result.error_message})
        return SynthesisResult(True, (result.result_text or "").strip(), len(prompt), trimmed, llm, None)
