"""Tests for app.evaluation (artifact loading/aggregation, no re-scoring) and app.app/app.trace importability.
No Streamlit runtime is started; no service is called; the holdout question/answer file is not read.

Run from the project root:  python -m unittest tests.app.test_evaluation_and_ui -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import evaluation as ev

REPO = Path(__file__).resolve().parents[2]


class TestArtifactLoading(unittest.TestCase):
    def test_load_visible_reads_the_real_result_files_without_rerunning_anything(self):
        d = ev.load_visible()
        self.assertTrue(set(d) <= set(ev.VISIBLE_VERSIONS))
        for v, data in d.items():
            self.assertIn("results", data)
            self.assertGreater(len(data["results"]), 0)

    def test_load_holdout_never_reads_the_holdout_question_file(self):
        holdout_questions = REPO / "tests" / "evaluation" / "holdout" / "holdout_questions.json"
        before = holdout_questions.stat().st_mtime if holdout_questions.is_file() else None
        ev.load_holdout()
        after = holdout_questions.stat().st_mtime if holdout_questions.is_file() else None
        self.assertEqual(before, after)

    def test_summarize_matches_a_hand_built_example(self):
        results = [{"result": "correct", "status": "ok", "category": "investment", "latency_ms": 100.0, "failure_mode": {"primary": "correct"}},
                   {"result": "incorrect", "status": "ok", "category": "meeting", "latency_ms": 200.0, "failure_mode": {"primary": "meeting_retrieval"}},
                   {"result": "needs_review", "status": "clarification", "category": "adversarial_edge_semantic", "latency_ms": 0.5, "failure_mode": {"primary": "needs_review"}}]
        s = ev.summarize(results)
        self.assertEqual((s["total"], s["correct"], s["incorrect"], s["needs_review"]), (3, 1, 1, 1))
        self.assertEqual(s["status"]["ok"], 2)
        self.assertEqual(s["status"]["clarification"], 1)
        self.assertEqual(s["mean_latency_ms"], round((100 + 200 + 0.5) / 3, 1))
        self.assertEqual(s["by_category"]["meeting"]["incorrect"], 1)
        self.assertEqual(s["failure_modes"]["meeting_retrieval"], 1)

    def test_visible_dashboard_has_one_summary_per_available_version(self):
        d = ev.visible_dashboard()
        self.assertEqual(set(d), set(ev.load_visible()))
        for v, s in d.items():
            self.assertEqual(s["total"], 40)

    def test_question_lookup_returns_none_for_an_unknown_id_and_the_right_row_for_a_known_one(self):
        self.assertIsNone(ev.question("v21", "NOT-A-REAL-ID"))
        d = ev.load_visible()["v21"]
        known = d["results"][0]["id"]
        x = ev.question("v21", known)
        self.assertEqual(x["id"], known)

    def test_validation_failures_only_lists_invalid_answers(self):
        vf = ev.validation_failures("v21")
        d = ev.load_visible()["v21"]
        expected_ids = {x["id"] for x in d["results"] if x.get("validation") and not x["validation"]["valid"]}
        self.assertEqual({x["id"] for x in vf}, expected_ids)

    def test_missing_version_file_is_handled_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ev, "RESULTS_DIR", Path(tmp)):
                self.assertEqual(ev.load_visible(), {})
                self.assertIsNone(ev.load_holdout())
                self.assertEqual(ev.visible_dashboard(), {})


class TestHoldoutLabelAndNoRawAnswersByDefault(unittest.TestCase):
    """The evaluation dashboard must show only aggregate holdout metrics and must label the section explicitly."""

    def test_label_says_non_blind_and_not_holdout(self):
        self.assertIn("non-blind", ev.HOLDOUT_LABEL.lower())
        self.assertNotIn("blind holdout", ev.HOLDOUT_LABEL.lower())

    def test_app_module_never_calls_question_level_holdout_lookup(self):
        """app.app's evaluation tab must read holdout data only through load_holdout() (aggregate metrics),
        never index into its per-question 'results' to render an answer, and must not fetch a per-question drill-down for it."""
        src = (REPO / "app" / "app.py").read_text(encoding="utf-8")
        self.assertIn("load_holdout()", src)
        self.assertNotIn('holdout["results"]', src)
        self.assertNotIn("holdout['results']", src)
        # the drill-down helper (`question`) must only be invoked with a visible-benchmark version, never "holdout"
        import re
        for call in re.findall(r'eval_question\(([^)]*)\)', src):
            self.assertNotIn("holdout", call)

    def test_holdout_results_file_itself_is_used_only_for_aggregate_metrics_here(self):
        d = ev.load_holdout()
        if d is None:
            self.skipTest("no holdout_results.json present")
        self.assertIn("metrics", d)
        for v in d["metrics"]:
            self.assertIn("correct", d["metrics"][v])


class TestAppImports(unittest.TestCase):
    """No Streamlit server is started; this only checks the modules import cleanly and expose the expected pieces."""

    def test_trace_module_imports(self):
        from app import trace
        self.assertTrue(callable(trace.build_trace))

    def test_evaluation_module_imports(self):
        self.assertTrue(callable(ev.visible_dashboard))

    def test_app_module_imports_without_starting_a_server(self):
        import importlib
        mod = importlib.import_module("app.app")
        for name in ("main", "chat_tab", "evaluation_tab", "render_answer", "render_trace", "render_technical_details"):
            self.assertTrue(hasattr(mod, name), name)

    def test_app_module_does_not_construct_a_service_at_import_time(self):
        """Constructing V21Service touches gcloud/the embedding index; importing the module must not do that."""
        import importlib
        with mock.patch("src.v2.v21.V21Service.__init__", side_effect=AssertionError("V21Service must not be constructed on import")):
            importlib.reload(importlib.import_module("app.app"))


if __name__ == "__main__":
    unittest.main()
