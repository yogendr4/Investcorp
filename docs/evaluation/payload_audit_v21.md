# Payload Audit: V2.1, 40 visible questions (read-only)

Audit of the response/evidence payload the frozen V2.1 pipeline produces, for all 40 visible questions. No source, test, prompt, benchmark or scoring file was changed; no Claude or Vertex call was made; the holdout was not read. `src/v2/`, `src/v1/`, `src/baseline/` were only read.

## 0. What was inspected, and a methodological note

Two artifacts exist for the same completed V2.1 run:
- `docs/evaluation/v21_results.json` — the **published report**. Its generator narrows the `meeting_evidence.hits` fields for readability (keeps `rank, meeting_id, client_id, meeting_date, matched_terms, lexical_rank, semantic_rank, semantic_score, rrf_score`; drops `snippet, company, sector, region, investment_stage, deal_size_estimate, action_items, action_items_state, summary_chars`).
- The run's raw capture (one JSON object per question, `answer_question(...).to_dict()` exactly as the service, `src/v2/synthesis.py` and `app/trace.py` all consume it) — this **is** the real service response and is not narrowed.

Both already exist on disk from the completed run; reading the raw capture is not a rerun (no Claude/Vertex call was made to produce it — it was written once, during the earlier evaluation, before this audit). It was used here because the published report alone is insufficient to answer the brief's structured/meeting-payload questions (it doesn't carry `snippet`, `company`, `sector`, `sql` for the meeting side beyond what's summarized, or the `candidates`/`selection`/`semantic` blocks in full). **Finding 0:** the published `v21_results.json` is not a faithful copy of the service payload; anyone auditing evidence completeness from the doc alone would wrongly conclude several fields are missing. This is itself a reporting-payload loss, listed in section 3.

All 40 questions were inspected; none needed a rerun.

## 1. Payload contract currently produced

**Structured evidence** (`structured_evidence`, `src/baseline/structured_query.py:StructuredEvidence.to_dict`) and **meeting-aggregate evidence** (`meeting_aggregate_evidence`, `src/v1/meeting_aggregates.py:MeetingAggregateEvidence.to_dict`) both carry: `outcome, sql, explanation, columns, rows, row_count, truncated, source_tables (SE only), objects_used, rules_applied, entities_used, timing_ms, llm, error, notes`.

**Meeting evidence** (`meeting_evidence`): `outcome, rank_method, query, filters, entity_notes, notes, timing_ms, top_k, filtered_meeting_count, matched_count, fewer_than_k, error, hits`. Each hit (`src/baseline/meeting_retrieval.py:MeetingHit` + V2/V2.1 extensions in `src/v2/retrieval.py`): `meeting_id, client_id, group_id, meeting_date, company, sector, region, investment_stage, deal_size_estimate, score, snippet, matched_terms, matched_fields, action_items_state, action_items, summary_chars, rank, lexical_rank, semantic_rank, semantic_score, semantic_sentence (folded into snippet), rrf_score, lexical_score`. For a **fused** (`rank_method == "rrf"`) result only, two more top-level fields exist: `semantic` (model, dimensions, query text/format, scope size, candidate counts, embed/search ms) and, from V2.1, `candidates` (the full ranked fusion list, each tagged `in_evidence: bool`) plus `selection` (the rule text, and `candidates/evidence/lexical_matches/semantic_only_in_top_cluster/dropped_meeting_ids` counts). A **listing** (`rank_method == "date_desc"`) result has none of `semantic/candidates/selection` — there is no fusion pool to distinguish, so this is a correct absence, not a loss.

## 2. Information preserved

Confirmed present, for all 40 questions, in the raw response:
- **Structured:** the exact rows/values, columns, row_count, `sql`, `source_tables`/`objects_used`, `rules_applied` — checked programmatically across all 40 successful structured/aggregate results; no missing key in any of them.
- **Meeting:** `meeting_id`, `date`, `client_id`, `lexical_rank`, `semantic_rank`, `semantic_score`, the matched snippet (with the semantic-matched sentence appended when applicable), and every field synthesis reads (`company`, `sector`, `action_items_state`, etc.) — verified on a fused example (MEET-08, hit for meeting 8623): all 22 `MeetingHit`/V2 fields present with real values, including `snippet` (confirmed **not** narrowed at the service-response level; only the published doc narrows it, see section 0).
- **Evidence-vs-candidate distinction:** present for every fused result via `candidates` (full list, `in_evidence` flag per item) and `selection` (counts + `dropped_meeting_ids`). Example, MEET-08: `candidates: 18, evidence: 15, lexical_matches: 15, semantic_only_in_top_cluster: 0`.
- **Hybrid, structured-plus-lexical-meeting** (HYB-01, HYB-03, HYB-05 — no aggregate side): the two evidence objects are passed to synthesis and validation **fully separately** (`src/v1/validation.py:validate_answer_v1`, the merge branch below is only entered `if aggregate is not None`; these three never do). No count merging; each side's `row_count`/`filtered_meeting_count` stays its own.

## 3. Information lost

### 3a. Correctness-impacting: hybrid structured+aggregate merge collapses two independent counts into one sum

`src/v1/validation.py:47-53`, entered only when both a structured route's evidence and a meeting-aggregate evidence exist together (hybrid, `aggregate is not None`, `structured is not None`):
```python
eff_structured = SimpleNamespace(rows=tuple(structured.rows) + tuple(aggregate.rows),
                                 row_count=structured.row_count + aggregate.row_count, ...)
eff_meeting, eff_meeting_no_data = None, True
```
The two sides' `row_count`s are **summed**, and their rows are concatenated into one flat tuple with no per-row source tag. `src/baseline/answer_validation.py:86` then grounds numbers as `[float(structured.row_count), float(len(structured.rows))]` — i.e. only the **combined** total, never either side's own count. This is safe only when the useful number also appears as a literal **row value** (e.g. a `COUNT(*)` result row `[[22]]`); it is lost whenever the useful number is a side's **row_count of a list-shaped result** (e.g. `SELECT DISTINCT ...` returning several rows, where the interesting fact is "N rows", not any cell value).

**Observed instance — HYB-10** ("For client D12376, list the deal names ... and the sectors and companies in its meetings"): structured evidence returns 8 rows (deal names, `row_count=8`); meeting-aggregate evidence returns 16 rows (distinct sector/company pairs, `row_count=16`). Merged: `row_count=24`, `rows` = 24 mixed-shape tuples. The correct, fully-grounded answer states "16 sector/company rows" — `16` is nowhere in the merged evidence (24 and 24 are the only counts present) — and is withheld:
```
FAILURE: numbers come from the evidence: numbers not found in the evidence: ['16']
WARNING: small integers and years are grounded: not found in the evidence: ['8']
```
**Not affected — HYB-02** (same merge branch, "how many investment records and how many meetings"): both sides' useful numbers (216, 22) are `COUNT(*)` results that also appear as literal row values, so grounding succeeds despite the merge. This shows the bug is specific to **list-shaped** (not scalar-count-shaped) aggregate/structured results being combined with anything else.

This is not new to V2.1: `src/v1/validation.py` is the same shared file used unmodified by V1, V2 and V2.1 (all three subclass `V1Service`, which calls `validate_answer_v1`). The identical failure on HYB-10 was already recorded in the V1 and V2 evaluations (`v1_results.md`, `v2_diagnosis.md`); this audit locates it precisely and confirms it is present, unchanged, in the frozen V2.1 payload too.

### 3b. Correctness-impacting: a number that is genuinely in the evidence text, but not in a field the validator scans for numbers

`src/baseline/answer_validation.py:evidence_facts` extracts numbers from: per-hit fields, `matched_count`/`filtered_meeting_count`/`top_k`/`len(hits)`, `structured.row_count`/`len(rows)`, and flattened row/hit text (`h.snippet`, `h.company`, …). It does **not** scan `MeetingEvidence.notes` or `.entity_notes` — yet `src/v1/synthesis.py:_meeting_lines` writes those notes into the prompt verbatim (`lines += [f"note: {n}" for n in list(ev.notes) + list(ev.entity_notes)]`), so the model can read and faithfully quote a number that exists **only** inside a note string.

**Observed instance — MEET-06** ("Which meetings did Sanjay López attend?"): the retriever's own note is `'627 meetings matched; only the top 50 are returned'` (`50` is the internal lexical-candidate depth constant, exposed only as prose). The rejected answer quotes it verbatim — *"Missing: the remaining matches (only the top 50 of 627 are returned...)"* — and is withheld:
```
FAILURE: numbers come from the evidence: numbers not found in the evidence: ['50']
```
Every other number in that same answer (627, 15, 4, meeting ids, dates) grounds correctly; `50` is the one number that lives only in a note.

### 3c. Correctness-impacting (design limitation, working as specified, listed for completeness): the unmatched-meetings count is not computed in fused mode

`src/v1/synthesis.py:_unmatched` computes "meetings in scope without a text match" **only** when `rank_method == "bm25"`; for a fused (`rrf`) result it returns `None` and the line is omitted from the prompt. The model is still shown `filtered_meeting_count` and `matched_count` individually and, in the observed case, subtracted them itself.

**Observed instance — MEET-07**: scope 23, matched 6, evidence (after V2.1 selection) exactly the 6 true matches; the correct answer additionally states "the remaining **17** meetings" (23 − 6, computed by the model, present nowhere in the evidence):
```
FAILURE: numbers come from the evidence: numbers not found in the evidence: ['17']
```
Unlike 3a/3b, this number was never given to the model at all (in a row, a note, or otherwise) — the model performed the subtraction itself. The validator's rule ("no new calculations") is working as designed here; this is a **capability gap** in what fused-mode evidence exposes, not a bug in the grounding check. It is included because it produces the same symptom (a plausible, correct-looking withheld answer) and was part of the specific example under investigation.

### 3d. Observability/UI-only: the execution-trace panel mislabels and drops one side of a structured+aggregate hybrid

`app/app.py`'s Chat tab renders a trace via `app/trace.py:build_trace`. When both `structured_evidence` and `meeting_aggregate_evidence` are present (the same hybrid combination as 3a — HYB-02, HYB-06, HYB-10), lines 117-121:
```python
if se or ae:
    ev = se or ae                                    # picks se whenever both exist
    rec.record("Structured Evidence", ..., metadata={..., "source": "meetings_meta (aggregate)" if ae else "; ".join(...), "row_count": ev.get("row_count"), ...})
```
`ev` is `se` (the investments query) whenever both exist, but the `"source"` label is chosen by `if ae` (existence of an aggregate side at all, not "is `ev` the aggregate"), so the panel wrongly labels `se`'s data as `"meetings_meta (aggregate)"`. The meeting-aggregate evidence itself (its own `sql`, `row_count`, rows) never gets a trace entry. The "Evidence Collection" summary event has the same defect: `evidence_count = (se and se.row_count) or (ae and ae.row_count) or ...` picks only `se`'s row_count, silently omitting the aggregate side's.

**Observed instance — HYB-10** (verified by running `app.trace.build_trace` on the stored HYB-10 response, read-only): the "Structured Evidence" panel shows `source: meetings_meta (aggregate)`, `row_count: 8` — 8 is actually the *investments* deal-name count, mislabeled as the meeting aggregate. The real 16-row meeting aggregate has no panel at all. This affects only the demo UI's trace display; it does not affect the answer, the validator, or any stored evaluation result (the answer/validation path uses the Evidence objects directly, not `app/trace.py`).

## 4. Questions affected

| Question | Loss | Severity |
|---|---|---|
| HYB-10 | 3a (merged row_count) and 3d (trace mislabeling) both occur | critical (3a: withheld a correct answer) / observability-only (3d) |
| MEET-06 | 3b (note text excluded from grounding) | critical |
| MEET-07 | 3c (unmatched count not computed in fused mode) | correctness-impacting (by design) |
| HYB-02, HYB-06 | Same merge code path as 3a runs, but the useful numbers happen to be literal row values (HYB-02) or the question fails earlier for an unrelated reason (HYB-06, cross-source date dependency) — **not currently symptomatic**, but one code change away from being so | latent / correctness-impacting if triggered |
| All other 35 questions | No payload loss found: structured/meeting evidence fully preserved through service response → synthesis → validation; hybrid structured+lexical-meeting (HYB-01, HYB-03, HYB-05) kept fully independent, no merge | none |

The published `docs/evaluation/v21_results.json` additionally under-represents `snippet`/`company`/`sector`/etc. for **every** meeting hit across all 40 questions (section 0) — a reporting-only gap, not a pipeline gap.

## 5. Root-cause categories

1. **Aggregation collapse in the hybrid validator merge** (`src/v1/validation.py:47-53`, `src/baseline/answer_validation.py:86`): summing two independently-meaningful counts into one, with no per-side number preserved. Root of 3a.
2. **Incomplete number-extraction scope in the frozen validator** (`src/baseline/answer_validation.py:evidence_facts`): scans hit/row fields but not `notes`/`entity_notes`, although synthesis is given those same notes. Root of 3b.
3. **Mode-dependent evidence computation** (`src/v1/synthesis.py:_unmatched`, gated on `rank_method == "bm25"`): a derived fact computed and shown in one retrieval mode but not the other, though the underlying numbers needed to derive it are shown in both. Root of 3c.
4. **Presentation-layer field selection bug** (`app/trace.py:117-121`, and the parallel logic in the "Evidence Collection" event): a boolean picked from the wrong variable (`ae` truthiness instead of `ev is ae`) when choosing a label for `ev = se or ae`. Root of 3d.
5. **Reporting-script field narrowing** (the `v21_results.json` generator, not any pipeline file): drops fields for readability that the audit brief's checklist explicitly asks about. Root of the section 0 finding.

## 6. Severity

- **Critical** (a correct, fully-derivable-from-evidence answer is silently withheld from the user): 3a (HYB-10) and 3b (MEET-06).
- **Correctness-impacting, by design** (the validator is refusing a genuine model-performed calculation, which is the intended behavior, but the fused retrieval mode could expose the fact deterministically instead of requiring the model to calculate it): 3c (MEET-07).
- **Observability/UI-only** (no effect on the answer, validation outcome, or any stored evaluation number): 3d (trace panel) and the section 0 reporting-doc narrowing.

## 7. Smallest fixes needed (not implemented)

- **3a:** when merging a structured and an aggregate evidence object for validation, ground each side's own `row_count` (and `len(rows)`) in addition to the combined total, instead of replacing them with only the sum.
- **3b:** include `meeting.notes` and `meeting.entity_notes` (and, symmetrically, `structured.notes`) in `evidence_facts`'s number/text extraction, since synthesis already receives that same text.
- **3c:** compute and expose "meetings in scope without a text match" for fused (`rrf`) results the same way it is already computed for `bm25` results (`filtered_meeting_count - matched_count` is well-defined regardless of rank method).
- **3d:** track which object `ev` actually is (`ev is ae`) rather than re-testing `ae`'s mere existence, for both the "Structured Evidence" event's `source` label and the "Evidence Collection" event's `evidence_count`; render a second panel when both `se` and `ae` are present rather than picking one.
- **Section 0:** either stop narrowing meeting-hit fields in the results-report generator, or state explicitly in `v21_results.md`'s methodology section that the published JSON is a narrowed view and point to the full response fields.

Nothing above has been implemented. No source, test, prompt, benchmark, or scoring file was changed to produce this audit; no Claude or Vertex call was made; the holdout was not read.
