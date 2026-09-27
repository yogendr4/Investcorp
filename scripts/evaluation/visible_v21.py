"""Reproducible runner and deterministic scorer for the 40 visible questions.

This intentionally does not claim to reproduce the unavailable historical
runner.  It is a small, documented procedure for future visible-set runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
VISIBLE_QUESTIONS = ROOT / "tests" / "evaluation" / "questions.json"
PROCEDURE = "visible-v21-reproducible-v1"

# These are the four historical terminal outcomes whose status and failure code
# are explicit in the recorded V2.1 result artifact.  They are deliberately
# kept as a small manifest rather than inferred from answer prose.
TERMINAL_EXPECTATIONS = {
    "PERF-05": ("unsupported", "metric_not_available"),
    "HYB-07": ("unsupported", "rm_meeting_link"),
    "ADV-01": ("clarification", "ambiguous_entity"),
    "ADV-03": ("clarification", "ambiguous_entity"),
}

# The recorded evaluation deliberately left this cited-ID case for review.
REVIEW_ONLY_IDS = {"MEET-10"}
MEETING_CITATION = re.compile(r"\[meeting:\s*(\d+)(?:\s*,[^\]]*)?\]", re.I)


def load_visible_questions(path: Path = VISIBLE_QUESTIONS) -> list[dict[str, Any]]:
    """Load exactly the visible questions without opening the holdout file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload["questions"]
    if payload["benchmark"].get("visible_total") != 40 or len(questions) != 40:
        raise ValueError("expected exactly 40 questions in the visible question file")
    if any(question.get("split") != "visible" for question in questions):
        raise ValueError("visible question file contains a non-visible question")
    return questions


def _json_safe(value: Any) -> Any:
    """Convert service output to JSON while retaining every available field."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _leaves(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from _leaves(child)
    else:
        yield value


def _evidence(response: dict[str, Any]) -> dict[str, Any]:
    """Only evidence payloads participate in exact-value matching."""
    return {
        key: response.get(key)
        for key in ("structured_evidence", "meeting_aggregate_evidence", "meeting_evidence")
    }


def _matches(expected: Any, actual: Any, tolerance: float = 0.0) -> bool:
    if isinstance(expected, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and not isinstance(actual, bool):
        try:
            return abs(float(expected) - float(actual)) <= tolerance
        except (TypeError, ValueError):
            return False
    return expected == actual


def _contains_value(expected: Any, evidence: dict[str, Any], tolerance: float = 0.0) -> bool:
    return any(_matches(expected, actual, tolerance) for actual in _leaves(evidence))


def _meeting_hit_ids(response: dict[str, Any]) -> set[int]:
    hits = (response.get("meeting_evidence") or {}).get("hits") or []
    return {int(hit["meeting_id"]) for hit in hits if hit.get("meeting_id") is not None}


def _cited_meeting_ids(answer: Any) -> set[int]:
    return {int(match.group(1)) for match in MEETING_CITATION.finditer(answer or "")}


def _answer_mentions_number(answer: Any, value: Any) -> bool:
    if answer is None:
        return False
    rendered = re.escape(str(value))
    return re.search(rf"(?<![\d.]){rendered}(?![\d.])", str(answer)) is not None


def score_question(question: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Score one response using only explicit visible-set metadata.

    Returns a result category plus diagnostics.  ``needs_review`` deliberately
    remains non-judgmental for behavior rules without an explicit terminal
    outcome, rather than implementing an unvalidated prose judge.
    """
    question_id = question["id"]
    status = response.get("status")
    failure_code = (response.get("failure") or {}).get("code")
    expected = question.get("expected_answer")

    if question_id in TERMINAL_EXPECTATIONS:
        wanted_status, wanted_code = TERMINAL_EXPECTATIONS[question_id]
        passed = status == wanted_status and failure_code == wanted_code
        return {
            "result": "correct" if passed else "incorrect",
            "method": "explicit terminal status and failure code",
            "status_matches": status == wanted_status,
            "failure_code_matches": failure_code == wanted_code,
        }

    if not expected:
        return {
            "result": "needs_review",
            "method": "behavior rule retained for human review",
            "status": status,
        }

    expected_type = expected["type"]
    if expected_type == "id_set":
        expected_ids = {int(value) for value in expected["value"]}
        hit_ids = _meeting_hit_ids(response)
        cited_ids = _cited_meeting_ids(response.get("answer"))
        evidence_complete = expected_ids.issubset(hit_ids)
        if question_id in REVIEW_ONLY_IDS:
            return {
                "result": "needs_review",
                "method": "historical cited-ID ambiguity retained for review",
                "evidence_complete": evidence_complete,
                "expected_ids": sorted(expected_ids),
                "hit_ids": sorted(hit_ids),
                "cited_ids": sorted(cited_ids),
            }
        answer_exact = cited_ids == expected_ids
        passed = status == "ok" and evidence_complete and answer_exact
        return {
            "result": "correct" if passed else "incorrect",
            "method": "meeting evidence recall plus cited-ID equality",
            "status_matches": status == "ok",
            "evidence_complete": evidence_complete,
            "answer_ids_match": answer_exact,
            "expected_ids": sorted(expected_ids),
            "hit_ids": sorted(hit_ids),
            "cited_ids": sorted(cited_ids),
        }

    if expected_type not in {"integer", "number", "object"}:
        raise ValueError(f"unsupported expected-answer type for {question_id}: {expected_type!r}")

    values = expected["value"].values() if expected_type == "object" else [expected["value"]]
    tolerance = float(expected.get("tolerance_abs", 0.0))
    evidence = _evidence(response)
    expected_values_present = all(_contains_value(value, evidence, tolerance) for value in values)
    wrong_values_present = any(
        _contains_value(item["value"], evidence, tolerance)
        for item in expected.get("wrong_values", [])
    )
    answer_count_present = True
    if question_id == "HYB-02":
        answer_count_present = _answer_mentions_number(response.get("answer"), expected["value"]["meetings"])
    passed = status == "ok" and expected_values_present and not wrong_values_present and answer_count_present
    return {
        "result": "correct" if passed else "incorrect",
        "method": "expected values present in service evidence",
        "status_matches": status == "ok",
        "expected_values_present": expected_values_present,
        "wrong_values_present": wrong_values_present,
        "answer_meeting_count_present": answer_count_present if question_id == "HYB-02" else None,
    }


def run_visible_benchmark(service: Any, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute one V21Service pass and retain the full raw response for every question."""
    results = []
    for question in questions:
        raw_response = _json_safe(service.answer_question(question["question"]).to_dict())
        scoring = score_question(question, raw_response)
        results.append({
            "id": question["id"],
            "category": question["category"],
            "difficulty": question["difficulty"],
            "question": question["question"],
            "status": raw_response.get("status"),
            "latency_ms": raw_response.get("latency_ms"),
            "raw_response": raw_response,
            "scoring": scoring,
            "result": scoring["result"],
        })
    return {
        "procedure": PROCEDURE,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "setup": {"questions": len(questions), "split": "visible", "service": "src.v2.v21.V21Service"},
        "results": results,
    }


def markdown_summary(artifact: dict[str, Any]) -> str:
    counts = Counter(result["result"] for result in artifact["results"])
    statuses = Counter(result.get("status") for result in artifact["results"])
    latencies = [result["latency_ms"] for result in artifact["results"] if isinstance(result.get("latency_ms"), (int, float))]
    lines = [
        "# Reproducible visible-set V2.1 evaluation",
        "",
        f"Procedure: `{artifact['procedure']}`. This is a new reproducible procedure, not a re-run of the unavailable historical runner.",
        "",
        f"Questions: {len(artifact['results'])} visible only.",
        "",
        "| Result | Count |",
        "| --- | ---: |",
        f"| Correct | {counts['correct']} |",
        f"| Incorrect | {counts['incorrect']} |",
        f"| Needs review | {counts['needs_review']} |",
        "",
        "| Service status | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {status or 'missing'} | {count} |" for status, count in sorted(statuses.items(), key=lambda item: str(item[0])))
    if latencies:
        lines.extend(["", f"Mean reported latency: {sum(latencies) / len(latencies):.1f} ms."])
    lines.extend(["", "| ID | Result | Status | Latency (ms) |", "| --- | --- | --- | ---: |"])
    for result in artifact["results"]:
        latency = result.get("latency_ms")
        rendered_latency = "" if latency is None else f"{float(latency):.1f}"
        lines.append(f"| {result['id']} | {result['result']} | {result.get('status') or ''} | {rendered_latency} |")
    return "\n".join(lines) + "\n"


def persist(artifact: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Persist a full canonical JSON artifact and its concise Markdown summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"visible_v21_{stamp}.json"
    markdown_path = output_dir / f"visible_v21_{stamp}.md"
    json_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_summary(artifact), encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the reproducible 40-question visible V2.1 evaluation.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "evaluation" / "runs")
    args = parser.parse_args()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.v2.v21 import V21Service  # Imported only when the benchmark is explicitly run.

    artifact = run_visible_benchmark(V21Service(), load_visible_questions())
    json_path, markdown_path = persist(artifact, args.output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
