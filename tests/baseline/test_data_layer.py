"""Tests for the Baseline data ingestion and SQLite analytical layer.

Unit tests use small synthetic rows. The integration tests build the real database
from data/Assignment_Data.xlsx once (about a minute) and are skipped if the
workbook is absent. No Claude CLI or network access is involved.

Run from the project root:  python -m unittest tests.baseline.test_data_layer -v
"""
import datetime as dt
from contextlib import closing
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.baseline import data_build, data_schema as ds
from src.baseline.data_validation import run_checks

WORKBOOK = data_build.DEFAULT_WORKBOOK


def investment_row(**over):
    row = {"Client_Id__c": "A12345", "Client Name": "Client_A12345", "Client_Group_Id_c": 346, "deal_id": "DL100000",
           "id_CapitalCall": 1, "Investment_Amount_USD_for_agg": 12568.75, "Investment_Amount_Natural_Currency": 10055,
           "Natural_Currency_Code": "GBP", "Investment_Exchange_Rate": 1.25, "dat_MinInvested": dt.datetime(2022, 7, 16),
           "deal_name": "Orion Infrastructure I", "cod_lob": "COP", "nam_lob": "Credit", "client_id": "A12345", "flg_Realised": True,
           "ClientStatus": "Active", "AccountName": "Client A12345 Holdings", "AccountName_org": "Client A12345 Org",
           "AccountRM": "Carlos Gomez", "AccountRM_org": "Carlos Gomez", "AccountOwnerId": "OWN1",
           "AccountRMEmail": "psharma@investcorp.com", "AccountRMEmail_org": "psharma@investcorp.com", "RM_Alias": "psharma"}
    row.update(over)
    return row


def meeting_row(**over):
    row = {"meeting_id": 1, "date": dt.datetime(2022, 9, 1), "attendees": "Sanjay LÃ³pez; Rahul Kaur", "company": "Orchid Ventures",
           "sector": "agritech", "region": "EMEA", "investment_stage": "seed", "deal_size_estimate": "209k USD",
           "summary": "agreedâ€”legal to draft; sensitivity to a Â±231 bps change", "action_items": None,
           "summary_char_length": 60, "client_id": "B12463", "group_id": 464}
    row.update(over)
    return row


def performance_row(**over):
    row = {c: None for c in ds.PERFORMANCE.source_columns}
    row.update({"Client_Group_Id": 346, "Client_Id": "A12345", "Client_Name": "Client_A12345", "IsGroup_Flag": "N",
                "CI_Current_MOIC": "2.26x", "CI_Current_IRR": 0.0684, "As_Of_Date": dt.datetime(2024, 10, 25), "Product_Count_Number": 4})
    row.update(over)
    return row


def run(spec, rows):
    header = spec.source_columns
    return ds.transform_rows(spec, header, [tuple(r[h] for h in header) for r in rows])


def as_dict(spec, out_row):
    return dict(zip(spec.column_names(), out_row))


class TestMoicParsing(unittest.TestCase):
    def test_representative_values(self):
        for raw, want in [("2.26x", 2.26), ("3.0x", 3.0), ("0.7x", 0.7), ("10x", 10.0), ("1.5x", 1.5), ("1.55x", 1.55)]:
            with self.subTest(raw=raw):
                self.assertEqual(ds.parse_moic(raw), (want, "ok"))

    def test_invalid_values_become_null_with_status(self):
        for raw in ["2.26", "x", "2.26 x", "2.26X", " 2.26x", "2.26x ", "2.26x\n", "-1.0x", "1,5x", "1.x", ".5x", "", "abc", 3.0, True]:
            with self.subTest(raw=raw):
                self.assertEqual(ds.parse_moic(raw), (None, "invalid"))

    def test_null_is_reported_as_null(self):
        self.assertEqual(ds.parse_moic(None), (None, "null"))

    def test_raw_value_is_preserved_next_to_derived_values(self):
        out = as_dict(ds.PERFORMANCE, run(ds.PERFORMANCE, [performance_row(CI_Current_MOIC="2.26x", CI_Total_MOIC="bad")])[0])
        self.assertEqual((out["ci_current_moic"], out["ci_current_moic_num"], out["ci_current_moic_parse_status"]), ("2.26x", 2.26, "ok"))
        self.assertEqual((out["ci_total_moic"], out["ci_total_moic_num"], out["ci_total_moic_parse_status"]), ("bad", None, "invalid"))
        self.assertEqual(out["re_core_moic_parse_status"], "null")


class TestDealSizeParsing(unittest.TestCase):
    def test_representative_values(self):
        for raw, want in [("2M USD", 2_000_000.0), ("209k USD", 209_000.0), ("0.5M USD", 500_000.0), ("150M USD", 150_000_000.0),
                          ("88k USD", 88_000.0), ("1.5B USD", 1_500_000_000.0), ("0.75M USD", 750_000.0)]:
            with self.subTest(raw=raw):
                self.assertEqual(ds.parse_deal_size(raw), (want, "ok"))

    def test_invalid_values_become_null_with_status(self):
        for raw in ["2m USD", "2K USD", "2b USD", "2M usd", "2M EUR", "2 M USD", "2MUSD", "M USD", "2M USD ", " 2M USD", "2M  USD",
                    "2T USD", "abc", "", "2", 2000000, "-2M USD"]:
            with self.subTest(raw=raw):
                self.assertEqual(ds.parse_deal_size(raw), (None, "invalid"))

    def test_null(self):
        self.assertEqual(ds.parse_deal_size(None), (None, "null"))

    def test_raw_text_is_never_modified(self):
        rows = run(ds.MEETINGS, [meeting_row(deal_size_estimate="209k USD"), meeting_row(meeting_id=2, deal_size_estimate="weird")])
        a, b = as_dict(ds.MEETINGS, rows[0]), as_dict(ds.MEETINGS, rows[1])
        self.assertEqual((a["deal_size_estimate"], a["deal_size_estimate_usd"], a["deal_size_estimate_parse_status"]), ("209k USD", 209000.0, "ok"))
        self.assertEqual((b["deal_size_estimate"], b["deal_size_estimate_usd"], b["deal_size_estimate_parse_status"]), ("weird", None, "invalid"))


class TestClientIdCanonicalization(unittest.TestCase):
    def test_single_canonical_identifier_is_stored(self):
        cols = ds.INVESTMENTS.column_names()
        self.assertIn("client_id", cols)
        self.assertNotIn("client_id__c", cols)
        self.assertEqual([c for c in cols if "client_id" in c], ["client_id"])
        out = as_dict(ds.INVESTMENTS, run(ds.INVESTMENTS, [investment_row()])[0])
        self.assertEqual(out["client_id"], "A12345")

    def test_disagreeing_duplicate_fields_stop_the_build(self):
        with self.assertRaises(ds.IngestError):
            run(ds.INVESTMENTS, [investment_row(Client_Id__c="B12345")])

    def test_case_and_whitespace_variants_are_not_silently_merged(self):
        for variant in ("a12345", "A12345 ", " A12345"):
            with self.subTest(variant=variant), self.assertRaises(ds.IngestError):
                run(ds.INVESTMENTS, [investment_row(Client_Id__c=variant)])

    def test_rm_and_status_columns(self):
        out = as_dict(ds.INVESTMENTS, run(ds.INVESTMENTS, [investment_row()])[0])
        self.assertEqual(out["account_rm"], "Carlos Gomez")
        self.assertEqual(out["client_status"], "Active")
        self.assertEqual(out["nonauth_account_rm_email"], "psharma@investcorp.com")
        self.assertNotIn("status", ds.INVESTMENTS.column_names())


class TestPreservation(unittest.TestCase):
    def test_null_action_items_stay_null_and_empty_text_stays_empty(self):
        rows = run(ds.MEETINGS, [meeting_row(action_items=None), meeting_row(meeting_id=2, action_items=""), meeting_row(meeting_id=3, action_items="Action: x")])
        got = [as_dict(ds.MEETINGS, r)["action_items"] for r in rows]
        self.assertEqual(got, [None, "", "Action: x"])

    def test_mojibake_is_loaded_exactly_as_it_is(self):
        out = as_dict(ds.MEETINGS, run(ds.MEETINGS, [meeting_row()])[0])
        self.assertEqual(out["attendees"], "Sanjay LÃ³pez; Rahul Kaur")
        self.assertIn("Â±231", out["summary"])
        self.assertIn("â€”", out["summary"])

    def test_group_rows_are_kept_with_null_client(self):
        rows = run(ds.PERFORMANCE, [performance_row(), performance_row(Client_Id=None, Client_Name=None, IsGroup_Flag="Y", As_Of_Date=dt.datetime(2012, 4, 26))])
        self.assertEqual(len(rows), 2)
        group = as_dict(ds.PERFORMANCE, rows[1])
        self.assertEqual((group["is_group_flag"], group["client_id"], group["client_name"], group["group_id"]), ("Y", None, None, 346))
        self.assertEqual(as_dict(ds.PERFORMANCE, rows[0])["is_group_flag"], "N")

    def test_snapshots_are_not_collapsed(self):
        rows = run(ds.PERFORMANCE, [performance_row(As_Of_Date=dt.datetime(2020, 1, d)) for d in (1, 2, 3)])
        self.assertEqual([as_dict(ds.PERFORMANCE, r)["as_of_date"] for r in rows], ["2020-01-01", "2020-01-02", "2020-01-03"])

    def test_dates_are_iso_and_not_altered(self):
        self.assertEqual(ds.convert_value("date", dt.datetime(2024, 10, 25), "x"), "2024-10-25")
        self.assertEqual(ds.convert_value("date", dt.datetime(2024, 10, 25, 13, 30), "x"), "2024-10-25 13:30:00")
        self.assertEqual(ds.convert_value("date", dt.date(2024, 10, 25), "x"), "2024-10-25")

    def test_unexpected_types_stop_the_build(self):
        with self.assertRaises(ds.IngestError):
            run(ds.INVESTMENTS, [investment_row(deal_id=123)])
        with self.assertRaises(ds.IngestError):
            run(ds.INVESTMENTS, [investment_row(dat_MinInvested="2022-07-16")])

    def test_schema_shape(self):
        self.assertEqual((len(ds.INVESTMENTS.cols), len(ds.MEETINGS.cols), len(ds.PERFORMANCE.cols)), (24, 13, 65))
        self.assertEqual(len(ds.PERFORMANCE.derived()), 16)
        self.assertEqual(len(ds.MEETINGS.derived()), 2)

    def test_header_mismatch_is_rejected(self):
        with self.assertRaises(ds.IngestError):
            ds.transform_rows(ds.MEETINGS, ds.MEETINGS.source_columns[:-1], [])


@unittest.skipUnless(WORKBOOK.is_file(), "assignment workbook not present")
class TestRealBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="baseline_db_test_"))
        cls.db = cls.tmp / "baseline.sqlite"
        cls.report = data_build.build_database(WORKBOOK, cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def connect(self, path=None):
        return data_build._open_read_only(path or self.db)

    def test_build_succeeded_and_all_checks_passed(self):
        self.assertTrue(self.report.checks)
        self.assertEqual([c for c in self.report.checks if not c.passed], [])

    def test_expected_row_counts(self):
        with closing(self.connect()) as conn:
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("investments", "meetings", "performance")}
        self.assertEqual(counts, {"investments": 50_000, "meetings": 20_000, "performance": 1_632})
        self.assertEqual(self.report.row_counts, counts)

    def test_database_can_be_reopened_and_queried(self):
        conn = self.connect()
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            n = conn.execute("SELECT COUNT(DISTINCT client_id) FROM investments").fetchone()[0]
            self.assertEqual(n, 192)
            rm = conn.execute("SELECT account_rm, COUNT(*) FROM investments GROUP BY account_rm ORDER BY 2 DESC LIMIT 1").fetchone()
            self.assertEqual(rm, ("Daniel Lee", 10_114))
        finally:
            conn.close()

    def test_database_is_read_only_when_opened_read_only(self):
        with closing(self.connect()) as conn, self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM investments")

    def test_group_rows_and_snapshots_preserved(self):
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM performance WHERE is_group_flag='Y'").fetchone()[0], 96)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM performance WHERE is_group_flag='N'").fetchone()[0], 1_536)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM performance WHERE is_group_flag='Y' AND client_id IS NOT NULL").fetchone()[0], 0)

    def test_null_action_items_preserved(self):
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meetings WHERE action_items IS NULL").fetchone()[0], 5_008)

    # ---- data_dictionary_DRAFT.md decisions 1, 2, 5, 7, 8 (official dictionary reconciliation): the raw values
    # are never reconciled or "fixed"; these facts are frozen so a future change that starts reconciling them
    # (or that changes the data) is caught, named individually rather than only through the generic checks gate.
    def test_first_after_last_performance_investment_dates_are_ignored_by_the_application(self):
        """Decision 1 (Abhishek's clarification, supersedes the earlier "preserve as contradictory" framing):
        performance First_*/Last_*_Investment_Date are not usable application facts and are not derived from or
        reconciled with anything. The raw columns are still physically stored (never deleted), just excluded
        from the approved SQL-generation schema."""
        with closing(self.connect()) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(performance)")}
            for fam in ("ci", "re", "hf"):
                self.assertIn(f"first_{fam}_investment_date", cols)
                self.assertIn(f"last_{fam}_investment_date", cols)
        # ...but the approved SQL-generation schema excludes them: no query route may use them as facts.
        from src.baseline.structured_schema import performance_columns
        for fam in ("ci", "re", "hf"):
            self.assertNotIn(f"first_{fam}_investment_date", performance_columns())
            self.assertNotIn(f"last_{fam}_investment_date", performance_columns())

    def test_client_last_met_date_is_not_reconciled_with_meetings_or_as_of_date(self):
        """Decision 2: performance.client_last_met_date is preserved raw; meetings.meeting_date remains the
        source used for 'last met' questions (A13); the two are never silently merged."""
        with closing(self.connect()) as conn:
            after_as_of = conn.execute("SELECT COUNT(*) FROM performance WHERE client_last_met_date IS NOT NULL AND as_of_date IS NOT NULL "
                                       "AND client_last_met_date > as_of_date").fetchone()[0]
            self.assertEqual(after_as_of, 832)
            mismatch = conn.execute("""SELECT COUNT(*) FROM (SELECT p.client_id, p.client_last_met_date,
                (SELECT MAX(m.meeting_date) FROM meetings m WHERE m.client_id = p.client_id) AS latest_meeting
                FROM performance p WHERE p.client_id IS NOT NULL AND p.client_last_met_date IS NOT NULL)
                WHERE latest_meeting IS NOT NULL AND client_last_met_date <> latest_meeting""").fetchone()[0]
            self.assertEqual(mismatch, 1_536)   # every client performance row
            # the field the SQL-generation LLM can query for "last met" never includes client_last_met_date
            from src.baseline.structured_schema import performance_columns
            self.assertNotIn("client_last_met_date", performance_columns())

    def test_total_aum_is_never_derived_from_or_equal_to_the_lob_sum(self):
        """Decision 5: total_aum_amount is preserved as given; it is never replaced by, or checked against, the
        sum of the LOB *_aum_amount fields."""
        with closing(self.connect()) as conn:
            mismatch = conn.execute("""SELECT COUNT(*) FROM performance WHERE total_aum_amount IS NOT NULL AND ABS(total_aum_amount -
                (COALESCE(ci_aum_amount,0)+COALESCE(hf_aum_amount,0)+COALESCE(re_aum_amount,0)+COALESCE(mena_aum_amount,0)+COALESCE(tech_aum_amount,0)+COALESCE(pref_shares_aum_amount,0))) > 0.01""").fetchone()[0]
            self.assertEqual(mismatch, 1_632)   # every row with a value

    def test_id_capital_call_is_a_flag_not_an_identifier(self):
        """Decision 7: the dictionary calls this a capital-call identifier; observed values are only 0/1."""
        with closing(self.connect()) as conn:
            values = {r[0] for r in conn.execute("SELECT DISTINCT id_capital_call FROM investments")}
        self.assertEqual(values, {0, 1})

    def test_group_membership_differs_by_source_and_neither_overrides_the_other(self):
        """Decision 8: for the group ids common to investments and meetings, membership can differ; investments
        is never overwritten by meetings' membership or vice versa (source-specific, per data_dictionary_DRAFT.md
        decision 8 and structured_schema/entity_resolution's own group handling)."""
        with closing(self.connect()) as conn:
            inv_groups = {r[0] for r in conn.execute("SELECT DISTINCT group_id FROM investments WHERE group_id IS NOT NULL")}
            meet_groups = {r[0] for r in conn.execute("SELECT DISTINCT group_id FROM meetings WHERE group_id IS NOT NULL")}
            common = inv_groups & meet_groups
            self.assertEqual(len(common), 96)
            differ = 0
            for g in common:
                inv_members = {r[0] for r in conn.execute("SELECT DISTINCT client_id FROM investments WHERE group_id = ?", (g,))}
                meet_members = {r[0] for r in conn.execute("SELECT DISTINCT client_id FROM meetings WHERE group_id = ?", (g,))}
                differ += inv_members != meet_members
            self.assertEqual(differ, 64)

    def test_moic_and_deal_size_derivation(self):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT ci_current_moic, ci_current_moic_num, ci_current_moic_parse_status FROM performance WHERE client_id='A12345' AND as_of_date='2024-10-25'").fetchone()
            self.assertEqual(row, ("1.33x", 1.33, "ok"))
            row = conn.execute("SELECT deal_size_estimate, deal_size_estimate_usd, deal_size_estimate_parse_status FROM meetings WHERE meeting_id=17166").fetchone()
            self.assertEqual(row, ("150M USD", 150_000_000.0, "ok"))

    def test_mojibake_preserved_in_database(self):
        with closing(self.connect()) as conn:
            n = conn.execute("SELECT COUNT(*) FROM meetings WHERE attendees LIKE ?", ("%Sanjay LÃ³pez%",)).fetchone()[0]
        self.assertEqual(n, 14)

    def test_validation_detects_a_tampered_database(self):
        copy = self.tmp / "tampered.sqlite"
        shutil.copy(self.db, copy)
        rw = sqlite3.connect(copy)
        rw.execute("DELETE FROM investments WHERE source_row = 2")
        rw.commit()
        rw.close()
        conn = self.connect(copy)
        try:
            failed = [c.name for c in run_checks(conn) if not c.passed]
        finally:
            conn.close()
        self.assertIn("investments: row count", failed)

    def test_failed_build_does_not_replace_existing_database(self):
        before = self.db.read_bytes()
        with self.assertRaises(ds.IngestError):
            data_build.build_database(self.tmp / "missing.xlsx", self.db)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
