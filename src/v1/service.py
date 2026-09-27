"""V1 service: the Baseline flow with the V1 meeting side.

question -> entity resolution -> routing (frozen) -> entity gate (frozen) -> evidence -> synthesis -> validation -> response

Only the evidence step for the meeting side, synthesis and validation differ from Baseline; everything else is
inherited unchanged from `src.baseline.service.BaselineService`. Meeting side (contract section 5.4):
  unsupported (date relation) / topical (frozen lexical retrieval with V1 terms) / listing (frozen newest-first listing)
  / aggregate (guarded SQL over meetings_meta). The structured side (investment or performance) is the frozen engine
and stays independent: no row-level cross-source join, no cross-source intersection.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, fields as dc_fields
from typing import Any, Optional

from src.baseline import meeting_retrieval as mr
from src.baseline import structured_query as sq
from src.baseline.question_routing import HYBRID, INVESTMENT, MEETING, PERFORMANCE, route_question
from src.baseline.service import (ERROR, OK, UNSUPPORTED, BaselineResponse, BaselineService, ServiceConfig, _classify_meeting, _classify_structured,
                                  _empty_meeting_answer, _entity_lines, _hybrid_structured_route, HYBRID_STRUCTURED_SUFFIX)

from . import meeting_aggregates as ma
from .meeting_intent import AGGREGATE, TOPICAL, UNSUPPORTED as INTENT_UNSUPPORTED, classify_meeting_intent
from .meeting_terms import DATE_RELATION_MESSAGE, query_text_for
from .synthesis import V1Synthesizer, unmatched_count
from .validation import validate_answer_v1


@dataclass(frozen=True)
class V1Response(BaselineResponse):
    meeting_mode: Optional[str] = None                 # topical / listing / aggregate / unsupported (None when the route has no meeting side)
    meeting_intent: Optional[dict] = None              # cues, reason and the term audit
    meeting_aggregate_evidence: Optional[dict] = None
    unmatched_count: Optional[int] = None
    requested_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(meeting_mode=self.meeting_mode, meeting_intent=self.meeting_intent, meeting_aggregate_evidence=self.meeting_aggregate_evidence,
                 unmatched_count=self.unmatched_count, requested_fields=list(self.requested_fields))
        return d


def _classify_aggregate(ev) -> str:
    if ev.success:
        return "evidence"
    if ev.outcome == ma.NO_MEETING_DATA:
        return "no_data"
    if ev.outcome in (ma.CLARIFICATION_NEEDED, ma.ENTITY_NOT_FOUND):
        return "clarification"
    if ev.outcome == ma.UNSUPPORTED_QUESTION:
        return "unsupported"
    return "error"


class V1Service(BaselineService):
    def __init__(self, config: ServiceConfig = ServiceConfig(), *, resolver: Any = None, engine: Any = None, retriever: Any = None,
                 aggregator: Any = None, adapter: Any = None, router: Any = route_question) -> None:
        super().__init__(config, resolver=resolver, engine=engine, retriever=retriever, adapter=adapter, router=router)
        self._aggregator = aggregator
        self._v1_synth: Optional[V1Synthesizer] = None
        self._extra: dict = {}

    @property
    def aggregator(self):
        if self._aggregator is None:
            self._aggregator = ma.MeetingAggregateEngine(ma.AggregateConfig(db_path=self.config.db_path), self.adapter)
        return self._aggregator

    @property
    def v1_synthesizer(self) -> V1Synthesizer:
        if self._v1_synth is None:
            self._v1_synth = V1Synthesizer(self.adapter)
        return self._v1_synth

    def close(self) -> None:
        super().close()
        if self._aggregator is not None and hasattr(self._aggregator, "close"):
            self._aggregator.close()

    def answer_question(self, question: str) -> V1Response:
        self._extra = {}
        base = super().answer_question(question)
        kw = {f.name: getattr(base, f.name) for f in dc_fields(BaselineResponse)}
        return V1Response(**kw, **self._extra)

    def _answer(self, q, routing, resolution, timing, ms, done, fail, clarify) -> BaselineResponse:
        route = routing.route
        s_ev = m_ev = a_ev = None
        s_state = m_state = a_state = None
        intent = None
        if route in (MEETING, HYBRID):
            intent = classify_meeting_intent(q, resolution, routing=routing, hybrid=route == HYBRID)
            self._extra.update(meeting_mode=intent.mode, meeting_intent=intent.to_dict(),
                               requested_fields=intent.audit.requested_fields if intent.audit else ())
            if intent.mode == INTENT_UNSUPPORTED:
                return fail(UNSUPPORTED, "routing", "date_relation_unsupported", DATE_RELATION_MESSAGE, {"date_relation": intent.date_relation})
        if route in (INVESTMENT, PERFORMANCE, HYBRID):
            s_route = route if route != HYBRID else _hybrid_structured_route(routing)
            if s_route is None:
                return fail(UNSUPPORTED, "routing", "hybrid_two_structured_sources",
                            "The question needs investment and performance figures together with meeting evidence; this version combines one structured source with meetings.")
            ts = time.monotonic()
            s_ev = self.engine.run(q + HYBRID_STRUCTURED_SUFFIX if route == HYBRID else q, s_route, resolution)
            timing["structured_ms"] = ms(ts)
            s_state = _classify_structured(s_ev)
        if route in (MEETING, HYBRID):
            ts = time.monotonic()
            if intent.mode == AGGREGATE:
                a_ev = self.aggregator.run(q + ma.HYBRID_SUFFIX if route == HYBRID else q, resolution)
                a_state = _classify_aggregate(a_ev)
                self._extra["meeting_aggregate_evidence"] = a_ev.to_dict()
            else:
                m_ev = self.retriever.retrieve(q, resolution, query_text=query_text_for(intent.audit) if intent.mode == TOPICAL else "")
                m_state = _classify_meeting(m_ev)
            timing["meeting_ms"] = ms(ts)
        s_dict = s_ev.to_dict() if s_ev is not None else None
        m_dict = m_ev.to_dict() if m_ev is not None else None
        ev_kw = {"structured_evidence": s_dict, "meeting_evidence": m_dict}
        if m_ev is not None:
            self._extra["unmatched_count"] = unmatched_count(m_ev)

        sides = [("structured", s_ev, s_state), ("meeting", m_ev, m_state), ("meeting aggregate", a_ev, a_state)]
        if any(st == "clarification" for _n, _e, st in sides):
            ev = next(e for _n, e, st in sides if st == "clarification")
            err = ev.error or {}
            return clarify(err.get("code", "clarification_needed"), err.get("message") or "Please clarify the question.", err.get("details", {}).get("entities", []), **ev_kw)
        hard = [(n, e, st) for n, e, st in sides if st in ("unsupported", "error")]
        if hard:
            status = UNSUPPORTED if any(st == "unsupported" for _n, _e, st in hard) and not any(st == "error" for _n, _e, st in hard) else ERROR
            details = {n: {"outcome": e.outcome, "error": e.error} for n, e, _st in hard}
            if route == HYBRID:
                good = {n: (e.to_dict()) for n, e, st in sides if e is not None and st in ("evidence", "no_data")}
                return fail(status, "evidence", "hybrid_partial_failure",
                            "One part of the question could not be answered, so no combined answer is given: " + "; ".join(f"{n}: {(e.error or {}).get('message')}" for n, e, _s in hard),
                            details, partial_evidence=good or None, **ev_kw)
            n, e, _st = hard[0]
            err = e.error or {}
            return fail(status, "evidence", err.get("code") or e.outcome, err.get("message") or e.outcome, details, **ev_kw)

        answer, qualified = None, False
        if route in (INVESTMENT, PERFORMANCE) and s_state == "no_data":
            answer, qualified = f"There is no {s_ev.route} data for this entity: {s_ev.error['message']}.", True
        elif route == MEETING and a_state == "no_data":
            answer, qualified = f"There are no meeting records for this entity: {a_ev.error['message']}.", True
        elif route == MEETING and m_state == "no_data":
            answer, qualified = f"There are no meeting records for this entity: {m_ev.error['message']}.", True
        elif route == MEETING and m_ev is not None and not m_ev.hits:
            answer, qualified = _empty_meeting_answer(m_ev), True
        meeting_no_data = "no_data" in (m_state, a_state)
        s_note = s_ev.error["message"] if s_state == "no_data" else None
        m_note = (m_ev.error["message"] if m_state == "no_data" else None) or (a_ev.error["message"] if a_state == "no_data" else None)
        s_use = None if s_state == "no_data" else s_ev
        m_use = None if m_state == "no_data" else m_ev
        a_use = None if a_state == "no_data" else a_ev
        fields = intent.audit.requested_fields if (intent is not None and intent.audit) else ()
        synth_info = None
        if answer is None:
            ts = time.monotonic()
            res = self.v1_synthesizer.synthesize(q, route, _entity_lines(resolution), s_use, m_use, a_use, s_note, m_note, fields)
            timing["synthesis_ms"] = ms(ts)
            synth_info = {"prompt_chars": res.prompt_chars, "evidence_trimmed": res.evidence_trimmed, "llm": res.llm}
            if not res.success:
                return fail(ERROR, "synthesis", (res.error or {}).get("code") or "synthesis_failure", (res.error or {}).get("message") or "answer synthesis failed",
                            None, synthesis=synth_info, **ev_kw)
            answer = res.text
        ts = time.monotonic()
        un = unmatched_count(m_use)
        val = validate_answer_v1(answer, route, s_use, m_use, a_use, q, structured_no_data=s_state == "no_data", meeting_no_data=meeting_no_data,
                                 extra_numbers=[un] if un is not None else ())
        timing["validation_ms"] = ms(ts)
        if val.invalid:
            return fail(ERROR, "validation", "validation_failed", "The generated answer failed validation and is withheld: " + "; ".join(val.reasons),
                        {"rejected_answer": answer, "reasons": list(val.reasons)}, validation=val.to_dict(), synthesis=synth_info, **ev_kw)
        return done(OK, answer=answer, validation=val.to_dict(), qualified=qualified, synthesis=synth_info, notes=tuple(val.warnings), **ev_kw)


_default: Optional[V1Service] = None


def answer_question(question: str) -> V1Response:
    """Answer one question with the default V1 service (created on first use)."""
    global _default
    if _default is None:
        _default = V1Service()
    return _default.answer_question(question)
