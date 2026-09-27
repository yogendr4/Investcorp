"""V1 service tests: listing fallback, metadata on request, aggregates, hybrid (structured side + each meeting mode), date relations,
the validation wrapper and the frozen-Baseline check. Real resolver, retriever and aggregator on a synthetic database; Claude is scripted.

Run from the project root:  python -m unittest tests.v1.test_v1_service -v
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline import meeting_retrieval as mr
from src.baseline import structured_query as sq
from src.baseline.claude_cli import CliResult, ErrorClass
from src.baseline.service import ServiceConfig
from src.v1 import service as v1
from src.v1.validation import validate_answer_v1
from src.baseline.answer_validation import validate_answer
from tests.v1.fixtures import FakeEngine, NoCalls, ScriptedAdapter, build_db, s_ev, sql_reply

ROOT = Path(__file__).resolve().parents[2]
V = "meetings_meta"
AUM = ((30.0,),)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v1_svc_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.resolver = er.EntityResolver.from_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def svc(self, *replies, structured=None, adapter=None):
        self.adapter = adapter or ScriptedAdapter(*replies)
        self.engine = FakeEngine(structured if structured is not None else s_ev(rows=AUM, columns=("total_aum_amount",)))
        s = v1.V1Service(ServiceConfig(db_path=self.db), resolver=self.resolver, engine=self.engine, adapter=self.adapter)
        self.addCleanup(s.close)
        return s


class TestListingFallback(Base):
    def test_latest_meeting_uses_the_newest_first_listing_not_an_empty_search(self):
        r = self.svc("Client A12345 was most recently met on 2025-02-20 [meeting: 4, 2025-02-20].").answer_question("When was client A12345 most recently met?")
        self.assertEqual((r.status, r.route, r.meeting_mode), ("ok", "meeting", "listing"))
        ev = r.meeting_evidence
        self.assertEqual((ev["rank_method"], ev["hits"][0]["meeting_id"], ev["hits"][0]["meeting_date"]), ("date_desc", 4, "2025-02-20"))
        self.assertEqual(ev["query"]["terms"], [])
        self.assertEqual(len(self.adapter.prompts), 1)                          # synthesis only: no SQL call, no lexical search
        self.assertIn("newest-first listing", self.adapter.prompts[0])
        self.assertIn("not a topical match", self.adapter.prompts[0])
        self.assertEqual(self.engine.calls, [])
        self.assertTrue(r.validation["valid"])

    def test_entity_first_filtering_is_preserved(self):
        r = self.svc("Two meetings [meeting: 8, 2025-01-12].").answer_question("Show the latest meetings for client B12463.")
        ids = {h["client_id"] for h in r.meeting_evidence["hits"]}
        self.assertEqual(ids, {"B12463"})
        self.assertEqual(r.meeting_evidence["filtered_meeting_count"], 2)

    def test_group_listing_uses_meeting_source_membership(self):
        r = self.svc("Latest [meeting: 4, 2025-02-20].").answer_question("What was the most recent meeting of group 346?")
        self.assertEqual(r.meeting_evidence["filters"]["group_id"], 346)
        self.assertEqual({h["client_id"] for h in r.meeting_evidence["hits"]}, {"A12345", "B12345"})
        self.assertEqual(r.meeting_evidence["filtered_meeting_count"], 6)
        self.assertEqual(r.meeting_evidence["filters"]["group_source"], "meetings.group_id")

    def test_default_k_is_still_20(self):
        r = self.svc("Showing 20 of 30 meetings; the newest is [meeting: 129, 2024-05-30].").answer_question("Show the meetings for client A12399.")
        ev = r.meeting_evidence
        self.assertEqual((ev["top_k"], len(ev["hits"]), ev["filtered_meeting_count"]), (20, 20, 30))
        self.assertIn("showing 20 of 30", self.adapter.prompts[0])
        self.assertEqual(r.status, "ok")

    def test_no_terms_and_no_filter_is_still_a_clarification(self):
        r = self.svc(adapter=NoCalls()).answer_question("Meetings, please.")
        self.assertEqual(r.status, "clarification")

    def test_bare_date_stays_an_exact_date_filter(self):
        r = self.svc("The action item is to introduce legal counsel [meeting: 2, 2024-03-01].").answer_question("What were the action items from client A12345's meeting on 2024-03-01?")
        self.assertEqual(r.meeting_evidence["filters"]["meeting_date"], "2024-03-01")
        self.assertEqual([h["meeting_id"] for h in r.meeting_evidence["hits"]], [2])


class TestMetadataAndUnmatchedCount(Base):
    def test_metadata_only_when_the_question_requests_it(self):
        self.svc("Latest [meeting: 4, 2025-02-20].").answer_question("When was client A12345 most recently met?")
        plain = self.adapter.prompts[0]
        for word in ("| sector ", "| region ", "| stage ", "deal size estimate"):
            self.assertNotIn(word, plain)
        self.svc("Latest [meeting: 4, 2025-02-20], sector edtech.").answer_question("What sector was client A12345's most recent meeting in?")
        asked = self.adapter.prompts[0]
        self.assertIn("| sector edtech", asked)
        self.assertNotIn("| region ", asked)
        self.svc("Latest [meeting: 4, 2025-02-20].").answer_question("What region and estimated deal size did client A12345's most recent meeting have?")
        both = self.adapter.prompts[0]
        self.assertIn("| region Americas", both)
        self.assertIn("deal size estimate 2M USD", both)
        self.assertNotIn("| sector ", both)

    def test_meeting_date_and_company_are_always_shown(self):
        self.svc("Latest [meeting: 4, 2025-02-20].").answer_question("When was client A12345 most recently met?")
        self.assertIn("meeting 4 | 2025-02-20 | client A12345 | group 346 | company Orchid Ventures", self.adapter.prompts[0])

    def test_unmatched_count_is_deterministic_and_grounded(self):
        r = self.svc("Two of client A12345's four meetings match; 2 in scope have no text match [meeting: 1, 2024-01-10].").answer_question(
            "What concerns were raised about hedging in client A12345's meetings?")
        ev = r.meeting_evidence
        self.assertEqual((r.meeting_mode, ev["query"]["terms"], ev["filtered_meeting_count"], ev["matched_count"]), ("topical", ["concerns", "hedging"], 4, 2))
        self.assertEqual(r.unmatched_count, 2)
        self.assertIn("in scope without a text match: 2", self.adapter.prompts[0])
        self.assertEqual(r.status, "ok")

    def test_topical_terms_no_longer_contain_request_words(self):
        r = self.svc("None [meeting: 1, 2024-01-10].").answer_question("How many of client A12345's meetings mention hedging by sector?")
        self.assertEqual(r.meeting_mode, "topical")
        self.assertEqual(r.meeting_evidence["query"]["terms"], ["hedging"])
        removed = {x["term"]: x["category"] for x in r.meeting_intent["term_audit"]["removed"]}
        self.assertEqual(removed, {"many": "R1_request", "sector": "R2_field"})

    def test_raw_text_and_mojibake_are_preserved(self):
        r = self.svc("Sanjay [meeting: 1, 2024-01-10].").answer_question("Which of client A12345's meetings mention CFO?")
        snippet = r.meeting_evidence["hits"][0]["snippet"]
        self.assertIn("[[CFO]]", snippet)


class TestDateRelations(Base):
    def test_unsupported_for_meeting_and_hybrid_with_no_work(self):
        for q in ("Which meetings of client A12345 happened since 2024-03-01?", "Meetings for client A12345 after 2024-01-01", "Meetings for client A12345 before 2024-02-01",
                  "Meetings for client A12345 until 2024-02-01", "Meetings for client A12345 prior to 2024-02-01", "Meetings for client A12345 between 2024-01-01 and 2024-06-30",
                  "What is client A12345's latest Total AUM and which meetings happened since 2024-03-01?"):
            with self.subTest(q=q):
                s = self.svc(adapter=NoCalls())
                r = s.answer_question(q)
                self.assertEqual((r.status, r.failure["code"]), ("unsupported", "date_relation_unsupported"))
                self.assertIsNone(r.answer)
                self.assertIn("not supported", r.message)
                self.assertIsNone(r.meeting_evidence)                          # nothing was searched: never an exact-date filter
                self.assertIsNone(r.structured_evidence)
                self.assertEqual(self.engine.calls, [])
                self.assertIn(r.meeting_intent["date_relation"]["relation"], ("since", "after", "before", "until", "prior to", "between"))


class TestAggregates(Base):
    def test_count_uses_the_aggregate_path_and_cites_meetings(self):
        s = self.svc(sql_reply(f"SELECT COUNT(*) AS meeting_count FROM {V} WHERE client_id = 'A12345'"), "Client A12345 had 4 meetings [meetings].")
        r = s.answer_question("How many meetings did client A12345 have?")
        self.assertEqual((r.status, r.route, r.meeting_mode), ("ok", "meeting", "aggregate"))
        self.assertEqual(r.meeting_aggregate_evidence["rows"], [[4]])
        self.assertEqual(r.meeting_aggregate_evidence["source_tables"], ["meetings"])
        self.assertIsNone(r.meeting_evidence)
        self.assertEqual(len(self.adapter.prompts), 2)                          # SQL generation + synthesis
        self.assertIn("AGGREGATE MEETING EVIDENCE", self.adapter.prompts[1])
        self.assertTrue(r.validation["valid"])

    def test_deal_size_argmax_may_cite_the_returned_meeting(self):
        sql = (f"SELECT meeting_id, meeting_date, deal_size_estimate_raw, deal_size_estimate_usd FROM {V} WHERE client_id = 'A12345' AND deal_size_estimate_usd IS NOT NULL "
               "ORDER BY deal_size_estimate_usd DESC, meeting_id ASC LIMIT 1")
        r = self.svc(sql_reply(sql), "The largest estimate is 150M USD in meeting 3 [meeting: 3, 2023-11-15].").answer_question(
            "What was the largest estimated deal size for client A12345?")
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.meeting_aggregate_evidence["rows"][0][:3], [3, "2023-11-15", "150M USD"])
        r = self.svc(sql_reply(sql), "The largest estimate is 150M USD in meeting 99 [meeting: 99, 2023-11-15].").answer_question(
            "What was the largest estimated deal size for client A12345?")
        self.assertEqual((r.status, r.failure["code"]), ("error", "validation_failed"))

    def test_wrong_citations_and_numbers_are_withheld(self):
        sql = sql_reply(f"SELECT COUNT(*) AS n FROM {V} WHERE client_id = 'A12345'")
        for answer in ("Client A12345 had 4 meetings [investments].", "Client A12345 had 41 meetings [meetings].", "Client A12345 had 4 meetings [performance]."):
            with self.subTest(answer=answer):
                r = self.svc(sql, answer).answer_question("How many meetings did client A12345 have?")
                self.assertEqual((r.status, r.answer), ("error", None))

    def test_aggregate_failure_is_reported_once_without_retry_or_repair(self):
        r = self.svc(sql_reply("SELECT COUNT(*) FROM meetings"), "NEVER USED").answer_question("How many meetings did client A12345 have?")
        self.assertEqual((r.status, r.failure["stage"]), ("error", "evidence"))
        self.assertEqual((len(self.adapter.prompts), len(self.adapter.replies)), (1, 1))
        self.assertIsNone(r.answer)

    def test_clarification_and_unsupported_make_no_llm_call(self):
        for q, status in (("How many meetings did client 12345 have?", "clarification"), ("How many meetings did client E12345 have?", "clarification"),
                          ("How many meetings did relationship manager Priya Sharma hold?", "unsupported")):
            with self.subTest(q=q):
                r = self.svc(adapter=NoCalls()).answer_question(q)
                self.assertEqual(r.status, status)

    def test_aggregate_for_a_client_without_meetings_is_a_qualified_answer_without_llm(self):
        r = self.svc(adapter=NoCalls()).answer_question("How many meetings did client C12345 have?")
        self.assertEqual((r.status, r.qualified), ("ok", True))
        self.assertIn("no meeting records", r.answer)


class TestHybrid(Base):
    Q_LIST = "What is client A12345's latest Total AUM and which company did their most recent meeting involve?"
    Q_AGG = "What is client A12345's latest Total AUM and how many meetings did they have?"
    Q_TOPIC = "What is client A12345's latest Total AUM and what did the meetings say about hedging?"

    def test_structured_side_plus_meeting_listing(self):
        r = self.svc("Latest Total AUM is 30.0 [performance]. The most recent meeting was 2025-02-20 with Orchid Ventures [meeting: 4, 2025-02-20].").answer_question(self.Q_LIST)
        self.assertEqual((r.status, r.route, r.meeting_mode), ("ok", "hybrid", "listing"))
        self.assertEqual(self.engine.calls[0][1], "performance")
        self.assertTrue(self.engine.calls[0][0].endswith("meeting evidence is retrieved separately.]"))
        self.assertEqual(r.meeting_evidence["rank_method"], "date_desc")
        self.assertEqual(len(self.adapter.prompts), 1)
        p = self.adapter.prompts[0]
        self.assertIn("STRUCTURED EVIDENCE", p)
        self.assertIn("MEETING EVIDENCE", p)
        self.assertIn("| company Orchid Ventures", p)

    def test_structured_side_plus_meeting_aggregate(self):
        s = self.svc(sql_reply(f"SELECT COUNT(*) AS n FROM {V} WHERE client_id = 'A12345'"),
                     "Latest Total AUM is 30.0 [performance]. Client A12345 had 4 meetings [meetings].")
        r = s.answer_question(self.Q_AGG)
        self.assertEqual((r.status, r.route, r.meeting_mode), ("ok", "hybrid", "aggregate"))
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(r.structured_evidence["source_tables"], ["performance"])
        self.assertEqual(r.meeting_aggregate_evidence["source_tables"], ["meetings"])
        self.assertTrue(r.validation["valid"], r.validation)
        self.assertEqual(len(self.adapter.prompts), 2)
        self.assertIn("meeting part of this question", self.adapter.prompts[0])
        for block in ("STRUCTURED EVIDENCE", "AGGREGATE MEETING EVIDENCE"):
            self.assertIn(block, self.adapter.prompts[1])

    def test_structured_side_plus_topical_retrieval(self):
        r = self.svc("Latest Total AUM is 30.0 [performance]. Meeting 1 mentions hedging [meeting: 1, 2024-01-10].").answer_question(self.Q_TOPIC)
        self.assertEqual((r.status, r.meeting_mode), ("ok", "topical"))
        self.assertEqual(r.meeting_evidence["query"]["terms"], ["hedging"])                     # structured words never reach the search
        self.assertTrue({"total", "aum"} & {x["term"] for x in r.meeting_intent["term_audit"]["removed"]})
        self.assertIn("lexical search", self.adapter.prompts[0])

    def test_no_raw_cross_source_join(self):
        bad = f"SELECT COUNT(*) FROM {V} a JOIN investments b ON a.client_id = b.client_id"
        r = self.svc(sql_reply(bad), "NEVER USED").answer_question(self.Q_AGG)
        self.assertEqual((r.status, r.failure["code"]), ("error", "hybrid_partial_failure"))
        self.assertEqual(len(self.adapter.prompts), 1)
        self.assertIn("structured", r.partial_evidence)                                        # the structured side stands on its own
        self.assertNotIn("meeting aggregate", r.partial_evidence)
        self.assertIsNone(r.answer)
        forbidden = ("JOIN ", "INNER JOIN", "LEFT JOIN")
        for f in (ROOT / "src" / "v1").glob("*.py"):
            code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.strip().startswith(("#", '"""')))
            if f.name != "meeting_aggregates.py":                                              # the engine only mentions JOIN to reject it
                for word in forbidden:
                    self.assertNotIn(word, code, f.name)

    def test_structured_failure_does_not_invent_the_other_side(self):
        err = {"stage": "execution", "code": "query_timeout", "message": "too slow", "details": {}}
        bad = sq.StructuredEvidence("q", "performance", sq.TIMEOUT, error=err)
        r = self.svc(structured=bad, adapter=ScriptedAdapter(sql_reply(f"SELECT COUNT(*) AS n FROM {V} WHERE client_id = 'A12345'"))).answer_question(self.Q_AGG)
        self.assertEqual((r.status, r.failure["code"]), ("error", "hybrid_partial_failure"))
        self.assertIn("meeting aggregate", r.partial_evidence)
        self.assertNotIn("structured", r.partial_evidence)

    def test_intersection_across_sources_is_not_attempted(self):
        # a question that would need a cross-source set intersection is answered per side; the meeting search is not restricted by the structured result
        r = self.svc("Latest Total AUM is 30.0 [performance]. Meeting 1 mentions the CFO [meeting: 1, 2024-01-10].").answer_question(
            "Which clients have a latest Total AUM below 1.0x and a meeting about a dedicated CFO?")
        self.assertEqual(r.route, "hybrid")
        self.assertEqual(self.engine.calls[0][1], "performance")
        self.assertEqual(r.meeting_evidence["filters"]["client_ids"], [])


class TestOtherRoutesAndFailures(Base):
    def test_investment_route_is_unchanged(self):
        ev = s_ev(rows=((48,),), columns=("n",), source="investments", route="investment")
        r = self.svc("Client A12345 has 48 investment records [investments].", structured=ev).answer_question("How many investment records does client A12345 have?")
        self.assertEqual((r.status, r.route, r.meeting_mode), ("ok", "investment", None))
        self.assertIsNone(r.meeting_aggregate_evidence)

    def test_synthesis_failure_is_an_error_and_is_not_retried(self):
        fail = CliResult(success=False, error_class=ErrorClass.TIMEOUT, error_message="too slow", timed_out=True)
        r = self.svc(fail, "NEVER").answer_question("When was client A12345 most recently met?")
        self.assertEqual((r.status, r.failure["stage"], len(self.adapter.prompts)), ("error", "synthesis", 1))

    def test_response_serializes_with_the_v1_fields(self):
        import json
        r = self.svc("Latest [meeting: 4, 2025-02-20].").answer_question("When was client A12345 most recently met?")
        d = json.loads(json.dumps(r.to_dict(), default=str))
        self.assertTrue({"meeting_mode", "meeting_intent", "meeting_aggregate_evidence", "unmatched_count", "requested_fields"} <= set(d))
        self.assertEqual(d["meeting_intent"]["mode"], "listing")


def hit(mid, date, **kw):
    base = dict(rank=1, meeting_id=mid, client_id="A12345", group_id=346, meeting_date=date, company="Orchid Ventures", sector="fintech", region="EMEA",
                investment_stage="seed", deal_size_estimate="1M USD", score=1.0, snippet="text", matched_terms=("x",), matched_fields=("summary",),
                action_items_state="not_recorded", action_items=None, summary_chars=10)
    base.update(kw)
    return mr.MeetingHit(**base)


class TestValidationWrapper(unittest.TestCase):
    def meeting(self, scope=23, matched=6):
        return mr.MeetingEvidence("q", mr.OK, hits=(hit(1, "2024-01-10"),), filtered_meeting_count=scope, matched_count=matched, rank_method="bm25",
                                  query={"terms": ["x"], "phrases": []}, filters={"client_ids": ["A12345"]})

    def agg(self, rows, columns):
        from src.v1 import meeting_aggregates as ma
        return ma.MeetingAggregateEvidence("q", ma.OK, sql="SELECT ...", columns=columns, rows=tuple(rows), row_count=len(rows), source_tables=("meetings",), objects_used=(ma.VIEW,))

    def test_unmatched_count_is_grounded_only_by_the_wrapper(self):
        ans = "Six meetings match and 17 others in scope do not [meeting: 1, 2024-01-10]."
        ev = self.meeting()
        self.assertFalse(validate_answer(ans, "meeting", None, ev, "q").valid)                                 # frozen validator: rejected
        self.assertTrue(validate_answer_v1(ans, "meeting", None, ev, None, "q", extra_numbers=[17]).valid)      # the code computed 23 - 6
        self.assertFalse(validate_answer_v1(ans.replace("17", "18"), "meeting", None, ev, None, "q", extra_numbers=[17]).valid)

    def test_no_other_check_is_weakened(self):
        ev = self.meeting()
        for bad in ("Meeting 99 matches [meeting: 99, 2024-01-10].", "Meeting 1 [meeting: 1, 2023-01-01].", "Six match and 71 do not [meeting: 1, 2024-01-10].",
                    "There were no action items [meeting: 1, 2024-01-10].", "The RM of client A12345 is Sara Khan [meeting: 1, 2024-01-10]."):
            with self.subTest(bad=bad):
                self.assertFalse(validate_answer_v1(bad, "meeting", None, ev, None, "q", extra_numbers=[17]).valid)

    def test_meetings_citation_needs_aggregate_evidence(self):
        r = validate_answer_v1("Four meetings [meetings].", "meeting", None, self.meeting(), None, "q")
        self.assertFalse(r.valid)
        self.assertTrue(any("no aggregate meeting evidence" in x for x in r.reasons))

    def test_aggregate_rows_are_grounded_evidence(self):
        a = self.agg([(40,)], ("n",))
        self.assertTrue(validate_answer_v1("There are 40 meetings [meetings].", "meeting", None, None, a, "q").valid)
        self.assertFalse(validate_answer_v1("There are 41 meetings [meetings].", "meeting", None, None, a, "q").valid)
        self.assertFalse(validate_answer_v1("There are 40 meetings [investments].", "meeting", None, None, a, "q").valid)
        r = validate_answer_v1("There are 40 meetings.", "meeting", None, None, a, "q")
        self.assertTrue(r.valid)
        self.assertTrue(any("no [meetings] reference" in w for w in r.warnings))

    def test_hybrid_structured_plus_aggregate(self):
        a = self.agg([(40,)], ("n",))
        s = s_ev(rows=((30.0,),), columns=("aum",))
        self.assertTrue(validate_answer_v1("AUM is 30.0 [performance]; 40 meetings [meetings].", "hybrid", s, None, a, "q").valid)
        self.assertFalse(validate_answer_v1("AUM is 31.0 [performance]; 40 meetings [meetings].", "hybrid", s, None, a, "q").valid)
        self.assertFalse(validate_answer_v1("AUM is 30.0 [performance]; 40 meetings [investments].", "hybrid", s, None, a, "q").valid)

    def test_a_raw_meeting_citation_stays_valid_and_checked(self):
        ev = self.meeting()
        self.assertTrue(validate_answer_v1("Meeting 1 is relevant [meeting: 1, 2024-01-10].", "meeting", None, ev, None, "q").valid)
        self.assertFalse(validate_answer_v1("Meeting 2 is relevant [meeting: 2, 2024-01-10].", "meeting", None, ev, None, "q").valid)

    def test_without_new_evidence_it_equals_the_frozen_validator(self):
        ev = self.meeting()
        for ans in ("Meeting 1 [meeting: 1, 2024-01-10].", "Meeting 9 [meeting: 9, 2024-01-10].", "", "There were no action items [meeting: 1, 2024-01-10]."):
            a, b = validate_answer(ans, "meeting", None, ev, "q"), validate_answer_v1(ans, "meeting", None, ev, None, "q")
            self.assertEqual((a.valid, a.reasons, a.warnings), (b.valid, b.reasons, b.warnings), ans)

    # ---- payload_audit_v21.md fixes 1 and 2 (regression tests) ----

    def test_hyb10_both_sides_row_counts_are_independently_groundable_not_summed(self):
        """Regression test for payload_audit_v21.md fix 1 (HYB-10): a hybrid answer must be able to cite EITHER
        side's own row count (8 deal names, 16 meeting sector/company rows) without the wrapper collapsing them
        into one synthetic 24."""
        s = s_ev(rows=(("Atlas Private Equity II",), ("BluePeak Venture II",), ("BluePeak Venture III",), ("Emerald Real Estate Fund",),
                      ("NorthBridge Growth Fund",), ("Orion Infrastructure I",), ("Orion Infrastructure IV",), ("Summit Credit Opportunities",)), columns=("deal_name",), source="investments", route="investment")
        a = self.agg([("agritech", "Redwood Partners"), ("consumer goods", "Harbor Fund"), ("consumer goods", "Summit Advisors"), ("edtech", "Quantum Growth"),
                      ("education", "Crescent Capital"), ("education", "NorthStar FO"), ("fintech", "Astra Investments"), ("fintech", "Harbor Fund"),
                      ("healthcare", "Pioneer LP"), ("logistics", "Astra Investments"), ("logistics", "Redwood Partners"), ("media", "GreenField Ventures"),
                      ("media", "Helix Capital"), ("media", "NorthStar FO"), ("real estate", "IVC Capital"), ("renewables", "Redwood Partners")], ("sector", "company"))
        self.assertEqual((s.row_count, a.row_count), (8, 16))                # the two real, independent counts this audit is about
        deal_names = "Atlas Private Equity II, BluePeak Venture II, BluePeak Venture III, Emerald Real Estate Fund, NorthBridge Growth Fund, Orion Infrastructure I, Orion Infrastructure IV, Summit Credit Opportunities"
        ans = f"Client D12376 has 8 investment records with these deal names: {deal_names} [investments]. Its meetings cover 16 sector/company rows [meetings]."
        r = validate_answer_v1(ans, "hybrid", s, None, a, "q")
        self.assertTrue(r.valid, r.reasons)
        # 16 alone (the aggregate's own row_count) must ground even without also mentioning 8 or 24
        self.assertTrue(validate_answer_v1(f"{deal_names} appear on investment records [investments]. There are 16 sector/company rows [meetings].", "hybrid", s, None, a, "q").valid)
        # 8 alone (the structured side's own row_count) must ground even without also mentioning 16 or 24
        self.assertTrue(validate_answer_v1(f"There are 8 deal names: {deal_names} [investments]. The meetings evidence lists sector/company pairs [meetings].", "hybrid", s, None, a, "q").valid)
        # a wrong number for either side is still rejected: the fix adds grounding, it does not accept anything
        self.assertFalse(validate_answer_v1(f"{deal_names} appear on investment records [investments]. There are 17 sector/company rows [meetings].", "hybrid", s, None, a, "q").valid)
        self.assertFalse(validate_answer_v1(f"There are 23 deal names: {deal_names} [investments]. The meetings evidence lists sector/company pairs [meetings].", "hybrid", s, None, a, "q").valid)

    def test_no_cross_source_count_summation(self):
        """The wrapper never fabricates row_count = side_a + side_b as the represented count of either side."""
        s = s_ev(rows=tuple((f"deal{i}",) for i in range(8)), columns=("deal_name",), source="investments", route="investment")
        a = self.agg([(f"s{i}", f"c{i}") for i in range(16)], ("sector", "company"))
        # 24 (the naive sum) is not required for a correct answer to validate: both real numbers ground it on their own
        self.assertTrue(validate_answer_v1("8 deal names [investments]. 16 sector/company rows [meetings].", "hybrid", s, None, a, "q").valid)
        # a number that is neither side's real count, nor their sum, nor a note, still fails (no weakening)
        self.assertFalse(validate_answer_v1("8 deal names [investments]. 99 sector/company rows [meetings].", "hybrid", s, None, a, "q").valid)

    def test_meet06_note_contained_number_is_groundable(self):
        """Regression test for payload_audit_v21.md fix 2 (MEET-06): a number that exists only inside the
        retriever's own note text (already shown to the model verbatim) must be groundable."""
        ev = mr.MeetingEvidence("q", mr.OK, hits=(hit(17056, "2023-05-19"),), filtered_meeting_count=20000, matched_count=627, rank_method="rrf",
                                query={"terms": ["sanjay", "lópez"], "phrases": []}, filters={"client_ids": []},
                                notes=("627 meetings matched; only the top 50 are returned",))
        ans = "Missing: the remaining matches (only the top 50 of 627 are returned) [meeting: 17056, 2023-05-19]."
        self.assertTrue(validate_answer_v1(ans, "meeting", None, ev, None, "q").valid)
        # an unrelated number that is in neither a row/hit field nor a note must still fail: notes do not open the
        # door to arbitrary free-text numbers, only to the two specific fields synthesis already renders verbatim
        bad = "Missing: the remaining matches (only the top 12345 of 627 are returned) [meeting: 17056, 2023-05-19]."
        self.assertFalse(validate_answer_v1(bad, "meeting", None, ev, None, "q").valid)

    def test_entity_notes_numbers_are_also_groundable(self):
        ev = mr.MeetingEvidence("q", mr.OK, hits=(hit(1, "2024-01-10"),), filtered_meeting_count=5, matched_count=1, rank_method="bm25",
                                query={"terms": ["x"], "phrases": []}, filters={"group_id": 346}, entity_notes=("group 346 has 6 members in the meetings source",))
        ans = "Group 346 has 6 members in the meetings source [meeting: 1, 2024-01-10]."
        self.assertTrue(validate_answer_v1(ans, "meeting", None, ev, None, "q").valid)


class TestFusedUnmatchedCount(unittest.TestCase):
    """payload_audit_v21.md fix 3: the unmatched-meeting count must be deterministic in fused (rrf) mode too,
    not only in lexical (bm25) mode, and the model must not have to derive `scope - matched` itself."""

    def ev(self, rank_method, scope=23, matched=6):
        return mr.MeetingEvidence("q", mr.OK, hits=(hit(1, "2024-01-10"),), filtered_meeting_count=scope, matched_count=matched, rank_method=rank_method,
                                  query={"terms": ["x"], "phrases": []}, filters={"client_ids": ["A12346"]})

    def test_unmatched_count_is_available_for_rrf_not_only_bm25(self):
        from src.v1.synthesis import unmatched_count
        self.assertEqual(unmatched_count(self.ev("rrf")), 17)                # MEET-07: 23 in scope, 6 matched
        self.assertEqual(unmatched_count(self.ev("bm25")), 17)               # unchanged lexical-mode behavior
        self.assertIsNone(unmatched_count(self.ev("date_desc")))             # a listing has no text match concept

    def test_the_count_is_exposed_in_the_evidence_text_shown_to_synthesis(self):
        from src.v1.synthesis import _meeting_lines
        lines = _meeting_lines(self.ev("rrf"), n_hits=1, fields=())
        self.assertIn("in scope without a text match: 17", lines[0])

    def test_meet07_style_answer_is_now_groundable_without_the_model_deriving_it(self):
        from src.v1.synthesis import unmatched_count
        ev = self.ev("rrf")
        ans = "Six meetings match. The evidence does not say whether the remaining 17 meetings mention independent directors [meeting: 1, 2024-01-10]."
        # src/v1/service.py always threads unmatched_count(...) into extra_numbers, unconditionally of rank_method;
        # this reproduces that wiring directly against the fixed, mode-independent unmatched_count (fix 3).
        self.assertTrue(validate_answer_v1(ans, "meeting", None, ev, None, "q", extra_numbers=[unmatched_count(ev)]).valid)

    def test_existing_bm25_behavior_is_unchanged(self):
        from src.v1.synthesis import unmatched_count
        ev = self.ev("bm25")
        self.assertTrue(validate_answer_v1("Six match and 17 others in scope do not [meeting: 1, 2024-01-10].", "meeting", None, ev, None, "q",
                                           extra_numbers=[unmatched_count(ev)]).valid)


class TestBaselineIsFrozen(unittest.TestCase):
    """The historical git tag `baseline-v0.1` and the current, dictionary-aligned working tree are two distinct
    reference points; see docs/architecture/baseline_reference.md. Both are guarded here:
      - the TAG itself (its object and the commit it points to) must never move, be re-created or be force-pushed;
      - the working tree may differ from it ONLY in the explicitly approved files below — any OTHER Baseline file
        drifting is still a failure.
    """

    # The tag's own object SHA and the commit it points to (`git rev-parse baseline-v0.1` / `baseline-v0.1^{commit}`),
    # recorded when the tag was created. If either changes, the tag was moved, deleted-and-recreated, or the
    # commit it points to was rewritten: baseline-v0.1 is no longer immutable.
    _TAG_OBJECT = "36b85f87d06d7d776e001a90c2d05b608920a9aa"
    _TAG_COMMIT = "3ee53fb1efa82af8fc8e53ed7ef73811e019f4f5"

    # Files where the current, dictionary-aligned Baseline is explicitly approved to differ from baseline-v0.1.
    # See docs/architecture/baseline_reference.md for the current-vs-historical distinction and the reason for
    # each file (prompt text and validation facts brought in line with data/Assignment_Data_Dictionary.xlsx).
    _DICTIONARY_ALIGNMENT_FILES = {
        "src/baseline/data_schema.py", "src/baseline/data_validation.py", "src/baseline/structured_schema.py",
        "tests/baseline/test_data_layer.py", "tests/baseline/test_structured_query.py",
    }

    # Files approved for the post-submission-v1.0 corrective bugfix patch: Issue 1 (canonical LOB phrases such
    # as "Private Equity" were wrongly surfaced as ambiguous deal_name entity mentions, entity_resolution.py;
    # and, found afterward, the same LOB-metric phrasing was only a weak performance routing signal, so a
    # question combining it with a strong meeting phrase routed meeting-only instead of hybrid,
    # question_routing.py) and Issue 2 (SQLite connections opened once in __init__ and reused across threads;
    # made lazy and thread-local in structured_query.py and meeting_retrieval.py — see their own docstrings).
    _ISSUE_1_2_BUGFIX_FILES = {
        "src/baseline/entity_resolution.py", "src/baseline/meeting_retrieval.py",
        "src/baseline/question_routing.py", "src/baseline/structured_query.py",
        "tests/baseline/test_baseline_service.py", "tests/baseline/test_entity_resolution.py",
        "tests/baseline/test_meeting_retrieval.py", "tests/baseline/test_question_routing.py",
        # tests/baseline/test_structured_query.py already listed in _DICTIONARY_ALIGNMENT_FILES; also touched here.
    }
    _APPROVED_DRIFT = _DICTIONARY_ALIGNMENT_FILES | _ISSUE_1_2_BUGFIX_FILES

    def _git(self, *args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")

    def test_baseline_v0_1_tag_itself_is_unmoved(self):
        obj = self._git("rev-parse", "--verify", "baseline-v0.1")
        if obj.returncode != 0:
            self.skipTest("tag baseline-v0.1 not found")
        self.assertEqual(obj.stdout.strip(), self._TAG_OBJECT, "the baseline-v0.1 tag object has changed: it is no longer immutable")
        commit = self._git("rev-parse", "baseline-v0.1^{commit}")
        self.assertEqual(commit.stdout.strip(), self._TAG_COMMIT, "the commit baseline-v0.1 points to has changed: it is no longer immutable")

    def test_baseline_files_differ_from_the_tag_only_where_explicitly_approved(self):
        tag = self._git("rev-parse", "--verify", "baseline-v0.1")
        if tag.returncode != 0:
            self.skipTest("tag baseline-v0.1 not found")
        diff = self._git("diff", "--name-only", "baseline-v0.1", "--", "src/baseline", "tests/baseline", "tests/evaluation/questions.json")
        changed = {line for line in diff.stdout.strip().splitlines() if line}
        unapproved = changed - self._APPROVED_DRIFT
        self.assertEqual(unapproved, set(), f"Baseline files differ from the frozen tag outside the approved dictionary-alignment set: {sorted(unapproved)}")


if __name__ == "__main__":
    unittest.main()
