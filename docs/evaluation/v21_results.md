# V2.1 Results (visible benchmark, 40 questions)

Measurement of the frozen V2.1 (`V21Service` in `src/v2/v21.py`: V2 retrieval with the fused top-20 kept as the ranked candidate list and only **lexical matches plus the top semantic cluster** passed to synthesis and validation; design in [`v2_1_evidence_selection.md`](../architecture/v2_1_evidence_selection.md)) on the 40 visible questions of `tests/evaluation/questions.json`. It is compared directly with the stored V2 ([`v2_results.*`](v2_results.md)), V1 ([`v1_results.*`](v1_results.md)) and Baseline ([`baseline_results.*`](baseline_results.md)) results, all unchanged. Raw data: [`v21_results.json`](v21_results.json). This document reports measured changes and attributed causes only; it does not rate any system.

## 1. Evaluation setup

- **Code:** V2.1 exactly as implemented and documented. No source, test, prompt, retrieval, fusion, selection, validation, benchmark or scoring change was made during or after the run, and nothing was tuned after a failure (source checksums taken before the run are compared in section 10). One run, the real Claude CLI (`claude-sonnet-5`, effort low) and real Vertex query embeddings, with `V21Service` in place of `V2Service` in the same runner. The embedding index was not rebuilt.
- **Questions:** the 40 visible questions only. The holdout file was not opened or read.
- **Scoring:** the same frozen rules and evaluators as the V2 measurement, no LLM judge, **MEET-10 stays `needs_review`**, rule-based questions stay `needs_review`, id-set questions require the cited meeting ids to equal the expected set. Nothing was re-scored.
- **Failure-mode labels** are analyst judgements assigned after scoring, each with its reason in the JSON.
- **What V2.1 changes relative to V2:** only which meetings reach synthesis and validation for a fused (topical meeting) result. In this run five questions were topical: MEET-06, MEET-07, MEET-08, MEET-10, ADV-10. Every other path (listing, aggregate, structured, refusals, clarifications) is identical to V2, so a change on those paths (MEET-05, HYB-06) is LLM run-to-run variance and cannot be attributed to V2.1.
- **Caveats:** one run of a non-deterministic LLM per question; latency includes Claude CLI start-up; post-hoc scoring concerns (`v1_diagnosis.md` section 4.7, `v2_diagnosis.md` section 7) are unchanged and documented separately.

## 2. Aggregate results

| Measure | Baseline | V1 | V2 | V2.1 | V2.1 vs V2 | V2.1 vs V1 | V2.1 vs Baseline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Questions | 40 | 40 | 40 | 40 | 0 | 0 | 0 |
| Correct (auto-scored) | 18 | 24 | 22 | 24 | +2 | 0 | +6 |
| Incorrect (auto-scored) | 11 | 5 | 7 | 5 | -2 | 0 | -6 |
| Needs review | 11 | 11 | 11 | 11 | 0 | 0 | 0 |
| Status `ok` | 31 | 28 | 29 | 29 | 0 | +1 | -2 |
| Status `clarification` | 4 | 4 | 4 | 4 | 0 | 0 | 0 |
| Status `unsupported` | 3 | 5 | 5 | 4 | -1 | -1 | +1 |
| Status `error` (all `validation_failed`) | 2 | 3 | 2 | 3 | +1 | 0 | +1 |

All 40 questions returned a response. Correct in Baseline but not in V2.1: none. Correct in V2 but not in V2.1: none; correct in V2.1 but not in V2: MEET-05, ADV-10. Correct in V1 but not in V2.1: MEET-07; correct in V2.1 but not in V1: MEET-08.

## 3. Results by category

| Category | Baseline (c / i / r) | V1 | V2 | V2.1 | V2.1 correct vs V2 | V2.1 status: ok / clar. / unsupp. / error |
| --- | --- | --- | --- | --- | --- | --- |
| Investment | 6 / 0 / 2 | 6 / 0 / 2 | 6 / 0 / 2 | 6 / 0 / 2 | 0 | 8 / 0 / 0 / 0 |
| Performance | 6 / 0 / 2 | 6 / 0 / 2 | 6 / 0 / 2 | 6 / 0 / 2 | 0 | 7 / 0 / 1 / 0 |
| Meeting | 0 / 6 / 2 | 4 / 2 / 2 | 3 / 3 / 2 | 4 / 2 / 2 | +1 | 6 / 0 / 0 / 2 |
| Hybrid | 2 / 5 / 1 | 4 / 3 / 1 | 4 / 3 / 1 | 4 / 3 / 1 | 0 | 5 / 0 / 2 / 1 |
| Adversarial / edge / semantic | 4 / 0 / 4 | 4 / 0 / 4 | 3 / 1 / 4 | 4 / 0 / 4 | +1 | 3 / 4 / 1 / 0 |

| Category | V2.1 correct | V2.1 incorrect | V2.1 needs review |
| --- | --- | --- | --- |
| Investment | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10 | - | INV-04, INV-06 |
| Performance | PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10 | - | PERF-06, PERF-07 |
| Meeting | MEET-01, MEET-04, MEET-05, MEET-08 | MEET-06, MEET-07 | MEET-02, MEET-10 |
| Hybrid | HYB-01, HYB-02, HYB-03, HYB-07 | HYB-06, HYB-09, HYB-10 | HYB-05 |
| Adversarial / edge / semantic | ADV-01, ADV-02, ADV-03, ADV-10 | - | ADV-05, ADV-06, ADV-07, ADV-08 |

### All 40 questions (Baseline / V1 / V2 / V2.1)

| ID | Route | V2.1 mode | Result: Baseline / V1 / V2 / V2.1 | V2.1 status (V2 to V2.1) | V2.1 primary failure | V2.1 latency |
| --- | --- | --- | --- | --- | --- | --- |
| INV-01 | investment | - | correct / correct / correct / correct | ok | - | 14.4 s |
| INV-02 | investment | - | correct / correct / correct / correct | ok | - | 12.0 s |
| INV-03 | investment | - | correct / correct / correct / correct | ok | - | 11.5 s |
| INV-04 | investment | - | review / review / review / review | ok | - | 11.8 s |
| INV-06 | investment | - | review / review / review / review | ok | - | 11.9 s |
| INV-07 | investment | - | correct / correct / correct / correct | ok | - | 10.8 s |
| INV-08 | investment | - | correct / correct / correct / correct | ok | - | 11.4 s |
| INV-10 | investment | - | correct / correct / correct / correct | ok | - | 12.0 s |
| PERF-01 | performance | - | correct / correct / correct / correct | ok | - | 10.8 s |
| PERF-02 | performance | - | correct / correct / correct / correct | ok | - | 11.4 s |
| PERF-04 | performance | - | correct / correct / correct / correct | ok | - | 11.4 s |
| PERF-05 | performance | - | correct / correct / correct / correct | unsupported | - | 1 ms |
| PERF-06 | performance | - | review / review / review / review | ok | - | 11.2 s |
| PERF-07 | performance | - | review / review / review / review | ok | - | 12.5 s |
| PERF-08 | performance | - | correct / correct / correct / correct | ok | - | 11.1 s |
| PERF-10 | performance | - | correct / correct / correct / correct | ok | - | 10.3 s |
| MEET-01 | meeting | aggregate | incorrect / correct / correct / correct | ok | - | 11.5 s |
| MEET-02 | meeting | listing | review / review / review / review | ok | - | 9.3 s |
| MEET-04 | meeting | aggregate | incorrect / correct / correct / correct | ok | - | 11.8 s |
| MEET-05 | meeting | aggregate | incorrect / correct / incorrect / **correct** | error to **ok** | - | 11.7 s |
| MEET-06 | meeting | topical (selected) | incorrect / incorrect / incorrect / incorrect | ok to **error** | meeting_retrieval | 13.7 s |
| MEET-07 | meeting | topical (selected) | incorrect / correct / incorrect / incorrect | ok to **error** | validation_false_positive | 9.5 s |
| MEET-08 | meeting | topical (selected) | incorrect / incorrect / correct / correct | ok | - | 9.3 s |
| MEET-10 | meeting | topical (selected) | review / review / review / review | ok | - | 8.0 s |
| HYB-01 | hybrid | listing | incorrect / correct / correct / correct | ok | - | 11.1 s |
| HYB-02 | hybrid | aggregate | correct / correct / correct / correct | ok | - | 16.0 s |
| HYB-03 | hybrid | listing | incorrect / correct / correct / correct | ok | - | 12.6 s |
| HYB-05 | hybrid | listing | review / review / review / review | ok | - | 6.9 s |
| HYB-06 | hybrid | aggregate | incorrect / incorrect / incorrect / incorrect | unsupported to **ok** | hybrid_limitation | 19.6 s |
| HYB-07 | ambiguous | - | correct / correct / correct / correct | unsupported | - | 1 ms |
| HYB-09 | hybrid | unsupported | incorrect / incorrect / incorrect / incorrect | unsupported | unsupported_or_ambiguity_handled | 1 ms |
| HYB-10 | hybrid | aggregate | incorrect / incorrect / incorrect / incorrect | error | validation_false_positive | 18.5 s |
| ADV-01 | investment | - | correct / correct / correct / correct | clarification | - | 2 ms |
| ADV-02 | investment | - | correct / correct / correct / correct | ok | - | 10.4 s |
| ADV-03 | ambiguous | - | correct / correct / correct / correct | clarification | - | 0 ms |
| ADV-05 | investment | - | review / review / review / review | ok | - | 12.7 s |
| ADV-06 | meeting | listing | review / review / review / review | clarification | - | 1 ms |
| ADV-07 | investment | - | review / review / review / review | clarification | - | 1 ms |
| ADV-08 | performance | - | review / review / review / review | unsupported | - | 5.7 s |
| ADV-10 | meeting | topical (selected) | correct / correct / incorrect / **correct** | ok | - | 8.4 s |

## 4. Component-level results

| Component | Baseline | V1 | V2 | V2.1 | Notes |
|---|---|---|---|---|---|
| Entity resolution | 28/28 | 28/28 | 28/28 | 28/28 | frozen |
| Routing | 40/40 | 40/40 | 40/40 | 40/40 | frozen |
| Structured query components correct | 18/18 | 17/18 | 17/18 | 17/18 | HYB-09 refused before the structured side runs (V1, V2, V2.1) |
| Expected evidence present in the evidence list | 5 questions | 8 | 8 | 8 (MEET-02, MEET-07, MEET-08, MEET-10, HYB-01, HYB-03, HYB-05, ADV-10) | MEET-06 5/14 in V2.1 (V1 1/14); HYB-09 refused; for V2.1 this is the **evidence** list, not the candidate list |
| Answer validation | 31/33 passed | 28/31 | 29/31 | 29/32 | V2.1 rejected: MEET-06, MEET-07, HYB-10 (V2: MEET-05, HYB-10; V1: MEET-06, HYB-05, HYB-10) |
| Validation false positives (analyst) | 1 | 2 | 2 | 2 | V2.1: MEET-07, HYB-10 |
| Semantic retrieval calls | 0 | 0 | 5 | 5 (all `ok`) | one query embedding per topical question |

### Evidence selection on the topical questions (candidates to evidence)

| ID | Passed to synthesis in V2 | Candidates to evidence (V2.1) | Evidence: lexical / semantic-only | Expected: candidates to evidence | Non-expected meetings cited, V2 | V2.1 | Status V2 to V2.1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MEET-06 | 20 (V2 evidence) | 20 to 15 | 12 / 3 | 5/14 to 5/14 | - | - | ok to error |
| MEET-07 | 20 (V2 evidence) | 20 to 6 | 6 / 0 | 6/6 to 6/6 | [4638] | - | ok to error |
| MEET-08 | 18 (V2 evidence) | 18 to 15 | 15 / 0 | 3/3 to 3/3 | - | - | ok to ok |
| MEET-10 | 20 (V2 evidence) | 20 to 18 | 18 / 0 | 1/1 to 1/1 | [11756] | [11756] | ok to ok |
| ADV-10 | 20 (V2 evidence) | 20 to 3 | 3 / 0 | 3/3 to 3/3 | [11707] | - | ok to ok |

The evidence-selection rule kept every expected meeting that the candidate list held. The semantic-only hits that reached the evidence were the three MEET-06 meetings (348, 1240, 19916). In the other four questions all evidence hits were lexical matches.

## 5. Latency

| Measure | Baseline | V1 | V2 | V2.1 |
| --- | --- | --- | --- | --- |
| Mean | 9.3 s | 9.2 s | 10.8 s | 9.5 s |
| Median | 11.4 s | 11.3 s | 12.1 s | 11.2 s |
| Max | 28.4 s (ADV-10) | 18.8 s (HYB-10) | 34.8 s (PERF-07) | 19.6 s (HYB-06) |

| Route | n | V2 mean | V2.1 mean / median / max |
| --- | --- | --- | --- |
| ambiguous | 2 | 1 ms | 0 ms / 0 ms / 1 ms |
| hybrid | 7 | 12.2 s | 12.1 s / 12.6 s / 19.6 s |
| investment | 12 | 11.2 s | 9.9 s / 11.6 s / 14.4 s |
| meeting | 10 | 10.1 s | 9.3 s / 9.4 s / 13.7 s |
| performance | 9 | 12.6 s | 9.4 s / 11.1 s / 12.5 s |

Selection is a list filter; it adds no API call. Latency differences between runs reflect Claude call variance (the V2.1 maximum is HYB-06, an aggregate-plus-structured question that does not use retrieval selection).

## 6. Failure taxonomy

| Primary failure mode | Baseline | V1 | V2 | V2.1 | V2.1 IDs |
| --- | --- | --- | --- | --- | --- |
| correct | 18 | 24 | 22 | 24 | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10, PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10, MEET-01, MEET-04, MEET-05, MEET-08, HYB-01, HYB-02, HYB-03, HYB-07, ADV-01, ADV-02, ADV-03, ADV-10 |
| entity_resolution | 0 | 0 | 0 | 0 | - |
| routing | 0 | 0 | 0 | 0 | - |
| sql_generation_or_query_correctness | 0 | 0 | 0 | 0 | - |
| meeting_retrieval | 2 | 2 | 1 | 1 | MEET-06 |
| hybrid_limitation | 5 | 1 | 1 | 1 | HYB-06 |
| answer_synthesis | 0 | 0 | 0 | 0 | - |
| validation_false_positive | 1 | 1 | 2 | 2 | MEET-07, HYB-10 |
| unsupported_or_ambiguity_handled | 0 | 1 | 1 | 1 | HYB-09 |
| other | 3 | 0 | 2 | 0 | - |
| needs_review | 11 | 11 | 11 | 11 | INV-04, INV-06, PERF-06, PERF-07, MEET-02, MEET-10, HYB-05, ADV-05, ADV-06, ADV-07, ADV-08 |

## 7. Detailed cases: questions that are not correct in V2.1

### MEET-06 (meeting): meeting_retrieval / encoding_and_top_k
- **Question:** Which meetings did Sanjay López attend?
- **Route / mode / status:** meeting / topical / error (validation_failed)
- **Baseline / V1 / V2:** incorrect (ok) / incorrect (error) / incorrect (ok)
- **Why:** Expected meetings in the evidence stay at 5 of 14: the 5 whose summaries carry the name (the other 9 have it only in the attendees field, which is not embedded). The answer says attendance cannot be established from 'noted by' lines and was also withheld by validation for '50' (from a retriever note, 'only the top 50 of 627 are returned', whose number the frozen validator does not count as evidence). Even if delivered it would not list the 14.
- **Secondary:** evidence 15 of 20 candidates: 12 lexical matches and 3 semantic-only hits in the top cluster (348, 1240, 19916, all expected)

### MEET-07 (meeting): validation_false_positive / derived_unmatched_count_not_grounded_in_fused_mode
- **Question:** In client A12346's meetings, which ones flag the absence of independent directors on the board?
- **Route / mode / status:** meeting / topical / error (validation_failed)
- **Baseline / V1 / V2:** incorrect (error) / correct (ok) / incorrect (ok)
- **Why:** Evidence selection worked (20 candidates, 6 evidence: exactly the 6 expected meetings) and the answer names exactly those six. It was withheld for the number 17 ('the remaining 17 meetings ... had no text match', i.e. 23 in scope minus 6). V1 showed the model that count and grounded it; in fused mode (rank_method 'rrf') the unmatched count is not computed or shown, so the model derived it and the frozen validator rejected the derived number. This gap exists in V2 as well; V2's answer happened not to state it.
- **Secondary:** V2 and V2.1 evidence: the same 6 meetings, no filler; V2 (which passed 20 hits) was scored incorrect for a cited extra meeting, V2.1 is withheld for a derived count; unmatched count is computed and shown only in lexical (bm25) mode

### HYB-06 (hybrid): hybrid_limitation / cross_source_date_dependency
- **Question:** How many of client A12345's meetings took place after its latest performance snapshot, and what are the two dates?
- **Route / mode / status:** hybrid / aggregate / ok
- **Baseline / V1 / V2:** incorrect (ok) / incorrect (unsupported) / incorrect (unsupported)
- **Why:** The aggregate step returned only the latest meeting date (2026-03-13) and the answer states that the count of meetings after the snapshot and the second date are not available. No count is produced; the sides are independent. (V1 and V2 reported the dependency as unsupported; the LLM-generated SQL differed between runs on a path V2.1 does not change.)

### HYB-09 (hybrid): unsupported_or_ambiguity_handled / date_relation_unsupported
- **Question:** Which clients with a latest CI Total MOIC below 1.0x had a meeting since 2026-01-01 where it came up that nobody in the company holds a permanent finance-chief role?
- **Route / mode / status:** hybrid / unsupported / unsupported (date_relation_unsupported)
- **Baseline / V1 / V2:** incorrect (ok) / incorrect (unsupported) / incorrect (unsupported)
- **Why:** Unchanged: 'since 2026-01-01' is a date relation, unsupported by the approved rule; nothing was searched.

### HYB-10 (hybrid): validation_false_positive / row_count_of_one_side_not_grounded
- **Question:** For client D12376, list the deal names in its investment records and the sectors and companies in its meetings.
- **Route / mode / status:** hybrid / aggregate / error (validation_failed)
- **Baseline / V1 / V2:** incorrect (ok) / incorrect (error) / incorrect (error)
- **Why:** Unchanged from V1 and V2: both sides are correct but the answer states '16' (the aggregate's row count), which the validation wrapper does not ground because it merges the two sides' rows.
- **Secondary:** evidence-level correct; the answer text was correct and complete but withheld

### Needs review (not scored)

| ID | Status (V2 to V2.1) | V2.1 observation (facts only) |
| --- | --- | --- |
| INV-04 | ok | All 5 RMs and counts are given, but the answer says the result has no client_id column and does not show the counts relate to B12346, although the SQL was filtered to B12346 (the synthesizer is not shown the query). Same pattern as Baseline, V1 and V2. |
| INV-06 | ok | 192 clients with a Closed status is reported; the answer says the evidence does not say how clients with several records were treated (the SQL counted distinct clients); 12,488 is not reported. |
| PERF-06 | ok | Average 1.78125 within tolerance; the number of snapshots and the span are not stated (same as Baseline, V1 and V2). |
| PERF-07 | ok | The 8 values are listed in date order and described as moving up and down with no steady improvement; whether that meets the rule needs review. |
| MEET-02 | ok | Answer says action items are not recorded for meeting 4560 (listing path with an exact-date filter; no embedding call). |
| MEET-10 | ok | Meeting 5506 is retrieved at rank 1 and named in the answer as the match. The answer also cites meeting 11756 (rank 2) and states it is NOT a match (+/-183 bps, not 128). Automated citation-set equality would score this wrongly, so it is left for review. |
| HYB-05 | ok | An answer was delivered: no performance data for B12463, meeting 706 on 2026-01-12 with its recorded action items (newest-first listing, no embedding call). |
| ADV-05 | ok | Per-currency totals, no mixed-currency sum (same as Baseline, V1 and V2); a single USD total is not offered. |
| ADV-06 | clarification | Unchanged: clarification 'no_terms' (no searchable terms, no entity or date filter); no reference date, window or answer. |
| ADV-07 | clarification | Unchanged: the email is treated as an unknown entity (entity_not_found); no explanation that the email field is unreliable. |
| ADV-08 | unsupported | Status unsupported_question; message: 'Field definitions are documentation, not queryable data; schema holds only values.' The message states that no definition is stored; whether that meets the rule is for review. |

## 8. Semantic-stress results

| ID | V1 | V2 | V2.1 |
| --- | --- | --- | --- |
| MEET-08 | 3/3 at lexical ranks 4, 8, 12; the expected sentence not shown; answer: concern not shown | 3/3 at semantic rank 1; sentence shown; **correct** | 3/3 in the evidence (15 lexical evidence hits of 18 candidates) at fused ranks 2, 4, 6, semantic rank 1 (score 0.788), lexical ranks 4, 8, 12; cited ids equal the expected three; **correct** |
| HYB-09 | refused (date relation) | refused | refused: retrieval and selection not exercised |
| ADV-10 | 3/3 at ranks 1-3 on the shared word `local`; correct | same 3 plus 17 semantic-only meetings; a non-match cited; **incorrect** | evidence 3 of 20 candidates (the three lexical matches, all in the top semantic cluster); the answer cites exactly the expected three; **correct** |

MEET-08 remains a semantic recovery in V2.1: the three expected meetings are in the semantic top cluster (rank 1, 0.788, against the next cluster at 0.761) and were retrieved because of it. In V2.1 they are evidence because they also carry lexical ranks (on generic words), so the cluster clause of the selection rule was not needed for them; the answer again names exactly the three meetings and shows the matched sentence.

## 9. Comparison

### Movement per question (V2 to V2.1)

| ID | V2 | V2.1 | Path |
| --- | --- | --- | --- |
| MEET-05 | incorrect (error) | correct (ok) | path not changed by V2.1 (LLM variance) |
| MEET-06 | incorrect (ok) | incorrect (error) | evidence selection (V2.1-changed path) |
| MEET-07 | incorrect (ok) | incorrect (error) | evidence selection (V2.1-changed path) |
| HYB-06 | incorrect (unsupported) | incorrect (ok) | path not changed by V2.1 (LLM variance) |
| ADV-10 | incorrect (ok) | correct (ok) | evidence selection (V2.1-changed path) |

### Counts
Correct 18 (Baseline), 24 (V1), 22 (V2), **24 (V2.1)**; incorrect 11, 5, 7, **5**; needs review 11, 11, 11, **11**. Change in correct by category, V2 to V2.1: Investment 0, Performance 0, Meeting +1, Hybrid 0, Adversarial / edge / semantic +1; V1 to V2.1: Investment 0, Performance 0, Meeting 0, Hybrid 0, Adversarial / edge / semantic 0.

### Attribution of the V2 to V2.1 differences
| Question | Change | Attribution |
|---|---|---|
| ADV-10 | incorrect to correct | evidence selection: the 17 filler candidates no longer reach the synthesizer, and the answer cites exactly the expected three |
| MEET-07 | incorrect to incorrect | evidence selection worked (evidence = exactly the 6 expected; the extra cited meeting is gone); the answer is now withheld for the derived count 17, an issue in fused mode that V2's answer avoided by wording (see below) |
| MEET-06 | incorrect to incorrect; status ok to error | evidence 15 of 20 candidates, expected 5 of 14 as in V2; the answer was withheld for the number 50 taken from a retriever note; the answer would not list the 14 either way |
| MEET-05 | incorrect (error) to correct | a path V2.1 does not change; LLM wording variance (V2's answer was withheld for an 'unavailable' phrase; this run's answer was not) |
| HYB-06 | incorrect to incorrect; status unsupported to ok | a path V2.1 does not change; the generated SQL differed between runs (V2.1's returned only the latest meeting date), and no count is produced |

### Findings that the selection change made visible
- **Fused mode does not show or compute the unmatched count.** V1 showed "in scope without a text match: 17" in lexical mode, and its validation wrapper grounded that number. Under RRF (`rank_method = rrf`) it is neither shown nor grounded, so when the model derives "the remaining 17 meetings" (MEET-07) the frozen validator withholds the answer. This gap is in V2 too; V2's MEET-07 answer did not state the number, and it was previously hidden by the other failure.
- **Validator and notes:** a number that appears only in a retriever note (MEET-06: "top 50 of 627") is not grounded by the frozen validator.

### Remaining failure clusters
| Cluster | Questions | V2.1 measurement |
|---|---|---|
| Answers withheld by the validation wrapper or the frozen validator | MEET-07 (derived 17), HYB-10 (`16`), MEET-06 (`50`) | 3 withheld (V2: MEET-05, HYB-10; V1: MEET-06, HYB-05, HYB-10; Baseline: MEET-07, HYB-01) |
| Encoding and top-K | MEET-06 | expected evidence 5 of 14 |
| Date relations refused by rule | HYB-09 | unchanged |
| Cross-source date dependency | HYB-06 | no count produced |
| Synthesis statements about the query | INV-04, MEET-04 (score unaffected) | unchanged |
| Rule-based questions with no answer | ADV-06, ADV-07; PERF-06 | unchanged |

- **Questions correct in Baseline but not in V2.1:** none.

## 10. Integrity and limitations

- All 32 Python source files under `src/` were checksummed (SHA-256) before the run and again after it: **identical**. No file under `tests/` is newer than the runner, the Baseline files still match the `baseline-v0.1` tag, the embedding index checksum is valid, the earlier V1 and V2 results and the V2 diagnosis were not touched, and the holdout was not read.
- Observed limits: the selection rule is only as good as the top semantic cluster (a target below it that has no lexical rank would not reach the evidence; not observed in this run); evidence can be smaller than 20; the derived-count gap in fused mode above; one run per question, so MEET-05 and HYB-06 movements show the size of LLM variance on paths V2.1 does not change.
- Latency: mean 9.5 s, median 11.2 s, max 19.6 s (HYB-06).
