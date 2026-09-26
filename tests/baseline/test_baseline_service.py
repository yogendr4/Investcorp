"""Tests for the Baseline service (orchestration, synthesis, validation).

Evidence and Claude replies are faked; the resolver and router are the real ones, on a small
synthetic database. No Claude CLI call is made. No benchmark questions.

Run from the project root:  python -m unittest tests.baseline.test_baseline_service -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from src.baseline import answer_validation as av
from src.baseline import entity_resolution as er
from src.baseline import meeting_retrieval as mr
from src.baseline import service as sv
from src.baseline import structured_query as sq
from src.baseline import synthesis as sy
from src.baseline.claude_cli import CliResult, ErrorClass
from tests.baseline.test_meeting_retrieval import build_db

Q_INV = "How many investment records does client A12345 have?"
Q_PERF = "What is client A12345's latest Total AUM?"
Q_MEET = "What concerns were raised in client A12345's meetings?"
Q_HYB = "What is client A12345's latest Total AUM, and what did their meetings say about the CFO?"


def s_ev(rows=((3,),), columns=("n",), source="investments", outcome=sq.OK, error=None, route="investment"):
    return sq.StructuredEvidence("q", route, outcome, sql="SELECT ...", columns=columns, rows=tuple(rows), row_count=len(rows), source_tables=(source,),
                                 objects_used=(source,), rules_applied=("rule applied",), error=error)


def hit(mid=1, date="2024-01-10", client="A12345", action=None, snippet="the sponsor lacks a dedicated [[CFO]]"):
    return mr.MeetingHit(1, mid, client, 346, date, "Orchid Ventures", "fintech", "EMEA", "seed", "209k USD", 1.5, snippet, ("cfo",), ("summary",),
                         "recorded" if action is not None else "not_recorded", action, 120)


def m_ev(hits=(hit(),), outcome=mr.OK, error=None, filtered=2, matched=1, terms=("concerns",)):
    return mr.MeetingEvidence("q", outcome, hits=tuple(hits), filtered_meeting_count=filtered, matched_count=matched, fewer_than_k=True, rank_method="bm25",
                              query={"terms": list(terms), "phrases": []}, filters={"client_ids": ["A12345"], "group_id": None, "rationale": ["client_id = A12345"]}, error=error)


def cli_ok(text):
    return CliResult(success=True, result_text=text, model="claude-sonnet-5", cli_version="2.1.283", exit_code=0, timing={"wall_clock_ms": 1.0})


class FakeAdapter:
    def __init__(self, result=None):
        self.result, self.prompts = result, []

    def run(self, prompt):
        self.prompts.append(prompt)
        return self.result


class FakeEngine:
    def __init__(self, evidence):
        self.evidence, self.calls = evidence, []

    def run(self, question, route, resolution=None):
        self.calls.append((question, route))
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence


class FakeRetriever:
    def __init__(self, evidence):
        self.evidence, self.calls = evidence, []

    def retrieve(self, question, resolution=None, **kw):
        self.calls.append((question, kw))
        return self.evidence


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="svc_test_"))
        db = cls.tmp / "t.sqlite"
        build_db(db)
        cls.resolver = er.EntityResolver.from_database(db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def svc(self, structured=None, meeting=None, answer=None, resolver=None):
        self.adapter = FakeAdapter(answer if isinstance(answer, CliResult) else (cli_ok(answer) if answer is not None else None))
        self.engine, self.retriever = FakeEngine(structured), FakeRetriever(meeting)
        return sv.BaselineService(resolver=resolver or self.resolver, engine=self.engine, retriever=self.retriever, adapter=self.adapter)

    def assertNoWork(self):
        self.assertEqual((self.adapter.prompts, self.engine.calls, self.retriever.calls), ([], [], []))


class TestEndToEnd(Base):
    def test_investment(self):
        s = self.svc(structured=s_ev(), answer="Client A12345 has 3 investment records [investments].")
        r = s.answer_question(Q_INV)
        self.assertEqual((r.status, r.route, r.answer), (sv.OK, "investment", "Client A12345 has 3 investment records [investments]."))
        self.assertTrue(r.validation["valid"])
        self.assertIsNotNone(r.structured_evidence)
        self.assertIsNone(r.meeting_evidence)
        self.assertEqual(self.engine.calls, [(Q_INV, "investment")])
        self.assertEqual(self.retriever.calls, [])
        self.assertEqual(len(self.adapter.prompts), 1)
        self.assertEqual([m["canonical"] for m in r.resolution["mentions"]], ["A12345"])
        for key in ("resolution_ms", "routing_ms", "structured_ms", "synthesis_ms", "validation_ms", "total_ms"):
            self.assertIn(key, r.timing)
        self.assertIsNone(r.failure)

    def test_performance(self):
        ev = s_ev(rows=(("A12345", "2022-01-01", 30.0),), columns=("client_id", "as_of_date", "total_aum_amount"), source="performance", route="performance")
        r = self.svc(structured=ev, answer="The latest Total AUM for client A12345 is 30.0 as of 2022-01-01 [performance].").answer_question(Q_PERF)
        self.assertEqual((r.status, r.route), (sv.OK, "performance"))
        self.assertEqual(self.engine.calls, [(Q_PERF, "performance")])

    def test_meeting(self):
        r = self.svc(meeting=m_ev(), answer="Meeting 1 records that the sponsor lacks a dedicated CFO [meeting: 1, 2024-01-10].").answer_question(Q_MEET)
        self.assertEqual((r.status, r.route), (sv.OK, "meeting"))
        self.assertEqual((self.engine.calls, len(self.retriever.calls)), ([], 1))
        self.assertIsNotNone(r.meeting_evidence)
        self.assertIsNone(r.structured_evidence)

    def test_hybrid_runs_both_sources_independently(self):
        ev = s_ev(rows=(("A12345", "2022-01-01", 30.0),), columns=("client_id", "as_of_date", "total_aum_amount"), source="performance", route="performance")
        s = self.svc(structured=ev, meeting=m_ev(), answer="Latest Total AUM is 30.0 as of 2022-01-01 [performance]. Meeting 1 notes a CFO gap [meeting: 1, 2024-01-10].")
        r = s.answer_question(Q_HYB)
        self.assertEqual((r.status, r.route), (sv.OK, "hybrid"))
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.engine.calls[0][1], "performance")
        self.assertTrue(self.engine.calls[0][0].endswith(sv.HYBRID_STRUCTURED_SUFFIX))
        self.assertEqual(len(self.retriever.calls), 1)
        query_text = self.retriever.calls[0][1]["query_text"]
        self.assertNotIn("aum", query_text)                              # structured words are not searched in meetings
        self.assertIn("cfo", query_text)
        self.assertEqual(set(self.retriever.calls[0][1]), {"query_text"})  # the structured result does not filter the meeting search
        self.assertEqual(len(self.adapter.prompts), 1)
        self.assertIn("STRUCTURED EVIDENCE", self.adapter.prompts[0])
        self.assertIn("MEETING EVIDENCE", self.adapter.prompts[0])
        self.assertIsNotNone(r.structured_evidence)
        self.assertIsNotNone(r.meeting_evidence)


class TestNoUnnecessaryClaudeCalls(Base):
    def test_clarification_and_unsupported_cases_do_no_work(self):
        cases = [("Show the investments for client 12345.", sv.CLARIFICATION, "ambiguous_entity"),
                 ("Tell me about client A12345.", sv.CLARIFICATION, "no_intent_signals"),
                 ("What is the latest Total AUM for client E12345?", sv.CLARIFICATION, "entity_not_found"),
                 ("How many meetings were held by relationship manager Priya Sharma?", sv.UNSUPPORTED, "rm_meeting_link"),
                 ("", sv.CLARIFICATION, "empty_question"),
                 ("   ", sv.CLARIFICATION, "empty_question")]
        for q, status, code in cases:
            with self.subTest(question=q):
                r = self.svc(structured=s_ev(), meeting=m_ev(), answer="x").answer_question(q)
                self.assertEqual((r.status, r.failure["code"]), (status, code))
                self.assertIsNone(r.answer)
                self.assertTrue(r.message)
                self.assertNoWork()

    def test_ambiguous_entity_lists_candidates_and_never_guesses(self):
        r = self.svc().answer_question("Show the investments for client 12345.")
        (e,) = r.clarification["entities"]
        self.assertEqual((e["text"], e["candidates"]), ("12345", ["A12345", "B12345", "C12345"]))
        self.assertEqual(r.route, "investment")

    def test_at_most_ten_candidates_are_returned(self):
        many = tuple(er.Candidate("client", f"A{12300 + i}", "investments") for i in range(15))
        amb = er.Resolution("client", "123", er.AMBIGUOUS, None, many, er.NUMERIC_SUFFIX, "none", "15 candidates", "ambiguous", ("investments",), {"candidate_count": 15})
        fake = _FakeResolver(er.QuestionResolution("Show 123", (er.Mention("123", 5, 8, amb),)))
        r = self.svc(resolver=fake).answer_question("Show the investments for 123")
        self.assertEqual((r.status, len(r.clarification["entities"][0]["candidates"]), r.clarification["max_candidates"]), (sv.CLARIFICATION, 10, 10))
        self.assertEqual(r.resolution["mentions"][0]["candidate_count"], 15)
        self.assertNoWork()

    def test_ambiguous_route_is_not_silently_chosen(self):
        r = self.svc(structured=s_ev(), meeting=m_ev(), answer="x").answer_question("What is the total amount?")
        self.assertEqual((r.status, r.route, r.failure["code"]), (sv.CLARIFICATION, "ambiguous", "weak_signal_only"))
        self.assertNoWork()

    def test_unavailable_metric_is_unsupported_and_skips_synthesis(self):
        err = {"stage": "metric_availability", "code": "metric_not_available", "message": "'COP MOIC' does not exist in the performance data", "details": {}}
        r = self.svc(structured=s_ev(outcome=sq.UNAVAILABLE_METRIC, error=err, rows=(), source="performance", route="performance"), answer="x").answer_question(
            "What is client A12345's latest COP MOIC?")
        self.assertEqual((r.status, r.failure["code"], self.adapter.prompts), (sv.UNSUPPORTED, "metric_not_available", []))
        self.assertIsNone(r.answer)

    def test_a_client_with_no_data_in_the_source_gets_an_explicit_answer_without_claude(self):
        err = {"stage": "entities", "code": "no_data_for_entity", "message": "client B12463 has no performance data (present in: meetings)", "details": {}}
        r = self.svc(structured=s_ev(outcome=sq.NO_STRUCTURED_DATA, error=err, rows=(), source="performance", route="performance"), answer="x").answer_question(
            "What is B12463's latest Total AUM?")
        self.assertEqual((r.status, r.qualified, self.adapter.prompts), (sv.OK, True, []))
        self.assertIn("no performance data", r.answer)
        self.assertTrue(r.validation["valid"])


class _FakeResolver:
    def __init__(self, resolution):
        self.resolution = resolution

    def resolve_question(self, q):
        return self.resolution


class TestFailureBehavior(Base):
    def test_structured_query_failure(self):
        err = {"stage": "sql_validation", "code": "unauthorized_table", "message": "table 'meetings' is not approved", "details": {}}
        s = self.svc(structured=s_ev(outcome=sq.SQL_REJECTED, error=err, rows=()), answer="x")
        r = s.answer_question(Q_INV)
        self.assertEqual((r.status, r.failure["code"], r.failure["stage"]), (sv.ERROR, "unauthorized_table", "evidence"))
        self.assertIsNone(r.answer)
        self.assertEqual(self.adapter.prompts, [])
        self.assertIsNotNone(r.structured_evidence)
        self.assertEqual(len(self.engine.calls), 1)               # no retry

    def test_retrieval_empty_is_a_qualified_answer_not_a_claim_of_absence(self):
        empty = m_ev(hits=(), outcome=mr.NO_LEXICAL_MATCH, filtered=2, matched=0)
        r = self.svc(meeting=empty, answer="x").answer_question(Q_MEET)
        self.assertEqual((r.status, r.qualified, self.adapter.prompts), (sv.OK, True, []))
        self.assertIn("does not mean the topic was not discussed", r.answer)
        self.assertTrue(r.validation["valid"])

    def test_hybrid_partial_failure_structured_side_fails(self):
        err = {"stage": "execution", "code": "query_timeout", "message": "too slow", "details": {}}
        r = self.svc(structured=s_ev(outcome=sq.TIMEOUT, error=err, rows=(), source="performance", route="performance"), meeting=m_ev(), answer="x").answer_question(Q_HYB)
        self.assertEqual((r.status, r.failure["code"], r.answer), (sv.ERROR, "hybrid_partial_failure", None))
        self.assertIn("meeting", r.partial_evidence)
        self.assertNotIn("structured", r.partial_evidence)
        self.assertEqual(self.adapter.prompts, [])
        self.assertIn("too slow", r.message)

    def test_hybrid_partial_failure_meeting_side_fails(self):
        err = {"code": "index_missing", "message": "the meetings_fts index is missing", "details": {}}
        r = self.svc(structured=s_ev(rows=(("A12345", "2022-01-01", 30.0),), source="performance", route="performance"),
                     meeting=m_ev(hits=(), outcome=mr.INDEX_MISSING, error=err), answer="x").answer_question(Q_HYB)
        self.assertEqual((r.status, r.failure["code"]), (sv.ERROR, "hybrid_partial_failure"))
        self.assertIn("structured", r.partial_evidence)
        self.assertEqual(self.adapter.prompts, [])

    def test_hybrid_with_an_explicit_no_data_side_is_answered_and_says_so(self):
        err = {"stage": "entities", "code": "no_data_for_entity", "message": "client A12345 has no performance data", "details": {}}
        s = self.svc(structured=s_ev(outcome=sq.NO_STRUCTURED_DATA, error=err, rows=(), source="performance", route="performance"), meeting=m_ev(),
                     answer="There is no performance data for client A12345. Meeting 1 notes a CFO gap [meeting: 1, 2024-01-10].")
        r = s.answer_question(Q_HYB)
        self.assertEqual(r.status, sv.OK)
        self.assertIn("no performance data", self.adapter.prompts[0])

    def test_synthesis_failure_is_an_error_and_not_retried(self):
        failure = CliResult(success=False, error_class=ErrorClass.TIMEOUT, error_message="too slow", timed_out=True)
        s = self.svc(structured=s_ev(), answer=failure)
        r = s.answer_question(Q_INV)
        self.assertEqual((r.status, r.failure["stage"], r.failure["code"], r.answer), (sv.ERROR, "synthesis", "LLM_CLI_TIMEOUT", None))
        self.assertEqual(len(self.adapter.prompts), 1)
        self.assertIsNotNone(r.structured_evidence)

    def test_validation_failure_withholds_the_answer(self):
        r = self.svc(structured=s_ev(), answer="Client A12345 has 999 investment records [investments].").answer_question(Q_INV)
        self.assertEqual((r.status, r.answer, r.failure["stage"], r.failure["code"]), (sv.ERROR, None, "validation", "validation_failed"))
        self.assertIn("999", r.failure["details"]["rejected_answer"])
        self.assertFalse(r.validation["valid"])
        self.assertTrue(r.validation["invalid"])
        self.assertEqual(len(self.adapter.prompts), 1)                # no regeneration

    def test_unexpected_exceptions_become_an_error_response(self):
        r = self.svc(structured=RuntimeError("boom"), answer="x").answer_question(Q_INV)
        self.assertEqual((r.status, r.failure["code"]), (sv.ERROR, "internal_error"))
        self.assertIn("boom", r.message)

    def test_hybrid_with_two_structured_sources_is_unsupported(self):
        r = self.svc(structured=s_ev(), meeting=m_ev(), answer="x").answer_question("What is the total investment amount and the latest MOIC, and what concerns were raised in meetings?")
        self.assertEqual((r.status, r.failure["code"]), (sv.UNSUPPORTED, "hybrid_two_structured_sources"))
        self.assertNoWork()


class TestSynthesisInput(Base):
    def test_prompt_contains_only_the_question_route_entities_evidence_and_rules(self):
        s = self.svc(meeting=m_ev(hits=(hit(1), hit(2, "2024-03-01", action="Action: x"))), answer="Meeting 1 [meeting: 1, 2024-01-10].")
        s.answer_question(Q_MEET)
        p = self.adapter.prompts[0]
        for needle in ("Answer ONLY from the evidence", "[meeting: <meeting_id>, <YYYY-MM-DD>]", "Records, not clients", "NOT RECORDED (NULL)", "not that there were no action items",
                       "Meetings have no RM field", f"Question: {Q_MEET}", "Route: meeting", "- client: A12345", "MEETING EVIDENCE", "meeting 1 | 2024-01-10"):
            self.assertIn(needle, p)
        self.assertNotIn("investments", p.lower().split("evidence:")[1])     # no other source leaks in
        self.assertLess(len(p), 20_000)

    def test_null_action_items_are_serialized_as_not_recorded(self):
        text, _ = sy.serialize_evidence(None, m_ev(hits=(hit(1), hit(2, action="Action: x"))), None, None)
        self.assertIn("action_items: NOT RECORDED (NULL)", text)
        self.assertIn('action_items: RECORDED: "Action: x"', text)

    def test_evidence_is_bounded(self):
        many = tuple(hit(i, snippet="word " * 60) for i in range(1, 21))
        rows = tuple((i, "x" * 200) for i in range(50))
        text, trimmed = sy.serialize_evidence(s_ev(rows=rows, columns=("a", "b")), m_ev(hits=many), None, None, max_chars=4000)
        self.assertTrue(trimmed)
        self.assertLess(len(text), 4400)
        self.assertIn("left out", text)
        small, trimmed = sy.serialize_evidence(s_ev(), m_ev(), None, None)
        self.assertFalse(trimmed)

    def test_structured_evidence_serialization_names_the_source_and_rules(self):
        text, _ = sy.serialize_evidence(s_ev(rows=(("GBP", 26101199982.5),), columns=("c", "t")), None, None, None)
        self.assertIn("source: investments", text)
        self.assertIn("rules applied: rule applied", text)
        self.assertIn('row: ["GBP", 26101199982.5]', text)


class TestValidator(Base):
    def check(self, answer, route="investment", structured=None, meeting=None, question="q", **kw):
        return av.validate_answer(answer, route, structured, meeting, question, **kw)

    def test_empty_answer(self):
        for text in ("", "   ", None):
            self.assertEqual(self.check(text, structured=s_ev()).valid, False)

    def test_required_evidence_is_present(self):
        self.assertFalse(self.check("Answer [investments].", "investment", None, None).valid)
        self.assertFalse(self.check("Answer [meeting: 1, 2024-01-10].", "meeting", None, None).valid)
        self.assertFalse(self.check("Answer.", "hybrid", s_ev(), None).valid)
        self.assertFalse(self.check("Answer.", "hybrid", None, m_ev()).valid)
        self.assertTrue(self.check("Only 3 records [investments]; meeting 1 [meeting: 1, 2024-01-10].", "hybrid", s_ev(), m_ev()).valid)

    def test_failed_evidence_cannot_back_an_answer(self):
        err = {"code": "x", "message": "y", "details": {}}
        self.assertFalse(self.check("3 records [investments].", "investment", s_ev(outcome=sq.SQL_REJECTED, error=err)).valid)

    def test_numbers_must_come_from_the_evidence(self):
        ev = s_ev(rows=(("GBP", 26101199982.5), ("EUR", 100.0)), columns=("c", "t"))
        self.assertTrue(self.check("GBP totals 26,101,199,982.50 [investments].", structured=ev).valid)
        self.assertTrue(self.check("GBP totals about 26.1 billion [investments].", structured=ev).valid)
        self.assertFalse(self.check("GBP totals 27.5 billion [investments].", structured=ev).valid)
        self.assertFalse(self.check("The two totals differ by 26,101,199,882.5 [investments].", structured=ev).valid)   # a calculation not in the evidence
        pct = s_ev(rows=(("C12355", -0.0483),), columns=("c", "irr"), source="performance", route="performance")
        self.assertTrue(self.check("The IRR is -0.0483, or 4.83% below zero [performance].", "performance", pct).valid)
        moic = s_ev(rows=((1.33,),), columns=("m",), source="performance", route="performance")
        self.assertTrue(self.check("MOIC is 1.33x [performance].", "performance", moic).valid)
        self.assertFalse(self.check("MOIC is 1.83x [performance].", "performance", moic).valid)

    def test_small_integers_and_years_are_only_warnings(self):
        r = self.check("There are 3 records [investments] across 7 currencies.", structured=s_ev())
        self.assertTrue(r.valid)
        self.assertTrue(any("small integers" in w for w in r.warnings))

    def test_dates_must_come_from_the_evidence(self):
        self.assertTrue(self.check("Meeting 1 [meeting: 1, 2024-01-10].", "meeting", meeting=m_ev()).valid)
        self.assertFalse(self.check("Meeting 1 on 2023-05-05 [meeting: 1, 2024-01-10].", "meeting", meeting=m_ev()).valid)

    def test_meeting_references_must_exist_in_the_evidence(self):
        ev = m_ev(hits=(hit(1, "2024-01-10"),))
        self.assertTrue(self.check("See [meeting: 1, 2024-01-10].", "meeting", meeting=ev).valid)
        for bad in ("See [meeting: 99, 2024-01-10].", "See [meeting: 1, 2024-01-11].", "See [meeting 1].", "See [meeting: one, 2024-01-10]."):
            with self.subTest(answer=bad):
                self.assertFalse(self.check(bad, "meeting", meeting=ev).valid)

    def test_source_references_must_match_the_source_used(self):
        self.assertTrue(self.check("3 records [investments].", structured=s_ev()).valid)
        self.assertFalse(self.check("3 records [performance].", structured=s_ev()).valid)

    def test_missing_references_are_warnings(self):
        r = self.check("There are 3 records.", structured=s_ev())
        self.assertTrue(r.valid)
        self.assertTrue(any("no [investments]" in w for w in r.warnings))

    def test_answer_may_not_deny_evidence_it_was_given(self):
        ev = s_ev(rows=(("A12345", "2022-01-01", 30.0),), columns=("c", "d", "a"), source="performance", route="performance")
        self.assertFalse(self.check("The latest Total AUM is not available.", "performance", ev).valid)
        self.assertTrue(self.check("The latest Total AUM for A12345 is 30.0 on 2022-01-01 [performance]; the currency is not available.", "performance", ev).valid)

    def test_null_action_items_are_not_turned_into_none(self):
        ev = m_ev(hits=(hit(1, action=None),))
        for bad in ("There were no action items [meeting: 1, 2024-01-10].", "The meeting had no action items [meeting: 1, 2024-01-10].",
                    "No action items were agreed [meeting: 1, 2024-01-10].", "It ended without any action items [meeting: 1, 2024-01-10]."):
            with self.subTest(answer=bad):
                self.assertFalse(self.check(bad, "meeting", meeting=ev).valid)
        for good in ("Action items are not recorded for this meeting [meeting: 1, 2024-01-10].", "No action items were recorded for meeting 1 [meeting: 1, 2024-01-10]; the field is not recorded."):
            with self.subTest(answer=good):
                self.assertTrue(self.check(good, "meeting", meeting=ev).valid)

    def test_recorded_action_items_can_be_described_normally(self):
        ev = m_ev(hits=(hit(1, action="Action: introduce legal counsel."),))
        self.assertTrue(self.check("The action item is to introduce legal counsel [meeting: 1, 2024-01-10].", "meeting", meeting=ev).valid)

    def test_rm_and_status_stay_record_level(self):
        ev = s_ev(rows=(("Sara Khan", 53), ("Rahul Mehta", 44)), columns=("account_rm", "n"))
        self.assertTrue(self.check("Client B12346's records are spread over 2 RMs: Sara Khan (53) and Rahul Mehta (44) [investments].", structured=ev).valid)
        for bad in ("The RM of client B12346 is Sara Khan [investments].", "Client B12346 is managed by Sara Khan [investments].", "Client status is Closed [investments].",
                    "Client A12345 is Closed [investments].", "The relationship manager is Sara Khan [investments].", "Client B12346 has a single relationship manager [investments].",
                    "Meetings were held by relationship manager Sara Khan."):
            with self.subTest(answer=bad):
                self.assertFalse(self.check(bad, structured=ev).valid)

    def test_service_withholds_a_client_wide_status_claim(self):
        ev = s_ev(rows=(("Closed", 5), ("Active", 7)), columns=("client_status", "n"))
        r = self.svc(structured=ev, answer="Client A12345 is Closed [investments].").answer_question(Q_INV)
        self.assertEqual((r.status, r.answer), (sv.ERROR, None))
        self.assertTrue(any("record-level" in reason or "client-wide" in reason for reason in r.failure["details"]["reasons"]))

    def test_service_withholds_action_items_wording_that_denies_missing_data(self):
        r = self.svc(meeting=m_ev(hits=(hit(1, action=None),)), answer="There were no action items [meeting: 1, 2024-01-10].").answer_question(Q_MEET)
        self.assertEqual((r.status, r.answer), (sv.ERROR, None))
        ok = self.svc(meeting=m_ev(hits=(hit(1, action=None),)), answer="Action items are not recorded for meeting 1 [meeting: 1, 2024-01-10].").answer_question(Q_MEET)
        self.assertEqual(ok.status, sv.OK)

    def test_service_withholds_an_invented_meeting_reference(self):
        r = self.svc(meeting=m_ev(), answer="A concern was raised [meeting: 42, 2024-01-10].").answer_question(Q_MEET)
        self.assertEqual((r.status, r.failure["code"]), (sv.ERROR, "validation_failed"))


class TestModuleEntryPoint(unittest.TestCase):
    def test_answer_question_is_exposed(self):
        self.assertTrue(callable(sv.answer_question))
        self.assertTrue(callable(sv.BaselineService.answer_question))

    def test_response_serializes(self):
        import json
        r = sv.BaselineResponse("q", "investment", sv.OK, answer="a")
        json.dumps(r.to_dict())
        self.assertEqual(set(r.to_dict()) >= {"question", "route", "status", "answer", "resolution", "structured_evidence", "meeting_evidence", "validation", "timing_ms", "failure"}, True)


if __name__ == "__main__":
    unittest.main()
