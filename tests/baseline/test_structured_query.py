"""Tests for the Baseline structured-query engine.

The Claude adapter is mocked in all unit tests (no CLI calls). Data is a small synthetic
database built with the real table definitions; a few tests use the real database and are
skipped if it has not been built. No benchmark questions are used.

Run from the project root:  python -m unittest tests.baseline.test_structured_query -v
"""
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.baseline import data_build, data_schema as ds, entity_resolution as er
from src.baseline import structured_query as sq
from src.baseline import structured_schema as ss
from src.baseline.claude_cli import CliResult, ErrorClass
from src.baseline.sql_guard import static_scan
from tests.baseline.test_data_layer import investment_row, meeting_row, performance_row, run


def build_db(path: Path) -> None:
    def inv(cid, gid, deal_id, deal_name, usd, rm, status, cur="USD"):
        return investment_row(Client_Id__c=cid, client_id=cid, **{"Client Name": f"Client_{cid}", "AccountName": f"Client {cid} Holdings", "Client_Group_Id_c": gid,
                              "deal_id": deal_id, "deal_name": deal_name, "Investment_Amount_USD_for_agg": usd, "Investment_Amount_Natural_Currency": int(usd),
                              "AccountRM": rm, "ClientStatus": status, "Natural_Currency_Code": cur})
    investments = [inv("A12345", 346, "DL100000", "Orion Infrastructure I", 100.0, "Carlos Gomez", "Active"),
                   inv("A12345", 346, "DL100001", "NorthBridge Growth Fund", 200.0, "Sara Khan", "Closed", "GBP"),
                   inv("A12345", 346, "DL100001", "Orion Infrastructure IV", 300.0, "Rahul Mehta", "Active", "GBP"),
                   inv("B12345", 346, "DL100002", "BluePeak Venture II", 50.0, "Sara Khan", "Dormant")]
    for i in range(60):   # many rows for the truncation test
        investments.append(inv("C12345", 346, "DL100003", "BluePeak Venture III", 1.0 + i, "Daniel Lee", "Prospect"))

    def perf(cid, date, moic, aum, **kw):
        return performance_row(Client_Id=cid, Client_Name=f"Client_{cid}", CI_Total_MOIC=moic, Total_AUM_Amount=aum, As_Of_Date=dt.datetime.fromisoformat(date), **kw)
    performances = [perf("A12345", "2020-01-01", "1.5x", 10.0), perf("A12345", "2021-01-01", "1.55x", 20.0), perf("A12345", "2022-01-01", "2.5x", 30.0),
                    perf("B12345", "2019-05-05", "3.0x", 40.0), perf("B12345", "2023-03-03", "1.9x", 50.0),
                    performance_row(Client_Id=None, Client_Name=None, IsGroup_Flag="Y", CI_Total_MOIC="9.9x", Total_AUM_Amount=999.0, As_Of_Date=dt.datetime(2024, 1, 1))]
    meetings = [meeting_row(client_id="B12463", group_id=464)]
    conn = sqlite3.connect(path)
    for spec, rows in ((ds.INVESTMENTS, investments), (ds.MEETINGS, meetings), (ds.PERFORMANCE, performances)):
        data_build._create_and_load(conn, spec, run(spec, rows))
    conn.commit()
    conn.close()


def cli_ok(text):
    return CliResult(success=True, result_text=text, model="claude-sonnet-5", cli_version="2.1.283", exit_code=0, timing={"wall_clock_ms": 1.0})


def reply(sql, explanation="x"):
    return cli_ok(json.dumps({"sql": sql, "explanation": explanation}))


class FakeAdapter:
    def __init__(self, result=None):
        self.result, self.prompts = result, []

    def run(self, prompt):
        self.prompts.append(prompt)
        return self.result


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sq_test_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.resolver = er.EntityResolver.from_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def engine(self, result=None, **cfg):
        e = sq.StructuredQueryEngine(sq.EngineConfig(db_path=self.db, **cfg), FakeAdapter(result))
        self.addCleanup(e.close)
        return e

    def ask(self, question, route, result, **cfg):
        e = self.engine(result, **cfg)
        return e, e.run(question, route, self.resolver.resolve_question(question))


class TestSuccess(Base):
    def test_normal_successful_result(self):
        e, ev = self.ask("How many investment records exist?", "investment", reply("SELECT COUNT(*) AS n FROM investments"))
        self.assertEqual((ev.outcome, ev.success, ev.columns, ev.rows, ev.row_count), (sq.OK, True, ("n",), ((64,),), 1))
        self.assertEqual((ev.source_tables, ev.objects_used), (("investments",), ("investments",)))
        self.assertEqual(ev.sql, "SELECT COUNT(*) AS n FROM investments")
        self.assertEqual(ev.llm["model"], "claude-sonnet-5")
        for key in ("llm_ms", "validation_ms", "execution_ms", "total_ms"):
            self.assertIn(key, ev.timing)
        self.assertTrue(ev.rules_applied)
        json.dumps(ev.to_dict())

    def test_fenced_json_is_accepted(self):
        text = "```json\n" + json.dumps({"sql": "SELECT 1 AS x FROM investments LIMIT 1"}) + "\n```"
        _, ev = self.ask("q", "investment", cli_ok(text))
        self.assertEqual(ev.outcome, sq.OK)

    def test_empty_result(self):
        _, ev = self.ask("q", "investment", reply("SELECT client_id FROM investments WHERE client_id = 'Z99999'"))
        self.assertEqual((ev.outcome, ev.success, ev.row_count, ev.rows), (sq.EMPTY_RESULT, True, 0, ()))
        self.assertIsNone(ev.error)

    def test_result_is_bounded(self):
        _, ev = self.ask("q", "investment", reply("SELECT client_id, deal_id FROM investments WHERE client_id = 'C12345'"), max_rows=5)
        self.assertEqual((ev.row_count, len(ev.rows), ev.truncated), (5, 5, True))
        self.assertTrue(any("truncated" in n for n in ev.notes))


class TestLlmOutput(Base):
    def test_malformed_output(self):
        for text in ("SELECT COUNT(*) FROM investments", "not json at all", "[1,2]", "Here is the query: {\"sql\": \"SELECT 1\"}", ""):
            with self.subTest(text=text):
                e, ev = self.ask("q", "investment", cli_ok(text))
                self.assertEqual((ev.outcome, ev.error["stage"]), (sq.MALFORMED_LLM_OUTPUT, "llm_output"))
                self.assertIsNone(ev.sql)
                self.assertEqual(len(e.adapter.prompts), 1)

    def test_missing_sql(self):
        for text in ('{"explanation": "x"}', '{"sql": ""}', '{"sql": "   "}', '{"sql": 5}', '{"sql": null}'):
            with self.subTest(text=text):
                _, ev = self.ask("q", "investment", cli_ok(text))
                self.assertEqual(ev.outcome, sq.MISSING_SQL)

    def test_llm_can_declare_the_question_unsupported(self):
        _, ev = self.ask("q", "investment", cli_ok('{"sql": null, "unsupported_reason": "no such field"}'))
        self.assertEqual((ev.outcome, ev.error["message"]), (sq.UNSUPPORTED_QUESTION, "no such field"))

    def test_llm_failure_is_explicit_and_not_retried(self):
        failure = CliResult(success=False, error_class=ErrorClass.TIMEOUT, error_message="too slow", timed_out=True)
        e, ev = self.ask("q", "investment", failure)
        self.assertEqual((ev.outcome, ev.error["code"]), (sq.LLM_FAILURE, "LLM_CLI_TIMEOUT"))
        self.assertEqual(len(e.adapter.prompts), 1)


class TestValidation(Base):
    def rejected(self, sql, code, route="investment"):
        """Rejected before execution. `code` may be a tuple when SQLite itself reports a name that does not exist at all."""
        e, ev = self.ask("q", route, reply(sql))
        self.assertEqual(ev.outcome, sq.SQL_REJECTED, sql)
        codes = code if isinstance(code, tuple) else (code,)
        self.assertTrue(set(codes) & set(ev.error["details"]["codes"]), (sql, ev.error))
        self.assertEqual(ev.rows, ())
        self.assertEqual(len(e.adapter.prompts), 1)
        return ev

    def test_invalid_sql(self):
        self.rejected("SELEC COUNT(*) FROM investments", "not_a_select")
        self.rejected("SELECT COUNT(* FROM investments", "sql_compile_error")
        self.rejected("SELECT no_such FROM investments", ("unauthorized_column", "sql_compile_error"))
        self.rejected("SELECT ci_total_moic_num FROM performance_client", "sql_compile_error", "performance")

    def test_write_and_ddl_statements_are_rejected(self):
        for sql in ["INSERT INTO investments (client_id) VALUES ('x')", "UPDATE investments SET account_rm = 'x'", "DELETE FROM investments",
                    "DROP TABLE investments", "ALTER TABLE investments ADD COLUMN z TEXT", "CREATE TABLE z (a)", "ATTACH DATABASE 'x.db' AS x",
                    "PRAGMA table_info(investments)", "VACUUM", "WITH x AS (SELECT 1) DELETE FROM investments", "REPLACE INTO investments VALUES (1)",
                    "BEGIN", "EXPLAIN SELECT 1"]:
            with self.subTest(sql=sql):
                self.rejected(sql, ("forbidden_keyword", "not_a_select"))

    def test_multiple_statements_and_comments_are_rejected(self):
        self.rejected("SELECT 1 FROM investments; DROP TABLE investments", "multiple_statements")
        self.rejected("SELECT 1 FROM investments; SELECT 2 FROM investments", "multiple_statements")
        self.rejected("SELECT 1 FROM investments -- trailing", "comment_not_allowed")
        self.rejected("SELECT 1 /* x */ FROM investments", "comment_not_allowed")
        self.rejected("SELECT 'unterminated FROM investments", "unbalanced_quotes")
        ok = self.ask("q", "investment", reply("SELECT COUNT(*) FROM investments;"))[1]
        self.assertEqual(ok.outcome, sq.OK)      # one trailing semicolon is fine

    def test_keywords_inside_string_literals_are_not_false_positives(self):
        _, ev = self.ask("q", "investment", reply("SELECT COUNT(*) FROM investments WHERE deal_name = 'Drop; Delete -- /* x */'"))
        self.assertEqual(ev.outcome, sq.OK)

    def test_unauthorized_table(self):
        for sql, route in [("SELECT * FROM meetings", "investment"), ("SELECT * FROM sqlite_master", "investment"), ("SELECT COUNT(*) FROM performance", "performance"),
                           ("SELECT COUNT(*) FROM main.performance", "performance"), 
                           ("SELECT COUNT(*) FROM investments WHERE client_id IN (SELECT client_id FROM meetings)", "investment")]:
            with self.subTest(sql=sql):
                self.rejected(sql, "unauthorized_table", route)

    def test_unauthorized_column(self):
        for sql in ["SELECT nonauth_rm_alias FROM investments", "SELECT account_name_org FROM investments", "SELECT * FROM investments",
                    "SELECT source_row FROM investments", "SELECT nonauth_account_owner_id, COUNT(*) FROM investments GROUP BY 1"]:
            with self.subTest(sql=sql):
                self.rejected(sql, "unauthorized_column")
        self.rejected("SELECT client_last_met_date FROM performance_client", ("unauthorized_column", "sql_compile_error"), "performance")
        self.rejected("SELECT is_group_flag FROM performance_client", ("unauthorized_column", "sql_compile_error"), "performance")

    def test_unauthorized_function(self):
        self.rejected("SELECT random() FROM investments", "unauthorized_function")
        self.rejected("SELECT load_extension('x') FROM investments", "forbidden_keyword")

    def test_with_queries_work_and_cte_names_cannot_shadow_real_objects(self):
        ok = self.ask("q", "investment", reply("WITH t AS (SELECT client_id, COUNT(*) AS n FROM investments GROUP BY client_id) SELECT MAX(n) FROM t"))[1]
        self.assertEqual((ok.outcome, ok.rows), (sq.OK, ((60,),)))
        for sql, route in [("WITH performance_client AS (SELECT * FROM performance) SELECT COUNT(*) FROM performance_client", "performance"),
                           ("WITH performance AS (SELECT 1 AS x) SELECT COUNT(*) FROM performance", "performance"),
                           ("WITH meetings AS (SELECT 1 AS x FROM investments) SELECT COUNT(*) FROM meetings", "investment")]:
            with self.subTest(sql=sql):
                self.rejected(sql, "cte_name_collision", route)
        self.rejected("WITH t AS (SELECT * FROM meetings) SELECT COUNT(*) FROM t", "unauthorized_table")

    def test_no_cross_source_queries(self):
        self.rejected("SELECT COUNT(*) FROM investments i JOIN performance_latest p ON i.client_id = p.client_id", "cross_source_query")

    def test_route_source_mismatch(self):
        self.rejected("SELECT COUNT(*) FROM investments", "route_source_mismatch", "performance")
        self.rejected("SELECT COUNT(*) FROM performance_client", "route_source_mismatch", "investment")

    def test_deal_name_is_never_matched_with_like(self):
        self.rejected("SELECT COUNT(*) FROM investments WHERE deal_name LIKE 'Orion Infrastructure I%'", "like_on_deal_name")
        self.rejected("SELECT COUNT(*) FROM investments WHERE deal_name NOT GLOB 'Orion*'", "like_on_deal_name")

    def test_guard_never_rewrites_or_repairs_sql(self):
        bad = "SELECT COUNT(*) FROM investments WHERE nonexistent = 1"
        e, ev = self.ask("q", "investment", reply(bad))
        self.assertEqual((ev.sql, len(e.adapter.prompts)), (bad, 1))

    def test_static_scan_directly(self):
        self.assertEqual(static_scan("SELECT 1"), [])
        self.assertEqual([i.code for i in static_scan("")], ["empty_sql"])
        self.assertEqual([i.code for i in static_scan(None)], ["empty_sql"])


class TestEntities(Base):
    def test_ambiguous_entity_stops_before_the_llm(self):
        e, ev = self.ask("Show the investments for client 12345.", "investment", reply("SELECT 1 FROM investments"))
        self.assertEqual((ev.outcome, ev.error["code"]), (sq.CLARIFICATION_NEEDED, "ambiguous_entity"))
        self.assertEqual(ev.error["details"]["entities"][0]["candidates"], ["A12345", "B12345", "C12345"])
        self.assertEqual(e.adapter.prompts, [])

    def test_unknown_entity_stops_before_the_llm(self):
        e, ev = self.ask("How many investment records does client E12345 have?", "investment", reply("SELECT 1 FROM investments"))
        self.assertEqual((ev.outcome, e.adapter.prompts), (sq.ENTITY_NOT_FOUND, []))

    def test_resolved_client_constrains_the_query(self):
        q = "How many investment records does client A12345 have?"
        e, ev = self.ask(q, "investment", reply("SELECT COUNT(*) FROM investments WHERE client_id = 'A12345'"))
        self.assertEqual((ev.outcome, ev.rows), (sq.OK, ((3,),)))
        self.assertEqual([u["canonical"] for u in ev.entities_used], ["A12345"])
        prompt = e.adapter.prompts[0]
        self.assertIn("client_id = 'A12345'", prompt)
        self.assertIn(q, prompt)

    def test_query_that_ignores_the_resolved_client_is_rejected(self):
        _, ev = self.ask("How many investment records does client A12345 have?", "investment", reply("SELECT COUNT(*) FROM investments"))
        self.assertEqual((ev.outcome, ev.error["code"]), (sq.SQL_REJECTED, "entity_constraint_missing"))
        _, ev = self.ask("How many investment records does client A12345 have?", "investment", reply("SELECT COUNT(*) FROM investments WHERE client_id = 'B12345'"))
        self.assertEqual(ev.error["code"], "entity_constraint_missing")

    def test_deal_id_with_multiple_names_is_queried_by_id_not_collapsed(self):
        q = "How many records carry deal ID DL100001, and which deal names appear?"
        e, ev = self.ask(q, "investment", reply("SELECT deal_name, COUNT(*) AS n FROM investments WHERE deal_id = 'DL100001' GROUP BY deal_name ORDER BY deal_name"))
        self.assertEqual((ev.outcome, ev.rows), (sq.OK, (("NorthBridge Growth Fund", 1), ("Orion Infrastructure IV", 1))))
        prompt = e.adapter.prompts[0]
        self.assertIn("deal_id = 'DL100001'", prompt)
        self.assertIn("NorthBridge Growth Fund", prompt)
        self.assertIn("Orion Infrastructure IV", prompt)
        self.assertIn("do not collapse", prompt)
        _, bad = self.ask(q, "investment", reply("SELECT COUNT(*) FROM investments WHERE deal_name = 'Orion Infrastructure IV'"))
        self.assertEqual(bad.error["code"], "entity_constraint_missing")

    def test_deal_name_uses_exact_match_and_prefix_collision_is_not_followed(self):
        q = "What is the total USD amount for the Orion Infrastructure I deal?"
        _, ev = self.ask(q, "investment", reply("SELECT SUM(investment_amount_usd_for_agg) FROM investments WHERE deal_name = 'Orion Infrastructure I'"))
        self.assertEqual((ev.outcome, ev.rows), (sq.OK, ((100.0,),)))
        self.assertEqual(ev.entities_used[0]["canonical"], "Orion Infrastructure I")

    def test_rm_uses_account_rm(self):
        e, ev = self.ask("How many records does relationship manager Sara Khan have?", "investment", reply("SELECT COUNT(*) FROM investments WHERE account_rm = 'Sara Khan'"))
        self.assertEqual((ev.outcome, ev.rows), (sq.OK, ((2,),)))
        self.assertIn("account_rm = 'Sara Khan'", e.adapter.prompts[0])

    def test_meeting_only_client_has_no_structured_data(self):
        for route in ("investment", "performance"):
            e, ev = self.ask("What is B12463's latest Total AUM?", route, reply("SELECT 1 FROM investments"))
            self.assertEqual((ev.outcome, ev.error["code"], e.adapter.prompts), (sq.NO_STRUCTURED_DATA, "no_data_for_entity", []))

    def test_deal_or_rm_entities_do_not_exist_in_performance(self):
        e, ev = self.ask("What is the latest MOIC for the Orion Infrastructure I deal?", "performance", reply("SELECT 1 FROM performance_latest"))
        self.assertEqual((ev.outcome, e.adapter.prompts), (sq.NO_STRUCTURED_DATA, []))

    def test_group_constraint_and_membership_note(self):
        q = "What is the Total AUM for group 346?"
        e, ev = self.ask(q, "performance", reply("SELECT total_aum_amount, as_of_date FROM performance_group WHERE group_id = 346"))
        self.assertEqual((ev.outcome, ev.rows), (sq.OK, ((999.0, "2024-01-01"),)))
        self.assertIn("group_id = 346", e.adapter.prompts[0])


class TestPerformanceRules(Base):
    def test_group_rows_are_excluded_from_client_queries(self):
        _, ev = self.ask("How many performance snapshots do clients have?", "performance", reply("SELECT COUNT(*) FROM performance_client"))
        self.assertEqual(ev.rows, ((5,),))                      # 6 physical rows, 1 is a group row
        self.assertIn("client snapshot rows only (group rows excluded)", ev.rules_applied)
        _, ev = self.ask("q", "performance", reply("SELECT COUNT(*) FROM performance_group"))
        self.assertEqual(ev.rows, ((1,),))
        _, ev = self.ask("q", "performance", reply("SELECT COUNT(DISTINCT client_id) FROM performance_client"))
        self.assertEqual(ev.rows, ((2,),))

    def test_latest_per_client(self):
        _, ev = self.ask("q", "performance", reply("SELECT client_id, as_of_date, total_aum_amount FROM performance_latest ORDER BY client_id"))
        self.assertEqual(ev.rows, (("A12345", "2022-01-01", 30.0), ("B12345", "2023-03-03", 50.0)))
        self.assertIn("latest snapshot per client (each client's own maximum as_of_date)", ev.rules_applied)
        self.assertEqual(ev.source_tables, ("performance",))
        self.assertEqual(ev.objects_used, ("performance_latest",))

    def test_moic_comparison_is_numeric_not_textual(self):
        # '1.5x' < '1.55x' numerically, but '1.5x' > '1.55x' as text ('x' sorts after '5')
        _, ev = self.ask("q", "performance", reply("SELECT as_of_date FROM performance_client WHERE client_id = 'A12345' AND ci_total_moic >= 1.5 AND ci_total_moic < 1.6 ORDER BY ci_total_moic"))
        self.assertEqual(ev.rows, (("2020-01-01",), ("2021-01-01",)))
        _, ev = self.ask("q", "performance", reply("SELECT client_id FROM performance_latest WHERE ci_total_moic > 2.0 ORDER BY client_id"))
        self.assertEqual(ev.rows, (("A12345",),))
        _, ev = self.ask("q", "performance", reply("SELECT MAX(ci_total_moic) FROM performance_client"))
        self.assertEqual(ev.rows, ((3.0,),))                    # the group row's 9.9 is excluded

    def test_raw_moic_text_columns_are_not_exposed(self):
        self.assertNotIn("ci_total_moic_num", ss.performance_columns())
        self.assertEqual(ss.performance_columns()["ci_total_moic"], "REAL")
        self.assertNotIn("_parse_status", " ".join(ss.performance_columns()))

    def test_unavailable_metric_is_explicit_and_skips_the_llm(self):
        for q in ("What is client A12345's latest COP MOIC?", "What is the latest HF Current MOIC?", "Latest PE IRR for client A12345?", "What is the Mena MOIC?"):
            with self.subTest(question=q):
                e, ev = self.ask(q, "performance", reply("SELECT 1 FROM performance_latest"))
                self.assertEqual((ev.outcome, ev.error["code"], e.adapter.prompts), (sq.UNAVAILABLE_METRIC, "metric_not_available", []))
                self.assertFalse(ev.success)

    def test_available_metrics_are_not_blocked(self):
        for q in ("What is the latest CI Total MOIC?", "latest RE Core IRR?", "latest COP Total IRR?", "latest HF Total MOIC", "latest CI AUM", "latest Mena AUM"):
            with self.subTest(question=q):
                self.assertIsNone(ss.check_metric_availability(q))

    def test_availability_comes_from_the_columns(self):
        have = ss.available_metrics()
        self.assertIn(("cop", "total", "irr"), have)
        self.assertNotIn(("cop", None, "moic"), have)
        self.assertNotIn(("hf", "current", "moic"), have)


class TestPromptAndContext(Base):
    def test_investment_context_hides_unapproved_columns_and_tables(self):
        ctx = ss.build_schema_context("investment")
        self.assertIn("account_rm", ctx)
        self.assertIn("record", ctx)
        for hidden in ("nonauth_", "account_name_org", "meetings", "performance", "summary", "attendees"):
            self.assertNotIn(hidden, ctx)

    def test_performance_context_uses_views_and_numeric_moic(self):
        ctx = ss.build_schema_context("performance")
        for name in ("performance_latest", "performance_client", "performance_group", "ci_total_moic", "NUMERIC", "COP"):
            self.assertIn(name, ctx)
        self.assertNotRegex(ctx, r"_num\b")
        for hidden in ("is_group_flag", "client_last_met_date", "investments", "nonauth_"):
            self.assertNotIn(hidden, ctx)

    def test_prompt_is_small_and_explicit(self):
        e, _ = self.ask("What is client A12345's latest Total AUM?", "performance", reply("SELECT total_aum_amount FROM performance_latest WHERE client_id = 'A12345'"))
        prompt = e.adapter.prompts[0]
        self.assertLess(len(prompt), 6000)
        for needle in ("ONLY a JSON object", '"sql"', "Route: performance", "client_id = 'A12345'", "performance_latest", "Never build 'latest' yourself"):
            self.assertIn(needle, prompt)

    def test_unsupported_route_makes_no_llm_call(self):
        for route in ("meeting", "hybrid", "ambiguous", "other"):
            e, ev = self.ask("q", route, reply("SELECT 1"))
            self.assertEqual((ev.outcome, e.adapter.prompts), (sq.UNSUPPORTED_ROUTE, []))


class TestExecution(Base):
    def test_read_only_execution(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        e = self.engine()
        for sql in ["DELETE FROM investments", "CREATE TABLE z (a)", "CREATE TEMP TABLE z (a)"]:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.Error):
                e.conn.execute(sql)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(e.execute_sql("SELECT COUNT(*) FROM investments", "investment").rows, ((64,),))

    def test_no_retry_after_sql_failure(self):
        e, ev = self.ask("q", "investment", reply("SELECT COUNT(*) FROM nonexistent_table"))
        self.assertEqual(ev.outcome, sq.SQL_REJECTED)
        self.assertEqual(len(e.adapter.prompts), 1)

    def test_query_timeout(self):
        sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
        ev = self.engine(query_timeout_s=0.3).execute_sql(sql, "investment")
        self.assertEqual((ev.outcome, ev.error["code"]), (sq.TIMEOUT, "query_timeout"))

    def test_engine_needs_a_database(self):
        with self.assertRaises(FileNotFoundError):
            sq.StructuredQueryEngine(sq.EngineConfig(db_path=self.tmp / "missing.sqlite"))


@unittest.skipUnless(data_build.DEFAULT_DB.is_file(), "Baseline database not built")
class TestRealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = sq.StructuredQueryEngine(sq.EngineConfig(max_rows=500), adapter=FakeAdapter())
        cls.raw = sqlite3.connect(f"{data_build.DEFAULT_DB.resolve().as_uri()}?mode=ro", uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.e.close()
        cls.raw.close()

    def rows(self, sql, route):
        ev = self.e.execute_sql(sql, route)
        self.assertEqual(ev.outcome, sq.OK, ev.error)
        return ev.rows

    def test_views_match_the_documented_grain(self):
        self.assertEqual(self.rows("SELECT COUNT(*) FROM performance_client", "performance"), ((1536,),))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM performance_group", "performance"), ((96,),))
        self.assertEqual(self.rows("SELECT COUNT(*), COUNT(DISTINCT client_id) FROM performance_latest", "performance"), ((192, 192),))

    def test_latest_view_equals_an_independent_computation(self):
        want = self.raw.execute("SELECT c.client_id, c.as_of_date FROM performance c JOIN (SELECT client_id, MAX(as_of_date) d FROM performance WHERE is_group_flag='N' GROUP BY client_id) m "
                                "ON c.client_id = m.client_id AND c.as_of_date = m.d WHERE c.is_group_flag = 'N' ORDER BY 1").fetchall()
        got = self.rows("SELECT client_id, as_of_date FROM performance_latest ORDER BY client_id", "performance")
        self.assertEqual(list(got), [tuple(r) for r in want])

    def test_numeric_moic_view_equals_the_derived_column(self):
        want = self.raw.execute("SELECT COUNT(*) FROM performance WHERE is_group_flag='N' AND ci_current_moic_num > 2.5").fetchone()[0]
        self.assertEqual(self.rows("SELECT COUNT(*) FROM performance_client WHERE ci_current_moic > 2.5", "performance"), ((want,),))


if __name__ == "__main__":
    unittest.main()
