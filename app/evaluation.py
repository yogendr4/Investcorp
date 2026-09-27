"""Loads the already-produced evaluation artifacts and aggregates the numbers the dashboard shows.

Reads only `docs/evaluation/{baseline,v1,v2,v21,holdout}_results.json`. It never calls a service, never runs a
question, and never touches the holdout question/answer file. Aggregation (counts, means) over numbers a run
already computed is not "re-running the evaluation".
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path
from typing import Optional

RESULTS_DIR = Path(__file__).resolve().parents[1] / "docs" / "evaluation"
VISIBLE_VERSIONS = ("baseline", "v1", "v2", "v21")
VERSION_LABEL = {"baseline": "Baseline", "v1": "V1", "v2": "V2", "v21": "V2.1"}
HOLDOUT_LABEL = "Post-development generalization evaluation (non-blind)"


def _load(name: str) -> Optional[dict]:
    p = RESULTS_DIR / f"{name}_results.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_visible() -> dict[str, dict]:
    """{version: {"results": [...], "semantic_stress": [...]}} for whichever of baseline/v1/v2/v21 have a results file."""
    return {v: d for v in VISIBLE_VERSIONS if (d := _load(v)) is not None}


def load_holdout() -> Optional[dict]:
    return _load("holdout")


def summarize(results: list[dict]) -> dict:
    """Counts and latency over an already-scored `results` list. Pure aggregation, no re-scoring."""
    c = lambda f: sum(1 for x in results if f(x))
    lat = [x["latency_ms"] for x in results if x.get("latency_ms") is not None]
    fm: dict[str, int] = {}
    for x in results:
        p = (x.get("failure_mode") or {}).get("primary", "unclassified")
        fm[p] = fm.get(p, 0) + 1
    by_cat: dict[str, dict] = {}
    for x in results:
        cat = x.get("category", "unknown")
        b = by_cat.setdefault(cat, {"total": 0, "correct": 0, "incorrect": 0, "needs_review": 0})
        b["total"] += 1
        b[x["result"]] = b.get(x["result"], 0) + 1
    return {"total": len(results), "correct": c(lambda x: x["result"] == "correct"), "incorrect": c(lambda x: x["result"] == "incorrect"),
            "needs_review": c(lambda x: x["result"] == "needs_review"), "status": {k: c(lambda x, k=k: x["status"] == k) for k in ("ok", "clarification", "unsupported", "error")},
            "mean_latency_ms": round(st.mean(lat), 1) if lat else None, "median_latency_ms": round(st.median(lat), 1) if lat else None,
            "by_category": by_cat, "failure_modes": fm}


def visible_dashboard() -> dict[str, dict]:
    """{version: summary} for the version-comparison table, computed from each version's stored `results` list."""
    return {v: summarize(d["results"]) for v, d in load_visible().items()}


def semantic_observations() -> dict[str, list[dict]]:
    return {v: d.get("semantic_stress", []) for v, d in load_visible().items()}


def validation_failures(version: str) -> list[dict]:
    d = load_visible().get(version)
    if not d:
        return []
    return [{"id": x["id"], "reasons": (x.get("validation") or {}).get("reasons", []), "warnings": (x.get("validation") or {}).get("warnings", [])}
            for x in d["results"] if x.get("validation") and not x["validation"].get("valid")]


def question(version: str, qid: str) -> Optional[dict]:
    """One visible-benchmark question's stored result, for the drill-down. Not used for the holdout."""
    d = load_visible().get(version)
    if not d:
        return None
    return next((x for x in d["results"] if x["id"] == qid), None)
