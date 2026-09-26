"""Deterministic answer validation for the Baseline service (no LLM judge).

Checks a synthesized answer against the evidence that was actually collected: empty answer,
required evidence, numeric and date grounding, reference validity, contradictions with the
evidence, missing-vs-none action items, and record-level RM/status wording. It looks for
obvious violations only; it is not a proof that every claim is correct.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

_CITE_MEETING = re.compile(r"\[meeting:\s*(\d+)\s*,\s*(\d{4}-\d{2}-\d{2})\s*\]", re.I)
_BRACKET = re.compile(r"\[([^\[\]]+)\]")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_ID = re.compile(r"\b(?:[A-Za-z]\d{5}|DL\d+)\b")
_NUM = re.compile(r"(?<![\w.])(?P<sign>[-−–])?\$?(?P<num>\d[\d,]*(?:\.\d+)?)(?P<pct>%)?(?:\s?(?P<scale>thousand|million|billion|trillion|bn|k|m|b)\b)?", re.I)
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s+")
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12}
_UNAVAILABLE = re.compile(r"\b(?:not available|unavailable|cannot be determined|can't be determined|no data (?:is |was )?available|is not provided|are not provided|does not exist)\b", re.I)
_NO_ACTION = re.compile(r"\bno\s+(?:recorded\s+)?(?:action[\s-]*items?|actions?|follow[\s-]?ups?)\b", re.I)
_NO_ACTION_OK_AFTER = re.compile(r"^\W{0,3}(?:\w+\s+){0,3}?(?:recorded|listed|on record|available|provided)", re.I)
_THERE_WERE_NONE = re.compile(r"\b(?:there\s+(?:were|was|are|is)\s+no\s+action|had\s+no\s+action|with\s+no\s+action|without\s+(?:any\s+)?action[\s-]*items?)\b", re.I)
_RECORD_LEVEL = [
    (re.compile(r"\b(?:the|its|their|his|her)\s+(?:relationship manager|account manager|RM)\s+(?:is|was)\b", re.I), "singular RM for a client"),
    (re.compile(r"\bclient\s+[A-Za-z]\d{5}(?:'s)?\s+(?:relationship manager|account manager|RM)\s+(?:is|was)\b", re.I), "singular RM for a client"),
    (re.compile(r"\b(?:relationship manager|account manager|RM)\s+(?:of|for)\s+(?:the\s+)?(?:client\s+)?(?:[A-Za-z]\d{5}|it|them)\s+(?:is|was)\b", re.I), "singular RM for a client"),
    (re.compile(r"\b(?:has|have)\s+(?:one|a single|only one)\s+(?:relationship manager|account manager|RM)\b", re.I), "singular RM for a client"),
    (re.compile(r"\bis\s+managed\s+by\b", re.I), "single manager stated"),
    (re.compile(r"\bclient\s+status\s+(?:is|was)\b", re.I), "client-wide status"),
    (re.compile(r"\b(?:the client|client\s+[A-Za-z]\d{5}|it)\s+(?:is|was)\s+(?:currently\s+)?(?:an?\s+)?(?:Active|Dormant|Prospect|Closed)\b", re.I), "client-wide status"),
    (re.compile(r"\bmeetings?\s+(?:were\s+)?(?:held|led|run|attended)\s+by\s+(?:relationship manager|RM)\b", re.I), "meeting attributed to an RM"),
]


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    warnings: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()          # why it is invalid
    checks: tuple[dict, ...] = ()

    @property
    def invalid(self) -> bool:
        return not self.valid

    def to_dict(self) -> dict:
        return {"valid": self.valid, "invalid": self.invalid, "warnings": list(self.warnings), "reasons": list(self.reasons), "checks": list(self.checks)}


def _flatten(values: Iterable[Any]) -> Iterable[Any]:
    for v in values:
        if isinstance(v, (list, tuple)):
            yield from _flatten(v)
        else:
            yield v


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(_DATE.sub(" ", text)):
        try:
            out.append(float(m.group("num").replace(",", "")))
        except ValueError:
            pass
    return out


def evidence_facts(structured, meeting, question: str) -> tuple[list[float], str, set[Any]]:
    """(numbers, lowercase text, non-null structured values) that the answer may use."""
    numbers: list[float] = []
    texts: list[str] = [question]
    values: set[Any] = set()
    if structured is not None:
        for v in _flatten(structured.rows):
            if v is None:
                continue
            values.add(v)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                numbers.append(float(v))
            else:
                texts.append(str(v))
        numbers += [float(structured.row_count), float(len(structured.rows))]
    if meeting is not None:
        for c in (meeting.matched_count, meeting.filtered_meeting_count, meeting.top_k, len(meeting.hits)):
            if c is not None:
                numbers.append(float(c))
        for h in meeting.hits:
            numbers += [float(h.meeting_id), float(h.group_id), float(h.rank), float(h.summary_chars)]
            texts += [h.meeting_date, h.snippet, h.action_items or "", h.company, h.sector, h.region, h.deal_size_estimate or "", " ".join(h.matched_terms)]
    text = " ".join(texts)
    numbers += _numbers_in(re.sub(_ID, " ", text))
    return numbers, text.lower(), values


def _grounded(n: float, decimals: int, scale: float, pct: bool, evidence: list[float]) -> bool:
    shown = abs(n * scale)
    tol = 0.5 * 10 ** (-decimals) * scale + 1e-9
    for v in evidence:
        for cand in (abs(v), abs(v) * 100) if pct else (abs(v),):
            if abs(shown - cand) <= tol:
                return True
    return False


def validate_answer(answer: Optional[str], route: str, structured=None, meeting=None, question: str = "", *,
                    structured_no_data: bool = False, meeting_no_data: bool = False) -> ValidationResult:
    """`structured` / `meeting` are the evidence objects passed to synthesis (None when that side was not used)."""
    checks: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    def add(name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        checks.append({"check": name, "passed": ok, "severity": severity, "detail": detail})
        if not ok:
            (errors if severity == "error" else warnings).append(f"{name}: {detail}")

    text = answer or ""
    add("answer is not empty", bool(text.strip()), "the answer is empty")
    if not text.strip():
        return ValidationResult(False, tuple(warnings), tuple(errors), tuple(checks))

    # -- required evidence, and evidence consistency with the status
    need_structured = route in ("investment", "performance", "hybrid")
    need_meeting = route in ("meeting", "hybrid")
    if need_structured:
        have = structured is not None or structured_no_data
        add("structured evidence present", have, f"a {route} answer needs structured evidence")
        if structured is not None:
            add("structured evidence is a successful query", structured.success, f"structured outcome was {structured.outcome}")
    if need_meeting:
        have = meeting is not None or meeting_no_data
        add("meeting evidence present", have, f"a {route} answer needs meeting evidence")
        if meeting is not None:
            add("meeting evidence is a valid retrieval", meeting.success, f"meeting outcome was {meeting.outcome}")
    if route not in ("investment", "performance", "meeting", "hybrid"):
        add("route supports an answer", False, f"route {route!r} cannot produce an answer")

    numbers, evidence_text, values = evidence_facts(structured, meeting, question)
    hits = {h.meeting_id: h for h in (meeting.hits if meeting is not None else ())}
    tables = set(structured.source_tables) if structured is not None else set()

    # -- references
    bad_refs, seen_meeting, seen_struct = [], False, False
    for inner in (m.group(1) for m in _BRACKET.finditer(text)):
        low = inner.strip().lower()
        if low in ("investments", "performance"):
            seen_struct = True
            if low not in tables:
                bad_refs.append(f"[{inner}] cites a source that was not used ({sorted(tables) or 'none'})")
        elif low.startswith("meeting"):
            mm = _CITE_MEETING.fullmatch(f"[{inner}]")
            if not mm:
                bad_refs.append(f"[{inner}] is not in the form [meeting: <id>, <YYYY-MM-DD>]")
                continue
            seen_meeting = True
            hit = hits.get(int(mm.group(1)))
            if hit is None:
                bad_refs.append(f"meeting {mm.group(1)} is not in the returned evidence")
            elif hit.meeting_date != mm.group(2):
                bad_refs.append(f"meeting {mm.group(1)} has date {hit.meeting_date}, not {mm.group(2)}")
    add("references point to returned evidence", not bad_refs, "; ".join(bad_refs))
    if structured is not None and structured.rows:
        add("structured facts are referenced", seen_struct, "no [investments]/[performance] reference", "warning")
    if hits:
        add("meeting evidence is referenced", seen_meeting, "no [meeting: id, date] reference", "warning")

    # -- dates and numbers must come from the evidence
    plain = _CITE_MEETING.sub(" ", text)
    plain = re.sub(r"\[(?:investments|performance)\]", " ", plain, flags=re.I)
    bad_dates = sorted({d for d in _DATE.findall(plain) if d not in evidence_text})
    add("dates come from the evidence", not bad_dates, f"dates not in the evidence: {bad_dates}")
    body = _LIST_MARKER.sub(" ", _ID.sub(" ", _DATE.sub(" ", plain)))
    ungrounded, soft = [], []
    for m in _NUM.finditer(body):
        raw = m.group("num")
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = _SCALE.get((m.group("scale") or "").lower(), 1.0)
        if _grounded(n, decimals, scale, bool(m.group("pct")), numbers):
            continue
        label = m.group(0).strip()
        if decimals == 0 and scale == 1.0 and (n <= 10 or (1900 <= n <= 2100)):
            soft.append(label)
        else:
            ungrounded.append(label)
    add("numbers come from the evidence", not ungrounded, f"numbers not found in the evidence: {ungrounded}")
    add("small integers and years are grounded", not soft, f"not found in the evidence: {soft}", "warning")

    # -- contradiction: says unavailable while the evidence holds values
    if structured is not None and values and _UNAVAILABLE.search(text):
        shown = [v for v in values if str(v).lower() in text.lower() or (isinstance(v, float) and any(_grounded(abs(x), 0, 1.0, False, [v]) for x in _numbers_in(text)))]
        if not shown:
            add("answer does not deny evidence it was given", False, "the answer says a value is unavailable, but the structured evidence contains values and none is used")
        else:
            add("answer does not deny evidence it was given", False, "the answer says something is unavailable while other values are present; check it is a different item", "warning")

    # -- action_items: NULL means not recorded
    null_hits = [h for h in hits.values() if h.action_items_state == "not_recorded"]
    if null_hits:
        bad = bool(_THERE_WERE_NONE.search(text))
        for m in _NO_ACTION.finditer(text):
            if not _NO_ACTION_OK_AFTER.match(text[m.end():m.end() + 40]):
                bad = True
        add("missing action items are not turned into 'none'", not bad, "the answer says there were no action items, but action_items is NULL (not recorded) for some returned meetings")
        if re.search(r"action[\s-]*items?", text, re.I):
            add("missing action items are described as not recorded", bool(re.search(r"not\s+recorded|isn't recorded|is missing|not been recorded", text, re.I)),
                "returned meetings have NULL action_items; say 'not recorded'", "warning")

    # -- record-level semantics
    bad = sorted({label for pattern, label in _RECORD_LEVEL if pattern.search(text)})
    add("RM and status stay record-level", not bad, f"the answer presents a record-level attribute as client-wide or single-valued: {bad}")

    return ValidationResult(not errors, tuple(warnings), tuple(errors), tuple(checks))
