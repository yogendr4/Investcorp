# Investcorp AI Engineer Assignment

An AI-driven question-answering system over Investcorp's synthetic investments, performance,
and meeting-notes data, built and hardened in explicit, measured stages: **Baseline → V1 → V2 →
V2.1**. Each stage is frozen at a git tag once measured; the current frozen state is
`baseline-v0.2` (dictionary-aligned Baseline, with V1/V2/V2.1 built on top of it).

**Local data location:** `data/Assignment_Data.xlsx`, `data/Assignment_Data_Dictionary.xlsx` (official column semantics).

## Architecture and flow

| Stage | Adds | Docs |
|---|---|---|
| **Baseline** | Deterministic entity resolution, question routing, SQL generation (Claude, guarded against unsafe SQL), lexical meeting retrieval (FTS5/BM25), synthesis, and answer validation over a local SQLite build of the source workbook. | [`baseline_contract.md`](docs/architecture/baseline_contract.md), [`baseline_service.md`](docs/architecture/baseline_service.md), [`entity_resolution.md`](docs/architecture/entity_resolution.md), [`question_routing.md`](docs/architecture/question_routing.md), [`structured_query.md`](docs/architecture/structured_query.md), [`meeting_retrieval.md`](docs/architecture/meeting_retrieval.md) |
| **V1** | Meeting-side query/evidence handling and meeting aggregates (counts, MIN/MAX date, distinct values) via a guarded view, wrapping Baseline. | [`v1_contract.md`](docs/architecture/v1_contract.md), [`v1_implementation.md`](docs/architecture/v1_implementation.md) |
| **V2** | Semantic meeting retrieval: Gemini Embedding 2 (Vertex AI) fused with lexical BM25 via deterministic RRF, wrapping V1. | [`v2_semantic_retrieval.md`](docs/architecture/v2_semantic_retrieval.md) |
| **V2.1** | Evidence-selection hardening: separates the fused candidate list from what synthesis/validation actually receive as evidence, wrapping V2. | [`v2_1_evidence_selection.md`](docs/architecture/v2_1_evidence_selection.md) |

Each stage is an additive wrapper around the one before it; no earlier stage's source is modified
by a later one. `docs/architecture/baseline_reference.md` explains the historical (`baseline-v0.1`)
vs. current dictionary-aligned (`baseline-v0.2`) distinction for the Baseline layer specifically.

**Development narrative:** Baseline was measured, its failures diagnosed, and V1 built to fix the
diagnosed gaps (meeting handling, aggregates, hybrid structured+meeting behavior) — a clear,
measured improvement. V2 then added semantic meeting retrieval (Gemini Embedding 2 fused with
lexical BM25); targeted testing (the exploratory 20-question set below) found the supplied meeting
corpus repetitive enough that lexical retrieval alone can often still reach a paraphrased answer,
so semantic retrieval's advantage was not conclusively isolated. V2.1 then added evidence
selection and validation hardening on top of V2, preserving V1's useful capabilities without
adding a measured accuracy gain on the sets tested. Finally, the official data dictionary and
Abhishek's clarifications on the open data questions were incorporated into the frozen Baseline
layer, producing the current `baseline-v0.2` state — this last step has not yet been re-measured
on the 40-question set (see Evaluation below).

## Data sources, entity relationships, and key assumptions

Full data facts and assumptions (A1-A18) live in [`docs/metadata/data_dictionary_DRAFT.md`](docs/metadata/data_dictionary_DRAFT.md),
the authoritative source for any data question (despite the filename). It reconciles the
project's own profiling against the official `Assignment_Data_Dictionary.xlsx`, and records
Abhishek's clarifications on every open question. Highlights:

- **Three sources, no cross-source joins:** `investments` (50,000 rows, 192 clients), `performance`
  (client/group snapshots), `meeting_notes` (20,000 meetings). The SQL guard structurally forbids
  joining `investments` and `performance`; meeting evidence is combined with structured results at
  the application layer, never via SQL.
- **Source precedence is source-specific, not merged:** meeting questions are answered from
  `meeting_notes` only; investment questions from `investments` only; performance fields are
  used only where the question is a performance question, and are never substituted for the
  other two sources when they disagree (see `client_last_met_date` below).
- **`AccountRM` is the sole authoritative RM field** (record-level, not client-level); the other
  RM-shaped fields (`AccountRM_org`, `AccountOwnerId`, `AccountRMEmail*`) are stored raw and unused.
- **LOB codes have one canonical meaning across both sheets** (confirmed by Abhishek): `PE`/`CI` =
  Private Equity, `HF` = Hedge Fund, `RE` = Real Estate, `COP` = Credit Opportunity, `INF` =
  Infrastructure. Investments' `nam_lob` raw text does **not** match this meaning and is not used
  for it; `cod_lob` is.
- **`Total_AUM_Amount` is never derived from, or reconciled with, the per-LOB `*_AUM_Amount`
  fields** — confirmed non-additive in the data; both are reported as given.
- **Group membership is source-specific.** Where investments/performance and meetings disagree on
  a group's client membership, neither source overrides the other; every current query already
  resolves membership from the one source its own route implies, and discloses the disagreement.
- **Performance `First_*/Last_*_Investment_Date` are not usable application facts** (confirmed by
  Abhishek) and are excluded from the SQL-generation schema entirely; first/last investment facts,
  if ever needed, come from `investments.dat_MinInvested`, not from these columns. The raw columns
  remain stored, unmodified.
- **No name-based join between performance investment-name fields and `investments.deal_name`** —
  no reliable key exists; they stay source-specific.
- **`id_CapitalCall` remains unresolved.** The official dictionary describes it as a capital-call
  identifier; observed values are only `{0, 1}`. It is preserved raw and treated as a flag of
  undocumented meaning, never reinterpreted as an identifier. Abhishek did not answer this item.

## Evaluation

Evaluation for this project has four distinct, non-interchangeable parts. Scores from one are not
comparable to another.

### 1. Historical 40-question development/evaluation set

The stable set of 40 questions in `tests/evaluation/questions.json`
([`baseline_question_set.md`](docs/evaluation/baseline_question_set.md)) used throughout iterative
development to measure each stage against the one before it. (This is a development/evaluation
set, not a training set — no system here is trained; it is the fixed benchmark each stage's
behavior was measured and diagnosed against.)

| Stage | Correct | Incorrect | Needs review |
|---|---|---|---|
| Baseline | 18 | 11 | 11 |
| V1 | 24 | 5 | 11 |
| V2 | 22 | 7 | 11 |
| V2.1 | 24 | 5 | 11 |

Full results, per-question breakdowns, and diagnoses: [`docs/evaluation/`](docs/evaluation/)
(`baseline_results.md`, `v1_results.md`, `v1_diagnosis.md`, `v2_results.md`, `v2_diagnosis.md`,
`v21_results.md`, `payload_audit_v21.md`).

**These four numbers are the last completed measurement on this set.** A reproducible benchmark
harness (`scripts/evaluation/visible_v21.py`,
[`docs/evaluation/reproducible_visible_v21.md`](docs/evaluation/reproducible_visible_v21.md)) was
built after the historical V2.1 runner and scorer could not be recovered from the repository, but
a fresh run has not completed — Claude CLI stalled before producing usable output. So: **the
historical V2.1 result above was not reproduced**, and **the final, dictionary-aligned
`baseline-v0.2` state has not itself been measured on this 40-question set.** The table above
predates the dictionary-alignment and Abhishek-clarification work described below.

### 2. Exploratory 20-question stress test (not comparable to the above)

A one-off, 20-question exploratory experiment on new, realistic-language questions, run once
across Baseline/V1/V2/V2.1 to check whether V2/V2.1 show a capability difference that the
40-question set's specific wording might not exercise. Full write-up, findings, and explicit
non-comparability statement: [`exploratory_20q_stress_test.md`](docs/evaluation/exploratory_20q_stress_test.md).

| Version | Correct / 20 |
|---|---|
| Baseline | 17 |
| V1 | 19 |
| V2 | 19 |
| V2.1 | 19 |

In short: Baseline→V1 improved on this set too; V2/V2.1 preserved V1's capabilities but did not
improve the aggregate score further, and the semantic-meeting questions turned out to all be
reachable by lexical retrieval alone, so this experiment did not isolate a semantic-retrieval
advantage. Full reasoning and caveats are in the linked document — treat the summary above as
directional, not as a benchmark result.

### 3. Automated regression/unit tests

479 automated tests across the baseline, V1, V2, V2.1, and local-UI code, **479/479 passing** as
of the current `baseline-v0.2` state (after the official-dictionary alignment and Abhishek's
clarifications were incorporated; see the assumptions list above). Run with
`python -m unittest` per module under `tests/`.

### 4. Validation / holdout status

A separate 10-question holdout set exists under `tests/evaluation/holdout/` for a later,
explicitly-authorized validation step; it is intentionally excluded from this repository's
tracked documentation, and its contents are not summarized or reproduced here. **No
post-clarification 40-question or 10-question holdout result exists yet** for the current
`baseline-v0.2` state.

## Local demo UI

A minimal Streamlit app (`app/`) provides a chat interface backed by V2.1 (with a deterministic
execution trace, never model reasoning) and an evaluation dashboard over the recorded
`docs/evaluation/*_results.json` files. See [`docs/architecture/ui_and_local_observability.md`](docs/architecture/ui_and_local_observability.md).

**Assumes:** `pip install -r requirements.txt`; the Baseline database built
(`python -m src.baseline.build_db`); the V2 embedding index built (`python -m src.v2.build_embeddings`);
`gcloud` authenticated with the Vertex AI API enabled, for meeting-question semantic retrieval.

```
streamlit run app/app.py
```

## Repository layout

```
src/baseline/   src/v1/   src/v2/     Production code, one folder per stage (CLAUDE.md discipline)
app/                                  Local Streamlit demo UI
scripts/evaluation/                   Reproducible benchmark harness
tests/                                Unit and regression tests, mirroring src/
docs/architecture/                    Contracts and implementation notes per stage
docs/evaluation/                      Benchmark results and diagnoses per stage
docs/metadata/                        Data dictionary and assumptions (authoritative)
```

## Development process

This project follows `CLAUDE.md`: staged, approval-gated development (baseline → test → fail →
diagnose → next version), no speculative scope expansion, deterministic logic preferred over
LLM calls, and every meaningful change accompanied by a regression test.
