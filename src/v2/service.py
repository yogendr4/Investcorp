"""V2 service: V1 with the topical meeting side replaced by lexical + semantic retrieval fused by RRF.

Only the retriever and the synthesizer are swapped; entity gating, routing, the V1 intent classifier, listing, aggregates, date-relation
refusal, structured queries, validation and status mapping are inherited unchanged. Listing, aggregate, structured and refused requests never
reach the embedding code: V1 calls the retriever with an empty query text for a listing (delegated unchanged) and never for aggregates.
"""
from __future__ import annotations

from typing import Any, Optional

from src.baseline.question_routing import route_question
from src.baseline.service import ServiceConfig
from src.v1.service import V1Response, V1Service

from .embedding_index import DEFAULT_INDEX_DIR
from .retrieval import V2Retriever
from .synthesis import V2Synthesizer


class V2Service(V1Service):
    def __init__(self, config: ServiceConfig = ServiceConfig(), *, index_dir=DEFAULT_INDEX_DIR, embedder: Any = None, semantic_retriever: Any = None,
                 resolver: Any = None, engine: Any = None, aggregator: Any = None, adapter: Any = None, router: Any = route_question) -> None:
        super().__init__(config, resolver=resolver, engine=engine, retriever=semantic_retriever, aggregator=aggregator, adapter=adapter, router=router)
        self._index_dir, self._embedder = index_dir, embedder
        self._v2_synth: Optional[V2Synthesizer] = None

    @property
    def embedder(self):
        if self._embedder is None:
            from .vertex import VertexEmbedder
            self._embedder = VertexEmbedder()
        return self._embedder

    @property
    def retriever(self):
        if self._retriever is None:
            self._retriever = V2Retriever(self.config.db_path, self._index_dir, self.embedder)
        return self._retriever

    @property
    def v1_synthesizer(self) -> V2Synthesizer:              # V1's _answer calls this property; V2 supplies its synthesizer
        if self._v2_synth is None:
            self._v2_synth = V2Synthesizer(self.adapter)
        return self._v2_synth


_default: Optional[V2Service] = None


def answer_question(question: str) -> V1Response:
    """Answer one question with the default V2 service (created on first use)."""
    global _default
    if _default is None:
        _default = V2Service()
    return _default.answer_question(question)
