"""V1-1 tests: term handling and the meeting-intent classifier. Pure functions plus the frozen resolver on a synthetic DB.

Run from the project root:  python -m unittest tests.v1.test_meeting_terms -v
"""
import ast
import shutil
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline.meeting_retrieval import derive_query
from src.baseline.question_routing import route_question
from src.v1 import meeting_intent as mi
from src.v1 import meeting_terms as mt
from tests.v1.fixtures import build_db


class TestTermRemoval(unittest.TestCase):
    def test_request_and_control_words_are_removed(self):
        a = mt.derive_topical_terms("How many meetings did the client have, and over what date range?")
        self.assertEqual(a.final_terms, ())
        self.assertEqual({r["term"]: r["category"] for r in a.removed}, {"many": "R1_request", "date": "R1_request", "range": "R1_request"})

    def test_field_names_are_removed(self):
        a = mt.derive_topical_terms("What sector and region and estimated deal size stage is it, and which companies?")
        self.assertEqual(a.final_terms, ())
        self.assertEqual({r["category"] for r in a.removed}, {"R1_request", "R2_field"})

    def test_cue_words_are_removed_as_their_own_category(self):
        a = mt.derive_topical_terms("What is the smallest number of distinct meetings, earliest and oldest?")
        self.assertEqual(a.final_terms, ())
        self.assertEqual({r["category"] for r in a.removed}, {"R4_cue"})

    def test_evidence_bearing_terms_are_preserved(self):
        a = mt.derive_topical_terms("Which meetings flag the absence of independent directors on the board?")
        self.assertEqual(a.final_terms, ("absence", "independent", "directors", "board"))
        self.assertEqual(a.removed, ())

    def test_topic_words_survive_next_to_removed_words(self):
        a = mt.derive_topical_terms("How many meetings discussed hedging or currency exposure?")
        self.assertEqual(a.final_terms, ("hedging", "currency", "exposure"))
        self.assertEqual([r["term"] for r in a.removed], ["many"])

    def test_audit_records_original_removed_final_and_reason(self):
        a = mt.derive_topical_terms("How many meetings mention hedging by sector?")
        self.assertEqual(a.original_terms, ("many", "hedging", "sector"))
        self.assertEqual(a.final_terms, ("hedging",))
        self.assertEqual({r["term"] for r in a.removed}, {"many", "sector"})
        for r in a.removed:
            self.assertTrue(r["reason"])
            self.assertIn(r["category"], mt.CATEGORY_REASON)
        d = a.to_dict()
        self.assertEqual(set(d), {"original_terms", "removed", "final_terms", "phrases", "hybrid", "wants_action_items", "meeting_date", "requested_fields"})

    def test_structured_vocabulary_is_removed_only_in_hybrid_questions(self):
        q = "What is the latest CI Total MOIC and what did the meetings say about the CFO?"
        single = mt.derive_topical_terms(q, hybrid=False)
        hybrid = mt.derive_topical_terms(q, hybrid=True)
        self.assertIn("total", single.final_terms)
        self.assertIn("ci", single.final_terms)
        self.assertEqual(hybrid.final_terms, ("say", "cfo") if "say" in hybrid.final_terms else ("cfo",))
        self.assertTrue({"ci", "total", "moic"} <= {r["term"] for r in hybrid.removed} | {"moic"})
        self.assertIn("R3_structured", {r["category"] for r in hybrid.removed})

    def test_router_signal_tokens_are_removed_in_hybrid(self):
        a = mt.derive_topical_terms("What is the latest AUM and what did they say about hedging?", hybrid=True, structured_signal_tokens={"aum", "hedging_x"})
        self.assertNotIn("aum", a.final_terms)
        self.assertIn("hedging", a.final_terms)

    def test_numbers_and_units_are_protected_but_multiples_go_in_hybrid(self):
        a = mt.derive_topical_terms("Which meeting discusses sensitivity to a 128 bps change?")
        self.assertIn("128", a.final_terms)
        self.assertIn("bps", a.final_terms)
        h = mt.derive_topical_terms("Clients below 1.0x with a meeting about a 128 bps change", hybrid=True)
        self.assertIn("128", h.final_terms)
        self.assertIn("0x", {r["term"] for r in h.removed})

    def test_quoted_phrases_are_kept_untouched(self):
        a = mt.derive_topical_terms('How many meetings said "lacks a dedicated CFO" by sector?')
        self.assertEqual(a.phrases, ("lacks a dedicated cfo",))
        self.assertEqual(a.final_terms, ())
        self.assertTrue(a.has_topic)
        self.assertEqual(mt.query_text_for(a), '"lacks a dedicated cfo"')

    def test_terms_that_are_already_topical_equal_the_frozen_derivation(self):
        for q in ("Which meetings flag concern about currency exposure and hedging?", "Who raised regulatory approval timing risks?",
                  "What did the meeting say about runway and burn?"):
            self.assertEqual(list(mt.derive_topical_terms(q).final_terms), derive_query(q)["terms"], q)

    def test_mojibake_and_accents_are_not_touched(self):
        a = mt.derive_topical_terms("Which meetings did Sanjay LÃ³pez attend?")
        self.assertIn("lã³pez", a.final_terms)

    def test_requested_fields_only_when_named(self):
        self.assertEqual(mt.requested_fields("When was client A12345 most recently met?"), ())
        self.assertEqual(mt.requested_fields("Which sector and region was that in?"), ("sector", "region"))
        self.assertEqual(mt.requested_fields("What was the estimated deal size at the series B stage?"), ("stage", "deal_size"))
        self.assertEqual(mt.requested_fields("Which company?"), ("company",))


class TestDateRelations(unittest.TestCase):
    def test_relations_with_dates_are_detected(self):
        for q, rel in [("Meetings since 2026-01-01", "since"), ("Meetings after 2024-10-25", "after"), ("Meetings before 2024-01-01", "before"),
                       ("Meetings until 2025-06-30", "until"), ("Meetings prior to 2024-01-01", "prior to"),
                       ("Meetings between 2023-01-01 and 2023-06-30", "between"), ("Meetings on or after 2024-05-05", "on or after"),
                       ("Meetings since March 2025", "since"), ("Meetings after the 2024-10-25 snapshot", "after")]:
            with self.subTest(q=q):
                self.assertEqual(mt.find_date_relation(q)["relation"], rel)

    def test_bare_dates_and_relations_without_dates_are_not_relations(self):
        for q in ("What happened in the meeting on 2022-10-01?", "Meetings after its latest performance snapshot", "Who joined after the deal closed?",
                  "How many meetings were held?"):
            with self.subTest(q=q):
                self.assertIsNone(mt.find_date_relation(q))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v1_terms_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.resolver = er.EntityResolver.from_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def intent(self, q, hybrid=None):
        qr = self.resolver.resolve_question(q)
        rt = route_question(q, qr)
        return mi.classify_meeting_intent(q, qr, routing=rt, hybrid=(rt.route == "hybrid") if hybrid is None else hybrid), rt


class TestIntentClassification(Base):
    def test_topical_question(self):
        it, _ = self.intent("What concerns did client A12345 raise about hedging?")
        self.assertEqual(it.mode, mi.TOPICAL)
        self.assertEqual(it.audit.final_terms, ("concerns", "hedging"))

    def test_latest_record_is_a_listing(self):
        it, _ = self.intent("When was client A12345 most recently met?")
        self.assertEqual((it.mode, it.cues), (mi.LISTING, ("latest",)))

    def test_action_items_of_a_meeting_is_a_listing(self):
        it, _ = self.intent("What action items were recorded for client A12345 at their last meeting?")
        self.assertEqual(it.mode, mi.LISTING)

    def test_filter_only_is_a_listing(self):
        it, _ = self.intent("Show the meetings for client A12345.")
        self.assertEqual(it.mode, mi.LISTING)
        self.assertTrue(it.filters_present)

    def test_aggregate_questions(self):
        cases = {"How many meetings did client A12345 have?": "how_many", "What was the largest estimated deal size for client A12345?": "extreme_deal_size",
                 "What was the smallest deal size across all meetings?": "extreme_deal_size", "What was the earliest meeting of client A12345?": "earliest",
                 "Over what date range did client A12345 meet us?": "date_range", "Which sectors are in client A12345's meetings?": "attribute_list",
                 "List the distinct regions of the meetings for client A12345.": "distinct", "How many meetings are at the series B stage?": "how_many"}
        for q, cue in cases.items():
            with self.subTest(q=q):
                it, _ = self.intent(q)
                self.assertEqual(it.mode, mi.AGGREGATE)
                self.assertIn(cue, it.cues)

    def test_a_topical_term_beats_a_count_cue(self):
        it, _ = self.intent("How many of client A12345's meetings mention hedging?")
        self.assertEqual(it.mode, mi.TOPICAL)
        self.assertEqual(it.audit.final_terms, ("hedging",))

    def test_aggregate_beats_listing_when_both_cues_exist(self):
        it, _ = self.intent("How many meetings did client A12345 have and when was the latest?")
        self.assertEqual(it.mode, mi.AGGREGATE)

    def test_no_topic_no_cue_no_filter_is_left_to_the_frozen_retriever(self):
        it, _ = self.intent("Meetings, please.")
        self.assertEqual(it.mode, mi.LISTING)
        self.assertFalse(it.filters_present)

    def test_date_relation_is_unsupported(self):
        for q in ("Which meetings of client A12345 happened since 2024-03-01?", "Meetings for client A12345 between 2024-01-01 and 2024-06-30",
                  "What was discussed before 2024-02-01 with client A12345?"):
            with self.subTest(q=q):
                it, _ = self.intent(q)
                self.assertEqual(it.mode, mi.UNSUPPORTED)
                self.assertIsNotNone(it.date_relation)
                self.assertIsNone(it.audit)

    def test_a_bare_date_stays_an_exact_date_filter(self):
        it, _ = self.intent("What were the action items from client A12345's meeting on 2024-03-01?")
        self.assertEqual(it.mode, mi.LISTING)
        self.assertEqual(it.audit.meeting_date, "2024-03-01")

    def test_hybrid_meeting_side_drops_structured_words(self):
        it, rt = self.intent("What is client A12345's latest Total AUM and how many meetings did they have?")
        self.assertEqual(rt.route, "hybrid")
        self.assertEqual(it.mode, mi.AGGREGATE)
        self.assertEqual(it.audit.final_terms, ())
        self.assertIn("R3_structured", {r["category"] for r in it.audit.removed})

    def test_hybrid_topical_and_listing(self):
        it, rt = self.intent("What is client A12345's latest Total AUM and what did the meetings say about hedging?")
        self.assertEqual((rt.route, it.mode, it.audit.final_terms), ("hybrid", mi.TOPICAL, ("hedging",)))
        it, rt = self.intent("What is client A12345's latest Total AUM and which company did their most recent meeting involve?")
        self.assertEqual((rt.route, it.mode), ("hybrid", mi.LISTING))
        self.assertEqual(it.audit.requested_fields, ("company",))


class TestNoSemanticMachinery(unittest.TestCase):
    """V1 adds no embeddings, vector store, semantic ranking, LLM reranking or other model."""

    FORBIDDEN = {"numpy", "scipy", "sklearn", "torch", "tensorflow", "faiss", "chromadb", "sentence_transformers", "transformers", "openai", "anthropic", "langgraph", "langchain"}

    def test_v1_imports_no_semantic_or_agent_library(self):
        root = Path(__file__).resolve().parents[2] / "src" / "v1"
        found = set()
        for f in root.glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    found |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module.split(".")[0])
        self.assertEqual(found & self.FORBIDDEN, set())

    def test_lexical_ranking_stays_the_frozen_bm25(self):
        from src.baseline import meeting_retrieval as mr
        self.assertEqual(mr.DEFAULT_TOP_K, 20)


if __name__ == "__main__":
    unittest.main()
