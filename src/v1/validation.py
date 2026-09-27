"""V1 validation: a thin wrapper around the frozen Baseline validator.

It changes no check. It only represents the new meeting evidence so the frozen checks can run:
  - aggregate evidence rows count as grounded evidence (numbers, dates) and `[meetings]` is a valid citation for them;
  - a `[meeting: id, date]` citation is valid when that pair is a row of the aggregate evidence;
  - numbers the code itself computed and showed to the model (the unmatched count) are grounded.
No LLM judge; no new rule about the answer's content. See docs/architecture/v1_contract.md section 6.
"""
from __future__ import annotations

import ast
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Iterable, Optional

from src.baseline.answer_validation import ValidationResult, validate_answer

_AGG_CITE = re.compile(r"\[\s*meetings\s*\]", re.I)
_MEETING_CITE = re.compile(r"\[\s*meeting:\s*(\d+)\s*,\s*(\d{4}-\d{2}-\d{2})\s*\]", re.I)
_NOT_FOUND = "numbers come from the evidence: numbers not found in the evidence: "


def _num(label: str) -> Optional[float]:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", label)
    return float(m.group(0).replace(",", "")) if m else None


def _aggregate_pairs(aggregate) -> set[tuple[int, str]]:
    cols = list(aggregate.columns)
    if "meeting_id" not in cols or "meeting_date" not in cols:
        return set()
    i, j = cols.index("meeting_id"), cols.index("meeting_date")
    return {(int(r[i]), str(r[j])) for r in aggregate.rows if r[i] is not None}


def validate_answer_v1(answer: Optional[str], route: str, structured=None, meeting=None, aggregate=None, question: str = "", *,
                       structured_no_data: bool = False, meeting_no_data: bool = False, extra_numbers: Iterable[float] = ()) -> ValidationResult:
    extra_errors: list[str] = []
    text = answer or ""
    cited_aggregate = bool(_AGG_CITE.search(text))
    eff_route, eff_structured, eff_meeting, eff_meeting_no_data = route, structured, meeting, meeting_no_data
    if aggregate is not None:
        pairs = _aggregate_pairs(aggregate)
        text = _AGG_CITE.sub(" ", text)
        text = _MEETING_CITE.sub(lambda m: " " if (int(m.group(1)), m.group(2)) in pairs else m.group(0), text)
        if structured is None:
            eff_route, eff_structured, eff_meeting = "investment", aggregate, None      # the aggregate is the structured evidence of a meeting-route answer
        else:                                                                             # hybrid: two independent structured results, side by side
            eff_structured = SimpleNamespace(rows=tuple(structured.rows) + tuple(aggregate.rows), row_count=structured.row_count + aggregate.row_count,
                                             source_tables=tuple(structured.source_tables) + ("meetings",), success=structured.success and aggregate.success,
                                             outcome=structured.outcome if not structured.success else aggregate.outcome)
            eff_meeting, eff_meeting_no_data = None, True                                 # the meeting side is carried by the aggregate rows
    elif _AGG_CITE.search(text):
        extra_errors.append("references point to returned evidence: [meetings] is cited but no aggregate meeting evidence was used")
        text = _AGG_CITE.sub(" ", text)
    res = validate_answer(text, eff_route, eff_structured, eff_meeting, question, structured_no_data=structured_no_data, meeting_no_data=eff_meeting_no_data)

    allowed = {float(x) for x in extra_numbers}
    reasons, checks = [], [dict(c) for c in res.checks]
    for reason in res.reasons:
        if allowed and reason.startswith(_NOT_FOUND):
            try:
                labels = ast.literal_eval(reason[len(_NOT_FOUND):])
            except (ValueError, SyntaxError):
                labels = None
            if isinstance(labels, list):
                left = [lab for lab in labels if _num(lab) is None or _num(lab) not in allowed]
                if not left:
                    for c in checks:
                        if c["check"] == "numbers come from the evidence":
                            c["passed"], c["detail"] = True, "numbers not in the evidence are the deterministic counts shown to the model: " + ", ".join(labels)
                    continue
                reason = _NOT_FOUND + repr(left)
        reasons.append(reason)
    reasons += extra_errors
    warnings = list(res.warnings)
    if aggregate is not None:
        warnings = [w for w in warnings if not w.startswith("structured facts are referenced")]     # the frozen check looks for [investments]/[performance]
        if aggregate.rows and not cited_aggregate:
            warnings.append("aggregate meeting facts are referenced: no [meetings] reference")
    return ValidationResult(not reasons, tuple(warnings), tuple(reasons), tuple(checks))
