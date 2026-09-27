"""V1-1: search-term handling for meeting questions.

Wraps the frozen `derive_query`: it removes words that describe the request or name a data field instead of
what a meeting says, keeps an audit trail, detects date relations (unsupported in V1) and works out which
meeting metadata fields the question actually asks for. Deterministic and lexical only: no synonyms, no
stemming, no embeddings, no LLM.

The word lists come from the failures recorded in docs/evaluation/v1_diagnosis.md (section 4.3) and from the
cue vocabulary of the V1 contract (section 5.4). Additions need recorded evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from src.baseline.meeting_retrieval import derive_query, tokenize

# R1: request/control words seen as search terms in Baseline failures (MEET-01/04/05, HYB-03/06/09)
R1_REQUEST = frozenset("many date dates range after since largest estimated involve up two".split())
# R4: words of the contract's cue vocabulary (5.4) that the intent classifier consumes; they say what is asked, not what a meeting says
R4_CUE = frozenset("count number earliest oldest smallest biggest highest lowest maximum minimum distinct different unique period".split())
# R2: names of meetings-table fields and their plurals (HYB-03, HYB-10, MEET-04, MEET-05)
R2_FIELD = frozenset("company companies sector sectors region regions stage stages size series deal deals".split())
# R3: structured-side vocabulary, removed only in hybrid questions (HYB-01, HYB-03, HYB-05, HYB-09, HYB-10)
R3_STRUCTURED = frozenset("ci re hf cop total current moic irr aum usd amount amounts investment investments performance snapshot snapshots names below above than".split())
_MULTIPLE = re.compile(r"^\d+x$")          # 0x, 10x (from '1.0x'): a metric multiple of the structured side

CATEGORY_REASON = {
    "R1_request": "request or control word (says what is asked, not what a meeting says)",
    "R4_cue": "aggregate/listing cue word consumed by the intent classifier",
    "R2_field": "name of a meetings-table field (a filter or attribute, not evidence text)",
    "R3_structured": "structured-side vocabulary in a hybrid question (answered from the other source)",
    "structured_signal": "word matched by the router as an investment/performance signal",
}

_MONTHS = "january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec".split()
_DATE_TOKEN = r"(?:\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|(?:19|20)\d{2}|(?:" + "|".join(_MONTHS) + r")\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(?:19|20)\d{2})"
_RELATION = r"(?:on\s+or\s+after|on\s+or\s+before|prior\s+to|since|after|before|until|between)"
_DATE_RELATION = re.compile(rf"\b({_RELATION})\b(?:\W+(?:the|a|an|of|to|from)\b)*\W+({_DATE_TOKEN})", re.I)
DATE_RELATION_MESSAGE = ("Date ranges and date relations ('since', 'after', 'before', 'until', 'prior to', 'between') are not supported in this version, "
                         "so nothing was searched. Ask about one exact date (for example 'on 2022-10-01') or without a date.")

_FIELD_PATTERNS = {
    "sector": re.compile(r"\bsectors?\b", re.I),
    "region": re.compile(r"\bregions?\b", re.I),
    "stage": re.compile(r"\bstages?\b|\bseries\b", re.I),
    "deal_size": re.compile(r"\bdeal[\s-]*sizes?\b|\bestimated\b|\bsizes?\b", re.I),
    "company": re.compile(r"\bcompan(?:y|ies)\b", re.I),
}


def find_date_relation(question: str) -> Optional[dict]:
    """A relation word applied to a date ('since 2026-01-01', 'after March 2025', 'between 2023-01-01 and ...'). None if there is none.
    A bare date ('the meeting on 2022-10-01') is not a relation."""
    m = _DATE_RELATION.search(question or "")
    if not m:
        return None
    return {"relation": re.sub(r"\s+", " ", m.group(1).lower()), "date_text": m.group(2), "matched": m.group(0)}


def requested_fields(question: str) -> tuple[str, ...]:
    """Meeting metadata fields the question names. Meeting date and company are always available in the evidence."""
    return tuple(name for name, pat in _FIELD_PATTERNS.items() if pat.search(question or ""))


@dataclass(frozen=True)
class TermAudit:
    original_terms: tuple[str, ...]                  # what the frozen derive_query extracted
    removed: tuple[dict, ...]                        # {term, category, reason}
    final_terms: tuple[str, ...]
    phrases: tuple[str, ...]
    hybrid: bool
    wants_action_items: bool = False
    meeting_date: Optional[str] = None               # bare ISO date found by the frozen code (an exact-date filter, as in Baseline)
    requested_fields: tuple[str, ...] = ()

    @property
    def has_topic(self) -> bool:
        return bool(self.final_terms or self.phrases)

    def to_dict(self) -> dict:
        return {"original_terms": list(self.original_terms), "removed": list(self.removed), "final_terms": list(self.final_terms), "phrases": list(self.phrases),
                "hybrid": self.hybrid, "wants_action_items": self.wants_action_items, "meeting_date": self.meeting_date, "requested_fields": list(self.requested_fields)}


def derive_topical_terms(question: str, remove_texts: Iterable[str] = (), *, hybrid: bool = False, structured_signal_tokens: Iterable[str] = ()) -> TermAudit:
    """Topical search terms for a meeting question, with an audit of everything removed on top of the frozen derivation."""
    spec = derive_query(question, remove_texts)
    signal = {t for t in structured_signal_tokens}
    kept, removed = [], []
    for t in spec["terms"]:
        cat = None
        if t in R1_REQUEST:
            cat = "R1_request"
        elif t in R4_CUE:
            cat = "R4_cue"
        elif t in R2_FIELD:
            cat = "R2_field"
        elif hybrid and (t in R3_STRUCTURED or _MULTIPLE.match(t)):
            cat = "R3_structured"
        elif hybrid and t in signal:
            cat = "structured_signal"
        if cat:
            removed.append({"term": t, "category": cat, "reason": CATEGORY_REASON[cat]})
        else:
            kept.append(t)
    return TermAudit(original_terms=tuple(spec["terms"]), removed=tuple(removed), final_terms=tuple(kept), phrases=tuple(spec["phrases"]), hybrid=hybrid,
                     wants_action_items=spec["wants_action_items"], meeting_date=spec["meeting_date"], requested_fields=requested_fields(question))


def query_text_for(audit: TermAudit) -> str:
    """The text handed to the frozen retriever's `query_text`: final terms plus quoted phrases. Empty means no topical search."""
    return " ".join(list(audit.final_terms) + [f'"{p}"' for p in audit.phrases])


def structured_signal_tokens(routing) -> set[str]:
    """Tokens of the words the router matched as investment/performance signals (the same source the Baseline hybrid query used)."""
    return {t for s in routing.signals if s.family in ("investment", "performance") for t in tokenize(s.phrase)}
