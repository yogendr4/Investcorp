"""Focused tests for the standalone visible-set evaluation harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "scripts" / "evaluation" / "visible_v21.py"
SPEC = importlib.util.spec_from_file_location("visible_v21", MODULE)
visible_v21 = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(visible_v21)


def response(**overrides):
    base = {
        "status": "ok",
        "answer": None,
        "failure": None,
        "structured_evidence": None,
        "meeting_aggregate_evidence": None,
        "meeting_evidence": None,
    }
    base.update(overrides)
    return base


def test_loader_accepts_the_40_visible_questions():
    questions = visible_v21.load_visible_questions()
    assert len(questions) == 40
    assert {question["split"] for question in questions} == {"visible"}


def test_exact_value_uses_evidence_and_status():
    question = {"id": "INV-X", "expected_answer": {"type": "number", "value": 12.5, "tolerance_abs": 0.1}}
    good = response(structured_evidence={"rows": [[12.55]]})
    bad_status = response(status="error", structured_evidence={"rows": [[12.55]]})
    assert visible_v21.score_question(question, good)["result"] == "correct"
    assert visible_v21.score_question(question, bad_status)["result"] == "incorrect"


def test_id_set_requires_retrieval_and_exact_citations():
    question = {"id": "MEET-X", "expected_answer": {"type": "id_set", "value": [10, 20]}}
    good = response(answer="[meeting: 10, 2024-01-01] [meeting: 20, 2024-01-02]",
                    meeting_evidence={"hits": [{"meeting_id": 10}, {"meeting_id": 20}]})
    extra_citation = response(answer="[meeting: 10] [meeting: 20] [meeting: 30]",
                              meeting_evidence={"hits": [{"meeting_id": 10}, {"meeting_id": 20}]})
    assert visible_v21.score_question(question, good)["result"] == "correct"
    assert visible_v21.score_question(question, extra_citation)["result"] == "incorrect"


def test_behavior_and_meet_10_remain_review_without_a_prose_judge():
    behavior = {"id": "ADV-X", "validation_rule": {"type": "behavior"}}
    meet_10 = {"id": "MEET-10", "expected_answer": {"type": "id_set", "value": [5506]}}
    reviewed = response(answer="[meeting: 5506]", meeting_evidence={"hits": [{"meeting_id": 5506}]})
    assert visible_v21.score_question(behavior, response())["result"] == "needs_review"
    assert visible_v21.score_question(meet_10, reviewed)["result"] == "needs_review"


def test_explicit_terminal_expectation_and_full_artifact_retention():
    question = {"id": "PERF-05", "validation_rule": {"type": "behavior"}}
    terminal = response(status="unsupported", failure={"code": "metric_not_available"})
    assert visible_v21.score_question(question, terminal)["result"] == "correct"

    class FakeService:
        def answer_question(self, text):
            class FakeResponse:
                def to_dict(self):
                    return {"status": "ok", "latency_ms": 1.0, "structured_evidence": {"rows": [[7]], "full_field": "kept"}}
            return FakeResponse()

    artifact = visible_v21.run_visible_benchmark(FakeService(), [{
        "id": "INV-X", "category": "investment", "difficulty": "easy", "question": "q",
        "expected_answer": {"type": "integer", "value": 7},
    }])
    assert artifact["results"][0]["raw_response"]["structured_evidence"]["full_field"] == "kept"
    assert "| Correct | 1 |" in visible_v21.markdown_summary(artifact)


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"{len(tests)} focused evaluator tests passed")


if __name__ == "__main__":
    main()
