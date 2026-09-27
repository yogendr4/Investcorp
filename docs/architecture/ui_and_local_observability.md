# Local Demo UI and Observability

Code: `app/` (`app.py`, `trace.py`, `evaluation.py`). Tests: `tests/app/` (30). This is a presentation layer only: it calls the existing frozen services and renders what they already return. No Baseline, V1, V2 or V2.1 logic was changed to build it.

## Why a local UI instead of LangSmith

LangSmith would add a hosted third-party dependency (an account, a key, network egress for every request) for a project whose brief is a local, reproducible, low-cost demo running on a constrained laptop. Everything the UI needs — the pipeline's own resolution, routing, evidence and validation objects — is already returned by `to_dict()` on every response; nothing about the pipeline needs new instrumentation to show it. A local Streamlit page reads those dicts directly, with no new service, no new credentials and no data leaving the machine beyond the Claude CLI and Vertex calls the pipeline already makes.

## Execution trace vs. chain-of-thought

The trace is a chronological list of **deterministic pipeline steps** the frozen service already took, rebuilt from the response's own fields (`app/trace.py:build_trace`): Entity Resolution, Routing, Evidence Collection, Structured Evidence, Meeting Evidence, Validation, and Failure if one occurred. Each step's timing comes from the `timing_ms` breakdown the pipeline already produces; no absolute wall-clock timestamps exist for sub-steps, so `LocalTraceRecorder` synthesizes them by laying the known durations end to end in pipeline order — a presentation detail, not a new measurement.

This is not chain-of-thought:
- The only Claude output ever shown is the **final answer text** the pipeline already validated. No prompt sent to Claude, and no intermediate model output, is rendered anywhere, expanded or collapsed.
- `build_trace` only copies specific, named fields out of the response dict (outcome, row count, meeting id/date/rank/score, validation verdict). It cannot leak a hidden-reasoning field because it never reads one.
- Every value passed into a trace event is run through `redact()` before it is stored, which drops keys matching email/token/secret/password/credential/api-key/org/alias/owner/account-id patterns and masks email-shaped strings, recursively. Test coverage in `tests/app/test_trace.py::TestRedaction` checks this against a realistic metadata blob.

## `TraceRecorder` abstraction

`TraceRecorder` (`app/trace.py`) is an abstract base with `record(name, status, duration_ms=None, metadata=None, evidence_refs=())` and `events()`. `LocalTraceRecorder` is the only implementation: it appends `TraceEvent`s to an in-memory list for the lifetime of one request and is never persisted. A future recorder — for example one that also forwards events to LangSmith — would only need to implement the same interface and be swapped in at the one call site (`build_trace`); no change to `app.py`, to `build_trace`'s field mapping, or to any Baseline/V1/V2/V2.1 code. `record()` redacts `metadata` and `evidence_refs` on the way in, so a future recorder inherits the same safety without repeating the logic.

## Evaluation dashboard

`app/evaluation.py` reads the already-produced `docs/evaluation/{baseline,v1,v2,v21,holdout}_results.json` files and only aggregates numbers those files already contain (counts, means over per-question fields that were computed once, during the recorded runs). It never re-scores a question and never calls a service. The dashboard shows: a version comparison table (correct/incorrect/needs-review/mean latency), results by category, the failure-mode distribution, semantic-retrieval observations (from each result file's `semantic_stress` block), validation failures, and a question-level drill-down restricted to the **visible benchmark** (`version` in `{baseline, v1, v2, v21}`).

The holdout is shown separately, from `holdout_results.json`'s precomputed `metrics` object only — aggregate correct/incorrect/needs-review/latency per version — never per-question answers or evidence, and the section is labelled exactly `Post-development generalization evaluation (non-blind)`, matching the label the evaluation itself used, never "blind holdout". `tests/app/test_evaluation_and_ui.py::TestHoldoutLabelAndNoRawAnswersByDefault` checks the label text and that `app.py` never indexes into the holdout's per-question `results`.

## V2.1 as the interactive backend

The Chat tab's only backend is `src.v2.v21.V21Service` (`app/app.py:_service`, cached with `st.cache_resource` so the embedding index and Claude/Vertex clients are built once per process, not per question). This is the final approved decision: the most capable frozen version. Baseline, V1 and V2 remain reachable through their own tags/tests and through the evaluation dashboard, but the chat UI does not offer them as alternate backends — that would need extra wiring this task did not ask for.

## Future compatibility with LangSmith (not implemented)

Nothing here precludes adding a LangSmith-backed recorder later:
1. Implement a `LangSmithTraceRecorder(TraceRecorder)` that forwards each `record()` call to a LangSmith run/span, alongside or instead of the in-memory list.
2. Swap the recorder `build_trace` (or its caller) constructs.
3. No change to `TraceEvent`, to the redaction step, or to any Baseline/V1/V2/V2.1 code, because the trace is already built only from each response's own `to_dict()` output.

This was not built, per the task's decision to keep tracing local.

## Known simplifications

- Sub-step timestamps are synthesized from durations in pipeline order, not measured independently; they are for display ordering, not profiling.
- The evaluation dashboard's category and failure-mode tables only include categories/modes that appear in at least one loaded result file.
- The Chat tab always uses V2.1; there is no in-app version switcher.
