"""V1 answer synthesis: the Baseline prompt and structured serialization, extended for the new meeting evidence.

Adds: meeting metadata fields only when the question asks for them, the unmatched count, the search mode
(lexical or newest-first listing), and aggregate evidence with its own citation [meetings]. Nothing else about
the prompt changes. The Baseline modules are imported, not modified.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from src.baseline.synthesis import MAX_EVIDENCE_CHARS, RULES as BASELINE_RULES, SynthesisResult, _structured_lines

_EXTRA_RULES = """9. Cite with [meetings] ONLY facts taken from the AGGREGATE MEETING EVIDENCE block (you may name a meeting id in the text). Facts from the MEETING EVIDENCE block, including its counts (meetings in scope, meetings with a text match), are cited with [meeting: <meeting_id>, <YYYY-MM-DD>] of a meeting shown there; never use [meetings] for them.
10. A 'newest-first listing' is not a topical match: do not say those meetings match, mention or discuss anything. Use it only for dates, order and attributes.
11. Use only the meeting fields shown. A field that is not shown was not requested; do not guess it."""
RULES_V1 = BASELINE_RULES.replace("9. Be concise", _EXTRA_RULES + "\n12. Be concise")


def _unmatched(ev) -> Optional[int]:
    if ev.rank_method == "bm25" and ev.filtered_meeting_count is not None and ev.matched_count is not None:
        return ev.filtered_meeting_count - ev.matched_count
    return None


def unmatched_count(meeting) -> Optional[int]:
    """Deterministic: meetings in scope without a text match (lexical mode only)."""
    return _unmatched(meeting) if meeting is not None else None


def _meeting_lines(ev, n_hits: int, fields: tuple[str, ...]) -> list[str]:
    f = ev.filters
    scope = []
    if f.get("client_ids"):
        scope.append("client " + ", ".join(f["client_ids"]))
    if f.get("group_id") is not None:
        scope.append(f"group {f['group_id']} (membership from the meetings source only)")
    if f.get("meeting_date"):
        scope.append(f"date {f['meeting_date']}")
    listing = ev.rank_method == "date_desc"
    mode = "newest-first listing, not ranked by relevance and not a topical match" if listing else f"lexical search, {ev.rank_method or 'no search'}"
    head = f"MEETING EVIDENCE ({mode}; scope: {', '.join(scope) or 'all meetings'}; "
    if not listing:
        head += f"terms: {', '.join(ev.query.get('terms', [])) or 'none'}; "
    head += f"meetings in scope: {ev.filtered_meeting_count}; "
    if listing:
        head += f"showing {min(n_hits, len(ev.hits))} of {ev.filtered_meeting_count}"
    else:
        head += f"with a text match: {ev.matched_count}; shown: {min(n_hits, len(ev.hits))}"
        un = _unmatched(ev)
        if un is not None:
            head += f"; in scope without a text match: {un}"
    lines = [head + ")"]
    for h in ev.hits[:n_hits]:
        action = "NOT RECORDED (NULL)" if h.action_items_state == "not_recorded" else f'RECORDED: "{h.action_items}"'
        extra = []
        if "sector" in fields:
            extra.append(f"sector {h.sector}")
        if "region" in fields:
            extra.append(f"region {h.region}")
        if "stage" in fields:
            extra.append(f"stage {h.investment_stage}")
        if "deal_size" in fields:
            extra.append(f"deal size estimate {h.deal_size_estimate}")
        matched = f" | matched {','.join(h.matched_fields)}" if h.matched_fields else ""
        lines.append(f"meeting {h.meeting_id} | {h.meeting_date} | client {h.client_id} | group {h.group_id} | company {h.company}"
                     + "".join(f" | {e}" for e in extra) + f"{matched} | action_items: {action} | text: \"{h.snippet}\"")
    lines += [f"note: {n}" for n in list(ev.notes) + list(ev.entity_notes)]
    return lines


def _aggregate_lines(ev, n_rows: int) -> list[str]:
    lines = [f"AGGREGATE MEETING EVIDENCE (source: meetings metadata; outcome: {ev.outcome})", "rules applied: " + "; ".join(ev.rules_applied), f"columns: {', '.join(ev.columns)}"]
    lines += [f"row: {json.dumps(list(r), ensure_ascii=False)}" for r in ev.rows[:n_rows]]
    lines.append(f"row_count: {ev.row_count}; truncated: {ev.truncated}" + (f"; shown to you: {n_rows}" if n_rows < len(ev.rows) else ""))
    lines += [f"note: {n}" for n in ev.notes]
    return lines


def serialize_evidence(structured, meeting, aggregate, structured_note: Optional[str], meeting_note: Optional[str], fields: tuple[str, ...] = (),
                       max_chars: int = MAX_EVIDENCE_CHARS) -> tuple[str, bool]:
    """Compact evidence text; over the cap, lowest-ranked meeting hits and then trailing rows are dropped (as in Baseline)."""
    n_hits = len(meeting.hits) if meeting is not None else 0
    n_rows = len(structured.rows) if structured is not None else 0
    n_agg = len(aggregate.rows) if aggregate is not None else 0
    trimmed = False

    def render() -> str:
        blocks = []
        if structured is not None:
            blocks.append("\n".join(_structured_lines(structured, n_rows)))
        elif structured_note:
            blocks.append(f"STRUCTURED EVIDENCE\nnote: {structured_note}")
        if aggregate is not None:
            blocks.append("\n".join(_aggregate_lines(aggregate, n_agg)))
        if meeting is not None:
            blocks.append("\n".join(_meeting_lines(meeting, n_hits, fields)))
        elif aggregate is None and meeting_note:
            blocks.append(f"MEETING EVIDENCE\nnote: {meeting_note}")
        return "\n\n".join(blocks)

    text = render()
    while len(text) > max_chars and (n_hits > 1 or n_rows > 1 or n_agg > 1):
        if n_hits > 1:
            n_hits -= 1
        elif n_agg > 1:
            n_agg -= 1
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

{RULES_V1}

Route: {route}
Question: {question}

Resolved entities:
{entities}

Evidence:
{evidence_text}
"""


class V1Synthesizer:
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
