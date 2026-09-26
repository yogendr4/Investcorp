"""Baseline lexical meeting retrieval: entity/source filters first, then SQLite FTS5 / BM25.

No embeddings, no LLM, no fuzzy matching, no stemming. Text is used exactly as stored (mojibake included).
See docs/architecture/meeting_retrieval.md.
"""
from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .data_build import DEFAULT_DB
from .entity_resolution import AMBIGUOUS as E_AMBIGUOUS, NOT_FOUND as E_NOT_FOUND, RESOLVED as E_RESOLVED, QuestionResolution
from .meeting_index import COLUMN_WEIGHTS, FTS_COLUMNS, FTS_TABLE

DEFAULT_TOP_K = 20     # covers the largest evidence set in the visible questions (14) and a typical client's meetings (12-23)
MAX_TOP_K = 50
SNIPPET_TOKENS = 30
MARK_OPEN, MARK_CLOSE = "[[", "]]"
ACTION_ITEMS_WEIGHT = 5.0    # applied when the question asks about action items (a field request), see derive_query

OK, NO_LEXICAL_MATCH, NO_MEETINGS_FOR_FILTER = "ok", "no_lexical_match", "no_meetings_for_filter"
NO_TERMS, CLARIFICATION_NEEDED, ENTITY_NOT_FOUND = "no_terms", "clarification_needed", "entity_not_found"
NO_MEETING_DATA, UNSUPPORTED_RM, INDEX_MISSING = "no_meeting_data_for_entity", "unsupported_rm_to_meeting", "index_missing"
VALID_RETRIEVALS = (OK, NO_LEXICAL_MATCH, NO_MEETINGS_FOR_FILTER)

_TOKEN_RE = re.compile(r"[^\W_]+")
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_QUOTED = re.compile(r'["“]([^"”]{2,80})["”]')
_ACTION_ITEMS = re.compile(r"\baction[\s-]+items?\b", re.I)
_STOPWORDS = set("""a an the of in on at to for and or by with from about that this these those it its their they them he she we our us you your me my be been being
is are was were am do does did has have had having any all some there then than as if into over under per also not no what which who whom whose when where why how
show list tell give find get please meeting meetings meet met client clients group groups record records recorded most last first latest recent recently previous next
s t d ll re ve which whichever one ones each every during between across
mention mentions mentioned mentioning discuss discussed discusses discussion discussions say says said talk talked talks raise raised raises raising
flag flagged flags note noted notes came come comes coming took take takes place held happen happened""".split())     # words that state the intent of the question, not what a meeting says


@dataclass(frozen=True)
class MeetingFilters:
    """Explicit structured filters on the meetings table (all optional, combined with AND)."""
    client_ids: tuple[str, ...] = ()
    group_id: Optional[int] = None
    meeting_date: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


@dataclass(frozen=True)
class MeetingHit:
    rank: int
    meeting_id: int
    client_id: str
    group_id: int
    meeting_date: str
    company: str
    sector: str
    region: str
    investment_stage: str
    deal_size_estimate: Optional[str]      # raw text, not parsed here
    score: Optional[float]                 # -bm25 (higher is better); None for filter-only listings
    snippet: str                           # raw text with matches marked [[like this]]
    matched_terms: tuple[str, ...]
    matched_fields: tuple[str, ...]
    action_items_state: str                # recorded / not_recorded
    action_items: Optional[str]            # None only when not recorded
    summary_chars: int


@dataclass(frozen=True)
class MeetingEvidence:
    question: str
    outcome: str
    hits: tuple[MeetingHit, ...] = ()
    top_k: int = DEFAULT_TOP_K
    filtered_meeting_count: Optional[int] = None     # meetings passing the entity/date filters, before any text search
    matched_count: Optional[int] = None              # of those, meetings with at least one lexical match
    fewer_than_k: Optional[bool] = None
    rank_method: Optional[str] = None                # bm25 or date_desc
    query: dict = field(default_factory=dict)
    filters: dict = field(default_factory=dict)
    entity_notes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    timing: dict = field(default_factory=dict)
    error: Optional[dict] = None

    @property
    def success(self) -> bool:
        return self.outcome in VALID_RETRIEVALS

    def to_dict(self) -> dict:
        return {"question": self.question, "outcome": self.outcome, "success": self.success, "top_k": self.top_k,
                "filtered_meeting_count": self.filtered_meeting_count, "matched_count": self.matched_count, "fewer_than_k": self.fewer_than_k,
                "rank_method": self.rank_method, "query": self.query, "filters": self.filters, "entity_notes": list(self.entity_notes), "notes": list(self.notes),
                "timing_ms": self.timing, "error": self.error,
                "hits": [{"rank": h.rank, "meeting_id": h.meeting_id, "client_id": h.client_id, "group_id": h.group_id, "meeting_date": h.meeting_date,
                          "company": h.company, "sector": h.sector, "region": h.region, "investment_stage": h.investment_stage,
                          "deal_size_estimate": h.deal_size_estimate, "score": h.score, "snippet": h.snippet, "matched_terms": list(h.matched_terms),
                          "matched_fields": list(h.matched_fields), "action_items_state": h.action_items_state, "action_items": h.action_items,
                          "summary_chars": h.summary_chars} for h in self.hits]}


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(unicodedata.normalize("NFC", text).casefold())


def derive_query(question: str, remove_texts: Iterable[str] = ()) -> dict:
    """Search terms from a question: entity text, ISO dates, stopwords and generic meeting words removed; quoted text kept as phrases."""
    text = question
    for r in remove_texts:
        text = text.replace(r, " ")
    date = None
    m = _ISO_DATE.search(text)
    if m:
        date = m.group(1)
        text = _ISO_DATE.sub(" ", text)
    phrases = [" ".join(tokenize(p)) for p in _QUOTED.findall(text)]
    text = _QUOTED.sub(" ", text)
    wants_action_items = bool(_ACTION_ITEMS.search(text))
    if wants_action_items:
        text = _ACTION_ITEMS.sub(" ", text)     # asks for the action_items field, not for the words
    terms: list[str] = []
    for t in tokenize(text):
        if len(t) >= 2 and t not in _STOPWORDS and t not in terms:
            terms.append(t)
    return {"terms": terms, "phrases": [p for p in phrases if p], "meeting_date": date, "wants_action_items": wants_action_items}


def _match_expression(terms: list[str], phrases: list[str]) -> str:
    return " OR ".join([f'"{t}"' for t in terms] + [f'"{p}"' for p in phrases])


class MeetingRetriever:
    def __init__(self, db_path: Path = DEFAULT_DB, top_k: int = DEFAULT_TOP_K) -> None:
        db = Path(db_path)
        if not db.is_file():
            raise FileNotFoundError(f"Baseline database not found: {db} (build it with: python -m src.baseline.build_db)")
        self.top_k = top_k
        self.conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        self.conn.execute("PRAGMA query_only = ON")
        self.has_index = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (FTS_TABLE,)).fetchone() is not None

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def retrieve(self, question: str, resolution: Optional[QuestionResolution] = None, *, query_text: Optional[str] = None,
                 filters: Optional[MeetingFilters] = None, top_k: Optional[int] = None) -> MeetingEvidence:
        t0 = time.monotonic()
        k = self.top_k if top_k is None else top_k
        if not 1 <= k <= MAX_TOP_K:
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}")

        def out(outcome, message=None, details=None, **kw):
            kw.setdefault("top_k", k)
            timing = dict(kw.pop("timing", {}), total_ms=round((time.monotonic() - t0) * 1000, 1))
            err = None if outcome in VALID_RETRIEVALS else {"code": outcome, "message": message, "details": details or {}}
            return MeetingEvidence(question, outcome, timing=timing, error=err, **kw)

        if not self.has_index:
            return out(INDEX_MISSING, f"the {FTS_TABLE} index is missing; rebuild the database (python -m src.baseline.build_db)")

        clients: list[str] = []
        group: Optional[int] = None
        remove: list[str] = []
        entity_notes: list[str] = []
        rationale: list[str] = []
        if resolution is not None:
            text_only = []     # ambiguous words that are only meeting text (company or attendee names): searched as words, not resolved
            for m in resolution.mentions:
                r = m.resolution
                if r.status == E_AMBIGUOUS and not any(c.entity_type for c in (r.all_candidates or r.candidates)):
                    text_only.append(m)
                    entity_notes.append(f"'{m.text}' matches {r.details.get('candidate_count', len(r.candidates))} company or attendee names; it is searched as a word and does not identify one person or company")
            for status, outcome, why in ((E_AMBIGUOUS, CLARIFICATION_NEEDED, "an entity mention is ambiguous; nothing is guessed"),
                                         (E_NOT_FOUND, ENTITY_NOT_FOUND, "an entity mention was not found; nothing is substituted")):
                bad = [m for m in resolution.mentions if m.resolution.status == status and m not in text_only]
                if bad:
                    return out(outcome, why, {"entities": [{"text": m.text, "entity_type": m.resolution.entity_type, "reason": m.resolution.reason,
                                                            "candidates": [c.value for c in m.resolution.candidates]} for m in bad]})
            for m in resolution.mentions:
                r = m.resolution
                if r.status != E_RESOLVED:
                    continue
                if r.entity_type == "rm":
                    return out(UNSUPPORTED_RM, "meetings have no RM field and no RM-to-meeting link is documented; meetings are not filtered or attributed by RM",
                               {"rm": r.canonical})
                if r.entity_type == "client":
                    if "meetings" not in r.details.get("sources_present", []):
                        return out(NO_MEETING_DATA, f"client {r.canonical} has no meeting records", {"entity": r.canonical})
                    clients.append(r.canonical)
                    remove.append(m.text)
                    rationale.append(f"client_id = {r.canonical} (meetings.client_id; resolved by exact match)")
                elif r.entity_type == "group":
                    if "meetings" not in r.sources:
                        return out(NO_MEETING_DATA, f"group {r.canonical} has no meeting records", {"entity": r.canonical})
                    group = int(r.canonical)
                    remove.append(m.text)
                    members = r.details.get("members_by_source", {}).get("meetings", [])
                    rationale.append(f"group_id = {group} (meetings.group_id; membership taken from the meetings source only: {len(members)} clients)")
                    if r.details.get("membership_differs_across_sources"):
                        entity_notes.append("group membership differs across sources; only the meetings source's own group_id is used, and no membership is imported from investments or performance")
                elif r.entity_type in ("deal_id", "deal_name"):
                    entity_notes.append(f"{r.entity_type} '{r.canonical}' is used only as search text; meetings carry no deal field, so no deal relationship is created and no filter is applied")

        spec = derive_query(question, remove)
        if query_text is not None:               # caller-supplied topic text: used as given (quoted text is a phrase), no stopword removal
            phrases = [" ".join(tokenize(p)) for p in _QUOTED.findall(query_text)]
            spec = dict(spec, terms=[], phrases=[p for p in phrases if p])
            for t in tokenize(_QUOTED.sub(" ", query_text)):
                if t not in spec["terms"]:
                    spec["terms"].append(t)
        flt = filters or MeetingFilters()
        client_ids = tuple(dict.fromkeys(list(flt.client_ids) + clients))
        group_id = flt.group_id if flt.group_id is not None else group
        meeting_date = flt.meeting_date or spec["meeting_date"]
        where, params = [], []
        if client_ids:
            where.append(f"m.client_id IN ({', '.join('?' * len(client_ids))})")
            params += list(client_ids)
        if group_id is not None:
            where.append("m.group_id = ?")
            params.append(group_id)
        if meeting_date:
            where.append("m.meeting_date = ?")
            params.append(meeting_date)
            rationale.append(f"meeting_date = {meeting_date} (ISO date found in the question)")
        if flt.date_from:
            where.append("m.meeting_date >= ?")
            params.append(flt.date_from)
        if flt.date_to:
            where.append("m.meeting_date <= ?")
            params.append(flt.date_to)
        filt_sql = (" AND " + " AND ".join(where)) if where else ""
        filters_info = {"client_ids": list(client_ids), "group_id": group_id, "meeting_date": meeting_date, "date_from": flt.date_from, "date_to": flt.date_to,
                        "group_source": "meetings.group_id" if group_id is not None else None, "rationale": rationale}
        query_info = {"terms": spec["terms"], "phrases": spec["phrases"], "wants_action_items": spec["wants_action_items"],
                      "match_expression": _match_expression(spec["terms"], spec["phrases"]) or None,
                      "fields": list(FTS_COLUMNS),
                      "weights": {c: (ACTION_ITEMS_WEIGHT if c == "action_items" and spec["wants_action_items"] else w) for c, w in zip(FTS_COLUMNS, COLUMN_WEIGHTS)},
                      "text_policy": "raw text as stored; tokens matched exactly (no stemming, no diacritic folding, no encoding repair)"}
        common = dict(query=query_info, filters=filters_info, entity_notes=tuple(entity_notes))
        has_terms = bool(spec["terms"] or spec["phrases"])
        if not has_terms and not where:
            return out(NO_TERMS, "the question has no searchable terms and no entity or date filter; an unrestricted listing is not returned", **common)

        te = time.monotonic()
        filtered = self.conn.execute(f"SELECT COUNT(*) FROM meetings m WHERE 1=1{filt_sql}", params).fetchone()[0]
        if filtered == 0:
            return out(NO_MEETINGS_FOR_FILTER, filtered_meeting_count=0, matched_count=0, hits=(), fewer_than_k=True, rank_method=None,
                       notes=("no meeting record passes the filters; this says nothing about meetings outside them",), **common)
        cols = ", ".join(f"m.{c}" for c in ("meeting_id", "client_id", "group_id", "meeting_date", "company", "sector", "region", "investment_stage",
                                              "deal_size_estimate", "summary", "attendees", "action_items"))
        if has_terms:
            match = query_info["match_expression"]
            matched = self.conn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE} JOIN meetings m ON m.source_row = {FTS_TABLE}.rowid WHERE {FTS_TABLE} MATCH ?{filt_sql}",
                                        [match] + params).fetchone()[0]
            weights = ", ".join(str(w) for w in query_info["weights"].values())
            rows = self.conn.execute(
                f"SELECT {cols}, bm25({FTS_TABLE}, {weights}) AS s, snippet({FTS_TABLE}, -1, '{MARK_OPEN}', '{MARK_CLOSE}', '...', {SNIPPET_TOKENS}) "
                f"FROM {FTS_TABLE} JOIN meetings m ON m.source_row = {FTS_TABLE}.rowid WHERE {FTS_TABLE} MATCH ?{filt_sql} "
                f"ORDER BY s ASC, m.meeting_date DESC, m.meeting_id ASC LIMIT ?", [match] + params + [k]).fetchall()
            method = "bm25"
        else:
            matched = filtered
            rows = self.conn.execute(f"SELECT {cols}, NULL, substr(m.summary, 1, 200) FROM meetings m WHERE 1=1{filt_sql} "
                                     f"ORDER BY m.meeting_date DESC, m.meeting_id ASC LIMIT ?", params + [k]).fetchall()
            method = "date_desc"
        timing = {"search_ms": round((time.monotonic() - te) * 1000, 1)}
        hits = tuple(self._hit(i, r, spec["terms"], spec["phrases"]) for i, r in enumerate(rows, start=1))
        notes = []
        if not has_terms:
            notes.append("no search terms: the filtered meetings are listed most recent first (not lexically ranked)")
        if has_terms and matched == 0:
            return out(NO_LEXICAL_MATCH, filtered_meeting_count=filtered, matched_count=0, hits=(), fewer_than_k=True, rank_method=method, timing=timing,
                       notes=(f"{filtered} meeting(s) pass the filters, but none contains the search terms; this does not mean the topic was not discussed (wording may differ)",), **common)
        if matched > len(hits):
            notes.append(f"{matched} meetings matched; only the top {len(hits)} are returned")
        return out(OK, hits=hits, filtered_meeting_count=filtered, matched_count=matched, fewer_than_k=matched < k, rank_method=method, timing=timing,
                   notes=tuple(notes), **common)

    @staticmethod
    def _hit(rank: int, r: tuple, terms: list[str], phrases: list[str]) -> MeetingHit:
        (mid, cid, gid, date, company, sector, region, stage, deal_size, summary, attendees, action_items, score, snip) = r
        fields = {"summary": summary, "attendees": attendees, "action_items": action_items, "company": company}
        found_terms, found_fields = [], []
        for name, value in fields.items():
            toks = tokenize(value or "")
            joined = " ".join(toks)
            hit = [t for t in terms if t in toks] + [p for p in phrases if f" {p} " in f" {joined} "]
            if hit:
                found_fields.append(name)
                found_terms += [t for t in hit if t not in found_terms]
        return MeetingHit(rank, mid, cid, gid, date, company, sector, region, stage, deal_size, None if score is None else round(-score, 6), snip,
                          tuple(found_terms), tuple(found_fields), "recorded" if action_items is not None else "not_recorded", action_items, len(summary or ""))
