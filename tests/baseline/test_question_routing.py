"""Tests for the deterministic Baseline question router.

Questions here are written for these tests (they are not the benchmark questions).
Entity-awareness tests reuse the small synthetic database from the entity-resolver
tests. No Claude CLI calls, no benchmark run, no holdout access.

Run from the project root:  python -m unittest tests.baseline.test_question_routing -v
"""
import inspect
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline import question_routing as qr
from src.baseline.question_routing import AMBIGUOUS, HYBRID, INVESTMENT, MEETING, PERFORMANCE, route_question
from tests.baseline.test_entity_resolution import build_synthetic_db

INVESTMENT_CASES = [
    "What is the total amount invested by client A12345?",
    "Which RM is associated with client B12346's records?",
    "What are the largest investments?",
    "How many distinct deals has client A12345 invested in?",
    "How many investment records does client C12355 have?",
    "Which relationship manager has the most investment records?",
    "What is the total investment amount in USD for the Orion Infrastructure I deal?",
    "How many records carry deal ID DL100001?",
    "List the deal names for client D12376.",
    "Which account manager appears on the most records?",
]
PERFORMANCE_CASES = [
    "What is client A12345's latest MOIC?",
    "What is the latest AUM for client A12345?",
    "What is the IRR of client B12346?",
    "Show the latest performance snapshot for client C12355.",
    "What is the average Total AUM across all clients?",
    "Which client has the highest CI Total MOIC in its latest snapshot?",
    "How many clients have performance data?",
    "What is the Total AUM for group 346?",
    "How many clients have a latest IRR above 10%?",
]
MEETING_CASES = [
    "What concerns were raised in client A12345's meetings?",
    "What action items were recorded for client A12345?",
    "When was the most recent meeting with client D12349?",
    "Which topics were discussed with client A12346?",
    "What did client A12345 say in their last meeting?",
    "Which meetings did Sanjay attend?",
    "How many meetings took place in 2023 with fintech companies in EMEA?",
    "Which meeting had the largest estimated deal size?",
    "Summarise the notes from client A12346's meetings.",
    "Who met client B12463 last?",
]
HYBRID_CASES = [
    "Which clients with MOIC below 1x raised concerns in meetings?",
    "What is client A12345's total investment exposure and what did they discuss in their latest meeting?",
    "Which clients with a latest IRR above 10% had a meeting in 2025?",
    "How many investment records and how many meetings does client B12346 have?",
    "What is the latest MOIC and when was the most recent meeting for client A12345?",
    "What is the total investment amount for client C12355 and which company was in their latest meeting?",
    "List the deal names in its investment records and the sectors in its meetings.",
    "How many meetings took place after the latest performance snapshot?",
    "Which clients with AUM above 10M discussed refinancing in meetings?",
    "What action items were recorded at the latest meeting for the client with the highest IRR?",
    "What is A12360's latest Private Equity multiple, and what came up in its most recent meeting?",
    "What is client A12345's Hedge Fund IRR, and what was discussed in their last meeting?",
]
AMBIGUOUS_CASES = [
    ("Tell me about Client A", "no_intent_signals"),
    ("Tell me about client A12345.", "no_intent_signals"),
    ("Show me client A12345", "no_intent_signals"),
    ("What is the latest?", "weak_signal_only"),
    ("What is the total amount?", "weak_signal_only"),
    ("How many are Active?", "status_source_unclear"),
    ("How many clients are Dormant?", "status_source_unclear"),
    ("What is client A12345's total investment and latest MOIC?", "multiple_structured_sources"),
    ("How many meetings were held by relationship manager Priya Sharma?", "rm_meeting_link"),
    ("What is the latest return amount?", "conflicting_weak_signals"),
    ("", "no_intent_signals"),
    ("???", "no_intent_signals"),
]


class TestRoutesByCategory(unittest.TestCase):
    def check(self, cases, expected):
        for q in cases:
            with self.subTest(question=q):
                r = route_question(q)
                self.assertEqual(r.route, expected, r.rationale)
                self.assertEqual(r.status, "routed")
                self.assertIn(r.confidence, (qr.HIGH, qr.MEDIUM))
                self.assertEqual(r.ambiguity, ())

    def test_investment(self):
        self.check(INVESTMENT_CASES, INVESTMENT)

    def test_performance(self):
        self.check(PERFORMANCE_CASES, PERFORMANCE)

    def test_meeting(self):
        self.check(MEETING_CASES, MEETING)

    def test_hybrid(self):
        self.check(HYBRID_CASES, HYBRID)

    def test_ambiguous(self):
        for q, code in AMBIGUOUS_CASES:
            with self.subTest(question=q):
                r = route_question(q)
                self.assertEqual((r.route, r.status, r.confidence), (AMBIGUOUS, "ambiguous", qr.NONE), r.rationale)
                self.assertEqual([a.code for a in r.ambiguity][0], code)

    def test_closed_is_investment_only_because_the_value_exists_in_one_source(self):
        r = route_question("How many clients are Closed?")
        self.assertEqual((r.route, r.confidence, r.rule), (INVESTMENT, qr.MEDIUM, "R6_STATUS"))
        self.assertIn("Closed", r.rationale)

    def test_client_status_phrase_names_the_investments_field(self):
        self.assertEqual(route_question("What is the client status of A12345?").route, INVESTMENT)
        self.assertEqual(route_question("What is the performance status of A12345?").route, PERFORMANCE)


class TestLobMetricPhraseIsAStrongPerformanceSignal(unittest.TestCase):
    """Regression: a canonical LOB name (A15) directly naming a metric ("Private Equity multiple",
    "Hedge Fund IRR") was previously only a WEAK performance signal ("latest"/"multiple" alone),
    so a question combining it with a strong meeting phrase ("most recent meeting", "came up")
    routed to meeting-only (R5_SINGLE_FAMILY) instead of hybrid, and the resulting answer cited
    performance evidence that was never fetched."""

    def test_the_exact_reported_question_routes_to_hybrid(self):
        r = route_question("What is A12360's latest Private Equity multiple, and what came up in its most recent meeting?")
        self.assertEqual((r.route, r.status, r.rule), (HYBRID, "routed", "R3_HYBRID"))
        self.assertTrue(any(s.family == PERFORMANCE and s.strength == qr.STRONG and s.rule == "P_LOB_METRIC" for s in r.signals))
        self.assertTrue(any(s.family == MEETING and s.strength == qr.STRONG for s in r.signals))

    def test_an_analogous_natural_language_hybrid_question_also_routes_correctly(self):
        r = route_question("What is client A12345's Hedge Fund IRR, and what was discussed in their last meeting?")
        self.assertEqual((r.route, r.rule), (HYBRID, "R3_HYBRID"))

    def test_the_lob_metric_phrase_alone_with_no_meeting_signal_stays_performance(self):
        r = route_question("What is the latest Real Estate MOIC for client A12360?")
        self.assertEqual((r.route, r.rule), (PERFORMANCE, "R5_SINGLE_FAMILY"))

    def test_bare_multiple_or_latest_without_a_lob_phrase_is_unaffected_and_stays_weak(self):
        # the fix is scoped to "<LOB phrase> <metric word>"; a bare "multiple" or "latest" next to
        # a strong meeting signal must still route to meeting-only, exactly as before this fix.
        r = route_question("What is the latest multiple, and what came up in its most recent meeting?")
        self.assertEqual((r.route, r.rule), (MEETING, "R5_SINGLE_FAMILY"))

    def test_existing_structured_only_and_meeting_only_cases_are_unaffected(self):
        for q in INVESTMENT_CASES:
            with self.subTest(question=q):
                self.assertEqual(route_question(q).route, INVESTMENT)
        for q in PERFORMANCE_CASES:
            with self.subTest(question=q):
                self.assertEqual(route_question(q).route, PERFORMANCE)
        for q in MEETING_CASES:
            with self.subTest(question=q):
                self.assertEqual(route_question(q).route, MEETING)


class TestKeywordPresenceAloneDoesNotDecide(unittest.TestCase):
    def test_a_structured_word_that_is_the_topic_of_a_meeting_question_stays_meeting(self):
        for q in ["What did the client say about MOIC in the meeting?", "Which meetings discussed IRR?", "Which meetings had MOIC discussions?",
                  "Did anyone raise concerns about AUM in the meeting?", "What was discussed about the Orion Infrastructure I deal?",
                  "How many investments were discussed in meetings?", "Which deals were mentioned in the meetings?"]:
            with self.subTest(question=q):
                r = route_question(q)
                self.assertEqual(r.route, MEETING, r.rationale)
                self.assertTrue(r.source_cues[INVESTMENT]["topic"] or r.source_cues[PERFORMANCE]["topic"])
                self.assertEqual(r.source_cues[INVESTMENT]["strong"] + r.source_cues[PERFORMANCE]["strong"], [])

    def test_the_same_word_as_a_structured_filter_makes_it_hybrid(self):
        for q in ["Which clients with MOIC above 2x discussed liquidity in meetings?", "Which clients with IRR below 5% raised concerns?",
                  "What is the latest MOIC and what did the client discuss?"]:
            with self.subTest(question=q):
                r = route_question(q)
                self.assertEqual(r.route, HYBRID, r.rationale)
                self.assertEqual(r.source_cues[PERFORMANCE]["topic"], [])

    def test_deal_size_is_a_meeting_field_not_an_investment_deal(self):
        r = route_question("Which meeting had the largest estimated deal size?")
        self.assertEqual(r.route, MEETING)
        self.assertEqual(r.source_cues[INVESTMENT]["strong"], [])
        self.assertEqual(route_question("What is the total for each deal?").route, INVESTMENT)

    def test_latest_meeting_is_not_a_performance_signal(self):
        r = route_question("When was the latest meeting with client A12345?")
        self.assertEqual(r.route, MEETING)
        self.assertEqual(r.source_cues[PERFORMANCE], {"strong": [], "weak": [], "topic": []})
        self.assertEqual(route_question("What is the latest MOIC?").route, PERFORMANCE)

    def test_investor_and_investment_stage_are_not_investment_signals(self):
        self.assertEqual(route_question("Which investors were mentioned in the meetings?").route, MEETING)
        r = route_question("How many meetings are at the series B investment stage?")
        self.assertEqual(r.route, MEETING)
        self.assertEqual(r.source_cues[INVESTMENT]["strong"], [])

    def test_generic_words_do_not_route_on_their_own(self):
        for q in ["What is the total amount?", "What is the latest?", "How many records?", "Show me the returns"]:
            with self.subTest(question=q):
                self.assertEqual(route_question(q).route, AMBIGUOUS)

    def test_weak_words_do_not_override_a_strong_source(self):
        r = route_question("What is the latest total amount invested by client A12345?")
        self.assertEqual((r.route, r.confidence), (INVESTMENT, qr.MEDIUM))
        self.assertIn("latest", r.rationale)

    def test_case_and_punctuation_do_not_matter(self):
        self.assertEqual(route_question("WHAT IS THE LATEST MOIC???").route, PERFORMANCE)
        self.assertEqual(route_question("  what   were the CONCERNS raised, in the meeting?  ").route, MEETING)


class TestEntityAwareness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="routing_test_"))
        db = cls.tmp / "t.sqlite"
        build_synthetic_db(db)
        cls.resolver = er.EntityResolver.from_database(db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def route(self, question):
        return route_question(question, self.resolver.resolve_question(question))

    def test_an_entity_present_in_all_sources_does_not_make_the_question_hybrid(self):
        r = self.route("What is client A12345's latest MOIC?")
        self.assertEqual(r.route, PERFORMANCE)
        self.assertEqual([(c["entity_type"], c["canonical"]) for c in r.entity_cues], [("client", "A12345")])

    def test_a_resolved_deal_id_does_not_force_the_investment_route(self):
        r = self.route("What was discussed about DL100001 in the meetings?")
        self.assertEqual(r.route, MEETING)
        self.assertEqual([(c["entity_type"], c["status"]) for c in r.entity_cues], [("deal_id", "resolved")])
        self.assertEqual(self.route("How many records carry deal ID DL100001?").route, INVESTMENT)

    def test_a_meeting_only_client_does_not_force_the_meeting_route(self):
        r = self.route("What is B12463's latest MOIC?")
        self.assertEqual(r.route, PERFORMANCE)
        self.assertTrue(any("meeting-only client" in n for n in r.notes))
        self.assertEqual(self.route("What concerns were raised for B12463?").route, MEETING)

    def test_ambiguous_entities_are_preserved_not_fixed(self):
        r = self.route("Show the investments for client 12345.")
        self.assertEqual(r.route, INVESTMENT)
        (a,) = r.entities_needing_attention
        self.assertEqual((a["text"], a["status"], a["candidate_count"]), ("12345", "ambiguous", 4))
        self.assertTrue(any("unresolved or ambiguous" in n for n in r.notes))

    def test_unknown_entities_are_preserved_not_fixed(self):
        r = self.route("What is the latest MOIC for client E12345?")
        self.assertEqual(r.route, PERFORMANCE)
        (a,) = r.entities_needing_attention
        self.assertEqual((a["status"], a["canonical"]), ("not_found", None))

    def test_a_resolved_rm_with_meetings_is_not_routed_on_an_assumed_link(self):
        r = self.route("How many meetings were held by Priya Sharma?")
        self.assertEqual((r.route, r.rule), (AMBIGUOUS, "R2_RM_MEETING_LINK"))
        self.assertEqual(r.ambiguity[0].kind, qr.UNSUPPORTED_ASSUMPTION)

    def test_an_rm_with_other_structured_intent_is_still_routed(self):
        self.assertEqual(self.route("How many investment records does Priya Sharma have?").route, INVESTMENT)
        self.assertEqual(self.route("Which clients with MOIC above 2x met relationship manager Priya Sharma?").route, HYBRID)

    def test_routing_without_an_entity_result_still_works(self):
        r = route_question("What is the latest MOIC for client A12345?")
        self.assertEqual((r.route, r.entity_cues, r.entities_needing_attention), (PERFORMANCE, (), ()))

    def test_unrelated_words_inside_entity_names_are_not_signals(self):
        r = self.route("Tell me about Summit Credit Opportunities.")
        self.assertEqual(r.route, AMBIGUOUS)        # 'Credit' or 'Opportunities' add no source signal
        self.assertEqual(r.rule, "R1_NO_SIGNALS")


class TestResultStructure(unittest.TestCase):
    ALL = INVESTMENT_CASES + PERFORMANCE_CASES + MEETING_CASES + HYBRID_CASES + [q for q, _ in AMBIGUOUS_CASES]

    def test_exactly_one_valid_route_and_consistent_status(self):
        for q in self.ALL:
            with self.subTest(question=q):
                r = route_question(q)
                self.assertIn(r.route, qr.ROUTES)
                self.assertEqual(r.route == AMBIGUOUS, r.status == "ambiguous")
                self.assertEqual(r.route == AMBIGUOUS, bool(r.ambiguity))
                self.assertEqual(r.confidence == qr.NONE, r.route == AMBIGUOUS)
                self.assertTrue(r.rationale)
                self.assertIn(r.rule, {rid for rid, _ in qr.ROUTING_RULES})

    def test_result_is_deterministic_and_serializable(self):
        for q in self.ALL:
            self.assertEqual(route_question(q), route_question(q))
            json.dumps(route_question(q).to_dict())

    def test_result_reports_signals_cues_and_rationale(self):
        r = route_question("Which clients with MOIC below 1x raised concerns in meetings?")
        d = r.to_dict()
        for key in ("route", "status", "confidence", "rule", "signals", "source_cues", "entity_cues", "entities_needing_attention", "ambiguity", "rationale", "notes"):
            self.assertIn(key, d)
        self.assertEqual([(s.family, s.phrase) for s in r.signals if s.strength == "strong"],
                         [("performance", "MOIC"), ("meeting", "raised"), ("meeting", "concerns"), ("meeting", "meetings")])
        self.assertIn("MOIC", r.rationale)

    def test_field_meaning_questions_are_noted_but_still_route_by_source(self):
        r = route_question("What does the L3Y_DIS field mean in the performance data?")
        self.assertEqual(r.route, PERFORMANCE)
        self.assertTrue(any("field_meaning_question" in n for n in r.notes))

    def test_router_is_rule_based_only(self):
        src = inspect.getsource(qr)
        for forbidden in ("claude_cli", "subprocess", "anthropic", "openai", "embedding", "difflib", "rapidfuzz"):
            self.assertNotIn(f"import {forbidden}", src)
            self.assertNotIn(f"from .{forbidden}", src)
        self.assertGreater(len(qr.SIGNAL_RULES), 20)
        self.assertEqual(len({r.id for r in qr.SIGNAL_RULES}), len(qr.SIGNAL_RULES))


if __name__ == "__main__":
    unittest.main()
