# Reproducible visible-set V2.1 evaluation

`scripts/evaluation/visible_v21.py` is the new, reproducible procedure for the 40 questions in `tests/evaluation/questions.json`. It is intentionally separate from the recorded historical V2.1 measurement in `v21_results.json` / `v21_results.md`: the historical executable runner and scorer were not recoverable.

**This harness has not itself produced a fresh, complete benchmark run.** A run was attempted after the `baseline-v0.2` freeze; the real Claude CLI execution stalled partway through and did not finish, so no new run artifact exists under `docs/evaluation/runs/`. The historical V2.1 result (24 correct / 5 incorrect / 11 needs-review, `v21_results.md`) remains the only completed measurement on the 40 visible questions; it should not be read as having been reproduced by this harness, only as the prior, independently-recorded result this harness is designed to be re-run against once execution succeeds.

## Run

From the repository root, after the required Claude CLI and Vertex credentials are already configured:

```powershell
python scripts/evaluation/visible_v21.py
```

The runner loads only the visible question file, verifies that it contains 40 questions all marked `split: visible`, constructs the existing `src.v2.v21.V21Service`, and calls `answer_question` once for each question in file order. It writes a timestamped canonical JSON artifact and Markdown summary to `docs/evaluation/runs/` (or the path given by `--output-dir`). No result file is narrowed: each entry preserves the complete `response.to_dict()` under `raw_response`.

## Deterministic scoring

The scorer uses the visible question metadata plus the four explicit terminal outcomes and MEET-10 treatment recorded in `v21_results.json`:

- Exact numeric/integer/object answers: the expected scalar values must be present in service evidence (`structured_evidence`, `meeting_aggregate_evidence`, or `meeting_evidence`) within the question's absolute tolerance, no listed wrong value may be present, and service status must be `ok`.
- `id_set` answers: every expected ID must be in `meeting_evidence.hits`, and the set of `[meeting: <id>]` citations in the answer must exactly equal the expected set; service status must be `ok`.
- HYB-02 additionally requires the answer to state the expected meeting count, matching the historical rule.
- PERF-05, HYB-07, ADV-01, and ADV-03 are correct only for their recorded explicit `(status, failure.code)` pairs: respectively `(unsupported, metric_not_available)`, `(unsupported, rm_meeting_link)`, `(clarification, ambiguous_entity)`, and `(clarification, ambiguous_entity)`.
- MEET-10 remains `needs_review`, while recording deterministic retrieval/citation diagnostics. The historical cited-ID rule cannot distinguish an ID cited as a match from one cited to say it is not a match.
- All other `validation_rule.type == behavior` questions remain `needs_review`; their prose rubric has no recoverable machine scorer, so this harness deliberately does not invent one.

This will normally classify the same kinds of questions as the historical 20 exact checks, four terminal checks, four ordinary cited-ID checks, HYB-02, and eleven review cases. It does **not** claim identical historical scoring implementation or comparable stochastic output.

## Test

```powershell
python tests/evaluation/test_visible_v21.py
```

The focused tests use synthetic service responses only; they do not construct `V21Service`, call external services, or execute the 40-question benchmark.
