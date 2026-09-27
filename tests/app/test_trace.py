"""Tests for app.trace: TraceRecorder/LocalTraceRecorder, serialization, redaction, and build_trace over a
response dict. No Streamlit runtime, no service call, no network.

Run from the project root:  python -m unittest tests.app.test_trace -v
"""
import unittest

from app.trace import LocalTraceRecorder, REDACTED, TraceRecorder, build_trace, redact


class TestRedaction(unittest.TestCase):
    def test_sensitive_keys_are_redacted_wherever_they_occur(self):
        d = {"account_rm": "Sara Khan", "nonauth_account_rm_email": "sara@investcorp.com", "auth_token": "abc123",
             "credentials": {"password": "x"}, "org_id": "o-1", "nested": {"api_key": "k", "safe": "kept"}}
        r = redact(d)
        self.assertEqual(r["account_rm"], "Sara Khan")                      # a real business field, not sensitive
        for k in ("nonauth_account_rm_email", "auth_token", "org_id"):
            self.assertEqual(r[k], REDACTED)
        self.assertEqual(r["credentials"], REDACTED)                        # the whole sub-tree is hidden once its own key is sensitive
        self.assertEqual(r["nested"], {"api_key": REDACTED, "safe": "kept"})

    def test_email_shaped_strings_are_masked_even_under_a_safe_key(self):
        r = redact({"note": "reach sara.khan@investcorp.com about this"})
        self.assertNotIn("investcorp.com", r["note"])
        self.assertIn("[email]", r["note"])

    def test_redact_recurses_into_lists_and_tuples(self):
        r = redact([{"auth": "x"}, {"ok": 1}])
        self.assertEqual(r, [{"auth": REDACTED}, {"ok": 1}])

    def test_redact_is_idempotent_on_plain_values(self):
        for v in (1, 1.5, True, None):
            self.assertEqual(redact(v), v)

    def test_no_auth_or_account_material_survives_a_realistic_metadata_blob(self):
        blob = {"stage": "llm", "model": "claude-sonnet-5", "gcloud_account": "s***@gmail.com", "access_token": "ya29.secret",
                "AccountRMEmail": "cgomez@investcorp.com", "row": ["A12345", "Sara Khan"]}
        r = redact(blob)
        dumped = str(r)
        self.assertNotIn("secret", dumped)
        self.assertNotIn("investcorp.com", dumped)
        self.assertEqual(r["row"], ["A12345", "Sara Khan"])                  # business data untouched


class TestLocalTraceRecorder(unittest.TestCase):
    def test_is_a_trace_recorder(self):
        self.assertIsInstance(LocalTraceRecorder(), TraceRecorder)
        with self.assertRaises(TypeError):
            TraceRecorder()                                                  # abstract: cannot be instantiated directly

    def test_records_events_in_order_with_cumulative_offsets(self):
        rec = LocalTraceRecorder()
        rec.record("Entity Resolution", "ok", duration_ms=5.0, metadata={"x": 1})
        rec.record("Routing", "ok", duration_ms=2.0)
        events = rec.events()
        self.assertEqual([e.name for e in events], ["Entity Resolution", "Routing"])
        self.assertEqual((events[0].start_ms, events[0].end_ms), (0.0, 5.0))
        self.assertEqual((events[1].start_ms, events[1].end_ms), (5.0, 7.0))
        self.assertEqual(events[1].duration_ms, 2.0)

    def test_missing_duration_defaults_to_zero_and_does_not_error(self):
        rec = LocalTraceRecorder()
        rec.record("Validation", "invalid")
        self.assertEqual(rec.events()[0].duration_ms, 0.0)

    def test_metadata_and_evidence_refs_are_redacted_on_the_way_in(self):
        rec = LocalTraceRecorder()
        rec.record("Structured Evidence", "ok", metadata={"api_key": "shh", "row_count": 3}, evidence_refs=["investments"])
        e = rec.events()[0]
        self.assertEqual(e.metadata["api_key"], REDACTED)
        self.assertEqual(e.metadata["row_count"], 3)
        self.assertEqual(e.evidence_refs, ("investments",))

    def test_to_list_is_json_shaped(self):
        import json
        rec = LocalTraceRecorder()
        rec.record("Routing", "ok", duration_ms=1.0, metadata={"route": "meeting"}, evidence_refs=["a"])
        dumped = json.dumps(rec.to_list())
        self.assertIn("Routing", dumped)
        d = json.loads(dumped)[0]
        self.assertEqual(set(d), {"name", "status", "start_ms", "end_ms", "duration_ms", "metadata", "evidence_refs"})


class TestBuildTrace(unittest.TestCase):
    def resp(self, **over):
        base = {"route": "investment", "resolution": {"mentions": [{"text": "A12345", "entity_type": "client", "status": "resolved", "canonical": "A12345"}]},
                "routing": {"rule": "R5_SINGLE_FAMILY"}, "structured_evidence": {"outcome": "ok", "row_count": 1, "source_tables": ["investments"], "columns": ["n"]},
                "meeting_evidence": None, "meeting_aggregate_evidence": None, "validation": {"valid": True, "warnings": [], "reasons": []}, "failure": None,
                "timing_ms": {"resolution_ms": 1.0, "routing_ms": 0.5, "structured_ms": 900.0, "validation_ms": 0.3, "total_ms": 950.0}}
        base.update(over)
        return base

    def test_events_cover_the_required_components_for_a_structured_question(self):
        events = build_trace(self.resp()).to_list()
        names = [e["name"] for e in events]
        self.assertEqual(names, ["Entity Resolution", "Routing", "Evidence Collection", "Structured Evidence", "Validation"])
        self.assertEqual(events[0]["status"], "ok")
        self.assertEqual(events[0]["metadata"]["mentions"][0]["canonical"], "A12345")
        self.assertTrue(events[2]["metadata"]["structured_query_used"])

    def test_ambiguous_entity_and_invalid_validation_are_flagged(self):
        r = self.resp(resolution={"mentions": [{"text": "12345", "entity_type": "client", "status": "ambiguous", "canonical": None}]},
                      validation={"valid": False, "warnings": [], "reasons": ["numbers come from the evidence: not found"]},
                      failure={"stage": "validation", "code": "validation_failed", "message": "withheld"})
        events = build_trace(r).to_list()
        by_name = {e["name"]: e for e in events}
        self.assertEqual(by_name["Entity Resolution"]["status"], "ambiguous")
        self.assertEqual(by_name["Validation"]["status"], "invalid")
        self.assertIn("Failure", by_name)
        self.assertEqual(by_name["Failure"]["metadata"]["code"], "validation_failed")

    def test_meeting_evidence_includes_source_and_short_snippet_only(self):
        r = self.resp(structured_evidence=None, meeting_evidence={"outcome": "ok", "rank_method": "rrf",
                     "hits": [{"meeting_id": 1, "meeting_date": "2024-01-10", "rank": 1, "lexical_rank": 1, "semantic_rank": 1, "semantic_score": 0.8, "snippet": "x" * 500}],
                     "semantic": {"semantic_candidates": 5, "scope_size": 23}})
        events = build_trace(r).to_list()
        me = next(e for e in events if e["name"] == "Meeting Evidence")
        self.assertEqual(me["metadata"]["hits"][0]["source"], "lexical+semantic")
        self.assertLessEqual(len(me["metadata"]["hits"][0]["snippet"]), 160)
        self.assertEqual(me["evidence_refs"], ["meeting:1:2024-01-10"])
        ec = next(e for e in events if e["name"] == "Evidence Collection")
        self.assertEqual(ec["metadata"]["semantic_candidate_count"], 5)
        self.assertEqual(ec["metadata"]["meeting_hit_count"], 1)
        self.assertIsNone(ec["metadata"]["structured_row_count"])

    def test_meeting_aggregate_evidence_alone_is_shown_as_structured_evidence_correctly_labelled(self):
        r = self.resp(structured_evidence=None, meeting_aggregate_evidence={"outcome": "ok", "row_count": 1, "sql": "SELECT COUNT(*) FROM meetings_meta"})
        events = build_trace(r).to_list()
        names = [e["name"] for e in events]
        self.assertIn("Structured Evidence", names)
        self.assertNotIn("Meeting Aggregate Evidence", names)                # only one side present: no separate panel needed
        se = next(e for e in events if e["name"] == "Structured Evidence")
        self.assertEqual((se["metadata"]["source"], se["metadata"]["row_count"]), ("meetings_meta (aggregate)", 1))

    def test_hyb10_style_hybrid_shows_both_evidence_sides_correctly_labelled_and_neither_dropped(self):
        """Regression test for the HYB-10 payload-audit finding: a hybrid question with an investments query (8 rows)
        AND a meetings_meta aggregate query (16 rows) at once must show two distinct, correctly-labelled panels."""
        r = self.resp(structured_evidence={"outcome": "ok", "row_count": 8, "columns": ["deal_name"], "source_tables": ["investments"], "sql": "SELECT DISTINCT deal_name FROM investments WHERE client_id = 'D12376'"},
                      meeting_evidence=None,
                      meeting_aggregate_evidence={"outcome": "ok", "row_count": 16, "columns": ["sector", "company"], "sql": "SELECT DISTINCT sector, company FROM meetings_meta WHERE client_id = 'D12376'"})
        events = build_trace(r).to_list()
        by_name = {e["name"]: e for e in events}
        self.assertIn("Structured Evidence", by_name)
        self.assertIn("Meeting Aggregate Evidence", by_name)                 # neither side is dropped
        se, ae = by_name["Structured Evidence"], by_name["Meeting Aggregate Evidence"]
        self.assertEqual((se["metadata"]["source"], se["metadata"]["row_count"]), ("investments", 8))           # correctly attributed to the structured side
        self.assertEqual((ae["metadata"]["source"], ae["metadata"]["row_count"]), ("meetings_meta (aggregate)", 16))  # correctly attributed to the aggregate side
        self.assertNotEqual(se["metadata"]["row_count"], ae["metadata"]["row_count"])
        ec = by_name["Evidence Collection"]
        self.assertEqual((ec["metadata"]["structured_row_count"], ec["metadata"]["meeting_aggregate_row_count"]), (8, 16))  # both counts visible, kept distinct, never summed
        self.assertNotIn("evidence_count", ec["metadata"])                    # the old, single conflated count no longer exists

    def test_no_hidden_reasoning_field_is_ever_produced(self):
        events = build_trace(self.resp()).to_list()
        dumped = str(events)
        for banned in ("chain_of_thought", "reasoning", "thinking", "scratchpad"):
            self.assertNotIn(banned, dumped.lower())

    def test_sensitive_response_fields_never_reach_the_trace(self):
        r = self.resp(structured_evidence={"outcome": "ok", "row_count": 1, "source_tables": ["investments"], "columns": ["n"], "api_key": "shh"})
        events = build_trace(r).to_list()
        self.assertNotIn("shh", str(events))


if __name__ == "__main__":
    unittest.main()
