"""V2 tests: negation words survive in the semantic query text, and nothing else about retrieval changed.

Before the fix the semantic text was the V1 topical terms only, so the frozen stopwords "no" and "not" were lost and contractions were split
("doesn't" -> "doesn"). The lexical query text is unchanged. Run:  python -m unittest tests.v2.test_semantic_query -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline import meeting_retrieval as mr
from src.baseline.service import ServiceConfig
from src.v1.meeting_terms import derive_topical_terms, query_text_for
from src.v2 import embedding_index as ei
from src.v2 import retrieval as rv
from src.v2.retrieval import V2Retriever, semantic_query_text
from src.v2.service import V2Service
from src.v2.vertex import VertexConfig
from tests.v1.fixtures import FakeEngine, ScriptedAdapter, s_ev
from tests.v2.fixtures import FakeEmbedder, build_db


def old_text(query_text: str) -> str:
    """The semantic text as built before this fix."""
    return " ".join(query_text.replace('"', " ").split())


def both(question: str, entities=()) -> tuple[str, str, str]:
    qt = query_text_for(derive_topical_terms(question, entities))       # the service removes resolved entity text before deriving terms
    return qt, old_text(qt), semantic_query_text(question, qt)


class TestNegationPreserved(unittest.TestCase):
    def test_no_is_preserved_in_position(self):
        qt, before, after = both("Which meetings raised that the company has no full-time finance chief?")
        self.assertEqual(before, "full time finance chief")                      # "no" was lost
        self.assertEqual(after, "no full time finance chief")
        self.assertEqual(qt, "full time finance chief")                          # the lexical text is untouched

    def test_not_is_preserved(self):
        _, before, after = both("Which meetings say the board was not independent?")
        self.assertNotIn("not", before.split())
        self.assertEqual(after, "board not independent")

    def test_never_and_without_are_preserved(self):
        _, before, after = both("Which meetings mention advisers who never met the team or acted without counsel?")
        self.assertIn("never", before.split())                                    # V1 already keeps these two
        self.assertIn("without", before.split())
        self.assertIn("never", after.split())
        self.assertIn("without", after.split())
        self.assertLess(after.split().index("never"), after.split().index("without"))

    def test_contractions_are_kept_whole(self):
        _, before, after = both("Which meetings say the sponsor doesn't have a CFO and isn't hiring one?")
        self.assertEqual(before, "sponsor doesn cfo isn hiring")                 # the negation fragment "t" was lost
        self.assertEqual(after, "sponsor doesn't cfo isn't hiring")
        self.assertNotIn("doesn ", after + " ")

    def test_other_negation_words(self):
        for word in ("nor", "neither", "cannot", "none", "nobody", "nothing", "nowhere"):
            with self.subTest(word=word):
                _, _, after = both(f"Which meetings say the sponsor has {word} for the audit trail?")
                self.assertIn(word, after.split())
        for word in ("wasn't", "won't", "couldn't", "hasn't"):
            with self.subTest(word=word):
                _, _, after = both(f"Which meetings say the sponsor {word} pay the invoice?")
                self.assertIn(word, after.split())

    def test_every_occurrence_and_order_are_preserved(self):
        _, _, after = both("Which meetings say there is no runway and not enough cash, and no board approval?")
        self.assertEqual(after.split(), ["no", "runway", "not", "enough", "cash", "no", "board", "approval"])

    def test_curly_apostrophe_and_uppercase(self):
        _, _, after = both("Which meetings say the sponsor DOESN’T have a CFO and is NOT hiring?")
        self.assertIn("doesn’t", after.split())
        self.assertIn("not", after.split())

    def test_quoted_phrase_negation_is_kept_once(self):
        qt, _, after = both('Which meetings said "no dedicated CFO" and not a treasurer?')
        self.assertIn('"no dedicated cfo"', qt)                                   # the lexical phrase is unchanged
        self.assertEqual(after.split().count("no"), 1)
        self.assertEqual(after, "not treasurer no dedicated cfo")

    def test_words_v1_removed_stay_removed(self):
        _, _, after = both("How many of client A12345's meetings on 2024-03-01 say there is no sector risk in the deals?", ["A12345"])
        for gone in ("a12345", "2024", "many", "sector", "meetings", "how"):
            self.assertNotIn(gone, after.split())
        self.assertIn("no", after.split())
        self.assertIn("risk", after.split())

    def test_without_negation_the_text_is_exactly_the_previous_behavior(self):
        for q in ("Which meetings discuss hedging and currency exposure?", "What concerns did the sponsor raise about the runway and burn rate?",
                  "Which meetings flag the absence of independent directors on the board?", "Which meetings mention regulatory approval timing risks in APAC?",
                  "Which meetings mention Sanjay LÃ³pez and a 128 bps change?", 'Which meetings quote "sensitivity to a change in growth"?'):
            with self.subTest(q=q):
                qt, before, after = both(q)
                self.assertEqual(after, before)

    def test_a_v1_term_is_never_lost(self):
        # even if a term cannot be located in the question text, it is appended: the new text is always a superset of the old one
        qt = "alpha beta"
        self.assertEqual(semantic_query_text("a question without those words but with no hope", qt).split(), ["without", "no", "alpha", "beta"])
        self.assertEqual(semantic_query_text("", qt), "alpha beta")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v2_neg_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.idx_dir = cls.tmp / "idx"
        ei.build_index(cls.db, cls.idx_dir, FakeEmbedder(), workers=1)
        cls.resolver = er.EntityResolver.from_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestRetrievalUsesThePreservedNegation(Base):
    def test_the_embedded_text_keeps_the_negation_and_the_lexical_text_is_unchanged(self):
        emb = FakeEmbedder()
        r = V2Retriever(self.db, self.idx_dir, emb)
        self.addCleanup(r.close)
        seen = []
        original = r.lexical.retrieve
        r.lexical.retrieve = lambda *a, **kw: (seen.append(kw.get("query_text")), original(*a, **kw))[1]
        q = "In client A12345's meetings, which raised that the company has no full-time finance chief?"
        qt = query_text_for(derive_topical_terms(q, ["A12345"]))
        ev = r.retrieve(q, self.resolver.resolve_question(q), query_text=qt)
        self.assertEqual(seen, [qt])                                              # the frozen lexical retriever got the V1 text as before
        self.assertNotIn("no", qt.split())
        self.assertEqual(emb.query_calls, ["no full time finance chief"])         # exactly one embedding, with the negation
        self.assertEqual(ev.semantic["query_text"], "no full time finance chief")
        self.assertEqual(ev.query["semantic_text"], "no full time finance chief")
        self.assertEqual(ev.filters["client_ids"], ["A12345"])                    # entity filtering unchanged and not part of the semantic text
        self.assertNotIn("a12345", emb.query_calls[0].lower())

    def test_service_level_negation_reaches_the_embedder_once(self):
        emb = FakeEmbedder()
        retr = V2Retriever(self.db, self.idx_dir, emb)
        s = V2Service(ServiceConfig(db_path=self.db), semantic_retriever=retr, resolver=self.resolver, engine=FakeEngine(s_ev()),
                      adapter=ScriptedAdapter("Meeting 1 lacks a CFO [meeting: 1, 2024-01-10]."))
        self.addCleanup(s.close)
        r = s.answer_question("Which of client A12345's meetings say the company has no full-time finance chief?")
        self.assertEqual(r.meeting_mode, "topical")
        self.assertEqual(len(emb.query_calls), 1)
        self.assertIn("no", emb.query_calls[0].split())
        self.assertNotIn("no", r.meeting_evidence["query"]["terms"])              # V1's lexical terms are unchanged
        self.assertEqual(r.status, "ok")


class TestRankingAndFusionConfigurationUnchanged(unittest.TestCase):
    def test_constants_and_configuration(self):
        self.assertEqual((rv.RRF_K, rv.CANDIDATE_DEPTH, mr.DEFAULT_TOP_K), (60, 50, 20))
        self.assertEqual(rv.TIE_BREAK, ("rrf_score desc", "lexical_rank asc (absent last)", "meeting_date desc", "meeting_id asc"))
        c = VertexConfig()
        self.assertEqual((c.model, c.dimensions, c.location, c.project), ("gemini-embedding-2", 128, "global", "investcorp-assignment"))
        self.assertAlmostEqual(rv.rrf(1, 1), 2 / 61, places=12)
        self.assertEqual(rv.competition_ranks([(1, 0.5), (2, 0.5), (3, 0.1)]), {1: 1, 2: 1, 3: 3})


if __name__ == "__main__":
    unittest.main()
