"""Deterministic Baseline entity resolver (see docs/architecture/entity_resolution.md).

Core entity types: client, group, deal_id, deal_name, rm. An entity is RESOLVED only
by an exact identifier, an exact known name, or an exact match after safe
normalisation (case, whitespace, underscores, surrounding punctuation). Partial,
truncated or misspelled text is never resolved: it is reported as a diagnostic
(ambiguous with candidates, capped at 10) or as not_found. No fuzzy matching,
no embeddings, no LLM, no SQL LIKE. Data comes from the Baseline SQLite database (read-only).
Meeting companies and attendee names are not entity types; they appear only as
diagnostic candidate contexts so that words like "Summit" or "Rahul" can be reported as ambiguous.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .data_build import DEFAULT_DB

# core entity types
CLIENT, GROUP, DEAL_ID, DEAL_NAME, RM = "client", "group", "deal_id", "deal_name", "rm"
ENTITY_TYPES = (CLIENT, GROUP, DEAL_ID, DEAL_NAME, RM)
UNTYPED = "untyped"          # a mention that has no single domain entity type (not an entity type)
# diagnostic contexts (not entity types)
CTX_COMPANY, CTX_ATTENDEE = "meeting_company", "meeting_attendee"
MAX_PUBLIC_CANDIDATES = 10

RESOLVED, AMBIGUOUS, NOT_FOUND = "resolved", "ambiguous", "not_found"
EXACT_IDENTIFIER, EXACT_NAME, NORMALIZED, NUMERIC_SUFFIX, PARTIAL_NAME, NON_AUTHORITATIVE, NO_MATCH = (
    "exact_identifier", "exact_name", "normalized_match", "numeric_suffix", "partial_name_diagnostic", "non_authoritative_field", "no_match")

_TOKEN_RE = re.compile(r"[^\W_]+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_STRIP_CHARS = " \t\r\n.,;:!?\"'()[]{}<>"
_NUMERIC_CLIENT_RE = re.compile(r"(?:client[\s_]*)?([0-9]{5})", re.I)
_GROUP_RE = re.compile(r"(?:(?:client[\s_]*)?group(?:[\s_]*id)?[\s:#_-]*)?([0-9]+)", re.I)
_CLIENT_ID_SHAPE = re.compile(r"[a-z][0-9]{5}")
_DEAL_ID_SHAPE = re.compile(r"dl[0-9]{4,}")
_LEADING_STOPWORDS = {"what", "which", "who", "whom", "whose", "when", "where", "why", "how", "show", "tell", "list", "give", "find", "get",
                      "the", "for", "is", "are", "was", "were", "do", "does", "did", "can", "please", "me", "of", "in", "on", "and", "or", "with"}
_PARTIAL_FORMS = ("deal_name", "account_rm", CTX_COMPANY, CTX_ATTENDEE)


def tokenize(text: str) -> list[str]:
    """Casefolded alphanumeric tokens; punctuation and underscores separate tokens."""
    return _TOKEN_RE.findall(unicodedata.normalize("NFC", text).casefold())


def norm_key(text: str) -> str:
    return " ".join(tokenize(text))


@dataclass(frozen=True)
class Candidate:
    entity_type: Optional[str]     # a core type, or None for a diagnostic-only context
    value: str
    source: str
    note: str = ""
    context: str = ""              # where the value comes from: client_id, deal_name, account_rm, meeting_company, meeting_attendee, ...


def _cand_key(c: Candidate) -> tuple:
    return (0 if c.entity_type else 1, c.entity_type or "", c.context, c.value)


@dataclass(frozen=True)
class Resolution:
    entity_type: str
    input_text: str
    status: str
    canonical: Optional[str]
    candidates: tuple[Candidate, ...]          # public list, at most MAX_PUBLIC_CANDIDATES, deterministic order
    match_rule: str
    confidence: str                            # exact / normalized / none
    reason: str                                # matching evidence
    rationale: str                             # why this status
    sources: tuple[str, ...]
    details: dict = field(default_factory=dict)
    all_candidates: tuple[Candidate, ...] = ()  # internal diagnostics: the full set (not in to_dict)

    def to_dict(self) -> dict:
        return {"entity_type": self.entity_type, "input_text": self.input_text, "resolution_status": self.status, "canonical": self.canonical,
                "candidates": [{"entity_type": c.entity_type, "value": c.value, "source": c.source, "context": c.context, "note": c.note} for c in self.candidates],
                "match_rule": self.match_rule, "confidence": self.confidence, "reason": self.reason, "rationale": self.rationale,
                "sources": list(self.sources), "details": self.details}


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    resolution: Resolution


@dataclass(frozen=True)
class QuestionResolution:
    question: str
    mentions: tuple[Mention, ...]

    def _by(self, status: str) -> tuple[Mention, ...]:
        return tuple(m for m in self.mentions if m.resolution.status == status)

    @property
    def resolved(self): return self._by(RESOLVED)

    @property
    def ambiguous(self): return self._by(AMBIGUOUS)

    @property
    def not_found(self): return self._by(NOT_FOUND)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.ambiguous or self.not_found)


@dataclass(frozen=True)
class Entry:
    etype: Optional[str]   # core entity type, or None for a diagnostic-only context
    value: str
    source: str
    form: str


# ---------------------------------------------------------------- dictionaries
@dataclass
class Dictionaries:
    client_tables: dict[str, tuple[str, ...]]
    client_group: dict[str, int]
    client_name_forms: list[tuple[str, str, str, str]]          # (name, client_id, form, table)
    group_members: dict[int, dict[str, tuple[str, ...]]]       # group -> table -> client ids
    deal_names_by_id: dict[str, dict[str, int]]                # deal_id -> {deal_name: records}
    rms: dict[str, int]
    companies: dict[str, int]                                  # diagnostic context only
    attendees: dict[str, int]                                  # diagnostic context only


def load_dictionaries(conn: sqlite3.Connection) -> Dictionaries:
    tables: dict[str, set[str]] = defaultdict(set)
    group_members: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    client_group: dict[str, int] = {}
    for table in ("investments", "performance", "meetings"):
        for cid, gid in conn.execute(f"SELECT DISTINCT client_id, group_id FROM {table} WHERE client_id IS NOT NULL"):
            tables[cid].add(table)
            client_group.setdefault(cid, gid)
            group_members[gid][table].add(cid)
    for (gid,) in conn.execute("SELECT DISTINCT group_id FROM performance WHERE group_id IS NOT NULL"):
        group_members[gid]  # group rows: the group exists even if it has no member rows there
    forms: set[tuple[str, str, str, str]] = set()
    for name, cid in conn.execute("SELECT DISTINCT client_name, client_id FROM investments WHERE client_name IS NOT NULL"):
        forms.add((name, cid, "client_name", "investments"))
    for name, cid in conn.execute("SELECT DISTINCT account_name, client_id FROM investments WHERE account_name IS NOT NULL"):
        forms.add((name, cid, "account_name", "investments"))
    for name, cid in conn.execute("SELECT DISTINCT client_name, client_id FROM performance WHERE client_name IS NOT NULL AND client_id IS NOT NULL"):
        forms.add((name, cid, "client_name", "performance"))
    deals: dict[str, dict[str, int]] = defaultdict(dict)
    for did, name, n in conn.execute("SELECT deal_id, deal_name, COUNT(*) FROM investments GROUP BY deal_id, deal_name"):
        deals[did][name] = n
    rms = dict(conn.execute("SELECT account_rm, COUNT(*) FROM investments WHERE account_rm IS NOT NULL GROUP BY account_rm"))
    companies = dict(conn.execute("SELECT company, COUNT(*) FROM meetings WHERE company IS NOT NULL GROUP BY company"))
    attendees: Counter = Counter()
    for (text,) in conn.execute("SELECT attendees FROM meetings WHERE attendees IS NOT NULL"):
        for name in text.split(";"):
            name = name.strip()
            if name:
                attendees[name] += 1
    return Dictionaries(
        client_tables={c: tuple(sorted(t)) for c, t in tables.items()}, client_group=client_group, client_name_forms=sorted(forms),
        group_members={g: {t: tuple(sorted(v)) for t, v in m.items()} for g, m in group_members.items()},
        deal_names_by_id={d: dict(n) for d, n in deals.items()}, rms=rms, companies=companies, attendees=dict(attendees))


class _Index:
    """Lookup structures over every known identifier and name (core entities and diagnostic contexts)."""

    def __init__(self, d: Dictionaries) -> None:
        self.by_text: dict[str, list[Entry]] = defaultdict(list)
        self.by_key: dict[str, list[Entry]] = defaultdict(list)
        self.by_tokens: dict[tuple[str, ...], list[Entry]] = defaultdict(list)
        self.first_token: dict[str, list[tuple[str, ...]]] = defaultdict(list)
        self.token_tuples: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        seen: set = set()

        def add(text: str, entry: Entry) -> None:
            if (text, entry) in seen:
                return
            seen.add((text, entry))
            toks = tuple(tokenize(text))
            if not toks:
                return
            self.by_text[text].append(entry)
            self.by_key[" ".join(toks)].append(entry)
            if entry not in self.by_tokens[toks]:
                if not self.by_tokens[toks]:
                    self.first_token[toks[0]].append(toks)
                self.by_tokens[toks].append(entry)
            if entry.form in _PARTIAL_FORMS:
                for t in set(toks):
                    self.token_tuples[t].add(toks)

        for cid, tabs in d.client_tables.items():
            add(cid, Entry(CLIENT, cid, "+".join(tabs), "client_id"))
        for name, cid, form, table in d.client_name_forms:
            add(name, Entry(CLIENT, cid, table, form))
        for did, names in d.deal_names_by_id.items():
            add(did, Entry(DEAL_ID, did, "investments", "deal_id"))
            for name in names:
                add(name, Entry(DEAL_NAME, name, "investments", "deal_name"))
        for name in d.rms:
            add(name, Entry(RM, name, "investments", "account_rm"))
        for name in d.companies:
            add(name, Entry(None, name, "meetings", CTX_COMPANY))
        for name in d.attendees:
            add(name, Entry(None, name, "meetings", CTX_ATTENDEE))

    def _hits(self, text: str) -> tuple[Optional[str], list[Entry]]:
        """Exact (input as given), else exact after safe normalisation. Never anything looser."""
        hits = self.by_text.get(text, [])
        if hits:
            return EXACT_NAME, list(hits)
        hits = self.by_key.get(norm_key(text.strip(_STRIP_CHARS)), [])
        return (NORMALIZED, list(hits)) if hits else (None, [])

    def lookup(self, etypes: Iterable[str], text: str) -> Optional[tuple[str, list[Entry]]]:
        etypes = tuple(etypes)
        rule, hits = self._hits(text)
        hits = [e for e in hits if e.etype in etypes]
        if not hits:
            return None
        if rule == EXACT_NAME and all(e.form in ("client_id", "deal_id") for e in hits):
            rule = EXACT_IDENTIFIER
        return rule, hits

    def contexts(self, text: str) -> list[Entry]:
        """Diagnostic-only exact/normalised matches (meeting companies, attendee names)."""
        return [e for e in self._hits(text)[1] if e.etype is None]

    def partial(self, tokens: list[str], forms: Iterable[str] = _PARTIAL_FORMS) -> list[Entry]:
        """DIAGNOSTIC ONLY: names that contain these words as consecutive whole words. Never used to resolve."""
        forms = tuple(forms)
        if not tokens or max(len(t) for t in tokens) < 3:
            return []
        out: list[Entry] = []
        for tup in self.token_tuples.get(tokens[0], ()):
            if any(tup[i:i + len(tokens)] == tuple(tokens) for i in range(len(tup) - len(tokens) + 1)):
                out += [e for e in self.by_tokens[tup] if e.form in forms]
        return out


# ---------------------------------------------------------------- resolver
class EntityResolver:
    def __init__(self, dictionaries: Dictionaries) -> None:
        self.d = dictionaries
        self.index = _Index(dictionaries)

    @classmethod
    def from_database(cls, db_path: Path = DEFAULT_DB) -> "EntityResolver":
        db_path = Path(db_path)
        if not db_path.is_file():
            raise FileNotFoundError(f"Baseline database not found: {db_path} (build it with: python -m src.baseline.build_db)")
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return cls(load_dictionaries(conn))
        finally:
            conn.close()

    # ---- public API
    def resolve(self, entity_type: str, text: str) -> Resolution:
        """Resolve one extracted mention as a core entity type."""
        fn = {CLIENT: self._client, GROUP: self._group, DEAL_ID: self._deal_id, DEAL_NAME: self._deal_name, RM: self._rm}.get(entity_type)
        if fn is None:
            raise ValueError(f"unsupported entity type {entity_type!r}; supported: {ENTITY_TYPES}")
        return fn(text)

    def resolve_term(self, text: str) -> Resolution:
        """Resolve a mention whose entity type is not known."""
        raw = text.strip()
        if _NUMERIC_CLIENT_RE.fullmatch(raw):
            return self._client(raw)
        if re.fullmatch(r"[0-9]+", raw) or re.fullmatch(r"(?:client[\s_]*)?group(?:[\s_]*id)?[\s:#_-]*[0-9]+", raw, re.I):
            return self._group(raw)
        hit = self.index.lookup(ENTITY_TYPES, text)
        if hit:
            rule, entries = hit
            kinds = {e.etype for e in entries}
            if len(kinds) == 1 and len({e.value for e in entries}) == 1:
                return self.resolve(next(iter(kinds)), text)
            return self._ambiguous(UNTYPED, text, rule, entries, "exact/normalized match to more than one entity")
        ctx = self.index.contexts(text)
        if ctx:
            return self._not_found(UNTYPED, text, "known only as meeting text (company or attendee names); these are not resolvable entities in Baseline",
                                   details={"diagnostic_contexts": self._context_details(ctx)})
        entries = self.index.partial(tokenize(text))
        if entries:
            return self._ambiguous(self._common_type(entries), text, PARTIAL_NAME, entries, "partial name: reported as a diagnostic, never resolved")
        if _CLIENT_ID_SHAPE.fullmatch(raw.casefold()):
            return self._client(raw)
        if _DEAL_ID_SHAPE.fullmatch(raw.casefold()):
            return self._deal_id(raw)
        why = "no exact or normalized match in the data"
        if not text.isascii():
            why += "; the raw meeting text can hold mojibake spellings of names (encoding repair is deferred, A11), so a name may exist under a different raw spelling"
        return self._not_found(UNTYPED, text, why)

    def resolve_question(self, question: str) -> QuestionResolution:
        """Find entity mentions in a question and resolve each one. Ambiguity and unknown identifiers are reported, never guessed."""
        toks = [(m.group(), m.start(), m.end()) for m in _TOKEN_RE.finditer(question)]
        fold = [unicodedata.normalize("NFC", t).casefold() for t, _, _ in toks]
        n = len(toks)
        spans: list[tuple[int, int, str, Any]] = []

        for i in range(n):                                 # known names and identifiers (whole-word exact, longest wins later)
            for tup in self.index.first_token.get(fold[i], ()):
                if tuple(fold[i:i + len(tup)]) == tup:
                    if len(tup) == 2 and tup[0] == "client" and tup[1].upper() in self.d.client_tables:
                        continue  # "client A12345": the identifier token is the mention, not the Client_A12345 name form
                    spans.append((i, i + len(tup), "name", self.index.by_tokens[tup]))
        for i in range(n):                                 # identifier-shaped tokens that are not in the data
            if _CLIENT_ID_SHAPE.fullmatch(fold[i]) and fold[i].upper() not in self.d.client_tables:
                spans.append((i, i + 1, "client_id_unknown", None))
            elif _DEAL_ID_SHAPE.fullmatch(fold[i]) and fold[i].upper() not in self.d.deal_names_by_id:
                spans.append((i, i + 1, "deal_id_unknown", None))
        for i in range(n):                                 # "group 346"
            if fold[i] == "group":
                j = i + 1
                if j < n and fold[j] == "id":
                    j += 1
                if j < n and fold[j].isdigit():
                    spans.append((i, j + 1, "group", None))
        for i in range(n):                                 # bare 5-digit number that matches a client's numeric part
            if re.fullmatch(r"[0-9]{5}", fold[i]) and any(c[1:] == fold[i] for c in self.d.client_tables):
                spans.append((i, i + 1, "numeric", None))
        emails = [(m.start(), m.end()) for m in _EMAIL_RE.finditer(question)]

        chosen: list[tuple[int, int, str, Any]] = []
        taken = [False] * n
        for s in sorted(spans, key=lambda s: (-(s[1] - s[0]), s[0])):
            if not any(taken[s[0]:s[1]]):
                chosen.append(s)
                for k in range(s[0], s[1]):
                    taken[k] = True
        for a, b in emails:
            for k, (_, ts, te) in enumerate(toks):
                if ts >= a and te <= b:
                    taken[k] = True

        mentions: list[Mention] = []
        for s in chosen:
            a, b = toks[s[0]][1], toks[s[1] - 1][2]
            text, kind = question[a:b], s[2]
            if kind == "name":
                core = [e for e in s[3] if e.etype]
                if not core:
                    continue                                # an exact company/attendee name is plain text, not an entity
                kinds = {e.etype for e in core}
                res = self.resolve(next(iter(kinds)), text) if len(kinds) == 1 else self._ambiguous(
                    UNTYPED, text, EXACT_NAME, core, "the same text is a known name for more than one entity type")
            elif kind in ("client_id_unknown", "numeric"):
                res = self._client(text)
            elif kind == "deal_id_unknown":
                res = self._deal_id(text)
            else:
                res = self._group(text)
            mentions.append(Mention(text, a, b, res))
        for a, b in emails:
            mentions.append(Mention(question[a:b], a, b, self._rm(question[a:b])))

        i = 0                                              # capitalised words that partly match a name: diagnostics only
        while i < n:
            if taken[i] or not toks[i][0][0].isupper():
                i += 1
                continue
            j = i
            while j < n and not taken[j] and toks[j][0][0].isupper():
                j += 1
            run = list(range(i, j))
            while run and fold[run[0]] in _LEADING_STOPWORDS:
                run.pop(0)
            if run:
                entries = self.index.partial([fold[k] for k in run])
                if entries:
                    a, b = toks[run[0]][1], toks[run[-1]][2]
                    mentions.append(Mention(question[a:b], a, b, self._ambiguous(
                        self._common_type(entries), question[a:b], PARTIAL_NAME, entries, "partial name: reported as a diagnostic, never resolved")))
            i = j
        mentions.sort(key=lambda m: m.start)
        return QuestionResolution(question, tuple(mentions))

    # ---- helpers
    @staticmethod
    def _common_type(entries: Iterable[Entry]) -> str:
        kinds = {e.etype for e in entries}
        return next(iter(kinds)) if len(kinds) == 1 and None not in kinds else UNTYPED

    def _context_details(self, entries: Iterable[Entry]) -> list[dict]:
        counts = {CTX_COMPANY: self.d.companies, CTX_ATTENDEE: self.d.attendees}
        return [{"context": e.form, "value": e.value, "meetings": counts[e.form].get(e.value)} for e in sorted(entries, key=lambda e: (e.form, e.value))]

    def _ambiguous(self, etype: str, text: str, rule: str, entries: Iterable[Entry], why: str) -> Resolution:
        allc = tuple(sorted({Candidate(e.etype, e.value, e.source, context=e.form) for e in entries}, key=_cand_key))
        public = allc[:MAX_PUBLIC_CANDIDATES]
        confidence = "normalized" if rule == NORMALIZED else ("exact" if rule in (EXACT_IDENTIFIER, EXACT_NAME) else "none")
        details = {"candidate_count": len(allc), "candidates_truncated": len(allc) > len(public), "max_public_candidates": MAX_PUBLIC_CANDIDATES,
                   "candidate_contexts": dict(sorted(Counter(c.context for c in allc).items()))}
        return Resolution(etype, text, AMBIGUOUS, None, public, rule, confidence,
                          f"{len(allc)} candidate(s) for {text!r}: {why}",
                          "ambiguous: more than one candidate remains, or only a partial match exists; none is chosen automatically",
                          tuple(sorted({s for c in allc for s in c.source.split("+")})), details, allc)

    def _not_found(self, etype: str, text: str, reason: str, rule: str = NO_MATCH, details: Optional[dict] = None) -> Resolution:
        return Resolution(etype, text, NOT_FOUND, None, (), rule, "none", reason, "not_found: no candidate exists; nothing was guessed", (), details or {})

    def _resolved(self, etype: str, text: str, value: str, rule: str, reason: str, sources: tuple[str, ...], details: dict) -> Resolution:
        conf = "normalized" if rule == NORMALIZED else "exact"
        return Resolution(etype, text, RESOLVED, value, (), rule, conf, reason,
                          f"resolved: exactly one candidate via {rule.replace('_', ' ')}", sources, details)

    def _single(self, etype: str, text: str, rule: str, entries: list[Entry]) -> Resolution:
        value = entries[0].value
        forms = sorted({e.form for e in entries})
        sources = tuple(sorted({s for e in entries for s in e.source.split("+")}))
        return self._resolved(etype, text, value, rule, f"{rule.replace('_', ' ')} on {', '.join(forms)} in {', '.join(sources)}", sources, {"matched_forms": forms})

    def _pick(self, etype: str, text: str, rule: str, entries: list[Entry]) -> Resolution:
        if len({e.value for e in entries}) > 1:
            return self._ambiguous(etype, text, rule, entries, "the text is a known form of more than one value")
        return self._single(etype, text, rule, entries)

    # ---- typed resolvers
    def _client(self, text: str) -> Resolution:
        raw = text.strip()
        hit = self.index.lookup((CLIENT,), text)
        if hit:
            rule, entries = hit
            res = self._pick(CLIENT, text, rule, entries)
            if res.status == RESOLVED:
                cid = res.canonical
                tabs = self.d.client_tables[cid]
                note = "" if len(tabs) > 1 else f"; present only in {tabs[0]}, no data in the other sources"
                return Resolution(CLIENT, text, RESOLVED, cid, (), rule, res.confidence, res.reason + note, res.rationale, tabs,
                                  {"sources_present": list(tabs), "meeting_only": tabs == ("meetings",), "group_id": self.d.client_group.get(cid),
                                   "matched_forms": res.details.get("matched_forms")})
            return res
        m = _NUMERIC_CLIENT_RE.fullmatch(raw)
        if m:
            cands = [Entry(CLIENT, c, "+".join(t), "client_id") for c, t in self.d.client_tables.items() if c[1:] == m.group(1)]
            if cands:
                return self._ambiguous(CLIENT, text, NUMERIC_SUFFIX, cands, "bare number without the client-id prefix letter; the number alone is not an identifier")
            return self._not_found(CLIENT, text, f"no client identifier ends in {m.group(1)}")
        why = "no client with this identifier or known name form in investments, performance or meetings; similar identifiers are not substituted"
        if norm_key(raw).endswith(" org"):
            why += " (the *_org name is undocumented and is not a known name form)"
        return self._not_found(CLIENT, text, why)

    def _group(self, text: str) -> Resolution:
        m = _GROUP_RE.fullmatch(text.strip())
        if not m:
            return self._not_found(GROUP, text, "not a group identifier (expected a number, optionally preceded by 'group')")
        gid = int(m.group(1))
        members = self.d.group_members.get(gid)
        if members is None:
            return self._not_found(GROUP, text, f"no group {gid} in investments, performance or meetings")
        tabs = tuple(sorted(members))
        sets = {v for v in members.values() if v}
        details = {"group_id": gid, "members_by_source": {t: list(v) for t, v in members.items()}, "membership_differs_across_sources": len(sets) > 1,
                   "membership_note": "group identity only; which source defines membership is not decided here"}
        return self._resolved(GROUP, text, str(gid), EXACT_IDENTIFIER, f"exact group id {gid}", tabs, details)

    def _deal_id(self, text: str) -> Resolution:
        """A deal id is independently resolvable. Its deal names are returned as details, never as ambiguity of the id."""
        hit = self.index.lookup((DEAL_ID,), text)
        if not hit:
            return self._not_found(DEAL_ID, text, "no deal id with this value in investments")
        rule, entries = hit
        did = entries[0].value
        names = dict(sorted(self.d.deal_names_by_id[did].items()))
        details = {"deal_id": did, "deal_names": names, "deal_name_count": len(names), "maps_to_multiple_names": len(names) > 1,
                   "deal_names_note": "deal_id and deal_name are independent identifiers (A5); the names are associated metadata, not candidates"}
        return self._resolved(DEAL_ID, text, did, rule, f"{rule.replace('_', ' ')} on deal_id in investments; carries {len(names)} deal name(s)", ("investments",), details)

    def _named(self, etype: str, text: str, counts: dict[str, int], source: str) -> Resolution:
        hit = self.index.lookup((etype,), text)
        if hit:
            rule, entries = hit
            res = self._pick(etype, text, rule, entries)
            if res.status != RESOLVED:
                return res
            d = dict(res.details, records=counts.get(res.canonical))
            if etype == DEAL_NAME:
                d["deal_ids"] = sorted(i for i, n in self.d.deal_names_by_id.items() if res.canonical in n)
            if etype == RM:
                d["note"] = "AccountRM is record-level; this does not identify a single RM for any client"
            others = self.index.contexts(res.canonical)
            if others:
                d["also_known_in_context"] = self._context_details(others)   # for example an RM name that is also an attendee name
            return Resolution(etype, text, RESOLVED, res.canonical, (), rule, res.confidence, res.reason, res.rationale, res.sources, d)
        entries = self.index.partial(tokenize(text), forms=("deal_name" if etype == DEAL_NAME else "account_rm",))
        if entries:
            return self._ambiguous(etype, text, PARTIAL_NAME, entries, "partial name: reported as a diagnostic, never resolved")
        return self._not_found(etype, text, f"no {etype.replace('_', ' ')} with this exact or normalized name in {source}")

    def _deal_name(self, text: str) -> Resolution:
        return self._named(DEAL_NAME, text, {n: c for names in self.d.deal_names_by_id.values() for n, c in names.items()}, "investments")

    def _rm(self, text: str) -> Resolution:
        if "@" in text:
            return self._not_found(RM, text, "an email address is not an AccountRM name; email and alias fields are non-authoritative (A3) and are not used to resolve RMs",
                                   NON_AUTHORITATIVE)
        return self._named(RM, text, self.d.rms, "investments (AccountRM)")
