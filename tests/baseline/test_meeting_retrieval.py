"""Tests for the Baseline lexical meeting retriever (SQLite FTS5 / BM25).

Unit tests use a small synthetic database built with the real table definitions and the real
FTS5 index code; a few tests use the real database. No Claude CLI, no benchmark questions.

Run from the project root:  python -m unittest tests.baseline.test_meeting_retrieval -v
"""
import datetime as dt
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.baseline import data_build, data_schema as ds, entity_resolution as er
from src.baseline import meeting_retrieval as mr
from src.baseline.meeting_index import FTS_TABLE, create_meeting_index
from tests.baseline.test_data_layer import investment_row, meeting_row, performance_row, run

MOJIBAKE = "Sanjay LÃ³pez"


def m(mid, client, group, date, summary, action=None, company="Orchid Ventures", attendees="Rahul Kaur", deal_size="209k USD"):
    return meeting_row(meeting_id=mid, client_id=client, group_id=group, date=dt.datetime.fromisoformat(date), summary=summary, action_items=action,
                       company=company, attendees=attendees, deal_size_estimate=deal_size, summary_char_length=len(summary))


MEETINGS = [
    m(1, "A12345", 346, "2024-01-10", "The sponsor lacks a dedicated CFO. Hedging concerns were raised about currency exposure.", None, attendees=f"Rahul Kaur; {MOJIBAKE}"),
    m(2, "A12345", 346, "2024-03-01", "Orion Infrastructure I deal terms were reviewed. Change-of-control clauses were noted.", "Action: introduce legal counsel to start drafting."),
    m(3, "B12345", 346, "2024-02-01", "The sponsor lacks a dedicated CFO. Orchid governance concerns were discussed.", "Action: schedule a management Q&A.", company="Summit Advisors"),
    m(4, "B12463", 464, "2023-05-05", "Hedging policy requested. Currency exposure discussed at length.", None),
    m(5, "A12350", 350, "2024-04-01", "Change-of-control clauses could complicate an exit.", "Action: review clauses."),
    m(6, "B12350", 350, "2024-04-02", "Change-of-control clauses flagged by counsel.", None),
    m(7, "C12345", 346, "2024-06-01", "Priya Sharma reviewed the deck.", None, attendees="Priya Sharma; Rahul Kaur"),
    m(8, "A12388", 388, "2024-07-01", "Hedging was mentioned once.", None),
    m(9, "A12377", 377, "2024-07-01", "Identical wording about runway.", None),
    m(10, "A12377", 377, "2024-07-01", "Identical wording about runway.", None),
    m(11, "A12377", 377, "2024-08-01", "Identical wording about runway.", None),
] + [m(100 + i, "A12399", 399, f"2024-05-{i + 1:02d}", f"Runway concerns were raised in session {i}.", None if i % 2 else f"Action: item {i}.") for i in range(30)]


def build_db(path: Path, index: bool = True) -> None:
    def inv(cid, gid, rm="Carlos Gomez"):
        return investment_row(Client_Id__c=cid, client_id=cid, **{"Client Name": f"Client_{cid}", "AccountName": f"Client {cid} Holdings", "Client_Group_Id_c": gid, "AccountRM": rm})
    investments = [inv("A12345", 346, "Priya Sharma"), inv("B12345", 346), inv("C12345", 346), inv("A12350", 350)]
    performances = [performance_row(Client_Id=c, Client_Name=f"Client_{c}", Client_Group_Id=g) for c, g in (("A12345", 346), ("B12345", 346), ("C12345", 346), ("A12350", 350))]
    conn = sqlite3.connect(path)
    for spec, rows in ((ds.INVESTMENTS, investments), (ds.MEETINGS, MEETINGS), (ds.PERFORMANCE, performances)):
        data_build._create_and_load(conn, spec, run(spec, rows))
    if index:
        create_meeting_index(conn)
    conn.commit()
    conn.close()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mr_test_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)
        cls.resolver = er.EntityResolver.from_database(cls.db)
        cls.r = mr.MeetingRetriever(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.r.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ask(self, question, **kw):
        return self.r.retrieve(question, self.resolver.resolve_question(question), **kw)

    def ids(self, ev):
        return [h.meeting_id for h in ev.hits]


class TestEntityFiltering(Base):
    def test_exact_client_filter_is_applied_before_text_search(self):
        ev = self.ask("What concerns were raised in client A12345's meetings?")
        self.assertEqual(ev.outcome, mr.OK)
        self.assertEqual(set(self.ids(ev)), {1})                       # only this client's meetings that contain the terms
        self.assertEqual((ev.filters["client_ids"], ev.filtered_meeting_count), (["A12345"], 2))
        self.assertTrue(any("meetings.client_id" in r for r in ev.filters["rationale"]))
        self.assertTrue(all(h.client_id == "A12345" for h in ev.hits))

    def test_a_meeting_only_client_is_retrievable(self):
        ev = self.ask("What did client B12463 say about hedging?")
        self.assertEqual((ev.outcome, self.ids(ev)), (mr.OK, [4]))
        self.assertEqual(ev.hits[0].client_id, "B12463")

    def test_group_filter_uses_the_meetings_sources_own_membership(self):
        ev = self.ask("Which meetings in group 350 mention change-of-control?")
        self.assertEqual((ev.outcome, set(self.ids(ev))), (mr.OK, {5, 6}))
        self.assertEqual((ev.filters["group_id"], ev.filters["group_source"]), (350, "meetings.group_id"))
        self.assertIn("B12350", {h.client_id for h in ev.hits})           # a member that exists only in the meetings source
        self.assertTrue(any("meetings source only" in r for r in ev.filters["rationale"]))
        self.assertTrue(any("membership differs across sources" in n for n in ev.entity_notes))
        self.assertTrue(all(h.group_id == 350 for h in ev.hits))

    def test_no_rm_to_meeting_inference(self):
        ev = self.ask("How many meetings were held by relationship manager Priya Sharma?")
        self.assertEqual((ev.outcome, ev.hits, ev.success), (mr.UNSUPPORTED_RM, (), False))
        self.assertIn("no RM field", ev.error["message"])
        self.assertIsNone(ev.filtered_meeting_count)                     # no search was run

    def test_an_rm_name_is_only_ever_plain_text_when_no_rm_entity_is_passed(self):
        ev = self.r.retrieve("Priya Sharma reviewed", None)
        self.assertEqual(self.ids(ev)[0], 7)
        self.assertEqual(ev.filters["client_ids"], [])

    def test_ambiguous_entity(self):
        ev = self.ask("Show meetings for client 12345")
        self.assertEqual((ev.outcome, ev.hits, ev.filtered_meeting_count), (mr.CLARIFICATION_NEEDED, (), None))
        self.assertEqual(ev.error["details"]["entities"][0]["candidates"], ["A12345", "B12345", "C12345"])

    def test_ambiguous_company_or_attendee_words_are_searched_as_text_not_blocked(self):
        ev = self.ask("Which meetings did Rahul mention hedging?")
        self.assertEqual(ev.outcome, mr.OK)
        self.assertIn("rahul", ev.query["terms"])
        self.assertTrue(any("does not identify one person or company" in n for n in ev.entity_notes))
        blocked = self.ask("Tell me what Orion said about hedging")            # Orion is a partial deal name: a real entity type is involved
        self.assertEqual(blocked.outcome, mr.CLARIFICATION_NEEDED)

    def test_entity_not_found(self):
        ev = self.ask("What concerns were raised for client E12345?")
        self.assertEqual((ev.outcome, ev.hits), (mr.ENTITY_NOT_FOUND, ()))

    def test_company_repetition_does_not_override_the_entity_filter(self):
        # 'Orchid' is the company of many meetings and appears in several summaries
        ev = self.ask("Orchid concerns for client B12345")
        self.assertEqual(self.ids(ev), [3])
        self.assertEqual(ev.filtered_meeting_count, 1)
        unfiltered = self.r.retrieve("Orchid concerns", None, top_k=50)
        self.assertGreater(len(unfiltered.hits), 5)
        self.assertGreater(len({h.client_id for h in unfiltered.hits}), 3)


class TestDealsAndTextFields(Base):
    def test_a_deal_name_in_meeting_text_creates_no_deal_relationship(self):
        ev = self.ask("What was said about Orion Infrastructure I?")
        self.assertEqual(self.ids(ev), [2])
        self.assertEqual((ev.filters["client_ids"], ev.filters["group_id"]), ([], None))
        self.assertTrue(any("no deal relationship" in n for n in ev.entity_notes))
        self.assertFalse({"deal_id", "deal_name"} & set(mr.MeetingHit.__dataclass_fields__))
        self.assertEqual(set(ev.to_dict()["hits"][0]) & {"deal_id", "deal_name"}, set())

    def test_deal_size_stays_raw_text(self):
        ev = self.ask("Which of client A12345's meetings discussed the CFO?")
        hit = ev.hits[0]
        self.assertEqual((hit.deal_size_estimate, type(hit.deal_size_estimate)), ("209k USD", str))
        self.assertNotIn("deal_size_usd", ev.to_dict()["hits"][0])

    def test_null_action_items_mean_not_recorded(self):
        ev = self.r.retrieve("q", None, query_text="hedging", filters=mr.MeetingFilters(client_ids=("A12345",)))
        (h,) = ev.hits
        self.assertEqual((h.meeting_id, h.action_items_state, h.action_items), (1, "not_recorded", None))
        ev = self.ask("What action items came from client A12345's meeting on 2024-03-01?")
        (h,) = ev.hits
        self.assertEqual((h.meeting_id, h.action_items_state, h.action_items), (2, "recorded", "Action: introduce legal counsel to start drafting."))

    def test_raw_mojibake_is_returned_as_stored_and_not_matched_by_clean_text(self):
        ev = self.r.retrieve("q", None, query_text=MOJIBAKE)
        self.assertEqual(self.ids(ev), [1])
        plain = re.sub(r"\[\[|\]\]", "", ev.hits[0].snippet)
        self.assertIn(MOJIBAKE, plain)
        clean = self.r.retrieve("q", None, query_text="Sanjay López")
        self.assertNotIn(1, [h.meeting_id for h in clean.hits if "lópez" in h.matched_terms])
        none = self.r.retrieve("q", None, query_text="López")
        self.assertEqual(none.outcome, mr.NO_LEXICAL_MATCH)

    def test_action_item_questions_weight_the_action_items_field(self):
        ev = self.ask("What action items mention legal counsel for client A12345?")
        self.assertTrue(ev.query["wants_action_items"])
        self.assertEqual(ev.query["weights"]["action_items"], mr.ACTION_ITEMS_WEIGHT)
        self.assertNotIn("action", ev.query["terms"])
        self.assertEqual(self.ids(ev)[0], 2)
        plain = self.ask("What concerns were raised for client A12345?")
        self.assertEqual(plain.query["weights"]["action_items"], 1.0)


class TestRankingAndBounds(Base):
    def test_documents_matching_more_terms_rank_higher(self):
        ev = self.r.retrieve("q", None, query_text="hedging currency", top_k=10)
        self.assertEqual(ev.rank_method, "bm25")
        self.assertEqual(self.ids(ev)[:2], [4, 1] if ev.hits[0].meeting_id == 4 else [1, 4])
        both = [h for h in ev.hits if set(h.matched_terms) == {"hedging", "currency"}]
        single = [h for h in ev.hits if len(h.matched_terms) == 1]
        self.assertTrue(both and single)
        self.assertGreater(min(h.score for h in both), max(h.score for h in single))
        self.assertEqual([h.rank for h in ev.hits], list(range(1, len(ev.hits) + 1)))

    def test_ranking_is_stable_and_ties_are_broken_by_date_then_id(self):
        first = self.r.retrieve("q", None, query_text="identical wording runway")
        again = self.r.retrieve("q", None, query_text="identical wording runway")
        self.assertEqual(first.hits, again.hits)
        tied = [h for h in first.hits if h.meeting_id in (9, 10, 11)]
        self.assertEqual([h.meeting_id for h in tied], [11, 9, 10])         # newest first, then lowest id
        self.assertEqual(tied[1].score, tied[2].score)

    def test_top_k_bound(self):
        ev = self.ask("What runway concerns were raised for client A12399?", top_k=5)
        self.assertEqual((len(ev.hits), ev.matched_count, ev.filtered_meeting_count, ev.fewer_than_k, ev.top_k), (5, 30, 30, False, 5))
        self.assertTrue(any("only the top 5" in n for n in ev.notes))
        default = self.ask("What runway concerns were raised for client A12399?")
        self.assertEqual((len(default.hits), default.top_k, mr.DEFAULT_TOP_K), (20, 20, 20))
        for bad in (0, 51, 1000):
            with self.subTest(top_k=bad), self.assertRaises(ValueError):
                self.r.retrieve("q", None, query_text="x", top_k=bad)

    def test_fewer_than_k_is_reported(self):
        ev = self.r.retrieve("q", None, query_text="hedging")
        self.assertEqual((ev.matched_count, len(ev.hits), ev.fewer_than_k), (3, 3, True))

    def test_evidence_is_bounded(self):
        ev = self.ask("What runway concerns were raised for client A12399?", top_k=50)
        self.assertLessEqual(len(ev.hits), 30)
        self.assertLess(len(json.dumps(ev.to_dict())), 60_000)
        self.assertTrue(all(len(h.snippet) < 400 for h in ev.hits))


class TestEmptyAndWeakResults(Base):
    def test_no_lexical_match_is_explicit_and_not_a_claim_of_absence(self):
        ev = self.ask("What zebra concerns for client A12345?")
        ev = self.r.retrieve("q", self.resolver.resolve_question("client A12345"), query_text="zebra")
        self.assertEqual((ev.outcome, ev.hits, ev.filtered_meeting_count, ev.matched_count, ev.success), (mr.NO_LEXICAL_MATCH, (), 2, 0, True))
        self.assertTrue(any("does not mean" in n for n in ev.notes))

    def test_no_meetings_for_the_filter(self):
        ev = self.r.retrieve("q", None, query_text="hedging", filters=mr.MeetingFilters(date_from="2030-01-01"))
        self.assertEqual((ev.outcome, ev.filtered_meeting_count, ev.hits), (mr.NO_MEETINGS_FOR_FILTER, 0, ()))
        ev = self.ask("What was discussed at client A12345's meeting on 1999-01-01?")
        self.assertEqual(ev.outcome, mr.NO_MEETINGS_FOR_FILTER)

    def test_no_terms_and_no_filter_returns_nothing(self):
        ev = self.r.retrieve("Which ones?", None)
        self.assertEqual((ev.outcome, ev.hits, ev.success), (mr.NO_TERMS, (), False))
        self.assertIn("unrestricted listing", ev.error["message"])

    def test_filter_only_listing_is_marked_as_not_ranked(self):
        ev = self.ask("Show meetings for client A12345")
        self.assertEqual((ev.outcome, ev.rank_method, self.ids(ev)), (mr.OK, "date_desc", [2, 1]))
        self.assertTrue(all(h.score is None for h in ev.hits))
        self.assertTrue(any("not lexically ranked" in n for n in ev.notes))

    def test_missing_index_is_an_explicit_error(self):
        db = self.tmp / "noindex.sqlite"
        build_db(db, index=False)
        with mr.MeetingRetriever(db) as r:
            ev = r.retrieve("q", None, query_text="hedging")
        self.assertEqual((ev.outcome, ev.success), (mr.INDEX_MISSING, False))

    def test_missing_database(self):
        with self.assertRaises(FileNotFoundError):
            mr.MeetingRetriever(self.tmp / "missing.sqlite")


class TestThreadSafety(Base):
    def test_retriever_built_on_one_thread_is_usable_from_another(self):
        """Regression: a sqlite3.Connection may only be used by the thread that created it. This
        mirrors the real bug: `cls.r` was built in setUpClass (the test runner's main thread) and
        must still answer correctly when called from a different thread, with no cross-thread
        ProgrammingError, and existing DB safety (read-only PRAGMA) intact on that other thread."""
        import threading

        results: list = []
        errors: list = []

        def worker():
            try:
                results.append(self.ask("What concerns were raised in client A12345's meetings?"))
                with self.assertRaises(sqlite3.Error):
                    self.r.conn.execute("DELETE FROM meetings")
            except Exception as exc:  # noqa: BLE001 - captured for assertion below, not swallowed
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual((results[0].outcome, set(self.ids(results[0]))), (mr.OK, {1}))
        # the main thread must still work afterwards, on its own connection
        self.assertEqual(set(self.ids(self.ask("What concerns were raised in client A12345's meetings?"))), {1})


class TestQueryDerivation(unittest.TestCase):
    def test_terms_exclude_stopwords_intent_words_and_dates(self):
        d = mr.derive_query("What concerns were discussed in the meeting on 2022-10-01 about hedging?")
        self.assertEqual((d["terms"], d["meeting_date"]), (["concerns", "hedging"], "2022-10-01"))

    def test_quoted_text_becomes_a_phrase(self):
        d = mr.derive_query('Which meetings say "absence of independent directors"?')
        self.assertEqual((d["terms"], d["phrases"]), ([], ["absence of independent directors"]))

    def test_terms_are_not_stemmed_or_folded(self):
        self.assertEqual(mr.derive_query("Concerns CONCERN López")["terms"], ["concerns", "concern", "lópez"])


class TestIndex(unittest.TestCase):
    def test_index_is_derived_from_meetings_and_leaves_them_unchanged(self):
        tmp = Path(tempfile.mkdtemp(prefix="idx_"))
        try:
            db = tmp / "t.sqlite"
            build_db(db, index=False)
            before = sqlite3.connect(db).execute("SELECT * FROM meetings ORDER BY source_row").fetchall()
            from src.baseline.meeting_index import rebuild_meeting_index
            rebuild_meeting_index(db)
            rebuild_meeting_index(db)            # rebuild is idempotent
            c = sqlite3.connect(db)
            self.assertEqual(c.execute("SELECT * FROM meetings ORDER BY source_row").fetchall(), before)
            self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0], len(MEETINGS))
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name = ?", (FTS_TABLE,)).fetchone()[0]
            self.assertIn("remove_diacritics 0", sql)
            for hidden in ("sector", "region", "investment_stage", "deal_size"):
                self.assertNotIn(hidden, sql)
            c.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _real_db_has_index():
    if not data_build.DEFAULT_DB.is_file():
        return False
    c = sqlite3.connect(f"{data_build.DEFAULT_DB.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return c.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (FTS_TABLE,)).fetchone() is not None
    finally:
        c.close()


@unittest.skipUnless(_real_db_has_index(), "Baseline database with meetings_fts not built")
class TestRealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = mr.MeetingRetriever()
        cls.raw = sqlite3.connect(f"{data_build.DEFAULT_DB.resolve().as_uri()}?mode=ro", uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.r.close()
        cls.raw.close()

    def test_index_covers_every_meeting(self):
        self.assertEqual(self.raw.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0], 20_000)

    def test_phrase_match_equals_an_independent_like_count(self):
        want = self.raw.execute("SELECT COUNT(*) FROM meetings WHERE summary LIKE ? OR attendees LIKE ?", (f"%{MOJIBAKE}%", f"%{MOJIBAKE}%")).fetchone()[0]
        ev = self.r.retrieve("q", None, query_text=f'"{MOJIBAKE}"', top_k=50)
        self.assertEqual(ev.matched_count, want)
        self.assertGreater(want, 0)

    def test_client_filter_returns_only_that_clients_meetings_and_all_are_reachable(self):
        want = self.raw.execute("SELECT COUNT(*) FROM meetings WHERE client_id = 'D12345'").fetchone()[0]
        ev = self.r.retrieve("q", None, query_text="concerns", filters=mr.MeetingFilters(client_ids=("D12345",)), top_k=50)
        self.assertEqual(ev.filtered_meeting_count, want)
        self.assertTrue(all(h.client_id == "D12345" for h in ev.hits))

    def test_meeting_group_membership_is_the_meetings_own(self):
        ev = self.r.retrieve("q", None, query_text="concerns", filters=mr.MeetingFilters(group_id=346), top_k=50)
        members = {r[0] for r in self.raw.execute("SELECT DISTINCT client_id FROM meetings WHERE group_id = 346")}
        self.assertTrue({h.client_id for h in ev.hits} <= members)

    def test_null_action_items_count_matches_the_table(self):
        ev = self.r.retrieve("q", None, query_text="concerns", filters=mr.MeetingFilters(client_ids=("A12345",)), top_k=50)
        nulls = self.raw.execute("SELECT COUNT(*) FROM meetings WHERE client_id = 'A12345' AND action_items IS NULL").fetchone()[0]
        self.assertEqual(sum(h.action_items_state == "not_recorded" for h in ev.hits), nulls if ev.matched_count == ev.filtered_meeting_count else sum(h.action_items is None for h in ev.hits))


if __name__ == "__main__":
    unittest.main()
