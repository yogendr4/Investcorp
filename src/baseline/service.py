"""Baseline application service: the end-to-end runtime flow (see docs/architecture/baseline_service.md).

question -> entity resolution -> routing -> evidence (structured SQL / lexical meeting retrieval / both)
         -> answer synthesis (one Claude CLI call) -> deterministic validation -> response

It orchestrates the approved components and adds no retrieval, SQL or resolution logic of its own.
No retries, no agents, no memory. Usage: `answer_question("...")`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import meeting_retrieval as mr
from . import structured_query as sq
from .answer_validation import ValidationResult, validate_answer
from .data_build import DEFAULT_DB
from .entity_resolution import AMBIGUOUS as E_AMBIGUOUS, NOT_FOUND as E_NOT_FOUND, RESOLVED as E_RESOLVED, QuestionResolution
from .question_routing import AMBIGUOUS as R_AMBIGUOUS, HYBRID, INVESTMENT, MEETING, PERFORMANCE, UNSUPPORTED_ASSUMPTION, route_question
from .synthesis import Synthesizer

OK, CLARIFICATION, UNSUPPORTED, ERROR = "ok", "clarification", "unsupported", "error"
MAX_CANDIDATES = 10
_CORE_TYPES = ("client", "group", "deal_id", "deal_name", "rm")
HYBRID_STRUCTURED_SUFFIX = " [Answer only the structured investment or performance part of this question; meeting evidence is retrieved separately.]"


@dataclass(frozen=True)
class ServiceConfig:
    db_path: Path = DEFAULT_DB


@dataclass(frozen=True)
class BaselineResponse:
    question: str
    route: Optional[str]
    status: str                                   # ok / clarification / unsupported / error
    answer: Optional[str] = None
    message: Optional[str] = None                 # explanation for a status other than ok
    resolution: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    structured_evidence: Optional[dict] = None
    meeting_evidence: Optional[dict] = None
    validation: Optional[dict] = None
    timing: dict = field(default_factory=dict)
    failure: Optional[dict] = None
    clarification: Optional[dict] = None
    qualified: bool = False                       # an ok answer that states missing or empty evidence
    partial_evidence: Optional[dict] = None
    synthesis: Optional[dict] = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"question": self.question, "route": self.route, "status": self.status, "answer": self.answer, "message": self.message,
                "resolution": self.resolution, "routing": self.routing, "structured_evidence": self.structured_evidence,
                "meeting_evidence": self.meeting_evidence, "validation": self.validation, "timing_ms": self.timing, "failure": self.failure,
                "clarification": self.clarification, "qualified": self.qualified, "partial_evidence": self.partial_evidence,
                "synthesis": self.synthesis, "notes": list(self.notes)}


def _classify_structured(ev) -> str:
    if ev.success:
        return "evidence"
    if ev.outcome == sq.NO_STRUCTURED_DATA:
        return "no_data"
    if ev.outcome in (sq.CLARIFICATION_NEEDED, sq.ENTITY_NOT_FOUND):
        return "clarification"
    if ev.outcome in (sq.UNAVAILABLE_METRIC, sq.UNSUPPORTED_QUESTION, sq.UNSUPPORTED_ROUTE):
        return "unsupported"
    return "error"


def _classify_meeting(ev) -> str:
    if ev.success:
        return "evidence"
    if ev.outcome == mr.NO_MEETING_DATA:
        return "no_data"
    if ev.outcome in (mr.CLARIFICATION_NEEDED, mr.ENTITY_NOT_FOUND, mr.NO_TERMS):
        return "clarification"
    if ev.outcome == mr.UNSUPPORTED_RM:
        return "unsupported"
    return "error"


class BaselineService:
    def __init__(self, config: ServiceConfig = ServiceConfig(), *, resolver: Any = None, engine: Any = None, retriever: Any = None,
                 adapter: Any = None, router: Any = route_question) -> None:
        self.config = config
        self._resolver, self._engine, self._retriever, self._adapter, self.router = resolver, engine, retriever, adapter, router
        self._synth: Optional[Synthesizer] = None

    # ---- lazily created real components (tests inject fakes instead)
    @property
    def adapter(self):
        if self._adapter is None:
            from .claude_cli import ClaudeCliAdapter
            self._adapter = ClaudeCliAdapter()
        return self._adapter

    @property
    def resolver(self):
        if self._resolver is None:
            from .entity_resolution import EntityResolver
            self._resolver = EntityResolver.from_database(self.config.db_path)
        return self._resolver

    @property
    def engine(self):
        if self._engine is None:
            self._engine = sq.StructuredQueryEngine(sq.EngineConfig(db_path=self.config.db_path), self.adapter)
        return self._engine

    @property
    def retriever(self):
        if self._retriever is None:
            self._retriever = mr.MeetingRetriever(self.config.db_path)
        return self._retriever

    @property
    def synthesizer(self) -> Synthesizer:
        if self._synth is None:
            self._synth = Synthesizer(self.adapter)
        return self._synth

    def close(self) -> None:
        for c in (self._engine, self._retriever):
            if c is not None and hasattr(c, "close"):
                c.close()

    # ---- entry point
    def answer_question(self, question: str) -> BaselineResponse:
        t0 = time.monotonic()
        timing: dict[str, float] = {}
        state: dict[str, Any] = {"route": None, "resolution": {}, "routing": {}}

        def ms(since: float) -> float:
            return round((time.monotonic() - since) * 1000, 1)

        def done(status: str, **kw) -> BaselineResponse:
            timing["total_ms"] = ms(t0)
            return BaselineResponse(question=question, route=state["route"], status=status, resolution=state["resolution"], routing=state["routing"],
                                    timing=dict(timing), **kw)

        def fail(status: str, stage: str, code: str, message: str, details: Optional[dict] = None, **kw) -> BaselineResponse:
            return done(status, message=message, failure={"stage": stage, "code": code, "message": message, "details": details or {}}, **kw)

        def clarify(code: str, message: str, entities: Optional[list] = None, **kw) -> BaselineResponse:
            entities = [dict(e, candidates=list(e.get("candidates", []))[:MAX_CANDIDATES]) for e in (entities or [])]
            return done(CLARIFICATION, message=message, clarification={"code": code, "message": message, "entities": entities, "max_candidates": MAX_CANDIDATES},
                        failure={"stage": kw.pop("stage", "clarification"), "code": code, "message": message, "details": {}}, **kw)

        if not isinstance(question, str) or not question.strip():
            return clarify("empty_question", "Please ask a question about investments, performance or meetings.")
        q = question.strip()
        try:
            ts = time.monotonic()
            resolution: QuestionResolution = self.resolver.resolve_question(q)
            timing["resolution_ms"] = ms(ts)
            state["resolution"] = _resolution_summary(resolution)
            ts = time.monotonic()
            routing = self.router(q, resolution)
            timing["routing_ms"] = ms(ts)
            state["route"], state["routing"] = routing.route, routing.to_dict()

            gate = _entity_gate(resolution)
            if gate:
                code, message, entities = gate
                return clarify(code, message, entities, stage="entities")
            if routing.route == R_AMBIGUOUS:
                amb = routing.ambiguity[0]
                if amb.kind == UNSUPPORTED_ASSUMPTION:
                    return fail(UNSUPPORTED, "routing", amb.code, f"This cannot be answered from the data: {amb.message}.")
                return clarify(amb.code, f"I cannot tell which data you mean: {amb.message}. Please say whether you want investment records, performance figures or meeting notes, and name what you are asking about.",
                               [dict(e, candidates=[]) for e in routing.entities_needing_attention], stage="routing")
            return self._answer(q, routing, resolution, timing, ms, done, fail, clarify)
        except Exception as exc:                       # no retry, no recovery: report and stop
            return fail(ERROR, "internal", "internal_error", f"{type(exc).__name__}: {exc}")

    # ---- evidence, synthesis, validation
    def _answer(self, q, routing, resolution, timing, ms, done, fail, clarify) -> BaselineResponse:
        route = routing.route
        s_ev = m_ev = None
        s_state = m_state = None
        if route in (INVESTMENT, PERFORMANCE, HYBRID):
            s_route = route if route != HYBRID else _hybrid_structured_route(routing)
            if s_route is None:
                return fail(UNSUPPORTED, "routing", "hybrid_two_structured_sources",
                            "The question needs investment and performance figures together with meeting evidence; Baseline combines one structured source with meetings.")
            ts = time.monotonic()
            s_ev = self.engine.run(q + HYBRID_STRUCTURED_SUFFIX if route == HYBRID else q, s_route, resolution)
            timing["structured_ms"] = ms(ts)
            s_state = _classify_structured(s_ev)
        if route in (MEETING, HYBRID):
            ts = time.monotonic()
            m_ev = self.retriever.retrieve(q, resolution, **({"query_text": _hybrid_meeting_query(q, routing, resolution)} if route == HYBRID else {}))
            timing["meeting_ms"] = ms(ts)
            m_state = _classify_meeting(m_ev)
        s_dict = s_ev.to_dict() if s_ev is not None else None
        m_dict = m_ev.to_dict() if m_ev is not None else None
        ev_kw = {"structured_evidence": s_dict, "meeting_evidence": m_dict}

        states = [s for s in (s_state, m_state) if s]
        if "clarification" in states:
            ev = s_ev if s_state == "clarification" else m_ev
            err = ev.error or {}
            entities = err.get("details", {}).get("entities", [])
            code = err.get("code", "clarification_needed")
            return clarify(code, err.get("message") or "Please clarify the question.", entities, **ev_kw)
        hard = [(name, ev, st) for name, ev, st in (("structured", s_ev, s_state), ("meeting", m_ev, m_state)) if st in ("unsupported", "error")]
        if hard:
            status = UNSUPPORTED if any(st == "unsupported" for _n, _e, st in hard) and not any(st == "error" for _n, _e, st in hard) else ERROR
            details = {name: {"outcome": ev.outcome, "error": ev.error} for name, ev, _st in hard}
            if route == HYBRID:
                good = {n: d for n, d, st in (("structured", s_dict, s_state), ("meeting", m_dict, m_state)) if st in ("evidence", "no_data")}
                return fail(status, "evidence", "hybrid_partial_failure",
                            "One part of the question could not be answered, so no combined answer is given: " + "; ".join(f"{n}: {(e.error or {}).get('message')}" for n, e, _s in hard),
                            details, partial_evidence=good or None, **ev_kw)
            name, ev, _st = hard[0]
            err = ev.error or {}
            return fail(status, "evidence", err.get("code") or ev.outcome, err.get("message") or ev.outcome, details, **ev_kw)

        # -- deterministic answers when there is nothing for the model to summarize
        answer, qualified, llm_info = None, False, None
        if route in (INVESTMENT, PERFORMANCE) and s_state == "no_data":
            answer, qualified = f"There is no {s_ev.route} data for this entity: {s_ev.error['message']}.", True
        elif route == MEETING and m_state == "no_data":
            answer, qualified = f"There are no meeting records for this entity: {m_ev.error['message']}.", True
        elif route == MEETING and m_ev is not None and not m_ev.hits:
            answer, qualified = _empty_meeting_answer(m_ev), True
        s_note = s_ev.error["message"] if s_state == "no_data" else None
        m_note = (m_ev.error["message"] if m_state == "no_data" else None)
        s_use = None if s_state == "no_data" else s_ev
        m_use = None if m_state == "no_data" else m_ev
        synth_info = None
        if answer is None:
            ts = time.monotonic()
            res = self.synthesizer.synthesize(q, route, _entity_lines(resolution), s_use, m_use, s_note, m_note)
            timing["synthesis_ms"] = ms(ts)
            synth_info = {"prompt_chars": res.prompt_chars, "evidence_trimmed": res.evidence_trimmed, "llm": res.llm}
            if not res.success:
                return fail(ERROR, "synthesis", (res.error or {}).get("code") or "synthesis_failure", (res.error or {}).get("message") or "answer synthesis failed",
                            None, synthesis=synth_info, **ev_kw)
            answer = res.text
        ts = time.monotonic()
        val: ValidationResult = validate_answer(answer, route, s_use, m_use, q, structured_no_data=s_state == "no_data", meeting_no_data=m_state == "no_data")
        timing["validation_ms"] = ms(ts)
        if val.invalid:
            return fail(ERROR, "validation", "validation_failed", "The generated answer failed validation and is withheld: " + "; ".join(val.reasons),
                        {"rejected_answer": answer, "reasons": list(val.reasons)}, validation=val.to_dict(), synthesis=synth_info, **ev_kw)
        notes = tuple(val.warnings)
        return done(OK, answer=answer, validation=val.to_dict(), qualified=qualified, synthesis=synth_info, notes=notes, **ev_kw)


def _resolution_summary(resolution: QuestionResolution) -> dict:
    return {"mentions": [{"text": m.text, "entity_type": m.resolution.entity_type, "status": m.resolution.status, "canonical": m.resolution.canonical,
                          "match_rule": m.resolution.match_rule, "candidates": [c.value for c in m.resolution.candidates][:MAX_CANDIDATES],
                          "candidate_count": m.resolution.details.get("candidate_count", len(m.resolution.candidates))} for m in resolution.mentions]}


def _entity_gate(resolution: QuestionResolution):
    """Ambiguous or unknown entities stop the request before any evidence is collected or Claude is called."""
    bad = []
    for m in resolution.mentions:
        r = m.resolution
        core = r.entity_type in _CORE_TYPES or any(c.entity_type for c in (r.all_candidates or r.candidates))
        if r.status == E_AMBIGUOUS and core:
            bad.append((m, "ambiguous_entity"))
        elif r.status == E_NOT_FOUND:
            bad.append((m, "entity_not_found"))
    if not bad:
        return None
    code = bad[0][1]
    entities = [{"text": m.text, "entity_type": m.resolution.entity_type, "reason": m.resolution.reason, "status": m.resolution.status,
                 "candidates": [c.value for c in m.resolution.candidates], "candidate_count": m.resolution.details.get("candidate_count", len(m.resolution.candidates))}
                for m, _c in bad]
    what = "ambiguous" if code == "ambiguous_entity" else "not found in the data"
    return code, f"'{bad[0][0].text}' is {what}; nothing is guessed. Please give the exact identifier or name.", entities


def _entity_lines(resolution: QuestionResolution) -> list[str]:
    lines = []
    for m in resolution.mentions:
        r = m.resolution
        if r.status != E_RESOLVED or r.entity_type not in _CORE_TYPES:
            continue
        extra = ""
        if r.entity_type == "client":
            extra = f" (sources: {', '.join(r.details.get('sources_present', []))}{'; meeting-only client' if r.details.get('meeting_only') else ''})"
        elif r.entity_type == "deal_id":
            extra = f" (carries deal names: {', '.join(r.details.get('deal_names', {}))}; the names are not collapsed)"
        elif r.entity_type == "group":
            extra = " (group identity only; membership differs across sources)" if r.details.get("membership_differs_across_sources") else " (group identity only)"
        lines.append(f"- {r.entity_type}: {r.canonical}{extra}")
    return lines


def _hybrid_structured_route(routing) -> Optional[str]:
    inv, perf = routing.source_cues[INVESTMENT]["strong"], routing.source_cues[PERFORMANCE]["strong"]
    if inv and perf:
        return None
    return PERFORMANCE if perf else INVESTMENT


def _hybrid_meeting_query(q: str, routing, resolution: QuestionResolution) -> str:
    """Meeting search words: the question's terms without the words that belong to the structured part."""
    spec = mr.derive_query(q, [m.text for m in resolution.mentions if m.resolution.entity_type in ("client", "group")])
    structured_tokens = {t for s in routing.signals if s.family in (INVESTMENT, PERFORMANCE) for t in mr.tokenize(s.phrase)}
    terms = [t for t in spec["terms"] if t not in structured_tokens]
    return " ".join(terms + [f'"{p}"' for p in spec["phrases"]])


def _empty_meeting_answer(ev) -> str:
    if ev.outcome == mr.NO_MEETINGS_FOR_FILTER:
        return "No meeting records pass the filters for this question (" + ", ".join(ev.filters.get("rationale") or ["the requested scope"]) + "). This says nothing about meetings outside that scope."
    terms = ", ".join(ev.query.get("terms", []) + ev.query.get("phrases", []))
    return (f"No meeting text matched the search terms ({terms}) among the {ev.filtered_meeting_count} meeting(s) in scope. "
            "This does not mean the topic was not discussed: the wording may differ, and Baseline search is lexical only.")


_default: Optional[BaselineService] = None


def answer_question(question: str) -> BaselineResponse:
    """Answer one question with the default Baseline service (created on first use)."""
    global _default
    if _default is None:
        _default = BaselineService()
    return _default.answer_question(question)
