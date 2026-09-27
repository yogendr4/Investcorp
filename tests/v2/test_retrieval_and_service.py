"""V2 tests: semantic + lexical retrieval with RRF, scope isolation, bypass paths, and the V2 service. Fake embedder, no Vertex or Claude call.

Run from the project root:  python -m unittest tests.v2.test_retrieval_and_service -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.baseline import entity_resolution as er
from src.baseline import meeting_retrieval as mr
from src.baseline.service import ServiceConfig
from src.v2 import embedding_index as ei
from src.v2.retrieval import CANDIDATE_DEPTH, RRF_K, V2Evidence, V2Hit, V2Retriever, competition_ranks, fuse_order, rrf
from src.v2.service import V2Service
from tests.v1.fixtures import NoCalls, ScriptedAdapter, s_ev, sql_reply, FakeEngine
from tests.v2.fixtures import CFO_SENT, DIM, FakeEmbedder, MOJI_1, TIE_SENT, build_db


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v2_ret_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.idx_dir = cls.tmp / "idx"
        ei.build_index(cls.db, cls.idx_dir, FakeEmbedder(), workers=1)
        cls.resolver = er.EntityResolver.from_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def retriever(self, **kw):
        self.emb = FakeEmbedder(**kw)
        r = V2Retriever(self.db, self.idx_dir, self.emb)
        self.addCleanup(r.close)
        return r

    def ask(self, r, question, query_text):
        return r.retrieve(question, self.resolver.resolve_question(question), query_text=query_text)


class TestFusionPrimitives(unittest.TestCase):
    def test_rrf_formula_and_absent_list(self):
        self.assertEqual(RRF_K, 60)
        self.assertAlmostEqual(rrf(1, 1), 2 / 61, places=12)
        self.assertAlmostEqual(rrf(3, None), 1 / 63, places=12)
        self.assertAlmostEqual(rrf(None, 7), 1 / 67, places=12)
        self.assertEqual(rrf(None, None), 0)
        self.assertGreater(rrf(1, 1), rrf(1, None))

    def test_competition_ranks_share_a_rank_for_equal_scores(self):
        r = competition_ranks([(1, 0.9), (2, 0.9), (3, 0.5), (4, 0.7)])
        self.assertEqual((r[1], r[2], r[4], r[3]), (1, 1, 3, 4))

    def test_final_tie_break_order(self):
        c = {1: {"lexical_rank": None, "rrf": 0.03}, 2: {"lexical_rank": 3, "rrf": 0.03}, 3: {"lexical_rank": 3, "rrf": 0.03}, 4: {"lexical_rank": 3, "rrf": 0.03},
             5: {"lexical_rank": 1, "rrf": 0.02}, 6: {"lexical_rank": None, "rrf": 0.04}}
        dates = {1: "2024-05-01", 2: "2024-01-01", 3: "2024-06-01", 4: "2024-06-01", 5: "2024-09-09", 6: "2020-01-01"}
        self.assertEqual(fuse_order(c, dates), [6, 3, 4, 2, 1, 5])
        # rrf desc: 6 first; then equal rrf: lexical rank asc (3, 4, 2 all rank 3; None last); date desc (3 and 4 on 2024-06-01, then 2); id asc (3 before 4); 5 has the lowest rrf
        self.assertEqual(fuse_order(c, dates), fuse_order(dict(reversed(list(c.items()))), dates))


class TestSemanticRetrieval(Base):
    def test_paraphrase_is_found_without_any_shared_word(self):
        r = self.retriever()
        q = "In client A12345's meetings, which mention a chief and providers?"
        self.assertEqual(r.lexical.retrieve(q, self.resolver.resolve_question(q), query_text="chief providers").outcome, mr.NO_LEXICAL_MATCH)
        ev = self.ask(r, q, "chief providers")
        self.assertEqual((ev.outcome, ev.rank_method), (mr.OK, "rrf"))
        top = ev.hits[0]
        self.assertEqual(top.meeting_id, 1)
        self.assertEqual((top.lexical_rank, top.semantic_rank), (None, 1))
        self.assertEqual(top.semantic_sentence, CFO_SENT)
        self.assertGreater(top.semantic_score, 0.5)
        self.assertIn(CFO_SENT, top.snippet)                                       # the sentence that matched by meaning is part of the evidence text
        self.assertEqual(ev.matched_count, 0)

    def test_similarity_ranking_orders_meetings_by_best_sentence(self):
        r = self.retriever()
        ev = self.ask(r, "What did client A12345 discuss about hedging?", "hedging currency")
        sems = [h for h in ev.hits if h.semantic_rank is not None]
        self.assertEqual(sems[0].meeting_id, 1)                                    # its second sentence is about hedging
        self.assertEqual(sems[0].semantic_sentence, "Hedging policy was requested.")
        scores = [h.semantic_score for h in sems]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_lexical_and_semantic_lists_are_fused_with_rrf(self):
        r = self.retriever()
        ev = self.ask(r, "What did client A12345 discuss about hedging?", "hedging")
        m1 = next(h for h in ev.hits if h.meeting_id == 1)
        self.assertEqual((m1.lexical_rank, m1.semantic_rank), (1, 1))
        self.assertAlmostEqual(m1.rrf_score, 2 / 61, places=10)
        self.assertEqual(ev.hits[0].meeting_id, 1)
        for h in ev.hits:
            self.assertAlmostEqual(h.rrf_score, rrf(h.lexical_rank, h.semantic_rank), places=10)
        self.assertEqual([h.rank for h in ev.hits], list(range(1, len(ev.hits) + 1)))
        cands = {h.meeting_id: {"lexical_rank": h.lexical_rank, "rrf": h.rrf_score} for h in ev.hits}
        self.assertEqual([h.meeting_id for h in ev.hits], fuse_order(cands, {h.meeting_id: h.meeting_date for h in ev.hits}))
        f = ev.fusion
        self.assertEqual((f["k"], f["method"]), (60, "reciprocal_rank_fusion"))
        self.assertEqual(f["tie_break"], ["rrf_score desc", "lexical_rank asc (absent last)", "meeting_date desc", "meeting_id asc"])

    def test_ties_from_repeated_template_sentences_are_deterministic(self):
        r = self.retriever()
        q = "What was said about currency exposure and hedging for client C12399?"
        a, b = self.ask(r, q, "currency exposure hedging"), self.ask(r, q, "currency exposure hedging")
        self.assertEqual([h.meeting_id for h in a.hits], [h.meeting_id for h in b.hits])
        self.assertEqual({h.semantic_rank for h in a.hits}, {1})                    # six identical sentences: one shared semantic rank
        self.assertEqual({h.semantic_score for h in a.hits}, {a.hits[0].semantic_score})
        self.assertEqual([h.meeting_id for h in a.hits], [25, 24, 23, 22, 21, 20])   # equal fused score and lexical rank order: date descending
        self.assertEqual([h.lexical_rank for h in a.hits], [1, 2, 3, 4, 5, 6])

    def test_entity_scope_is_applied_before_semantic_search_and_cannot_be_crossed(self):
        r = self.retriever()
        ev = self.ask(r, "Which of client A12350's meetings mention a chief and providers?", "chief providers")
        self.assertEqual([h.meeting_id for h in ev.hits], [5])                     # the CFO meetings of A12345 / B12345 are more similar but out of scope
        self.assertEqual(ev.semantic["scope_size"], 1)
        ev = self.ask(r, "Which meetings of group 346 mention a chief and providers?", "chief providers")
        self.assertTrue({h.meeting_id for h in ev.hits} <= {1, 2, 3, 4, 6})
        self.assertEqual(ev.semantic["scope_size"], 5)
        self.assertTrue(all(h.client_id in ("A12345", "B12345") for h in ev.hits))
        ev = self.ask(r, "Which meetings of client A12345 mention a chief and providers?", "chief providers")
        self.assertTrue(all(h.client_id == "A12345" for h in ev.hits))
        self.assertNotIn(4, {h.meeting_id for h in ev.hits})                       # B12345's identical sentence
        self.assertEqual(ev.semantic["scope_size"], 4)

    def test_scope_gates_never_reach_the_embedding_api(self):
        r = self.retriever()
        for q in ("What concerns did client 12345 raise about hedging?", "What concerns did client E12345 raise about hedging?", "What did relationship manager Carlos Gomez discuss about hedging?"):
            with self.subTest(q=q):
                ev = self.ask(r, q, "concerns hedging")
                self.assertFalse(ev.success)
        self.assertEqual(self.emb.query_calls, [])

    def test_exactly_one_query_embedding_per_request_and_none_stored(self):
        r = self.retriever()
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.idx_dir.iterdir()}
        tmp_before = sorted(p.name for p in self.tmp.iterdir())
        for i in range(3):
            self.ask(r, "What did client A12345 discuss about hedging?", "hedging currency")
            self.assertEqual(len(self.emb.query_calls), i + 1)                     # one per retrieval request
        self.assertEqual(self.emb.doc_calls, [])                                   # documents are never embedded at runtime
        self.assertEqual({p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.idx_dir.iterdir()}, before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), tmp_before)
        for obj in (r, r.index):
            self.assertFalse(any(isinstance(v, np.ndarray) and v.shape == (DIM,) for v in vars(obj).values()))

    def test_exact_lexical_control_is_preserved(self):
        r = self.retriever()
        q = "Which of client A12345's meetings mention Sanjay LÃ³pez?"
        frozen = r.lexical.retrieve(q, self.resolver.resolve_question(q), query_text="sanjay lã³pez", top_k=20)
        ev = self.ask(r, q, "sanjay lã³pez")
        self.assertEqual(frozen.hits[0].meeting_id, 6)
        self.assertEqual(ev.hits[0].meeting_id, 6)
        self.assertEqual(ev.hits[0].lexical_rank, 1)
        self.assertEqual(ev.hits[0].semantic_rank, 1)

    def test_raw_mojibake_is_preserved_in_evidence(self):
        r = self.retriever()
        ev = self.ask(r, "Which of client A12345's meetings mention Sanjay LÃ³pez?", "sanjay lã³pez")
        top = ev.hits[0]
        self.assertEqual(top.semantic_sentence, MOJI_1)
        self.assertIn("LÃ³pez", top.snippet)
        self.assertNotIn("López", json.dumps(ev.to_dict(), ensure_ascii=False))

    def test_embedding_failure_degrades_explicitly_to_lexical_only(self):
        r = self.retriever(fail_query=True)
        q = "What did client A12345 discuss about hedging?"
        frozen = r.lexical.retrieve(q, self.resolver.resolve_question(q), query_text="hedging")
        ev = self.ask(r, q, "hedging")
        self.assertEqual(ev.semantic["status"], "unavailable")
        self.assertEqual(ev.semantic["error"]["status"], 403)
        self.assertEqual([h.meeting_id for h in ev.hits], [h.meeting_id for h in frozen.hits])
        self.assertEqual(ev.rank_method, "bm25")
        self.assertTrue(any("unavailable" in n for n in ev.notes))
        self.assertTrue(all(h.semantic_rank is None for h in ev.hits))

    def test_final_k_is_20_and_evidence_serializes(self):
        r = self.retriever()
        ev = self.ask(r, "What did client A12345 discuss about hedging?", "hedging")
        self.assertEqual((ev.top_k, mr.DEFAULT_TOP_K, CANDIDATE_DEPTH), (20, 20, 50))
        self.assertLessEqual(len(ev.hits), 20)
        small = r.retrieve("What did client A12345 discuss about hedging?", self.resolver.resolve_question("What did client A12345 discuss about hedging?"), query_text="hedging", top_k=2)
        self.assertEqual(len(small.hits), 2)
        d = json.loads(json.dumps(ev.to_dict(), default=str))
        for key in ("lexical_rank", "semantic_rank", "semantic_score", "semantic_sentence", "rrf_score", "meeting_id", "client_id", "meeting_date"):
            self.assertIn(key, d["hits"][0])
        self.assertEqual(d["semantic"]["model"], "gemini-embedding-2")
        self.assertTrue(isinstance(ev, V2Evidence) and isinstance(ev.hits[0], V2Hit))

    def test_a_listing_request_is_delegated_unchanged_with_no_embedding(self):
        r = self.retriever()
        q = "When was client A12345 most recently met?"
        ev = r.retrieve(q, self.resolver.resolve_question(q), query_text="")
        frozen = r.lexical.retrieve(q, self.resolver.resolve_question(q), query_text="")
        self.assertEqual((ev.rank_method, [h.meeting_id for h in ev.hits]), ("date_desc", [h.meeting_id for h in frozen.hits]))
        self.assertEqual(self.emb.query_calls, [])


class TestV2Service(Base):
    def service(self, *replies, adapter=None, structured=None):
        self.emb = FakeEmbedder()
        self.adapter = adapter or ScriptedAdapter(*replies)
        self.engine = FakeEngine(structured if structured is not None else s_ev())
        retr = V2Retriever(self.db, self.idx_dir, self.emb)
        s = V2Service(ServiceConfig(db_path=self.db), semantic_retriever=retr, resolver=self.resolver, engine=self.engine, adapter=self.adapter)
        self.addCleanup(s.close)
        return s

    def test_topical_question_uses_fused_evidence_and_the_v2_prompt(self):
        s = self.service("Meeting 1 records that the sponsor lacks a dedicated CFO [meeting: 1, 2024-01-10].")
        r = s.answer_question("Which of client A12345's meetings raise concerns about a chief and outside providers?")
        self.assertEqual((r.status, r.meeting_mode), ("ok", "topical"))
        self.assertEqual(len(self.emb.query_calls), 1)
        self.assertEqual(r.meeting_evidence["rank_method"], "rrf")
        ids = {h["meeting_id"]: h for h in r.meeting_evidence["hits"]}
        self.assertIn(1, ids)
        self.assertEqual(ids[1]["semantic_sentence"], CFO_SENT)
        p = self.adapter.prompts[0]
        self.assertIn("hybrid retrieval: lexical BM25 + semantic embedding similarity, reciprocal rank fusion", p)
        self.assertIn("semantic match", p)
        self.assertIn("12. In a hybrid-retrieval list", p)
        self.assertTrue(r.validation["valid"], r.validation)

    def test_listing_aggregate_structured_and_refused_paths_bypass_embeddings(self):
        cases = [("When was client A12345 most recently met?", ["Latest [meeting: 6, 2024-06-15]."], "listing"),
                 ("How many meetings did client A12345 have?", [sql_reply("SELECT COUNT(*) AS n FROM meetings_meta WHERE client_id = 'A12345'"), "It had 4 meetings [meetings]."], "aggregate"),
                 ("Which meetings of client A12345 happened since 2024-03-01?", [], "unsupported"),
                 ("What concerns did client 12345 raise about hedging?", [], None),
                 ("What is client A12345's latest Total AUM?", ["30.0 [performance]."], None)]
        for q, replies, mode in cases:
            with self.subTest(q=q):
                s = self.service(*replies, structured=s_ev(rows=((30.0,),), columns=("aum",)))
                r = s.answer_question(q)
                self.assertEqual(self.emb.query_calls, [], q)
                self.assertEqual(self.emb.doc_calls, [])
                self.assertEqual(r.meeting_mode, mode)
                if mode == "listing":
                    self.assertEqual(r.meeting_evidence["rank_method"], "date_desc")

    def test_date_relations_stay_unsupported_and_never_become_exact_dates(self):
        s = self.service(adapter=NoCalls())
        r = s.answer_question("Which meetings of client A12345 mention hedging after 2024-01-01?")
        self.assertEqual((r.status, r.failure["code"], r.meeting_evidence), ("unsupported", "date_relation_unsupported", None))
        self.assertEqual(self.emb.query_calls, [])

    def test_hybrid_topical_side_uses_semantic_retrieval_and_the_structured_side_stays_independent(self):
        s = self.service("Latest Total AUM is 30.0 [performance]. Meeting 1 lacks a CFO [meeting: 1, 2024-01-10].", structured=s_ev(rows=((30.0,),), columns=("aum",)))
        r = s.answer_question("What is client A12345's latest Total AUM and which meetings raise a chief and providers?")
        self.assertEqual((r.route, r.meeting_mode), ("hybrid", "topical"))
        self.assertEqual(len(self.emb.query_calls), 1)
        self.assertNotIn("aum", self.emb.query_calls[0].lower())
        self.assertEqual(self.engine.calls[0][1], "performance")


if __name__ == "__main__":
    unittest.main()
