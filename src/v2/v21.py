"""V2.1: separate retrieval candidates from synthesis evidence (docs/architecture/v2_1_evidence_selection.md).

V2 returns the fused top-K list and passes all of it to the synthesizer, so semantic-only filler completes the list to K = 20.
V2.1 keeps that fused list unchanged as the ranked CANDIDATE list (for diagnostics) and gives synthesis and validation only the EVIDENCE:

    evidence = candidates that have a lexical rank            (lexical matches)
             + candidates without one whose semantic_rank == 1 (semantic-only hits in the top semantic cluster)

The top semantic cluster is the set of meetings tied at the highest semantic score inside the entity scope: `semantic_rank == 1` in V2's
competition ranking. No score threshold, no new ranking. Everything else (model, 128 dimensions, RRF k = 60, K = 20, lexical retrieval,
entity filtering, V1 and Baseline) is unchanged: V2.1 wraps the V2 retriever and adds a service class; no V2 file is modified.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from typing import Any, Optional

from src.baseline.question_routing import route_question
from src.baseline.service import ServiceConfig

from .embedding_index import DEFAULT_INDEX_DIR
from .retrieval import V2Evidence, V2Hit, V2Retriever
from .service import V2Service

TOP_SEMANTIC_CLUSTER_RANK = 1
EVIDENCE_RULE = ("evidence = candidates with a lexical rank, plus candidates without one whose semantic_rank == 1 "
                 "(the top semantic cluster: meetings tied at the highest semantic score in the entity scope); candidates keep their fused order and ranks")


def is_evidence(hit: V2Hit) -> bool:
    """A lexical match, or a semantic-only hit in the top semantic cluster."""
    return hit.lexical_rank is not None or hit.semantic_rank == TOP_SEMANTIC_CLUSTER_RANK


@dataclass(frozen=True)
class V21Evidence(V2Evidence):
    """`hits` is the EVIDENCE passed to synthesis and validation; `candidates` is V2's fused top-K list, unchanged."""
    candidates: tuple = ()
    selection: Optional[dict] = None

    def to_dict(self) -> dict:
        d = super().to_dict()                                                    # `hits` = the evidence, with the V2 rank fields
        as_candidates = V2Evidence(**{f.name: getattr(self, f.name) for f in dc_fields(V2Evidence) if f.name != "hits"}, hits=self.candidates).to_dict()["hits"]
        keep = {h.meeting_id for h in self.hits}
        d["candidates"] = [dict(c, in_evidence=c["meeting_id"] in keep) for c in as_candidates]
        d["selection"] = self.selection
        return d


def select_evidence(ev: V2Evidence) -> V21Evidence:
    """Apply the evidence-selection rule to a fused V2 result. Order and ranks of the candidates are not touched."""
    candidates = tuple(ev.hits)
    evidence = tuple(h for h in candidates if is_evidence(h))
    lexical = sum(1 for h in candidates if h.lexical_rank is not None)
    selection = {"rule": EVIDENCE_RULE, "candidates": len(candidates), "evidence": len(evidence), "lexical_matches": lexical,
                 "semantic_only_in_top_cluster": len(evidence) - lexical, "dropped_meeting_ids": [h.meeting_id for h in candidates if not is_evidence(h)]}
    return V21Evidence(**{f.name: getattr(ev, f.name) for f in dc_fields(V2Evidence) if f.name != "hits"}, hits=evidence, candidates=candidates, selection=selection)


class EvidenceSelectingRetriever:
    """Wraps a V2Retriever. Fused (rank_method 'rrf') results get the selection; everything else passes through unchanged:
    listings, entity/scope gates, no-terms, and the lexical-only result returned when the embedding call fails."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def close(self) -> None:
        self.inner.close()

    def retrieve(self, question: str, resolution=None, **kw):
        ev = self.inner.retrieve(question, resolution, **kw)
        if isinstance(ev, V2Evidence) and ev.rank_method == "rrf":
            return select_evidence(ev)
        return ev


class V21Service(V2Service):
    """V2 with the evidence-selection step. V2Service itself is unchanged."""

    @property
    def retriever(self):
        if self._retriever is None:
            self._retriever = EvidenceSelectingRetriever(V2Retriever(self.config.db_path, self._index_dir, self.embedder))
        elif not isinstance(self._retriever, EvidenceSelectingRetriever):
            self._retriever = EvidenceSelectingRetriever(self._retriever)
        return self._retriever


_default: Optional[V21Service] = None


def answer_question(question: str):
    """Answer one question with the default V2.1 service (created on first use)."""
    global _default
    if _default is None:
        _default = V21Service()
    return _default.answer_question(question)
