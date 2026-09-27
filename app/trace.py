"""Execution trace: a deterministic record of the pipeline STEPS a `BaselineResponse`/`V1Response` already took
(entity resolution, routing, evidence collection, validation, timing), never the model's hidden reasoning.

`TraceRecorder` is an abstraction so a future backend (e.g. LangSmith) could be added later without changing
`build_trace` or any UI code: only a new `TraceRecorder` subclass would be written. `LocalTraceRecorder` is the only
implementation here; it keeps events in memory for one run and is not persisted anywhere.

What a trace event is built from: the response's own dict, produced by the frozen service (`answer_question(...).to_dict()`).
Nothing here calls a component a second time or inspects anything the service does not already return.
"""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Keys removed from any metadata before it is ever stored or shown, wherever they occur (recursively).
_SENSITIVE_KEY = re.compile(r"email|token|secret|password|credential|^auth$|_auth$|auth_|nonauth|api[_-]?key|_org$|org_id|organization|account_owner|account_id|alias|owner", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[\w.-]+")
REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    """Recursively drop sensitive keys and mask email-shaped strings. Safe to call on any JSON-shaped value."""
    if isinstance(value, dict):
        return {k: (REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _EMAIL.sub("[email]", value)
    return value


@dataclass(frozen=True)
class TraceEvent:
    """One deterministic pipeline step. `metadata` and `evidence_refs` are already redacted before storage."""
    name: str
    status: str                                  # ok / ambiguous / not_found / unsupported / error / invalid / skipped
    start_ms: float
    end_ms: float
    metadata: dict = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()

    @property
    def duration_ms(self) -> float:
        return round(self.end_ms - self.start_ms, 1)

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "start_ms": self.start_ms, "end_ms": self.end_ms, "duration_ms": self.duration_ms,
                "metadata": self.metadata, "evidence_refs": list(self.evidence_refs)}


class TraceRecorder(ABC):
    """Records execution steps for one request. A future recorder (e.g. one that also forwards to LangSmith) only
    needs to implement this interface; no caller-side code changes."""

    @abstractmethod
    def record(self, name: str, status: str, *, duration_ms: Optional[float] = None, metadata: Optional[dict] = None, evidence_refs: Iterable[str] = ()) -> None: ...

    @abstractmethod
    def events(self) -> list[TraceEvent]: ...

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self.events()]


class LocalTraceRecorder(TraceRecorder):
    """In-memory only: one Python list for the lifetime of one request/response. Nothing is written to disk."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []
        self._t = 0.0

    def record(self, name: str, status: str, *, duration_ms: Optional[float] = None, metadata: Optional[dict] = None, evidence_refs: Iterable[str] = ()) -> None:
        start = self._t
        end = start + (duration_ms or 0.0)
        self._events.append(TraceEvent(name=name, status=status, start_ms=round(start, 1), end_ms=round(end, 1),
                                       metadata=redact(metadata or {}), evidence_refs=tuple(redact(list(evidence_refs)))))
        self._t = end

    def events(self) -> list[TraceEvent]:
        return list(self._events)


def _mention_status(mentions: list[dict]) -> str:
    if any(m.get("status") == "not_found" for m in mentions):
        return "not_found"
    if any(m.get("status") == "ambiguous" for m in mentions):
        return "ambiguous"
    return "ok" if mentions else "skipped"


def build_trace(resp: dict) -> LocalTraceRecorder:
    """Build a deterministic execution trace purely from an already-produced response dict (`Response.to_dict()`).
    This makes no additional call into any service; it only reorganizes fields the frozen pipeline already returned."""
    rec = LocalTraceRecorder()
    timing = resp.get("timing_ms") or {}

    mentions = (resp.get("resolution") or {}).get("mentions", [])
    rec.record("Entity Resolution", _mention_status(mentions), duration_ms=timing.get("resolution_ms"),
               metadata={"mentions": [{"text": m.get("text"), "entity_type": m.get("entity_type"), "status": m.get("status"), "canonical": m.get("canonical")} for m in mentions]})

    routing = resp.get("routing") or {}
    rec.record("Routing", "ambiguous" if resp.get("route") == "ambiguous" else "ok", duration_ms=timing.get("routing_ms"),
               metadata={"route": resp.get("route"), "rule": routing.get("rule"), "meeting_mode": resp.get("meeting_mode")})

    se, ae, me = resp.get("structured_evidence"), resp.get("meeting_aggregate_evidence"), resp.get("meeting_evidence")
    lex_n = (me.get("filtered_meeting_count") if me else None)
    sem = (me or {}).get("semantic") or {}
    sem_n = sem.get("semantic_candidates") or sem.get("scope_size")
    # Each side's own count, kept distinct (never summed into one "evidence_count"): a hybrid question can have a
    # structured side AND a meeting-aggregate side at once, and they must not be conflated (payload_audit_v21.md, fix 4).
    rec.record("Evidence Collection", "ok" if (se or ae or me) else "skipped", duration_ms=(timing.get("structured_ms") or 0) + (timing.get("meeting_ms") or 0),
               metadata={"structured_query_used": bool(se or ae), "lexical_candidate_count": lex_n, "semantic_candidate_count": sem_n,
                         "structured_row_count": se.get("row_count") if se else None, "meeting_aggregate_row_count": ae.get("row_count") if ae else None,
                         "meeting_hit_count": len(me.get("hits") or []) if me else None})

    def _structured_event(name: str, ev: dict, source: str, duration_ms) -> None:
        rec.record(name, "ok" if ev.get("outcome") in ("ok", "empty_result") else ev.get("outcome", "error"), duration_ms=duration_ms,
                   metadata={"outcome": ev.get("outcome"), "source": source, "row_count": ev.get("row_count"), "columns": ev.get("columns")},
                   evidence_refs=[source] if source == "meetings_meta (aggregate)" else [f"{c}" for c in (ev.get("source_tables") or [])])

    if se and ae:
        # Both sides present at once (a structured route's own query plus a meeting-aggregate query): each gets its
        # own panel with its own true source and row_count. Neither is dropped and neither is mislabeled as the other.
        _structured_event("Structured Evidence", se, "; ".join(se.get("source_tables") or []), timing.get("structured_ms"))
        _structured_event("Meeting Aggregate Evidence", ae, "meetings_meta (aggregate)", 0.0)
    elif se or ae:
        ev = se or ae
        _structured_event("Structured Evidence", ev, "meetings_meta (aggregate)" if ev is ae else "; ".join(ev.get("source_tables") or []), timing.get("structured_ms"))

    if me:
        hits = me.get("hits") or []
        rec.record("Meeting Evidence", "ok" if me.get("outcome") in ("ok", "no_lexical_match", "no_meetings_for_filter") else me.get("outcome", "error"),
                   duration_ms=timing.get("meeting_ms"),
                   metadata={"outcome": me.get("outcome"), "rank_method": me.get("rank_method"), "hit_count": len(hits),
                             "hits": [{"meeting_id": h.get("meeting_id"), "date": h.get("meeting_date"), "rank": h.get("rank"),
                                       "source": ("lexical" if h.get("lexical_rank") and not h.get("semantic_rank") else
                                                  "semantic" if h.get("semantic_rank") and not h.get("lexical_rank") else
                                                  "lexical+semantic" if h.get("lexical_rank") else None),
                                       "semantic_score": h.get("semantic_score"), "snippet": (h.get("snippet") or "")[:160]} for h in hits[:8]]},
                   evidence_refs=[f"meeting:{h.get('meeting_id')}:{h.get('meeting_date')}" for h in hits[:8]])

    val = resp.get("validation")
    if val is not None:
        rec.record("Validation", "ok" if val.get("valid") else "invalid", duration_ms=timing.get("validation_ms"),
                   metadata={"valid": val.get("valid"), "warnings": val.get("warnings"), "reasons": val.get("reasons")})

    failure = resp.get("failure")
    if failure:
        rec.record("Failure", "error", duration_ms=0.0, metadata={"stage": failure.get("stage"), "code": failure.get("code"), "message": failure.get("message")})

    return rec
