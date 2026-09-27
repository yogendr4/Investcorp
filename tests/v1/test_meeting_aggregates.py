"""V1-2 tests: the guarded meeting-aggregate engine on a synthetic database. The adapter is scripted: no Claude call.

Run from the project root:  python -m unittest tests.v1.test_meeting_aggregates -v
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline.claude_cli import CliResult, ErrorClass
from src.v1 import meeting_aggregates as ma
from tests.v1.fixtures import NoCalls, ScriptedAdapter, build_db, sql_reply

V = ma.VIEW


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v1_agg_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.resolver = er.EntityResolver.from_database(cls.db)
        cls.cfg = ma.AggregateConfig(db_path=cls.db)
        cls.engine = ma.MeetingAggregateEngine(cls.cfg, NoCalls())      # SQL-only tests never reach the adapter

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def sql(self, statement):
        return self.engine.execute_sql(statement)

    def ask(self, question, *replies, config=None):
        adapter = ScriptedAdapter(*replies)
        eng = ma.MeetingAggregateEngine(config or self.cfg, adapter)
        try:
            return eng.run(question, self.resolver.resolve_question(question)), adapter
        finally:
            eng.close()


class TestView(Base):
    def test_view_exposes_exactly_the_approved_columns_and_no_text(self):
        cols = self.sql(f"SELECT * FROM {V} LIMIT 1").columns
        self.assertEqual(cols, ("meeting_id", "client_id", "group_id", "meeting_date", "company", "sector", "region", "stage", "deal_size_estimate_raw", "deal_size_estimate_usd"))
        for hidden in ("summary", "attendees", "action_items", "summary_char_length"):
            self.assertNotIn(hidden, cols)

    def test_prompt_shows_the_view_vocabulary_but_no_narrative_columns(self):
        _ev, adapter = self.ask("How many meetings are there?", sql_reply(f"SELECT COUNT(*) AS n FROM {V}"))
        p = adapter.prompts[0]
        self.assertIn("meetings_meta", p)
        self.assertIn("series_b", p)
        self.assertIn("fintech", p)
        self.assertNotIn("summary", p)
        self.assertNotIn("attendees", p)


class TestAggregateShapes(Base):
    def test_count_meetings(self):
        ev = self.sql(f"SELECT COUNT(*) AS n FROM {V} WHERE client_id = 'A12345'")
        self.assertEqual((ev.outcome, ev.rows), (ma.OK, ((4,),)))
        self.assertEqual((ev.source_tables, ev.objects_used), (("meetings",), (V,)))

    def test_latest_and_earliest_meeting_date_and_range(self):
        self.assertEqual(self.sql(f"SELECT MAX(meeting_date) FROM {V} WHERE client_id = 'A12345'").rows, (("2025-02-20",),))
        self.assertEqual(self.sql(f"SELECT MIN(meeting_date) FROM {V} WHERE client_id = 'A12345'").rows, (("2023-11-15",),))
        ev = self.sql(f"SELECT COUNT(*), MIN(meeting_date), MAX(meeting_date) FROM {V} WHERE client_id = 'B12463'")
        self.assertEqual(ev.rows, ((2, "2023-05-05", "2025-01-12"),))

    def test_distinct_values(self):
        ev = self.sql(f"SELECT DISTINCT sector FROM {V} WHERE client_id = 'A12345' ORDER BY sector")
        self.assertEqual([r[0] for r in ev.rows], ["edtech", "fintech", "healthcare"])
        ev = self.sql(f"SELECT COUNT(DISTINCT sector), COUNT(DISTINCT company) FROM {V} WHERE client_id = 'A12345'")
        self.assertEqual(ev.rows, ((3, 2),))
        ev = self.sql(f"SELECT stage, COUNT(*) FROM {V} GROUP BY stage ORDER BY stage")
        self.assertEqual(dict(ev.rows), {"growth": 32, "seed": 5, "series_b": 2})

    def test_largest_and_smallest_parsed_deal_size(self):
        big = self.sql(f"SELECT meeting_id, deal_size_estimate_raw, deal_size_estimate_usd FROM {V} WHERE client_id = 'A12345' AND deal_size_estimate_usd IS NOT NULL "
                       "ORDER BY deal_size_estimate_usd DESC, meeting_id ASC LIMIT 1")
        self.assertEqual(big.rows, ((3, "150M USD", 150000000.0),))
        small = self.sql(f"SELECT meeting_id, deal_size_estimate_usd FROM {V} WHERE client_id = 'A12345' ORDER BY deal_size_estimate_usd ASC, meeting_id ASC LIMIT 1")
        self.assertEqual(small.rows, ((1, 209000.0),))

    def test_filtered_aggregates(self):
        self.assertEqual(self.sql(f"SELECT COUNT(*) FROM {V} WHERE stage = 'series_b'").rows, ((2,),))
        self.assertEqual(self.sql(f"SELECT COUNT(*) FROM {V} WHERE client_id = 'A12345' AND stage = 'series_b'").rows, ((1,),))
        self.assertEqual(self.sql(f"SELECT COUNT(*) FROM {V} WHERE region = 'Global' AND sector = 'agritech'").rows, ((2,),))

    def test_group_aggregation_uses_meeting_source_membership(self):
        ev, adapter = self.ask("How many meetings did group 346 have?", sql_reply(f"SELECT COUNT(*) AS n FROM {V} WHERE group_id = 346"))
        self.assertEqual((ev.outcome, ev.rows), (ma.OK, ((6,),)))              # A12345 (4) + B12345 (2); C12345 has no meetings
        self.assertTrue(any("meetings source's own group_id" in n for n in ev.notes))
        self.assertIn("group_id = 346", adapter.prompts[0])
        self.assertIn("meetings source", adapter.prompts[0])

    def test_empty_result_is_a_valid_outcome(self):
        ev = self.sql(f"SELECT meeting_id FROM {V} WHERE client_id = 'A12345' AND stage = 'pre-seed'")
        self.assertEqual((ev.outcome, ev.rows, ev.success), (ma.EMPTY_RESULT, (), True))

    def test_row_cap_is_reported(self):
        eng = ma.MeetingAggregateEngine(ma.AggregateConfig(db_path=self.db, max_rows=5), NoCalls())
        ev = eng.execute_sql(f"SELECT meeting_id FROM {V} ORDER BY meeting_id")
        eng.close()
        self.assertEqual((ev.row_count, ev.truncated), (5, True))


class TestGuard(Base):
    def rejected(self, statement, code=None):
        ev = self.sql(statement)
        self.assertEqual(ev.outcome, ma.SQL_REJECTED, statement)
        self.assertEqual(ev.rows, ())
        if code:
            self.assertIn(code, ev.error["details"]["codes"], ev.error)
        return ev

    def test_read_only(self):
        raw = sqlite3.connect(f"{self.db.resolve().as_uri()}?mode=ro", uri=True)
        before = raw.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        for stmt in (f"DELETE FROM {V}", "INSERT INTO meetings (meeting_id) VALUES (999)", "UPDATE meetings SET sector = 'x'", "DROP VIEW meetings_meta",
                     "PRAGMA writable_schema = 1", "CREATE TABLE t (a)", f"SELECT 1; DELETE FROM {V}", "ATTACH DATABASE 'x.db' AS x"):
            with self.subTest(stmt=stmt):
                self.rejected(stmt)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM meetings").fetchone()[0], before)
        raw.close()
        with self.assertRaises(sqlite3.DatabaseError):
            self.engine.conn.execute("CREATE TABLE forbidden (a)")           # refused by the authorizer and by the read-only connection

    def test_unauthorized_sources_are_rejected(self):
        self.rejected("SELECT COUNT(*) FROM meetings", "unauthorized_table")
        self.rejected("SELECT summary FROM meetings WHERE meeting_id = 1", "unauthorized_table")
        self.rejected("SELECT * FROM investments", "unauthorized_table")
        self.rejected("SELECT * FROM performance", "unauthorized_table")
        self.rejected("SELECT * FROM meetings_fts", "unauthorized_table")
        self.rejected("SELECT name FROM sqlite_master", "unauthorized_table")

    def test_narrative_text_is_not_reachable_through_the_view(self):
        for col in ("summary", "attendees", "action_items"):
            with self.subTest(col=col):
                self.rejected(f"SELECT {col} FROM {V}")

    def test_no_second_source_and_no_joins(self):
        self.rejected(f"SELECT COUNT(*) FROM {V} WHERE client_id IN (SELECT client_id FROM investments)", "unauthorized_table")
        self.rejected(f"SELECT COUNT(*) FROM {V} a JOIN investments b ON a.client_id = b.client_id", "join_not_allowed")
        self.rejected(f"SELECT COUNT(*) FROM {V} a JOIN {V} b ON a.client_id = b.client_id", "join_not_allowed")
        self.rejected(f"SELECT COUNT(*) FROM {V} a, meetings b", "unauthorized_table")
        self.rejected(f"SELECT COUNT(*) FROM {V} WHERE client_id IN (SELECT client_id FROM performance_latest)")
        self.rejected("SELECT 1")                                              # reads no approved source at all

    def test_functions_and_comments(self):
        self.rejected(f"SELECT load_extension('x') FROM {V}")
        self.rejected(f"SELECT COUNT(*) FROM {V} -- comment", "comment_not_allowed")


class TestPipeline(Base):
    def test_full_pipeline_with_a_scripted_reply(self):
        ev, adapter = self.ask("How many meetings did client A12345 have?", sql_reply(f"SELECT COUNT(*) AS meeting_count FROM {V} WHERE client_id = 'A12345'"))
        self.assertEqual((ev.outcome, ev.rows, len(adapter.prompts)), (ma.OK, ((4,),), 1))
        self.assertEqual(ev.entities_used[0]["constraint"], "client_id = 'A12345'")
        self.assertIn("client_id = 'A12345'", adapter.prompts[0])

    def test_entity_literal_is_required(self):
        ev, _ = self.ask("How many meetings did client A12345 have?", sql_reply(f"SELECT COUNT(*) FROM {V}"))
        self.assertEqual(ev.outcome, ma.SQL_REJECTED)
        self.assertIn("entity_constraint_missing", ev.error["details"]["codes"])

    def test_no_retry_and_no_repair_on_rejected_sql(self):
        ev, adapter = self.ask("How many meetings did client A12345 have?", sql_reply("SELECT COUNT(*) FROM meetings WHERE client_id = 'A12345'"), "SHOULD NEVER BE USED")
        self.assertEqual(ev.outcome, ma.SQL_REJECTED)
        self.assertEqual((len(adapter.prompts), len(adapter.replies)), (1, 1))     # one call, the second scripted reply is untouched
        self.assertIn("meetings", ev.sql)                                          # the rejected SQL is reported as generated, not repaired

    def test_no_retry_on_malformed_missing_unsupported_and_llm_failure(self):
        cases = [("not json at all", ma.MALFORMED_LLM_OUTPUT), ('{"explanation": "x"}', ma.MISSING_SQL),
                 ('{"sql": null, "unsupported_reason": "asks about meeting text"}', ma.UNSUPPORTED_QUESTION),
                 (CliResult(success=False, error_class=ErrorClass.TIMEOUT, error_message="too slow", timed_out=True), ma.LLM_FAILURE)]
        for reply, outcome in cases:
            with self.subTest(outcome=outcome):
                ev, adapter = self.ask("How many meetings did client A12345 have?", reply, "SHOULD NEVER BE USED")
                self.assertEqual((ev.outcome, len(adapter.prompts)), (outcome, 1))
                self.assertFalse(ev.success)

    def test_execution_error_and_timeout_are_not_retried(self):
        ev, adapter = self.ask("How many meetings are there?", sql_reply(f"SELECT COUNT(*) FROM {V} WHERE no_such_column = 1"), "NEVER")
        self.assertEqual((ev.outcome, len(adapter.prompts)), (ma.SQL_REJECTED, 1))
        slow = ma.AggregateConfig(db_path=self.db, query_timeout_s=0.0)
        heavy = f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 50000000) SELECT COUNT(*) FROM c, {V}"
        ev, adapter = self.ask("How many meetings are there?", sql_reply(heavy), "NEVER", config=slow)
        self.assertIn(ev.outcome, (ma.TIMEOUT, ma.SQL_REJECTED))
        self.assertEqual(len(adapter.prompts), 1)

    def test_entity_gate_stops_before_any_llm_call(self):
        for question, outcome, code in [("How many meetings did client 12345 have?", ma.CLARIFICATION_NEEDED, "ambiguous_entity"),
                                        ("How many meetings did client E12345 have?", ma.ENTITY_NOT_FOUND, "entity_not_found"),
                                        ("How many meetings did client C12345 have?", ma.NO_MEETING_DATA, "no_data_for_entity")]:
            with self.subTest(question=question):
                ev, adapter = self.ask(question)
                self.assertEqual((ev.outcome, ev.error["code"], adapter.prompts), (outcome, code, []))

    def test_rm_and_deal_filters_are_unsupported(self):
        ev, adapter = self.ask("How many meetings did Priya Sharma hold?")
        self.assertEqual((ev.outcome, ev.error["code"], adapter.prompts), (ma.UNSUPPORTED_QUESTION, "rm_meeting_link", []))
        ev, adapter = self.ask("How many meetings did the Orion Infrastructure I deal have?")
        if ev.error and ev.error["code"] == "deal_not_in_meetings":
            self.assertEqual((ev.outcome, adapter.prompts), (ma.UNSUPPORTED_QUESTION, []))


class TestThreadSafety(Base):
    def test_engine_built_on_one_thread_is_usable_from_another(self):
        """Regression: a sqlite3.Connection may only be used by the thread that created it. This
        mirrors the real bug: `cls.engine` was built in setUpClass (the test runner's main
        thread) and must still answer correctly when called from a different thread, with no
        cross-thread ProgrammingError, and existing DB safety (the guard) intact there too."""
        import threading

        results: list = []
        errors: list = []

        def worker():
            try:
                results.append(self.sql(f"SELECT COUNT(*) AS n FROM {V}"))
                v = self.engine.guard.validate("SELECT * FROM meetings")   # the base table is never approved, only the view
                self.assertFalse(v.ok)
            except Exception as exc:  # noqa: BLE001 - captured for assertion below, not swallowed
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, ma.OK)
        # the main thread must still work afterwards, on its own connection
        self.assertEqual(self.sql(f"SELECT COUNT(*) AS n FROM {V}").outcome, ma.OK)


if __name__ == "__main__":
    unittest.main()
