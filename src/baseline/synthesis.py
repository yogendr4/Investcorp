"""Answer synthesis for the Baseline service: bounded evidence in, one Claude CLI call, answer text out.

The LLM sees only the question, the route, the resolved entities and the compact evidence below.
It gets no tools, no database and no application state. See docs/architecture/baseline_service.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

MAX_EVIDENCE_CHARS = 16_000

RULES = """Rules:
1. Answer ONLY from the evidence below. If it is not enough, say exactly what is missing. Use no outside knowledge.
2. Do not calculate new numbers. Use the values as given (you may round a value that is given, and say you rounded).
3. Cite every fact with a source reference in exactly this notation: [investments] or [performance] for structured facts (use the source named in the evidence), and [meeting: <meeting_id>, <YYYY-MM-DD>] for meeting facts, using only meeting ids and dates shown in the evidence. Never invent a reference.
4. Records, not clients: an RM or a ClientStatus belongs to a RECORD. Never say a client "has" one RM or one status; describe the records.
5. "action_items NOT RECORDED (NULL)" means the field is missing, not that there were no action items. Say "not recorded".
6. Meeting text says what was discussed. Do not claim that a meeting concerns a deal, an RM or another entity unless the evidence says so. Meetings have no RM field.
7. State plainly anything that is unavailable, ambiguous or unsupported by the evidence.
8. For a hybrid question keep structured facts and meeting evidence in separate paragraphs, and link them only when the same client_id appears in both.
9. Be concise: a few sentences or a short list. Return only the answer text."""


def _structured_lines(ev, n_rows: int) -> list[str]:
    lines = [f"STRUCTURED EVIDENCE (source: {', '.join(ev.source_tables) or 'none'}; outcome: {ev.outcome})",
             "rules applied: " + "; ".join(ev.rules_applied), f"columns: {', '.join(ev.columns)}"]
    lines += [f"row: {json.dumps(list(r), ensure_ascii=False)}" for r in ev.rows[:n_rows]]
    lines.append(f"row_count: {ev.row_count}; truncated: {ev.truncated}" + (f"; shown to you: {n_rows}" if n_rows < len(ev.rows) else ""))
    lines += [f"note: {n}" for n in ev.notes]
    return lines


def _meeting_lines(ev, n_hits: int) -> list[str]:
    f = ev.filters
    scope = []
    if f.get("client_ids"):
        scope.append("client " + ", ".join(f["client_ids"]))
    if f.get("group_id") is not None:
        scope.append(f"group {f['group_id']} (membership from the meetings source only)")
    if f.get("meeting_date"):
        scope.append(f"date {f['meeting_date']}")
    lines = [f"MEETING EVIDENCE (lexical search, {ev.rank_method or 'no search'}; scope: {', '.join(scope) or 'all meetings'}; terms: {', '.join(ev.query.get('terms', [])) or 'none'}; "
             f"meetings in scope: {ev.filtered_meeting_count}; with a text match: {ev.matched_count}; shown: {min(n_hits, len(ev.hits))})"]
    for h in ev.hits[:n_hits]:
        action = "NOT RECORDED (NULL)" if h.action_items_state == "not_recorded" else f'RECORDED: "{h.action_items}"'
        lines.append(f'meeting {h.meeting_id} | {h.meeting_date} | client {h.client_id} | group {h.group_id} | company {h.company} | matched {",".join(h.matched_fields)} | '
                     f'action_items: {action} | text: "{h.snippet}"')
    lines += [f"note: {n}" for n in list(ev.notes) + list(ev.entity_notes)]
    return lines


def serialize_evidence(structured, meeting, structured_note: Optional[str], meeting_note: Optional[str], max_chars: int = MAX_EVIDENCE_CHARS) -> tuple[str, bool]:
    """Compact evidence text. If it would exceed max_chars, the lowest-ranked meeting hits and then trailing structured rows are dropped."""
    n_hits = len(meeting.hits) if meeting is not None else 0
    n_rows = len(structured.rows) if structured is not None else 0
    trimmed = False

    def render() -> str:
        blocks = []
        if structured is not None:
            blocks.append("\n".join(_structured_lines(structured, n_rows)))
        elif structured_note:
            blocks.append(f"STRUCTURED EVIDENCE\nnote: {structured_note}")
        if meeting is not None:
            blocks.append("\n".join(_meeting_lines(meeting, n_hits)))
        elif meeting_note:
            blocks.append(f"MEETING EVIDENCE\nnote: {meeting_note}")
        return "\n\n".join(blocks)

    text = render()
    while len(text) > max_chars and (n_hits > 1 or n_rows > 1):
        if n_hits > 1:
            n_hits -= 1
        else:
            n_rows -= 1
        trimmed = True
        text = render()
    if trimmed:
        text += "\n\nnote: some evidence was left out to keep this within the size limit"
    return text, trimmed


def build_prompt(question: str, route: str, entity_lines: list[str], evidence_text: str) -> str:
    entities = "\n".join(entity_lines) if entity_lines else "none"
    return f"""You write the final answer for a data question, using only the evidence provided.

{RULES}

Route: {route}
Question: {question}

Resolved entities:
{entities}

Evidence:
{evidence_text}
"""


@dataclass(frozen=True)
class SynthesisResult:
    success: bool
    text: Optional[str] = None
    prompt_chars: int = 0
    evidence_trimmed: bool = False
    llm: Optional[dict] = None
    error: Optional[dict] = None


class Synthesizer:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def synthesize(self, question: str, route: str, entity_lines: list[str], structured=None, meeting=None,
                   structured_note: Optional[str] = None, meeting_note: Optional[str] = None) -> SynthesisResult:
        evidence, trimmed = serialize_evidence(structured, meeting, structured_note, meeting_note)
        prompt = build_prompt(question, route, entity_lines, evidence)
        result = self.adapter.run(prompt)
        llm = {"model": result.model, "cli_version": result.cli_version, "exit_code": result.exit_code, "timing": dict(result.timing),
               "error_class": result.error_class.value if result.error_class else None}
        if not result.success:
            return SynthesisResult(False, None, len(prompt), trimmed, llm, {"code": llm["error_class"], "message": result.error_message})
        text = (result.result_text or "").strip()
        return SynthesisResult(True, text, len(prompt), trimmed, llm, None)
