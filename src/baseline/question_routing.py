"""Deterministic Baseline question router (see docs/architecture/question_routing.md).

Classifies a question as exactly one of: investment, performance, meeting, hybrid, ambiguous.
Rules are explicit and inspectable: a vocabulary table (SIGNAL_RULES) and an ordered
list of routing rules (ROUTING_RULES). No LLM, no embeddings, no fuzzy matching.
Entity-resolution output is consumed and preserved, but it never decides the route
(an entity existing in several sources does not make a question hybrid).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .entity_resolution import AMBIGUOUS as ENTITY_AMBIGUOUS, NOT_FOUND as ENTITY_NOT_FOUND, RM as ENTITY_RM, QuestionResolution

INVESTMENT, PERFORMANCE, MEETING, HYBRID, AMBIGUOUS = "investment", "performance", "meeting", "hybrid", "ambiguous"
ROUTES = (INVESTMENT, PERFORMANCE, MEETING, HYBRID, AMBIGUOUS)
STATUS = "status"                       # status words are handled by their own rules, not as a source family
STRONG, WEAK = "strong", "weak"
STRUCTURAL, TOPIC = "structural", "topic"
HIGH, MEDIUM, NONE = "high", "medium", "none"
ROUTED = "routed"

# ambiguity kinds
CONFLICT, INSUFFICIENT_INTENT, UNSUPPORTED_ASSUMPTION = "conflict", "insufficient_intent", "unsupported_assumption"


@dataclass(frozen=True)
class SignalRule:
    id: str
    family: str
    strength: str
    pattern: re.Pattern
    note: str = ""


def _r(id, family, strength, regex, note=""):
    return SignalRule(id, family, strength, re.compile(regex, re.I), note)


# Ordered: longer phrases first. A later rule cannot match text already claimed by an earlier one.
SIGNAL_RULES: tuple[SignalRule, ...] = (
    # --- multi-word phrases that would otherwise be read as another family
    _r("M_LATEST_MEETING", MEETING, STRONG, r"\b(?:latest|most\s+recent|last|recent|earliest|first|previous|next)\s+meetings?\b", "'latest meeting' is a meeting phrase, not a performance 'latest'"),
    _r("M_DEAL_SIZE", MEETING, STRONG, r"\b(?:estimated\s+)?deal[\s-]*size(?:\s+estimates?)?\b", "deal_size_estimate is a meeting field; 'deal' here is not an investment deal"),
    _r("M_INVESTMENT_STAGE", MEETING, WEAK, r"\binvestment\s+stages?\b", "investment_stage is a meeting field"),
    _r("P_LOB_METRIC", PERFORMANCE, STRONG, r"\b(?:private\s+equity|hedge\s+fund|real\s+estate|credit\s+opportunit(?:y|ies)|infrastructure)\s+"
       r"(?:multiples?|moic|irr|returns?|aum)\b", "a canonical LOB name (A15) directly naming a performance metric is a strong performance signal, "
       "unlike the bare word 'multiple'/'latest' alone"),
    _r("I_INVESTMENT_RECORDS", INVESTMENT, STRONG, r"\binvestment\s+records?\b"),
    _r("I_CLIENT_STATUS", INVESTMENT, STRONG, r"\bclient[\s_]*status\b", "ClientStatus is an investments field (A4)"),
    _r("P_PERFORMANCE_STATUS", PERFORMANCE, STRONG, r"\b(?:performance|investment)[\s_]+status\b", "Investment_Status_Name is a performance field"),
    _r("I_DEAL_ID", INVESTMENT, STRONG, r"\bdeal[\s_-]*ids?\b"),
    _r("I_DEAL_NAME", INVESTMENT, STRONG, r"\bdeal[\s_-]*names?\b"),
    _r("M_ACTION_ITEMS", MEETING, STRONG, r"\baction[\s-]+items?\b"),
    _r("P_MOIC", PERFORMANCE, STRONG, r"\bmoic\b|\bmultiples?\s+on\s+invested\s+capital\b"),
    _r("P_IRR", PERFORMANCE, STRONG, r"\birr\b|\binternal\s+rate\s+of\s+return\b"),
    _r("P_AUM", PERFORMANCE, STRONG, r"\baum\b|\bassets\s+under\s+management\b"),
    _r("P_SNAPSHOT", PERFORMANCE, STRONG, r"\bsnapshots?\b|\bas[\s-]of\b"),
    _r("P_PERFORMANCE", PERFORMANCE, STRONG, r"\bperformance\b"),
    _r("P_FIELDS", PERFORMANCE, STRONG, r"\b(?:receivables?|call\s+account\s+balance|future\s+distributions?|distributions?)\b"),
    # --- meeting vocabulary
    _r("M_MEETING", MEETING, STRONG, r"\bmeetings?\b"),
    _r("M_MET", MEETING, STRONG, r"\b(?:met|meet|meets)\b"),
    _r("M_DISCUSS", MEETING, STRONG, r"\b(?:discuss(?:ed|es|ion|ions)?|conversations?|talk(?:ed|s)?|spoke|spoken)\b"),
    _r("M_RAISED", MEETING, STRONG, r"\b(?:said|says?|rais(?:ed|es|ing|e)|mention(?:ed|s)?|comment(?:ed|s)?|flag(?:ged|s)?|noted|came\s+up|come\s+up)\b"),
    _r("M_CONCERN", MEETING, STRONG, r"\bconcerns?\b"),
    _r("M_TOPIC", MEETING, STRONG, r"\b(?:topics?|themes?)\b"),
    _r("M_ATTEND", MEETING, STRONG, r"\battend(?:ed|ees?|ing|s)?\b"),
    _r("M_NOTES", MEETING, STRONG, r"\b(?:minutes|agenda|notes|summar(?:y|ies))\b"),
    _r("M_ATTRIBUTES", MEETING, WEAK, r"\b(?:sectors?|regions?|stages?|compan(?:y|ies)|counterpart(?:y|ies))\b", "meeting columns, but also ordinary words"),
    # --- investment vocabulary
    _r("I_INVESTMENT", INVESTMENT, STRONG, r"\binvest(?:ments?|ed|ing|s)?\b"),
    _r("I_DEAL", INVESTMENT, STRONG, r"\bdeals?\b"),
    _r("I_RM", INVESTMENT, STRONG, r"\b(?:rms?|relationship\s+managers?|account\s+managers?|account\s*rm)\b", "AccountRM is an investments field (A1)"),
    _r("I_COMMITMENT_EXPOSURE", INVESTMENT, WEAK, r"\b(?:commitments?|exposures?)\b", "not documented fields; weak on their own"),
    _r("I_AMOUNT", INVESTMENT, WEAK, r"\b(?:amounts?|usd|currency|currencies|gbp|eur|inr|sgd|aed|records?)\b", "generic words that also occur in other sources"),
    _r("I_LOB", INVESTMENT, WEAK, r"\b(?:line\s+of\s+business|lob|capital\s+call)\b"),
    # --- performance weak vocabulary
    _r("P_RETURN", PERFORMANCE, WEAK, r"\b(?:returns?|multiples?)\b", "ordinary words as well"),
    _r("P_LATEST", PERFORMANCE, WEAK, r"\b(?:latest|current|currently|trend(?:s|ing)?|improv(?:ing|ed|e)|declin(?:ing|ed|e)|over\s+time)\b"),
    # --- status words (own rules)
    _r("S_CLOSED", STATUS, WEAK, r"\bclosed\b", "'Closed' exists only in investments ClientStatus"),
    _r("S_STATUS", STATUS, WEAK, r"\b(?:active|dormant|prospects?)\b", "exists in both investments ClientStatus and performance Investment_Status_Name"),
)

# A structured term is treated as the TOPIC of a meeting question when it follows a speech/discussion word.
_INTRODUCERS = re.compile(r"\b(?:discuss\w*|mention\w*|says?|said|rais\w+|concern\w*|about|regarding|comment\w*|noted|flagged|came\s+up|talk\w*)\b", re.I)
_TOPIC_AFTER = re.compile(r"\s*(?:\w+\s+){0,2}?(?:(?:were|was|are|is)\s+(?:discussed|mentioned|raised|flagged|noted)|discussions?|mentions?|comments?|conversations?|concerns?)\b", re.I)
_META_QUESTION = re.compile(r"\b(?:what\s+(?:does|do)\b.*\b(?:mean|stand)|meaning\s+of|definition\s+of|stands?\s+for)\b", re.I)


@dataclass(frozen=True)
class Signal:
    family: str
    phrase: str
    strength: str
    rule: str
    start: int
    end: int
    role: str = STRUCTURAL          # topic = a structured word used as the subject matter of a meeting question


@dataclass(frozen=True)
class Ambiguity:
    code: str
    kind: str
    message: str


@dataclass(frozen=True)
class RoutingResult:
    route: str
    status: str                       # routed / ambiguous
    confidence: str                   # high / medium / none
    rule: str                         # the routing rule that decided
    signals: tuple[Signal, ...]
    source_cues: dict
    entity_cues: tuple[dict, ...]
    entities_needing_attention: tuple[dict, ...]
    ambiguity: tuple[Ambiguity, ...]
    rationale: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"route": self.route, "status": self.status, "confidence": self.confidence, "rule": self.rule,
                "signals": [{"family": s.family, "phrase": s.phrase, "strength": s.strength, "rule": s.rule, "role": s.role} for s in self.signals],
                "source_cues": self.source_cues, "entity_cues": list(self.entity_cues), "entities_needing_attention": list(self.entities_needing_attention),
                "ambiguity": [{"code": a.code, "kind": a.kind, "message": a.message} for a in self.ambiguity],
                "rationale": self.rationale, "notes": list(self.notes)}


# ---------------------------------------------------------------- signal extraction
def extract_signals(question: str) -> list[Signal]:
    """All vocabulary matches, longest phrases first, no overlaps."""
    taken = [False] * len(question)
    found: list[Signal] = []
    for rule in SIGNAL_RULES:
        for m in rule.pattern.finditer(question):
            if any(taken[m.start():m.end()]):
                continue
            for k in range(m.start(), m.end()):
                taken[k] = True
            found.append(Signal(rule.family, m.group(), rule.strength, rule.id, m.start(), m.end()))
    found.sort(key=lambda s: s.start)
    return found


def _assign_roles(question: str, signals: list[Signal]) -> list[Signal]:
    """Mark investment/performance words that are the topic of a meeting question (for example 'what was discussed about MOIC')."""
    has_meeting = any(s.family == MEETING and s.strength == STRONG for s in signals)
    out = []
    for s in signals:
        topic = False
        if has_meeting and s.family in (INVESTMENT, PERFORMANCE):
            segment = re.split(r"[,;.?!:]", question[max(0, s.start - 60):s.start])[-1]
            intro = list(_INTRODUCERS.finditer(segment))
            if intro and len(segment[intro[-1].end():].split()) <= 4:
                topic = True
            elif _TOPIC_AFTER.match(question, s.end):
                topic = True
        out.append(Signal(s.family, s.phrase, s.strength, s.rule, s.start, s.end, TOPIC if topic else STRUCTURAL))
    return out


# ---------------------------------------------------------------- entity awareness
def _entity_info(resolution: Optional[QuestionResolution]) -> tuple[list[dict], list[dict], bool, list[str]]:
    cues, attention, has_rm, notes = [], [], False, []
    if resolution is None:
        return cues, attention, has_rm, notes
    for m in resolution.mentions:
        r = m.resolution
        cue = {"text": m.text, "entity_type": r.entity_type, "status": r.status, "canonical": r.canonical}
        cues.append(cue)
        if r.entity_type == ENTITY_RM:
            has_rm = True
        if r.status in (ENTITY_AMBIGUOUS, ENTITY_NOT_FOUND):
            attention.append(dict(cue, match_rule=r.match_rule, candidate_count=r.details.get("candidate_count", len(r.candidates))))
        if r.details.get("meeting_only"):
            notes.append(f"{m.text}: meeting-only client (no investment or performance data); the route is chosen from the question's intent, not from this")
    return cues, attention, has_rm, notes


# ---------------------------------------------------------------- routing rules
ROUTING_RULES = (
    ("R1_NO_SIGNALS", "no vocabulary signal at all -> ambiguous (insufficient intent)"),
    ("R2_RM_MEETING_LINK", "an RM cue plus meeting evidence and no other structured signal -> ambiguous (meetings have no RM field)"),
    ("R3_HYBRID", "structured investment/performance signal AND meeting signal -> hybrid"),
    ("R4_MULTI_STRUCTURED", "investment AND performance signals with no meeting signal -> ambiguous (conflict)"),
    ("R5_SINGLE_FAMILY", "strong signals from exactly one family -> that route"),
    ("R6_STATUS", "only status words: 'closed' -> investment; other status words -> ambiguous (two sources)"),
    ("R7_WEAK_ONLY", "only weak signals: two or more from one family -> that route (medium); otherwise ambiguous"),
)


def route_question(question: str, resolution: Optional[QuestionResolution] = None) -> RoutingResult:
    """Route one question. `resolution` is the entity-resolution result for the same question (optional)."""
    signals = _assign_roles(question, extract_signals(question))
    cues, attention, entity_rm, notes = _entity_info(resolution)
    notes = list(notes)
    if _META_QUESTION.search(question):
        notes.append("field_meaning_question: asks what a field or term means; the route reflects the source named, and undocumented meanings must not be invented")
    if attention:
        notes.append("entity mentions are unresolved or ambiguous; they are preserved here and not changed by routing")

    def fam(f, strength=None, role=None):
        return [s for s in signals if s.family == f and (strength is None or s.strength == strength) and (role is None or s.role == role)]

    source_cues = {f: {"strong": [s.phrase for s in fam(f, STRONG, STRUCTURAL)], "weak": [s.phrase for s in fam(f, WEAK)],
                       "topic": [s.phrase for s in fam(f, role=TOPIC)]} for f in (INVESTMENT, PERFORMANCE, MEETING)}
    source_cues[STATUS] = {"words": [s.phrase for s in fam(STATUS)]}
    inv, perf, meet = fam(INVESTMENT, STRONG, STRUCTURAL), fam(PERFORMANCE, STRONG, STRUCTURAL), fam(MEETING, STRONG)
    topics = [s for s in signals if s.role == TOPIC]
    rm_word = any(s.rule == "I_RM" and s.role == STRUCTURAL for s in signals)
    weak = {f: fam(f, WEAK) for f in (INVESTMENT, PERFORMANCE, MEETING)}
    status_words = fam(STATUS)

    def done(route, status, confidence, rule, rationale, ambiguity=()):
        return RoutingResult(route, status, confidence, rule, tuple(signals), source_cues, tuple(cues), tuple(attention), tuple(ambiguity), rationale, tuple(notes))

    def quote(ss):
        return ", ".join(f"'{s.phrase}'" for s in ss)

    def topic_note():
        return f" Structured words used as the topic of a meeting question: {quote(topics)}." if topics else ""

    if not signals:
        return done(AMBIGUOUS, "ambiguous", NONE, "R1_NO_SIGNALS", "No investment, performance or meeting vocabulary found; the wording does not say what is being asked.",
                    [Ambiguity("no_intent_signals", INSUFFICIENT_INTENT, "the question names no measure, record type or activity")])

    if (rm_word or entity_rm) and meet and not perf and not [s for s in inv if s.rule != "I_RM"]:
        return done(AMBIGUOUS, "ambiguous", NONE, "R2_RM_MEETING_LINK",
                    "An RM is asked about together with meetings, but meetings carry no RM field and no link between an RM and a meeting is documented." + topic_note(),
                    [Ambiguity("rm_meeting_link", UNSUPPORTED_ASSUMPTION, "answering would assume an RM-to-meeting link that the data does not have (A2, A3)")])

    structured = inv + perf
    if structured and meet:
        return done(HYBRID, ROUTED, HIGH, "R3_HYBRID",
                    f"Structured signals ({quote(structured)}) and meeting signals ({quote(meet)}) both present, so evidence from both is needed." + topic_note())

    if inv and perf:
        return done(AMBIGUOUS, "ambiguous", NONE, "R4_MULTI_STRUCTURED",
                    f"Investment signals ({quote(inv)}) and performance signals ({quote(perf)}) but no meeting signal; Baseline has no route that combines two structured sources.",
                    [Ambiguity("multiple_structured_sources", CONFLICT, "two structured sources are asked for; hybrid is defined only as structured plus meeting evidence")])

    strong_families = [(f, s) for f, s in ((INVESTMENT, inv), (PERFORMANCE, perf), (MEETING, meet)) if s]
    if len(strong_families) == 1:
        f, ss = strong_families[0]
        others = [w for g, ws in weak.items() if g != f for w in ws]
        conf = MEDIUM if others else HIGH
        extra = f" Weak words from another source ({quote(others)}) are not enough to change the route." if others else ""
        if f == INVESTMENT and status_words:
            extra += f" Status words ({quote(status_words)}) are treated as filters."
        return done(f, ROUTED, conf, "R5_SINGLE_FAMILY", f"Only {f} signals are strong: {quote(ss)}." + extra + topic_note())

    if status_words and not any(weak.values()):
        if [s for s in status_words if s.rule == "S_CLOSED"] and len(status_words) == 1:
            return done(INVESTMENT, ROUTED, MEDIUM, "R6_STATUS", "'Closed' is a value of investments ClientStatus only (performance status has no 'Closed'), so the investments source is implied.")
        return done(AMBIGUOUS, "ambiguous", NONE, "R6_STATUS", f"Status words ({quote(status_words)}) exist in both investments (ClientStatus) and performance (Investment_Status_Name) and no source is named.",
                    [Ambiguity("status_source_unclear", CONFLICT, "two sources define a status; the question does not say which (metadata section 8)")])

    counts = {f: len(ws) for f, ws in weak.items() if ws}
    if len(counts) == 1 and next(iter(counts.values())) >= 2:
        f = next(iter(counts))
        return done(f, ROUTED, MEDIUM, "R7_WEAK_ONLY", f"No strong signal; two or more weak {f} words: {quote(weak[f])}.")
    if len(counts) > 1:
        return done(AMBIGUOUS, "ambiguous", NONE, "R7_WEAK_ONLY", "Only weak words from different sources: " + "; ".join(f"{f}: {quote(ws)}" for f, ws in weak.items() if ws) + ".",
                    [Ambiguity("conflicting_weak_signals", CONFLICT, "weak words point to different sources and none is decisive")])
    words = [w for ws in weak.values() for w in ws] or status_words
    return done(AMBIGUOUS, "ambiguous", NONE, "R7_WEAK_ONLY", f"Only a single weak word ({quote(words)}); it does not identify the source or the intent.",
                [Ambiguity("weak_signal_only", INSUFFICIENT_INTENT, "one generic word is not enough to choose a route")])
