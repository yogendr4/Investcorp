"""V2.1 tests: candidates are separated from synthesis evidence. Fake embedder, scripted Claude, no network.

Rule under test: evidence = candidates with a lexical rank + candidates without one whose semantic_rank == 1 (top semantic cluster);
the fused candidate list, its order and ranks, entity scope, model, dimensions, RRF k and K are unchanged.

Run from the project root:  python -m unittest tests.v2.test_evidence_selection -v
"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline import meeting_retrieval as mr
from src.baseline.service import ServiceConfig
from src.v2 import embedding_index as ei
from src.v2 import retrieval as rv
from src.v2.retrieval import V2Evidence, V2Hit, V2Retriever
from src.v2.service import V2Service
from src.v2.v21 import EVIDENCE_RULE, EvidenceSelectingRetriever, V21Evidence, V21Service, is_evidence, select_evidence
from src.v2.vertex import VertexConfig
from tests.v1.fixtures import FakeEngine, NoCalls, ScriptedAdapter, s_ev, sql_reply
from tests.v2.fixtures import CFO_SENT, FakeEmbedder, build_db


def hit(mid, lex, sem, rank, score=0.5):
    return V2Hit(rank=rank, meeting_id=mid, client_id="A12346", group_id=347, meeting_date=f"2024-01-{rank:02d}", company="Helix Capital", sector="fintech", region="EMEA",
                 investment_stage="seed", deal_size_estimate="1M USD", score=0.03, snippet="text", matched_terms=(), matched_fields=(), action_items_state="not_recorded",
                 action_items=None, summary_chars=10, lexical_rank=lex, semantic_rank=sem, semantic_score=score, semantic_sentence="a sentence", rrf_score=0.03, lexical_score=1.0)


def evidence_of(hits):
    return V2Evidence("q", mr.OK, hits=tuple(hits), top_k=20, filtered_meeting_count=23, matched_count=6, fewer_than_k=False, rank_method="rrf",
                      query={"terms": ["x"], "phrases": []}, filters={"client_ids": ["A12346"], "group_id": None, "meeting_date": None, "date_from": None, "date_to": None},
                      semantic={"status": "ok", "scope_size": 23}, fusion={"k": 60})


class TestSelectionRule(unittest.TestCase):
    def test_rule_is_lexical_or_top_cluster(self):
        self.assertTrue(is_evidence(hit(1, 3, 9, 1)))                   # lexical match, weak semantic rank: kept
        self.assertTrue(is_evidence(hit(2, None, 1, 2)))                # semantic-only, top cluster: kept
        for sem in (2, 3, 7, 20, 50):
            self.assertFalse(is_evidence(hit(3, None, sem, 3)), sem)    # semantic-only below the top cluster: not evidence
        self.assertFalse(is_evidence(hit(4, None, None, 4)))
        self.assertIn("semantic_rank == 1", EVIDENCE_RULE)

    def test_meet07_style_filler_does_not_enter_evidence(self):
        cands = [hit(m, m - 100, 1, m - 100) for m in range(101, 107)] + [hit(200 + i, None, 7 + i, 7 + i) for i in range(14)]
        ev = select_evidence(evidence_of(cands))
        self.assertEqual([h.meeting_id for h in ev.hits], [101, 102, 103, 104, 105, 106])
        self.assertEqual(len(ev.candidates), 20)
        self.assertEqual(ev.selection["dropped_meeting_ids"], [200 + i for i in range(14)])
        self.assertEqual((ev.selection["candidates"], ev.selection["evidence"], ev.selection["lexical_matches"], ev.selection["semantic_only_in_top_cluster"]), (20, 6, 6, 0))

    def test_semantic_only_top_cluster_is_retained_next_to_lexical_matches(self):
        cands = [hit(1, 2, 1, 1), hit(2, 40, 1, 2), hit(3, None, 1, 3), hit(4, None, 1, 4), hit(5, 1, None, 5), hit(6, None, 6, 6), hit(7, None, 9, 7)]
        ev = select_evidence(evidence_of(cands))
        self.assertEqual([h.meeting_id for h in ev.hits], [1, 2, 3, 4, 5])
        self.assertEqual(ev.selection["semantic_only_in_top_cluster"], 2)

    def test_lexical_matches_are_always_retained(self):
        cands = [hit(m, m, 30 + m, m) for m in range(1, 9)]              # every one has a poor semantic rank
        self.assertEqual([h.meeting_id for h in select_evidence(evidence_of(cands)).hits], list(range(1, 9)))

    def test_candidates_order_and_ranks_are_untouched(self):
        cands = [hit(1, 3, 1, 1), hit(2, None, 5, 2), hit(3, None, 1, 3), hit(4, 1, 8, 4), hit(5, None, 12, 5)]
        src = evidence_of(cands)
        ev = select_evidence(src)
        self.assertEqual(ev.candidates, tuple(cands))
        self.assertEqual(src.hits, tuple(cands))                          # the input is not modified
        self.assertEqual([h.rank for h in ev.hits], [1, 3, 4])            # evidence keeps the fused ranks, in fused order
        self.assertTrue(set(h.meeting_id for h in ev.hits) <= set(h.meeting_id for h in ev.candidates))

    def test_everything_else_in_the_evidence_object_is_unchanged(self):
        src = evidence_of([hit(1, 1, 1, 1), hit(2, None, 5, 2)])
        ev = select_evidence(src)
        for name in ("outcome", "top_k", "filtered_meeting_count", "matched_count", "rank_method", "query", "filters", "semantic", "fusion", "notes", "entity_notes"):
            self.assertEqual(getattr(ev, name), getattr(src, name), name)
        d = json.loads(json.dumps(ev.to_dict(), default=str))
        self.assertEqual([c["in_evidence"] for c in d["candidates"]], [True, False])
        self.assertEqual([h["meeting_id"] for h in d["hits"]], [1])
        self.assertIn("lexical_rank", d["hits"][0])
        self.assertEqual(d["selection"]["evidence"], 1)

    def test_configuration_constants_are_unchanged(self):
        self.assertEqual((rv.RRF_K, rv.CANDIDATE_DEPTH, mr.DEFAULT_TOP_K), (60, 50, 20))
        c = VertexConfig()
        self.assertEqual((c.model, c.dimensions, c.location), ("gemini-embedding-2", 128, "global"))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v21_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.idx_dir = cls.tmp / "idx"
        ei.build_index(cls.db, cls.idx_dir, FakeEmbedder(), workers=1)
        cls.resolver = er.EntityResolver.from_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pair(self):
        """(inner V2 retriever, V2.1 wrapper around an identical one), each with its own counting embedder."""
        self.e_inner, self.e_wrap = FakeEmbedder(), FakeEmbedder()
        inner = V2Retriever(self.db, self.idx_dir, self.e_inner)
        wrap = EvidenceSelectingRetriever(V2Retriever(self.db, self.idx_dir, self.e_wrap))
        self.addCleanup(inner.close)
        self.addCleanup(wrap.close)
        return inner, wrap

    def ask(self, r, q, query_text):
        return r.retrieve(q, self.resolver.resolve_question(q), query_text=query_text)


class TestRetrievalWrapper(Base):
    def test_filler_is_excluded_and_lexical_match_kept(self):
        inner, wrap = self.pair()
        q = "What did client A12345 discuss about hedging?"
        base, ev = self.ask(inner, q, "hedging"), self.ask(wrap, q, "hedging")
        self.assertIsInstance(ev, V21Evidence)
        self.assertEqual([h.meeting_id for h in base.hits], [h.meeting_id for h in ev.candidates])
        self.assertEqual([h.meeting_id for h in ev.hits], [1])                        # the lexical match; meetings 2, 3, 6 are filler
        self.assertEqual(sorted(ev.selection["dropped_meeting_ids"]), [2, 3, 6])
        self.assertEqual(len(base.hits), 4)                                            # V2 alone passes all four on

    def test_expected_semantic_evidence_is_retained_without_lexical_overlap(self):
        inner, wrap = self.pair()
        q = "In client A12345's meetings, which mention a chief and providers?"
        self.assertEqual(self.ask(inner, q, "chief providers").hits[0].meeting_id, 1)
        ev = self.ask(wrap, q, "chief providers")
        self.assertEqual([h.meeting_id for h in ev.hits], [1])                        # semantic-only, semantic rank 1
        self.assertEqual((ev.hits[0].lexical_rank, ev.hits[0].semantic_rank), (None, 1))
        self.assertEqual(ev.hits[0].semantic_sentence, CFO_SENT)
        self.assertIn(CFO_SENT, ev.hits[0].snippet)
        self.assertGreater(len(ev.candidates), 1)

    def test_lexical_matches_survive_even_when_their_semantic_rank_is_low(self):
        inner, wrap = self.pair()
        q = "What did client A12345 say about runway and hedging?"
        ev = self.ask(wrap, q, "runway hedging")
        lexical = [h for h in ev.candidates if h.lexical_rank is not None]
        self.assertGreaterEqual(len(lexical), 2)
        self.assertEqual({h.meeting_id for h in lexical}, {h.meeting_id for h in ev.hits if h.lexical_rank is not None})
        self.assertTrue(any(h.semantic_rank != 1 for h in lexical), "the test needs a lexical match below the top semantic cluster")
        self.assertTrue(all(h.meeting_id in {e.meeting_id for e in ev.hits} for h in lexical))

    def test_fused_candidate_list_is_identical_to_v2(self):
        inner, wrap = self.pair()
        for q, qt in (("What did client A12345 discuss about hedging?", "hedging"), ("Which meetings of client A12345 mention a chief and providers?", "chief providers"),
                      ("What was said about currency exposure and hedging for client C12399?", "currency exposure hedging"), ("Which meetings of group 346 mention hedging or runway?", "hedging runway")):
            with self.subTest(q=q):
                base, ev = self.ask(inner, q, qt), self.ask(wrap, q, qt)
                key = lambda hs: [(h.meeting_id, h.rank, h.lexical_rank, h.semantic_rank, h.semantic_score, h.rrf_score, h.snippet) for h in hs]
                self.assertEqual(key(ev.candidates), key(base.hits))
                sub = [k for k in key(base.hits) if is_evidence(next(h for h in base.hits if h.meeting_id == k[0]))]
                self.assertEqual(key(ev.hits), sub)                                   # evidence is an order-preserving subsequence with the same ranks
                self.assertEqual((ev.rank_method, ev.top_k, ev.filtered_meeting_count, ev.matched_count), (base.rank_method, base.top_k, base.filtered_meeting_count, base.matched_count))
                self.assertEqual(ev.fusion, base.fusion)

    def test_entity_scope_is_unchanged(self):
        inner, wrap = self.pair()
        q = "Which of client A12350's meetings mention a chief and providers?"
        base, ev = self.ask(inner, q, "chief providers"), self.ask(wrap, q, "chief providers")
        self.assertEqual(ev.filters, base.filters)
        self.assertEqual(ev.semantic["scope_size"], base.semantic["scope_size"])
        self.assertEqual(ev.semantic["scope_size"], 1)
        self.assertEqual({h.meeting_id for h in ev.candidates} | {h.meeting_id for h in ev.hits}, {5})
        q = "Which meetings of group 346 mention a chief and providers?"
        ev = self.ask(wrap, q, "chief providers")
        self.assertTrue({h.meeting_id for h in ev.candidates} <= {1, 2, 3, 4, 6})
        self.assertTrue({h.meeting_id for h in ev.hits} <= {h.meeting_id for h in ev.candidates})
        self.assertEqual(len(self.e_wrap.query_calls), 2)                              # one query embedding per request, as in V2

    def test_non_fused_results_pass_through_unchanged(self):
        inner, wrap = self.pair()
        q = "When was client A12345 most recently met?"                                # listing: empty query text
        a, b = self.ask(inner, q, ""), self.ask(wrap, q, "")
        self.assertNotIsInstance(b, V21Evidence)
        self.assertEqual((a.rank_method, [h.meeting_id for h in a.hits]), (b.rank_method, [h.meeting_id for h in b.hits]))
        self.assertEqual(self.e_wrap.query_calls, [])
        q = "What concerns did client 12345 raise about hedging?"                       # ambiguous entity gate
        b = self.ask(wrap, q, "concerns hedging")
        self.assertFalse(b.success)
        self.assertNotIsInstance(b, V21Evidence)
        self.assertEqual(self.e_wrap.query_calls, [])

    def test_embedding_failure_result_is_not_altered(self):
        emb = FakeEmbedder(fail_query=True)
        wrap = EvidenceSelectingRetriever(V2Retriever(self.db, self.idx_dir, emb))
        self.addCleanup(wrap.close)
        q = "What did client A12345 discuss about hedging?"
        ev = self.ask(wrap, q, "hedging")
        self.assertNotIsInstance(ev, V21Evidence)
        self.assertEqual((ev.rank_method, ev.semantic["status"]), ("bm25", "unavailable"))
        self.assertTrue(all(h.lexical_rank is not None for h in ev.hits))


class TestV21Service(Base):
    def service(self, cls, *replies, adapter=None):
        self.emb = FakeEmbedder()
        self.adapter = adapter or ScriptedAdapter(*replies)
        retr = V2Retriever(self.db, self.idx_dir, self.emb)
        s = cls(ServiceConfig(db_path=self.db), semantic_retriever=retr, resolver=self.resolver, engine=FakeEngine(s_ev()), adapter=self.adapter)
        self.addCleanup(s.close)
        return s

    Q = "What did client A12345 discuss about hedging?"

    def hit_ids_in_prompt(self):
        return [int(m) for m in re.findall(r"(?m)^meeting (\d+) \|", self.adapter.prompts[0])]

    def test_synthesis_receives_only_the_evidence(self):
        r = self.service(V21Service, "Meeting 1 records that hedging policy was requested [meeting: 1, 2024-01-10].").answer_question(self.Q)
        self.assertEqual(r.status, "ok")
        self.assertEqual(self.hit_ids_in_prompt(), [1])                                # meetings 2, 3, 6 are filler and never reach the model
        self.assertIn("shown: 1", self.adapter.prompts[0])
        me = r.meeting_evidence
        self.assertEqual([h["meeting_id"] for h in me["hits"]], [1])
        self.assertEqual([(c["meeting_id"], c["in_evidence"]) for c in me["candidates"]], [(1, True), (6, False), (3, False), (2, False)])
        self.assertEqual(me["selection"]["dropped_meeting_ids"], [6, 3, 2])
        self.assertTrue(r.validation["valid"])

    def test_v2_service_is_unchanged_and_still_passes_every_candidate(self):
        r = self.service(V2Service, "Meeting 1 [meeting: 1, 2024-01-10].").answer_question(self.Q)
        self.assertEqual(sorted(self.hit_ids_in_prompt()), [1, 2, 3, 6])
        self.assertNotIn("candidates", r.meeting_evidence)

    def test_a_dropped_candidate_cannot_be_cited(self):
        r = self.service(V21Service, "Meeting 2 also matters [meeting: 2, 2024-03-01].").answer_question(self.Q)
        self.assertEqual((r.status, r.failure["code"]), ("error", "validation_failed"))
        self.assertIsNone(r.answer)

    def test_listing_aggregate_structured_and_refused_paths_are_unchanged(self):
        cases = [("When was client A12345 most recently met?", ["Latest [meeting: 6, 2024-06-15]."]),
                 ("How many meetings did client A12345 have?", [sql_reply("SELECT COUNT(*) AS n FROM meetings_meta WHERE client_id = 'A12345'"), "It had 4 meetings [meetings]."]),
                 ("Which meetings of client A12345 happened since 2024-03-01?", []), ("What concerns did client 12345 raise about hedging?", []),
                 ("What is client A12345's latest Total AUM?", ["3 [performance]."])]
        for q, replies in cases:
            with self.subTest(q=q):
                self.service(V21Service, *replies).answer_question(q)
                self.assertEqual(self.emb.query_calls, [])

    def test_response_serializes(self):
        r = self.service(V21Service, "Meeting 1 [meeting: 1, 2024-01-10].").answer_question(self.Q)
        d = json.loads(json.dumps(r.to_dict(), default=str))
        self.assertEqual(d["meeting_evidence"]["selection"]["evidence"], 1)
        self.assertEqual(d["meeting_mode"], "topical")


if __name__ == "__main__":
    unittest.main()
