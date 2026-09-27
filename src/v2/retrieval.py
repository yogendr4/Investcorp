"""V2 topical meeting retrieval: entity/filter scope -> lexical FTS5/BM25 + Gemini semantic search -> reciprocal rank fusion -> top-K.

Wraps the frozen Baseline `MeetingRetriever` (it still decides entities, filters and lexical ranking). For a topical query V2:
  1. runs the lexical retrieval (top 50 candidates, the frozen retriever's maximum) and reads the filters it applied;
  2. reads the meeting ids in that exact scope from SQLite (semantic search can never leave the scope);
  3. embeds the topical query text ONCE (Gemini Embedding 2, query format) and scores every meeting in scope by its best sentence;
  4. fuses the two candidate lists: rrf(m) = sum over lists of 1 / (60 + rank), a missing list contributes 0;
  5. orders by (rrf desc, lexical rank asc, meeting date desc, meeting_id asc) and returns K = 20.
Listing and other no-topic requests are delegated unchanged (no embedding). Query vectors are never stored.
"""
from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.baseline import meeting_retrieval as mr
from src.baseline.data_build import DEFAULT_DB
from .embedding_index import DEFAULT_INDEX_DIR, source_fingerprint
from .semantic import SemanticIndex
from .vertex import QUERY_FORMAT, VertexError

RRF_K = 60
CANDIDATE_DEPTH = 50           # lexical: the frozen retriever's MAX_TOP_K; semantic: the same depth, for symmetry
TIE_BREAK = ("rrf_score desc", "lexical_rank asc (absent last)", "meeting_date desc", "meeting_id asc")


_NEGATIONS = frozenset("no not never without nor neither cannot none nobody nothing nowhere".split())
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_QUOTED_SPAN = re.compile(r'["“][^"”]*["”]')


def _is_negation(word: str) -> bool:
    return word in _NEGATIONS or word.endswith(("n't", "n’t"))


def semantic_query_text(question: str, query_text: str) -> str:
    """Text for the query embedding: the V1 topical terms and phrases, plus every negation word of the question in its original position.

    V1's lexical term list (query_text, passed unchanged to the frozen retriever) drops the stop words "no" and "not" and splits
    contractions ("doesn't" -> "doesn"), which can reverse the meaning of a semantic query. Here the question is walked in order:
    a negation word (no, not, never, without, nor, neither, cannot, none, nobody, nothing, nowhere, any word ending in n't) is always
    kept; any other word is kept only if it is one of V1's terms (first occurrence). Words V1 removed (request/field words, entity
    text, dates, other stop words) stay removed. Quoted phrases are appended as before. With no negation the result equals
    the previous behavior (the V1 terms then the phrases)."""
    phrases = re.findall(r'"([^"]*)"', query_text)
    terms = mr.tokenize(re.sub(r'"[^"]*"', " ", query_text))
    remaining = set(terms)
    out: list[str] = []
    for raw in _WORD.findall(_QUOTED_SPAN.sub(" ", question or "")):
        low = unicodedata.normalize("NFC", raw).casefold()
        if _is_negation(low):
            out.append(low)
            remaining.discard(low)
            remaining.difference_update(mr.tokenize(raw))          # the fragments V1 made of a contraction ("doesn", "t") are consumed
            continue
        for part in mr.tokenize(raw):
            if part in remaining:
                out.append(part)
                remaining.discard(part)
    out += [t for t in terms if t in remaining]                    # a V1 term is never lost
    return " ".join(out + [" ".join(p.split()) for p in phrases if p.strip()])


@dataclass(frozen=True)
class V2Hit(mr.MeetingHit):
    lexical_rank: Optional[int] = None
    semantic_rank: Optional[int] = None
    semantic_score: Optional[float] = None
    semantic_sentence: Optional[str] = None
    rrf_score: float = 0.0
    lexical_score: Optional[float] = None


@dataclass(frozen=True)
class V2Evidence(mr.MeetingEvidence):
    semantic: Optional[dict] = None
    fusion: Optional[dict] = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        for h, dh in zip(self.hits, d["hits"]):
            if isinstance(h, V2Hit):
                dh.update(lexical_rank=h.lexical_rank, semantic_rank=h.semantic_rank, semantic_score=h.semantic_score, semantic_sentence=h.semantic_sentence,
                          rrf_score=h.rrf_score, lexical_score=h.lexical_score)
        d.update(semantic=self.semantic, fusion=self.fusion)
        return d


def rrf(*ranks: Optional[int], k: int = RRF_K) -> float:
    """Reciprocal rank fusion: a list that does not contain the meeting contributes 0."""
    return round(sum(1.0 / (k + r) for r in ranks if r is not None), 12)


def competition_ranks(scores: list[tuple[int, float]]) -> dict[int, int]:
    """Rank 1 = highest score; equal scores share a rank (1, 2, 2, 4). Repeated template sentences make exact ties common."""
    ordered = sorted(scores, key=lambda x: -x[1])
    ranks: dict[int, int] = {}
    prev, rank = None, 0
    for i, (mid, sc) in enumerate(ordered, start=1):
        if sc != prev:
            rank, prev = i, sc
        ranks[mid] = rank
    return ranks


def fuse_order(candidates: dict[int, dict], dates: dict[int, str]) -> list[int]:
    """Deterministic final order: rrf desc, lexical rank asc (absent last), meeting date desc, meeting_id asc (stable sorts, last key first)."""
    ids = sorted(candidates)                                                   # meeting_id asc
    ids.sort(key=lambda m: dates[m], reverse=True)                            # date desc (stable, keeps id asc inside a date)
    ids.sort(key=lambda m: candidates[m]["lexical_rank"] if candidates[m]["lexical_rank"] is not None else 10 ** 9)
    ids.sort(key=lambda m: -candidates[m]["rrf"])
    return ids


class V2Retriever:
    def __init__(self, db_path: Path = DEFAULT_DB, index_dir: Path = DEFAULT_INDEX_DIR, embedder: Any = None, top_k: int = mr.DEFAULT_TOP_K, *,
                 index: Optional[SemanticIndex] = None, verify_source: bool = True) -> None:
        self.db_path, self.top_k = Path(db_path), top_k
        self.lexical = mr.MeetingRetriever(self.db_path, top_k=top_k)
        self.conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
        self.embedder = embedder
        self.index = index or SemanticIndex.load(Path(index_dir), expected_fingerprint=source_fingerprint(self.db_path) if verify_source else None,
                                                 expected_config={"model": getattr(getattr(embedder, "config", None), "model", None)} if getattr(embedder, "config", None) else None)

    def close(self) -> None:
        self.lexical.close()
        self.conn.close()
        self.index.close()

    # ---- scope: the same WHERE the frozen retriever applied (read back from its evidence)
    def _scope(self, filters: dict) -> dict[int, str]:
        where, params = [], []
        if filters.get("client_ids"):
            where.append(f"client_id IN ({', '.join('?' * len(filters['client_ids']))})")
            params += list(filters["client_ids"])
        if filters.get("group_id") is not None:
            where.append("group_id = ?")
            params.append(filters["group_id"])
        if filters.get("meeting_date"):
            where.append("meeting_date = ?")
            params.append(filters["meeting_date"])
        if filters.get("date_from"):
            where.append("meeting_date >= ?")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            where.append("meeting_date <= ?")
            params.append(filters["date_to"])
        sql = "SELECT meeting_id, meeting_date FROM meetings" + (" WHERE " + " AND ".join(where) if where else "")
        return {int(r[0]): r[1] for r in self.conn.execute(sql, params)}

    def _row_hit(self, mid: int, **extra) -> V2Hit:
        r = self.conn.execute("SELECT meeting_id, client_id, group_id, meeting_date, company, sector, region, investment_stage, deal_size_estimate, action_items, summary_char_length "
                              "FROM meetings WHERE meeting_id = ?", (mid,)).fetchone()
        return V2Hit(rank=0, meeting_id=r[0], client_id=r[1], group_id=r[2], meeting_date=r[3], company=r[4], sector=r[5], region=r[6], investment_stage=r[7],
                     deal_size_estimate=r[8], score=None, snippet="", matched_terms=(), matched_fields=(), action_items_state="not_recorded" if r[9] is None else "recorded",
                     action_items=r[9], summary_chars=r[10], **extra)

    def retrieve(self, question: str, resolution=None, *, query_text: Optional[str] = None, filters=None, top_k: Optional[int] = None) -> mr.MeetingEvidence:
        k = self.top_k if top_k is None else top_k
        if query_text is None or not query_text.strip():                         # listing / no topical text: no embedding, frozen behavior
            return self.lexical.retrieve(question, resolution, query_text=query_text, filters=filters, top_k=k)
        t0 = time.monotonic()
        lex = self.lexical.retrieve(question, resolution, query_text=query_text, filters=filters, top_k=CANDIDATE_DEPTH)
        if lex.outcome not in (mr.OK, mr.NO_LEXICAL_MATCH):                       # entity gates, unsupported, empty scope, missing index: unchanged, no embedding
            return lex
        sem_text = semantic_query_text(question, query_text)
        scope = self._scope(lex.filters)
        if lex.filtered_meeting_count is not None and len(scope) != lex.filtered_meeting_count:
            raise RuntimeError(f"semantic scope ({len(scope)}) differs from the lexical scope ({lex.filtered_meeting_count})")
        base_fusion = {"method": "reciprocal_rank_fusion", "formula": f"1/({RRF_K}+rank) per list; absent list = 0", "k": RRF_K, "lexical_depth": CANDIDATE_DEPTH,
                       "semantic_depth": CANDIDATE_DEPTH, "semantic_rank_ties": "competition ranking (equal scores share a rank)", "tie_break": list(TIE_BREAK), "final_k": k}
        try:
            te = time.monotonic()
            qv = self.embedder.embed_query(sem_text)                              # exactly one query embedding per retrieval request
            embed_ms = round((time.monotonic() - te) * 1000, 1)
        except VertexError as exc:
            return self._lexical_only(lex, k, {"status": "unavailable", "error": {"kind": exc.kind, "status": exc.status, "message": exc.message[:300]}, "query_text": sem_text},
                                      base_fusion, "semantic retrieval was unavailable (" + exc.kind + "); the result is lexical only")
        ts = time.monotonic()
        matches = {m.meeting_id: m for m in self.index.search(qv, scope)}
        ranks = competition_ranks([(mid, m.score) for mid, m in matches.items()])
        sem_cand = {mid: r for mid, r in ranks.items() if r <= CANDIDATE_DEPTH}
        lex_by_id = {h.meeting_id: h for h in lex.hits}
        cands: dict[int, dict] = {}
        for mid in set(lex_by_id) | set(sem_cand):
            lr = lex_by_id[mid].rank if mid in lex_by_id else None
            sr = sem_cand.get(mid)
            cands[mid] = {"lexical_rank": lr, "semantic_rank": sr, "rrf": rrf(lr, sr)}
        order = fuse_order(cands, scope)[:k]
        hits = []
        for i, mid in enumerate(order, start=1):
            c, m = cands[mid], matches.get(mid)
            sent = self.index.sentence_text(m.sentence_id) if (m is not None and c["semantic_rank"] is not None) else None
            base = lex_by_id.get(mid)
            common = dict(lexical_rank=c["lexical_rank"], semantic_rank=c["semantic_rank"], semantic_score=round(m.score, 6) if m is not None else None,
                          semantic_sentence=sent, rrf_score=c["rrf"], lexical_score=base.score if base is not None else None)
            snippet = base.snippet if base is not None else ""
            if sent and sent[:40] not in snippet.replace(mr.MARK_OPEN, "").replace(mr.MARK_CLOSE, ""):
                snippet = (snippet + " | " if snippet else "") + f'semantic match ({m.score:.3f}): "{sent}"'
            if base is not None:
                hits.append(V2Hit(**{**{f: getattr(base, f) for f in mr.MeetingHit.__dataclass_fields__}, "rank": i, "score": c["rrf"], "snippet": snippet}, **common))
            else:
                h = self._row_hit(mid, **common)
                hits.append(V2Hit(**{**{f: getattr(h, f) for f in mr.MeetingHit.__dataclass_fields__}, "rank": i, "score": c["rrf"], "snippet": snippet}, **common))
        search_ms = round((time.monotonic() - ts) * 1000, 1)
        semantic = {"status": "ok", "model": self.index.manifest["model"], "dimensions": self.index.dimensions, "query_text": sem_text, "query_format": QUERY_FORMAT,
                    "scope_size": len(scope), "semantic_candidates": len(sem_cand), "lexical_candidates": len(lex.hits), "index_sentences": int(self.index.manifest["counts"]["sentences"]),
                    "embed_ms": embed_ms, "search_ms": search_ms}
        notes = tuple(lex.notes) + ("hybrid retrieval: lexical BM25 and semantic similarity fused by reciprocal rank fusion (k=60); hits are ordered by the fused score",)
        query = dict(lex.query, semantic_text=sem_text)
        return V2Evidence(question, mr.OK, hits=tuple(hits), top_k=k, filtered_meeting_count=lex.filtered_meeting_count, matched_count=lex.matched_count,
                          fewer_than_k=len(hits) < k, rank_method="rrf", query=query, filters=lex.filters, entity_notes=lex.entity_notes, notes=notes,
                          timing=dict(lex.timing, embed_ms=embed_ms, semantic_ms=search_ms, total_ms=round((time.monotonic() - t0) * 1000, 1)), error=None,
                          semantic=semantic, fusion=base_fusion)

    def _lexical_only(self, lex, k, semantic, fusion, note) -> V2Evidence:
        hits = tuple(V2Hit(**{**{f: getattr(h, f) for f in mr.MeetingHit.__dataclass_fields__}}, lexical_rank=h.rank, lexical_score=h.score) for h in lex.hits[:k])
        return V2Evidence(lex.question, lex.outcome, hits=hits, top_k=k, filtered_meeting_count=lex.filtered_meeting_count, matched_count=lex.matched_count,
                          fewer_than_k=lex.fewer_than_k, rank_method=lex.rank_method, query=lex.query, filters=lex.filters, entity_notes=lex.entity_notes,
                          notes=tuple(lex.notes) + (note,), timing=lex.timing, error=lex.error, semantic=semantic, fusion=fusion)
