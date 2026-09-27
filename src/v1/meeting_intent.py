"""V1: deterministic classification of the meeting side of a question.

The frozen router decides the route (meeting or hybrid). This layer only decides how the meeting side is answered:

  TOPICAL    a topical term or phrase remains after V1-1 term derivation -> frozen lexical retrieval
  LISTING    no topical term; a record lookup or a filter -> frozen newest-first listing
  AGGREGATE  no topical term; a count / earliest / range / distinct / extreme cue -> guarded aggregate SQL (V1-2)
  UNSUPPORTED  a date relation applied to a date (V1 does not support ranges or relations)

Cue lists are code, not an LLM. Order of rules: contract section 5.4. No embeddings, no semantics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .meeting_terms import TermAudit, derive_topical_terms, find_date_relation, structured_signal_tokens

TOPICAL, LISTING, AGGREGATE, UNSUPPORTED = "topical", "listing", "aggregate", "unsupported"

_AGG_CUES = [
    ("how_many", re.compile(r"\bhow\s+many\b", re.I)),
    ("number_of", re.compile(r"\bnumber\s+of\b", re.I)),
    ("count", re.compile(r"\bcount\b", re.I)),
    ("earliest", re.compile(r"\b(?:earliest|oldest|first\s+(?:ever\s+)?meeting)\b", re.I)),
    ("date_range", re.compile(r"\bdate\s+range\b|\bover\s+what\s+period\b|\bwhat\s+period\b", re.I)),
    ("distinct", re.compile(r"\b(?:distinct|unique|different)\b", re.I)),
    ("attribute_list", re.compile(r"\b(?:sectors|regions|companies|stages)\b", re.I)),
    ("extreme_deal_size", re.compile(r"\b(?:largest|biggest|highest|smallest|lowest|maximum|minimum)\b.{0,40}\b(?:deal|size|estimate|estimated)\b|"
                                    r"\b(?:deal|size|estimate|estimated)\b.{0,40}\b(?:largest|biggest|highest|smallest|lowest|maximum|minimum)\b", re.I)),
]
_LISTING_CUES = [
    ("latest", re.compile(r"\b(?:latest|most\s+recent(?:ly)?|last\s+(?:met|meeting)|newest)\b", re.I)),
    ("action_items", re.compile(r"\baction[\s-]+items?\b", re.I)),
]
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class MeetingIntent:
    mode: str
    reason: str
    cues: tuple[str, ...]
    audit: Optional[TermAudit] = None
    date_relation: Optional[dict] = None
    filters_present: bool = False

    def to_dict(self) -> dict:
        return {"mode": self.mode, "reason": self.reason, "cues": list(self.cues), "term_audit": self.audit.to_dict() if self.audit else None,
                "date_relation": self.date_relation, "filters_present": self.filters_present}


def _filters_present(resolution) -> bool:
    if resolution is None:
        return False
    return any(m.resolution.status == "resolved" and m.resolution.entity_type in ("client", "group") for m in resolution.mentions)


def classify_meeting_intent(question: str, resolution=None, *, routing=None, hybrid: bool = False) -> MeetingIntent:
    """Mode for the meeting side of `question`. `routing` supplies the structured-signal tokens for a hybrid question."""
    rel = find_date_relation(question)
    if rel:
        return MeetingIntent(UNSUPPORTED, f"date relation '{rel['relation']}' applied to '{rel['date_text']}' is not supported", (), None, rel)
    remove = [m.text for m in (resolution.mentions if resolution is not None else ()) if m.resolution.entity_type in ("client", "group")]
    audit = derive_topical_terms(question, remove, hybrid=hybrid, structured_signal_tokens=structured_signal_tokens(routing) if (hybrid and routing is not None) else ())
    filters = _filters_present(resolution) or bool(audit.meeting_date)
    if audit.has_topic:
        return MeetingIntent(TOPICAL, "topical terms remain after removing request and field words", (), audit, None, filters)
    agg = tuple(name for name, pat in _AGG_CUES if pat.search(question))
    if agg:
        return MeetingIntent(AGGREGATE, "no topical term; aggregate cue(s): " + ", ".join(agg), agg, audit, None, filters)
    lst = tuple(name for name, pat in _LISTING_CUES if pat.search(question))
    if lst or filters:
        return MeetingIntent(LISTING, "no topical term; " + (("record-lookup cue(s): " + ", ".join(lst)) if lst else "a filter is present (newest-first listing)"), lst, audit, None, filters)
    return MeetingIntent(LISTING, "no topical term, no cue and no filter: the frozen retriever reports 'no terms' (clarification)", (), audit, None, False)
