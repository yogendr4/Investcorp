"""Tests for the deterministic Baseline entity resolver.

Most tests use a small synthetic database that mirrors the documented data
realities (four clients sharing the number 12345, a deal id with two names,
prefix-colliding deal names, a meeting-only client, an RM who is also an attendee).
A few integration tests use the real Baseline database and the visible benchmark
question texts (the holdout file is never read); they are skipped if the
database has not been built.

Run from the project root:  python -m unittest tests.baseline.test_entity_resolution -v
"""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.baseline import entity_resolution as er
from src.baseline.data_build import DEFAULT_DB

VISIBLE = Path(__file__).resolve().parents[1] / "evaluation" / "questions.json"
QUINNS = "; ".join(f"Quinn {s}" for s in ["Ash", "Bay", "Cole", "Dunn", "East", "Fox", "Gray", "Hall", "Ivy", "Jay", "Kerr", "Lane", "Moss", "Nash"])

INVESTMENTS = [  # client_id, client_name, account_name, group_id, deal_id, deal_name, account_rm
    ("A12345", "Client_A12345", "Client A12345 Holdings", 346, "DL100000", "Orion Infrastructure I", "Carlos Gomez"),
    ("A12345", "Client_A12345", "Client A12345 Holdings", 346, "DL100001", "NorthBridge Growth Fund", "Sara Khan"),
    ("A12345", "Client_A12345", "Client A12345 Holdings", 346, "DL100001", "Orion Infrastructure IV", "Rahul Mehta"),
    ("B12345", "Client_B12345", "Client B12345 Holdings", 346, "DL100002", "BluePeak Venture II", "Priya Sharma"),
    ("B12345", "Client_B12345", "Client B12345 Holdings", 346, "DL100003", "BluePeak Venture III", "Daniel Lee"),
    ("C12345", "Client_C12345", "Client C12345 Holdings", 346, "DL100009", "Summit Credit Opportunities", "Priya Sharma"),
    ("D12345", "Client_D12345", "Client D12345 Holdings", 346, "DL100013", "Emerald Real Estate Fund", "Rahul Mehta"),
    ("A12350", "Client_A12350", "Client A12350 Holdings", 350, "DL100000", "Orion Infrastructure I", "Carlos Gomez"),
]
PERFORMANCE = [  # client_id (None = group row), client_name, group_id
    ("A12345", "Client_A12345", 346), ("B12345", "Client_B12345", 346), ("C12345", "Client_C12345", 346), ("D12345", "Client_D12345", 346),
    ("A12350", "Client_A12350", 350), (None, None, 346), (None, None, 350),
]
MEETINGS = [  # client_id, group_id, company, attendees
    ("A12345", 346, "Orchid Ventures", "Rahul Kaur; Sanjay LÃ³pez"),
    ("B12345", 346, "Summit Advisors", "Rahul Mehta; Rahul Dutta"),
    ("C12345", 346, "Maple Asset Mgmt", "Priya Sharma; Rahul Kaur"),
    ("D12345", 346, "Orchid Ventures", "Wang Lee"),
    ("A12350", 350, "Orchid Ventures", "Rahul Dutta"), ("B12350", 350, "Orchid Ventures", "Wang Lee"),
    ("C12350", 350, "Orchid Ventures", "Wang Lee"), ("D12350", 350, "Orchid Ventures", "Wang Lee"),
    ("B12463", 464, "Summit Advisors", "Rahul Kaur; Wang Lee"),
    ("B12463", 464, "Orchid Ventures", QUINNS),
]


def build_synthetic_db(path: Path) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE investments (client_id TEXT, client_name TEXT, account_name TEXT, group_id INTEGER, deal_id TEXT, deal_name TEXT, account_rm TEXT)")
    c.execute("CREATE TABLE performance (client_id TEXT, client_name TEXT, group_id INTEGER)")
    c.execute("CREATE TABLE meetings (client_id TEXT, group_id INTEGER, company TEXT, attendees TEXT)")
    c.executemany("INSERT INTO investments VALUES (?,?,?,?,?,?,?)", INVESTMENTS)
    c.executemany("INSERT INTO performance VALUES (?,?,?)", PERFORMANCE)
    c.executemany("INSERT INTO meetings VALUES (?,?,?,?)", MEETINGS)
    c.commit()
    c.close()


class SyntheticCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="entity_test_"))
        db = cls.tmp / "t.sqlite"
        build_synthetic_db(db)
        cls.r = er.EntityResolver.from_database(db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def values(self, res):
        return [c.value for c in res.candidates]


class TestEntityTypes(SyntheticCase):
    def test_core_entity_types_only(self):
        self.assertEqual(er.ENTITY_TYPES, ("client", "group", "deal_id", "deal_name", "rm"))
        self.assertFalse(hasattr(er, "COMPANY") or hasattr(er, "ATTENDEE"))
        for bad in ("company", "attendee", "planet"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.r.resolve(bad, "Summit Advisors")

    def test_company_and_attendee_names_are_not_resolvable_entities(self):
        for text, ctx in [("Summit Advisors", er.CTX_COMPANY), ("Wang Lee", er.CTX_ATTENDEE), ("Rahul Kaur", er.CTX_ATTENDEE)]:
            with self.subTest(text=text):
                x = self.r.resolve_term(text)
                self.assertEqual((x.status, x.canonical, x.entity_type, x.candidates), (er.NOT_FOUND, None, er.UNTYPED, ()))
                self.assertEqual({d["context"] for d in x.details["diagnostic_contexts"]}, {ctx})

    def test_exact_company_or_attendee_text_in_a_question_is_not_a_mention(self):
        self.assertEqual(self.r.resolve_question("Meetings with Summit Advisors and Rahul Kaur?").mentions, ())


class TestClient(SyntheticCase):
    def test_exact_client_id(self):
        x = self.r.resolve(er.CLIENT, "A12345")
        self.assertEqual((x.status, x.canonical, x.match_rule, x.confidence), (er.RESOLVED, "A12345", er.EXACT_IDENTIFIER, "exact"))
        self.assertEqual(x.details["sources_present"], ["investments", "meetings", "performance"])
        self.assertEqual(x.details["group_id"], 346)

    def test_ambiguous_numeric_client_id(self):
        for text in ("12345", "client 12345", " 12345 "):
            with self.subTest(text=text):
                x = self.r.resolve(er.CLIENT, text)
                self.assertEqual(x.status, er.AMBIGUOUS)
                self.assertEqual(self.values(x), ["A12345", "B12345", "C12345", "D12345"])
                self.assertEqual((x.match_rule, x.canonical), (er.NUMERIC_SUFFIX, None))
        x = self.r.resolve_term("12345")
        self.assertEqual((x.status, x.entity_type), (er.AMBIGUOUS, er.CLIENT))

    def test_exact_client_name(self):
        x = self.r.resolve(er.CLIENT, "Client_A12345")
        self.assertEqual((x.status, x.canonical, x.match_rule), (er.RESOLVED, "A12345", er.EXACT_NAME))

    def test_alternate_known_client_name_form(self):
        x = self.r.resolve(er.CLIENT, "Client A12345 Holdings")
        self.assertEqual((x.status, x.canonical, x.match_rule), (er.RESOLVED, "A12345", er.EXACT_NAME))
        self.assertIn("account_name", x.details["matched_forms"])

    def test_unknown_client(self):
        for text in ("E12345", "Client_Z99999", "A99999", "99999"):
            with self.subTest(text=text):
                x = self.r.resolve(er.CLIENT, text)
                self.assertEqual((x.status, x.canonical, x.candidates), (er.NOT_FOUND, None, ()))

    def test_meeting_only_client_resolves_from_its_exact_id(self):
        x = self.r.resolve(er.CLIENT, "B12463")
        self.assertEqual((x.status, x.canonical), (er.RESOLVED, "B12463"))
        self.assertTrue(x.details["meeting_only"])
        self.assertEqual(x.sources, ("meetings",))
        self.assertIn("no data in the other sources", x.reason)

    def test_meeting_only_client_has_no_name_forms_and_none_are_invented(self):
        self.assertEqual(self.r.resolve(er.CLIENT, "Client_B12463").status, er.NOT_FOUND)
        self.assertEqual(self.r.resolve(er.CLIENT, "Client B12463 Holdings").status, er.NOT_FOUND)

    def test_org_name_is_not_a_known_form(self):
        x = self.r.resolve(er.CLIENT, "Client A12345 Org")
        self.assertEqual(x.status, er.NOT_FOUND)
        self.assertIn("org", x.reason)


class TestNormalization(SyntheticCase):
    def test_case_and_whitespace(self):
        for text, rule in [("  a12345 ", er.NORMALIZED), ("A12345", er.EXACT_IDENTIFIER), ("client_a12345", er.NORMALIZED),
                           ("CLIENT   A12345", er.NORMALIZED), ("client a12345 holdings", er.NORMALIZED), ("\tClient_A12345\n", er.NORMALIZED)]:
            with self.subTest(text=text):
                x = self.r.resolve(er.CLIENT, text)
                self.assertEqual((x.status, x.canonical, x.match_rule), (er.RESOLVED, "A12345", rule))

    def test_deal_name_and_id_normalization(self):
        x = self.r.resolve(er.DEAL_NAME, "  orion   INFRASTRUCTURE  i ")
        self.assertEqual((x.status, x.canonical, x.match_rule, x.confidence), (er.RESOLVED, "Orion Infrastructure I", er.NORMALIZED, "normalized"))
        x = self.r.resolve(er.DEAL_ID, "dl100000.")
        self.assertEqual((x.status, x.canonical, x.match_rule), (er.RESOLVED, "DL100000", er.NORMALIZED))

    def test_rm_normalization(self):
        x = self.r.resolve(er.RM, "  sara   KHAN ")
        self.assertEqual((x.status, x.canonical), (er.RESOLVED, "Sara Khan"))

    def test_resolution_is_deterministic(self):
        self.assertEqual(self.r.resolve_term("Summit"), self.r.resolve_term("Summit"))
        self.assertEqual(self.r.resolve(er.CLIENT, "12345"), self.r.resolve(er.CLIENT, "12345"))


class TestDealId(SyntheticCase):
    def test_deal_id_with_one_name(self):
        x = self.r.resolve(er.DEAL_ID, "DL100000")
        self.assertEqual((x.status, x.entity_type, x.canonical, x.match_rule), (er.RESOLVED, er.DEAL_ID, "DL100000", er.EXACT_IDENTIFIER))
        self.assertEqual(list(x.details["deal_names"]), ["Orion Infrastructure I"])
        self.assertFalse(x.details["maps_to_multiple_names"])

    def test_deal_id_with_multiple_names_is_resolved_not_ambiguous(self):
        for x in (self.r.resolve(er.DEAL_ID, "DL100001"), self.r.resolve_term("DL100001")):
            self.assertEqual((x.status, x.entity_type, x.canonical), (er.RESOLVED, er.DEAL_ID, "DL100001"))
            self.assertEqual((x.candidates, x.all_candidates), ((), ()))          # the names are metadata, not candidates
            self.assertTrue(x.details["maps_to_multiple_names"])
            self.assertEqual(x.details["deal_name_count"], 2)
            self.assertEqual(x.details["deal_names"], {"NorthBridge Growth Fund": 1, "Orion Infrastructure IV": 1})
            self.assertNotIn(x.canonical, x.details["deal_names"])                # never collapsed into one name
            self.assertEqual(x.match_rule, er.EXACT_IDENTIFIER)
            self.assertIn("independent", x.details["deal_names_note"])

    def test_deal_id_in_a_question_and_after_normalization(self):
        (m,) = self.r.resolve_question("How many records carry deal ID DL100001?").mentions
        self.assertEqual((m.text, m.resolution.status, m.resolution.canonical), ("DL100001", er.RESOLVED, "DL100001"))
        x = self.r.resolve(er.DEAL_ID, " dl100001 ")
        self.assertEqual((x.status, x.canonical, x.match_rule), (er.RESOLVED, "DL100001", er.NORMALIZED))

    def test_a_name_and_an_id_stay_independent(self):
        by_name = self.r.resolve(er.DEAL_NAME, "Orion Infrastructure IV")
        self.assertEqual((by_name.status, by_name.canonical, by_name.details["deal_ids"]), (er.RESOLVED, "Orion Infrastructure IV", ["DL100001"]))
        by_name = self.r.resolve(er.DEAL_NAME, "Orion Infrastructure I")
        self.assertEqual(by_name.details["deal_ids"], ["DL100000"])

    def test_unknown_deal_id(self):
        self.assertEqual(self.r.resolve(er.DEAL_ID, "DL999999").status, er.NOT_FOUND)


class TestDealName(SyntheticCase):
    def test_exact_deal_name(self):
        x = self.r.resolve(er.DEAL_NAME, "Emerald Real Estate Fund")
        self.assertEqual((x.status, x.canonical, x.match_rule), (er.RESOLVED, "Emerald Real Estate Fund", er.EXACT_NAME))
        self.assertEqual(x.details["deal_ids"], ["DL100013"])

    def test_prefix_collision_is_never_resolved_through_substring_matching(self):
        for text in ("Orion Infrastructure I", "Orion Infrastructure IV", "BluePeak Venture II", "BluePeak Venture III"):
            with self.subTest(text=text):
                x = self.r.resolve(er.DEAL_NAME, text)
                self.assertEqual((x.status, x.canonical), (er.RESOLVED, text))     # each resolves to itself only
        for text in ("Orion Infrastructure", "BluePeak Venture", "Orion", "Infrastructure"):
            with self.subTest(text=text):
                x = self.r.resolve(er.DEAL_NAME, text)
                self.assertNotEqual(x.status, er.RESOLVED)
                self.assertIsNone(x.canonical)

    def test_partial_name_is_a_diagnostic_and_lists_the_collision(self):
        x = self.r.resolve(er.DEAL_NAME, "Orion Infrastructure")
        self.assertEqual((x.status, x.canonical, x.match_rule, x.confidence), (er.AMBIGUOUS, None, er.PARTIAL_NAME, "none"))
        self.assertEqual(self.values(x), ["Orion Infrastructure I", "Orion Infrastructure IV"])
        self.assertEqual(self.values(self.r.resolve(er.DEAL_NAME, "BluePeak Venture")), ["BluePeak Venture II", "BluePeak Venture III"])


class TestNoPartialOrFuzzyResolution(SyntheticCase):
    def test_typos_are_not_found(self):
        for etype, text in [(er.DEAL_NAME, "Orion Infrastructur"), (er.DEAL_NAME, "Orion Infrastrcture I"), (er.DEAL_NAME, "Orion Infrastructure Fund"),
                            (er.DEAL_NAME, "Orion Infrastructure 1"), (er.DEAL_NAME, "Emerald Reel Estate Fund"), (er.CLIENT, "A1234"),
                            (er.CLIENT, "A123456"), (er.CLIENT, "A12354"), (er.DEAL_ID, "DL10000"), (er.DEAL_ID, "DL1000011"),
                            (er.RM, "Carlos Gomes"), (er.RM, "C Gomez"), (er.RM, "Sara Kahn")]:
            with self.subTest(etype=etype, text=text):
                x = self.r.resolve(etype, text)
                self.assertEqual((x.status, x.canonical), (er.NOT_FOUND, None))

    def test_truncations_are_never_resolved(self):
        for etype, text in [(er.DEAL_NAME, "Orion Infra"), (er.DEAL_NAME, "Emerald"), (er.DEAL_NAME, "Emerald Real"), (er.DEAL_NAME, "Summit Credit"),
                            (er.RM, "Carlos"), (er.RM, "Gomez"), (er.RM, "Sara"), (er.CLIENT, "Client A"), (er.CLIENT, "A123")]:
            with self.subTest(etype=etype, text=text):
                x = self.r.resolve(etype, text)
                self.assertIn(x.status, (er.NOT_FOUND, er.AMBIGUOUS))
                self.assertIsNone(x.canonical)
                if x.status == er.AMBIGUOUS:
                    self.assertEqual(x.match_rule, er.PARTIAL_NAME)

    def test_single_partial_match_is_never_promoted(self):
        for etype, text, only in [(er.DEAL_NAME, "Emerald", "Emerald Real Estate Fund"), (er.RM, "Carlos", "Carlos Gomez")]:
            x = self.r.resolve(etype, text)
            self.assertEqual((x.status, x.canonical, self.values(x)), (er.AMBIGUOUS, None, [only]))

    def test_very_short_terms_produce_no_candidates(self):
        self.assertEqual(self.r.resolve_term("I").status, er.NOT_FOUND)
        self.assertEqual(self.r.resolve_term("Al").status, er.NOT_FOUND)

    def test_no_truncation_or_typo_of_any_known_name_or_id_can_resolve(self):
        """Property test: for every known identifier/name, generate prefixes, suffixes, word truncations,
        deletions and swaps. A result may be resolved only if the input is itself an exact (normalised) known form."""
        known = [(t, e) for t, entries in self.r.index.by_text.items() for e in entries if e.etype]
        variants = set()
        for text, _ in known:
            words = text.split()
            variants |= {text[:k] for k in range(1, len(text))} | {text[k:] for k in range(1, len(text))}
            variants |= {" ".join(words[:k]) for k in range(1, len(words))} | {" ".join(words[k:]) for k in range(1, len(words))}
            variants |= {text[:i] + text[i + 1:] for i in range(len(text))}
            variants |= {text[:i] + text[i + 1] + text[i] + text[i + 2:] for i in range(len(text) - 1)}
        variants = {v for v in variants if v.strip()}
        self.assertGreater(len(variants), 1000)
        checked = 0
        for v in sorted(variants):
            key = er.norm_key(v.strip(er._STRIP_CHARS))
            legit = {(e.etype, e.value) for e in self.r.index.by_key.get(key, []) if e.etype}
            for etype in (er.CLIENT, er.DEAL_ID, er.DEAL_NAME, er.RM):
                x = self.r.resolve(etype, v)
                checked += 1
                if x.status == er.RESOLVED:
                    self.assertIn((etype, x.canonical), legit, f"{etype}: {v!r} resolved to {x.canonical!r} without being an exact known form")
                else:
                    self.assertIsNone(x.canonical)
            if not v.strip().isdigit():
                t = self.r.resolve_term(v)
                checked += 1
                if t.status == er.RESOLVED:
                    self.assertIn((t.entity_type, t.canonical), legit, f"term {v!r} resolved to {t.canonical!r} without being an exact known form")
        self.assertGreater(checked, 4000)

    def test_client_ids_are_never_completed_from_a_prefix(self):
        self.assertEqual(self.r.resolve_term("A1234").status, er.NOT_FOUND)
        self.assertEqual(self.r.resolve_term("A").status, er.NOT_FOUND)


class TestAmbiguousTerms(SyntheticCase):
    def test_summit_lists_competing_contexts_without_new_entity_types(self):
        x = self.r.resolve_term("Summit")
        self.assertEqual((x.status, x.entity_type, x.canonical, x.match_rule), (er.AMBIGUOUS, er.UNTYPED, None, er.PARTIAL_NAME))
        self.assertEqual([(c.entity_type, c.context, c.value) for c in x.candidates],
                         [(er.DEAL_NAME, "deal_name", "Summit Credit Opportunities"), (None, er.CTX_COMPANY, "Summit Advisors")])
        self.assertEqual(x.details["candidate_contexts"], {"deal_name": 1, er.CTX_COMPANY: 1})

    def test_rahul_lists_the_rm_first_then_attendee_contexts(self):
        x = self.r.resolve_term("Rahul")
        self.assertEqual((x.status, x.entity_type, x.canonical), (er.AMBIGUOUS, er.UNTYPED, None))
        self.assertEqual((x.candidates[0].entity_type, x.candidates[0].value), (er.RM, "Rahul Mehta"))
        self.assertTrue(all(c.entity_type is None and c.context == er.CTX_ATTENDEE for c in x.candidates[1:]))
        self.assertEqual(x.details["candidate_contexts"], {"account_rm": 1, er.CTX_ATTENDEE: 3})

    def test_candidate_cap_is_explicit_and_deterministic(self):
        self.assertEqual(er.MAX_PUBLIC_CANDIDATES, 10)
        x = self.r.resolve_term("Quinn")
        self.assertEqual(x.status, er.AMBIGUOUS)
        self.assertEqual(len(x.candidates), 10)
        self.assertEqual(len(x.all_candidates), 14)
        self.assertEqual((x.details["candidate_count"], x.details["candidates_truncated"], x.details["max_public_candidates"]), (14, True, 10))
        self.assertEqual(self.values(x), sorted(f"Quinn {s}" for s in ["Ash", "Bay", "Cole", "Dunn", "East", "Fox", "Gray", "Hall", "Ivy", "Jay", "Kerr", "Lane", "Moss", "Nash"])[:10])
        self.assertEqual(x.candidates, x.all_candidates[:10])
        self.assertEqual(self.r.resolve_term("Quinn"), x)
        public = x.to_dict()
        self.assertEqual(len(public["candidates"]), 10)
        self.assertNotIn("all_candidates", public)

    def test_small_ambiguity_is_not_truncated(self):
        x = self.r.resolve(er.CLIENT, "12345")
        self.assertEqual((len(x.candidates), x.details["candidates_truncated"]), (4, False))

    def test_same_rm_name_also_used_by_attendees_resolves_as_rm_with_context_noted(self):
        x = self.r.resolve(er.RM, "Rahul Mehta")
        self.assertEqual((x.status, x.entity_type, x.canonical), (er.RESOLVED, er.RM, "Rahul Mehta"))
        self.assertEqual([(d["context"], d["value"]) for d in x.details["also_known_in_context"]], [(er.CTX_ATTENDEE, "Rahul Mehta")])
        y = self.r.resolve_term("Rahul Mehta")
        self.assertEqual((y.status, y.entity_type, y.canonical), (er.RESOLVED, er.RM, "Rahul Mehta"))


class TestRm(SyntheticCase):
    def test_exact_rm(self):
        x = self.r.resolve(er.RM, "Priya Sharma")
        self.assertEqual((x.status, x.canonical, x.sources), (er.RESOLVED, "Priya Sharma", ("investments",)))
        self.assertEqual(x.details["records"], 2)
        self.assertIn("record-level", x.details["note"])

    def test_unknown_rm(self):
        x = self.r.resolve(er.RM, "Nobody Real")
        self.assertEqual((x.status, x.canonical), (er.NOT_FOUND, None))

    def test_rm_is_not_resolved_from_noisy_fields(self):
        for text in ("cgomez@investcorp.com", "cgomez"):
            with self.subTest(text=text):
                self.assertEqual(self.r.resolve(er.RM, text).status, er.NOT_FOUND)
        x = self.r.resolve(er.RM, "cgomez@investcorp.com")
        self.assertEqual(x.match_rule, er.NON_AUTHORITATIVE)
        self.assertIn("non-authoritative", x.reason)

    def test_accented_attendee_name_is_not_repaired_or_guessed(self):
        clean = self.r.resolve_term("Sanjay López")
        self.assertEqual(clean.status, er.NOT_FOUND)
        self.assertIn("mojibake", clean.reason)
        raw = self.r.resolve_term("Sanjay LÃ³pez")
        self.assertEqual((raw.status, raw.canonical), (er.NOT_FOUND, None))   # attendee names are not entities


class TestGroups(SyntheticCase):
    def test_group_resolves_by_id_in_several_forms(self):
        for text in ("346", "group 346", "Group 346", "group_id 346", "Client_Group_Id 346"):
            with self.subTest(text=text):
                x = self.r.resolve(er.GROUP, text)
                self.assertEqual((x.status, x.canonical), (er.RESOLVED, "346"))

    def test_group_membership_is_reported_per_source_not_chosen(self):
        x = self.r.resolve(er.GROUP, "group 350")
        self.assertEqual(x.status, er.RESOLVED)
        members = x.details["members_by_source"]
        self.assertEqual(members["investments"], ["A12350"])
        self.assertEqual(members["meetings"], ["A12350", "B12350", "C12350", "D12350"])
        self.assertTrue(x.details["membership_differs_across_sources"])
        self.assertIn("not decided", x.details["membership_note"])
        self.assertFalse(self.r.resolve(er.GROUP, "346").details["membership_differs_across_sources"])

    def test_meeting_only_group_and_unknown_group(self):
        self.assertEqual(self.r.resolve(er.GROUP, "464").status, er.RESOLVED)
        self.assertEqual(self.r.resolve(er.GROUP, "999").status, er.NOT_FOUND)
        self.assertEqual(self.r.resolve(er.GROUP, "not a group").status, er.NOT_FOUND)


class TestQuestions(SyntheticCase):
    def texts(self, question):
        return [(m.text, m.resolution.entity_type, m.resolution.status, m.resolution.canonical) for m in self.r.resolve_question(question).mentions]

    def test_client_id_with_possessive_and_client_word(self):
        self.assertEqual(self.texts("What is client A12345's latest MOIC?"), [("A12345", er.CLIENT, er.RESOLVED, "A12345")])

    def test_numeric_and_name_form(self):
        self.assertEqual(self.texts("Show me the investments for client 12345.")[0][2], er.AMBIGUOUS)
        self.assertEqual(self.texts("How many records does Client A12345 Holdings have?"), [("Client A12345 Holdings", er.CLIENT, er.RESOLVED, "A12345")])

    def test_deals_in_questions(self):
        self.assertEqual(self.texts("What is the total for the Orion Infrastructure I deal?"), [("Orion Infrastructure I", er.DEAL_NAME, er.RESOLVED, "Orion Infrastructure I")])
        self.assertEqual(self.texts("Records with deal ID DL100001?"), [("DL100001", er.DEAL_ID, er.RESOLVED, "DL100001")])

    def test_unknown_identifier_shapes_are_reported_not_guessed(self):
        self.assertEqual(self.texts("Show client E12345.")[0][2:], (er.NOT_FOUND, None))
        self.assertEqual(self.texts("Deal DL999999?")[0][:3], ("DL999999", er.DEAL_ID, er.NOT_FOUND))

    def test_rm_full_name_resolves_and_notes_the_attendee_context(self):
        (m,) = self.r.resolve_question("How many meetings were held by relationship manager Priya Sharma?").mentions
        self.assertEqual((m.resolution.entity_type, m.resolution.status, m.resolution.canonical), (er.RM, er.RESOLVED, "Priya Sharma"))
        self.assertIn("also_known_in_context", m.resolution.details)
        (m,) = self.r.resolve_question("Which meetings did Rahul Mehta attend?").mentions
        self.assertEqual((m.resolution.entity_type, m.resolution.canonical), (er.RM, "Rahul Mehta"))

    def test_first_name_in_a_question_is_a_diagnostic_ambiguity(self):
        (m,) = self.r.resolve_question("Which meetings did Rahul attend?").mentions
        x = m.resolution
        self.assertEqual((m.text, x.status, x.entity_type, x.match_rule), ("Rahul", er.AMBIGUOUS, er.UNTYPED, er.PARTIAL_NAME))
        self.assertEqual(set(x.details["candidate_contexts"]), {"account_rm", er.CTX_ATTENDEE})

    def test_ambiguous_term_in_question(self):
        m = self.r.resolve_question("Tell me about Summit.").mentions
        self.assertEqual((len(m), m[0].text, m[0].resolution.status), (1, "Summit", er.AMBIGUOUS))

    def test_lowercase_common_words_are_not_treated_as_names(self):
        self.assertEqual(self.texts("How many investments and meetings, and what growth, capital or fund?"), [])
        self.assertEqual(self.texts("What is the total USD amount for GBP records?"), [])

    def test_email_is_flagged_as_non_authoritative(self):
        self.assertEqual(self.texts("Who is cgomez@investcorp.com?"), [("cgomez@investcorp.com", er.RM, er.NOT_FOUND, None)])

    def test_group_in_question(self):
        self.assertEqual(self.texts("What is the Total AUM for group 346?"), [("group 346", er.GROUP, er.RESOLVED, "346")])

    def test_five_digit_numbers_that_match_no_client_are_ignored(self):
        self.assertEqual(self.texts("Which clients have more than 50000 in amount?"), [])

    def test_needs_clarification_flag(self):
        self.assertTrue(self.r.resolve_question("Show 12345").needs_clarification)
        self.assertFalse(self.r.resolve_question("Show A12345").needs_clarification)
        self.assertFalse(self.r.resolve_question("Records with deal ID DL100001").needs_clarification)

    def test_multiple_mentions_are_reported_in_order(self):
        res = self.r.resolve_question("Does client A12345 have Emerald Real Estate Fund and B12463 records?")
        self.assertEqual([m.text for m in res.mentions], ["A12345", "Emerald Real Estate Fund", "B12463"])

    def test_truncated_name_in_a_question_is_never_resolved(self):
        res = self.r.resolve_question("What about Orion Infrastructure?")
        self.assertEqual([m.resolution.status for m in res.mentions], [er.AMBIGUOUS])
        self.assertEqual(res.mentions[0].resolution.canonical, None)


class TestLobPhrasesAreNotEntities(SyntheticCase):
    """Regression tests for the LOB-phrase-as-entity bug: "Private Equity", "Hedge Fund", "Real
    Estate", "Credit Opportunity" and "Infrastructure" are canonical LOB concepts (A15), not deal
    names, even though some of them are word-for-word substrings of this fixture's fictional deal
    names ("Emerald Real Estate Fund", "Orion Infrastructure I/IV", "Summit Credit Opportunities").
    A question naming one of them must not stop at entity resolution."""

    def test_lob_phrases_produce_no_mention_even_though_they_partially_match_a_deal_name(self):
        for phrase in ("Real Estate", "Infrastructure", "Credit Opportunity"):
            with self.subTest(phrase=phrase):
                res = self.r.resolve_question(f"How is A12345 performing in {phrase}?")
                self.assertEqual([m.text for m in res.mentions], ["A12345"])   # no second mention for the LOB phrase

    def test_lob_phrase_alone_is_still_not_an_entity(self):
        for phrase in ("Private Equity", "Hedge Fund", "Real Estate", "Credit Opportunity", "Infrastructure"):
            with self.subTest(phrase=phrase):
                res = self.r.resolve_question(f"What is the latest {phrase} multiple?")
                self.assertEqual(res.mentions, ())

    def test_real_deal_name_still_resolves_even_though_it_contains_a_lob_phrase(self):
        # "Emerald Real Estate Fund" contains the LOB phrase "Real Estate", but the full deal
        # name is matched first (exact whole-tuple match), before the LOB-phrase check ever runs.
        res = self.r.resolve_question("Tell me about Emerald Real Estate Fund")
        self.assertEqual([(m.text, m.resolution.entity_type, m.resolution.status, m.resolution.canonical) for m in res.mentions],
                         [("Emerald Real Estate Fund", er.DEAL_NAME, er.RESOLVED, "Emerald Real Estate Fund")])
        res = self.r.resolve_question("What deals exist under Summit Credit Opportunities?")
        self.assertEqual([(m.resolution.entity_type, m.resolution.status, m.resolution.canonical) for m in res.mentions],
                         [(er.DEAL_NAME, er.RESOLVED, "Summit Credit Opportunities")])

    def test_ambiguous_real_deal_name_remains_ambiguous_and_is_not_suppressed_by_the_lob_check(self):
        # "Orion Infrastructure" is a genuine partial match of two real deal names and is not a
        # canonical LOB phrase itself (the LOB phrase is "Infrastructure" alone); it must still
        # surface as the existing ambiguous-diagnostic mention, unaffected by this fix.
        res = self.r.resolve_question("What about Orion Infrastructure?")
        self.assertEqual([(m.resolution.status, m.resolution.match_rule) for m in res.mentions], [(er.AMBIGUOUS, er.PARTIAL_NAME)])

    def test_existing_entity_resolution_behavior_is_otherwise_unchanged(self):
        # A spot check that ordinary exact/normalized/partial resolution (none of it LOB-phrase
        # related) is untouched by this fix.
        self.assertEqual(self.r.resolve(er.DEAL_NAME, "Orion Infrastructure I").status, er.RESOLVED)
        self.assertEqual(self.r.resolve(er.CLIENT, "A12345").status, er.RESOLVED)
        self.assertEqual(self.r.resolve(er.DEAL_NAME, "BluePeak Venture").status, er.AMBIGUOUS)


class TestApi(SyntheticCase):
    def test_result_fields(self):
        d = self.r.resolve(er.CLIENT, "A12345").to_dict()
        for key in ("entity_type", "input_text", "resolution_status", "canonical", "candidates", "match_rule", "confidence", "reason", "rationale", "sources", "details"):
            self.assertIn(key, d)
        json.dumps(d)
        json.dumps(self.r.resolve_term("Summit").to_dict())

    def test_missing_database_is_an_explicit_error(self):
        with self.assertRaises(FileNotFoundError):
            er.EntityResolver.from_database(self.tmp / "missing.sqlite")


@unittest.skipUnless(DEFAULT_DB.is_file(), "Baseline database not built")
class TestRealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = er.EntityResolver.from_database()

    def test_required_manual_cases(self):
        def status(text):
            x = self.r.resolve_term(text)
            return x.status, x.entity_type, x.canonical
        self.assertEqual(status("12345")[:2], (er.AMBIGUOUS, er.CLIENT))
        self.assertEqual(status("Client A12345 Holdings"), (er.RESOLVED, er.CLIENT, "A12345"))
        self.assertEqual(status("Orion Infrastructure I"), (er.RESOLVED, er.DEAL_NAME, "Orion Infrastructure I"))
        self.assertEqual(status("DL100001"), (er.RESOLVED, er.DEAL_ID, "DL100001"))
        self.assertEqual(status("Summit")[:2], (er.AMBIGUOUS, er.UNTYPED))
        self.assertEqual(status("B12463"), (er.RESOLVED, er.CLIENT, "B12463"))
        self.assertEqual(status("E12345")[0], er.NOT_FOUND)
        self.assertEqual(status("Rahul")[:2], (er.AMBIGUOUS, er.UNTYPED))

    def test_real_data_facts(self):
        self.assertEqual([c.value for c in self.r.resolve(er.CLIENT, "12345").candidates], ["A12345", "B12345", "C12345", "D12345"])
        x = self.r.resolve(er.DEAL_ID, "DL100001")
        self.assertEqual((x.status, x.canonical), (er.RESOLVED, "DL100001"))
        self.assertEqual(x.details["deal_names"], {"NorthBridge Growth Fund": 8356, "Orion Infrastructure IV": 594})
        self.assertEqual([c.value for c in self.r.resolve(er.DEAL_NAME, "Orion Infrastructure").candidates], ["Orion Infrastructure I", "Orion Infrastructure IV"])
        self.assertTrue(self.r.resolve(er.CLIENT, "B12463").details["meeting_only"])
        self.assertEqual(self.r.resolve(er.RM, "Priya Sharma").details["records"], 9_882)
        self.assertEqual(self.r.resolve(er.GROUP, "group 346").status, er.RESOLVED)

    def test_rahul_is_capped_at_ten_public_candidates(self):
        x = self.r.resolve_term("Rahul")
        self.assertEqual(len(x.candidates), 10)
        self.assertEqual((x.details["candidate_count"], x.details["candidates_truncated"]), (50, True))
        self.assertEqual(len(x.all_candidates), 50)
        self.assertEqual(x.details["candidate_contexts"], {"account_rm": 1, er.CTX_ATTENDEE: 49})
        self.assertEqual((x.candidates[0].entity_type, x.candidates[0].value), (er.RM, "Rahul Mehta"))

    def test_visible_question_entity_cases(self):
        by_id = {q["id"]: q["question"] for q in json.loads(VISIBLE.read_text(encoding="utf-8"))["questions"]}
        self.assertEqual(len(by_id), 40)

        def mentions(qid):
            return self.r.resolve_question(by_id[qid]).mentions
        self.assertEqual([(m.resolution.status, m.resolution.canonical) for m in mentions("INV-03")], [(er.RESOLVED, "A12345")])
        self.assertEqual([(m.resolution.status, m.resolution.canonical) for m in mentions("INV-07")], [(er.RESOLVED, "Orion Infrastructure I")])
        (m,) = mentions("INV-08")
        self.assertEqual((m.resolution.entity_type, m.resolution.status, m.resolution.canonical), (er.DEAL_ID, er.RESOLVED, "DL100001"))
        self.assertEqual(len(m.resolution.details["deal_names"]), 2)
        self.assertEqual([(m.resolution.entity_type, m.resolution.canonical) for m in mentions("PERF-10")], [(er.GROUP, "346")])
        for qid in ("MEET-01", "HYB-05"):
            (m,) = mentions(qid)
            self.assertEqual((m.resolution.status, m.resolution.canonical, m.resolution.details["meeting_only"]), (er.RESOLVED, "B12463", True))
        self.assertEqual([(m.resolution.entity_type, m.resolution.canonical) for m in mentions("HYB-07")], [(er.RM, "Priya Sharma")])
        (m,) = mentions("ADV-01")
        self.assertEqual((m.resolution.status, [c.value for c in m.resolution.candidates]), (er.AMBIGUOUS, ["A12345", "B12345", "C12345", "D12345"]))
        self.assertEqual([(m.resolution.status, m.resolution.canonical) for m in mentions("ADV-02")], [(er.RESOLVED, "A12345")])
        (m,) = mentions("ADV-03")
        self.assertEqual((m.resolution.status, m.resolution.entity_type), (er.AMBIGUOUS, er.UNTYPED))
        self.assertEqual(set(m.resolution.details["candidate_contexts"]), {"deal_name", er.CTX_COMPANY})
        (m,) = mentions("ADV-07")
        self.assertEqual((m.resolution.entity_type, m.resolution.status, m.resolution.match_rule), (er.RM, er.NOT_FOUND, er.NON_AUTHORITATIVE))
        for qid in ("INV-06", "INV-10", "PERF-02", "PERF-04", "PERF-08", "MEET-05", "HYB-09", "ADV-05", "ADV-06", "ADV-08"):
            self.assertEqual(mentions(qid), (), qid)   # no entity mention to resolve; no false positives


if __name__ == "__main__":
    unittest.main()
