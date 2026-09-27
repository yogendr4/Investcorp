# V2 Results (visible benchmark, 40 questions)

Measurement of the frozen V2 (`src/v2/`: lexical FTS5/BM25 + Gemini Embedding 2 semantic retrieval + reciprocal rank fusion for topical meeting questions; design in [`v2_semantic_retrieval.md`](../architecture/v2_semantic_retrieval.md)) on the 40 visible questions of `tests/evaluation/questions.json`, compared directly with the stored Baseline ([`baseline_results.*`](baseline_results.md)) and V1 ([`v1_results.*`](v1_results.md)) results, both unchanged. Raw data: [`v2_results.json`](v2_results.json). This document reports measured changes and attributed causes only; it does not rate any system.

## 1. Evaluation setup

- **Code:** V2 exactly as implemented and documented, including the negation-preserving semantic query text. No source, test, prompt, routing, retrieval, fusion, validation, benchmark or scoring change was made during or after the run, and nothing was tuned after a failure. One run, the real Claude CLI (`claude-sonnet-5`, effort low) and real Vertex AI query embeddings, with `V2Service` in place of `V1Service` in the same runner. The embedding index (26,407 sentence vectors, 128 dimensions) was built before the run and not rebuilt.
- **Questions:** the 40 visible questions only. The holdout file was not opened or read; no holdout content appears here.
- **Scoring:** the same frozen rules and the same evaluators as the V1 measurement (the evaluators added in that measurement for aggregate evidence are unchanged), no LLM judge, **MEET-10 stays `needs_review`**, rule-based questions stay `needs_review`. Id-set questions require the cited meeting ids to equal the expected set. That rule was not changed after seeing any V2 answer.
- **Failure-mode labels** are analyst judgements assigned after scoring, each with its reason in the JSON.
- **What V2 changes:** only topical meeting retrieval (a topical question triggers one query embedding) and, through the same service, the synthesis prompt (a header and one extra rule for fused evidence). Listing, aggregates, structured queries, date-relation refusal, entity resolution and routing are unchanged. In this run five questions were topical meeting questions: MEET-06, MEET-07, MEET-08, MEET-10, ADV-10 (five query embeddings, all `status ok`).
- **Caveats:** one run of a non-deterministic LLM per question, so a change on a path V2 does not alter (MEET-05, HYB-05) cannot be attributed to V2. Latency includes Claude CLI start-up and, for the first topical question, the gcloud token fetch. Post-hoc scoring concerns (`v1_diagnosis.md` section 4.7) are unchanged and documented separately; they are not applied here.

## 2. Aggregate results

| Measure | Baseline | V1 | V2 | V2 vs V1 | V2 vs Baseline |
| --- | --- | --- | --- | --- | --- |
| Questions | 40 | 40 | 40 | 0 | 0 |
| Correct (auto-scored) | 18 | 24 | 22 | -2 | +4 |
| Incorrect (auto-scored) | 11 | 5 | 7 | +2 | -4 |
| Needs review | 11 | 11 | 11 | 0 | 0 |
| Status `ok` | 31 | 28 | 29 | +1 | -2 |
| Status `clarification` | 4 | 4 | 4 | 0 | 0 |
| Status `unsupported` | 3 | 5 | 5 | 0 | +2 |
| Status `error` (all `validation_failed`) | 2 | 3 | 2 | -1 | 0 |

All 40 questions returned a response. Correct in Baseline but not in V2: ADV-10. Correct in V1 but not in V2: MEET-05, MEET-07, ADV-10. Correct in V2 but not in V1: MEET-08.

## 3. Results by category

| Category | Baseline (c / i / r) | V1 (c / i / r) | V2 (c / i / r) | V2 correct vs V1 | V2 status: ok / clar. / unsupp. / error |
| --- | --- | --- | --- | --- | --- |
| Investment | 6 / 0 / 2 | 6 / 0 / 2 | 6 / 0 / 2 | 0 | 8 / 0 / 0 / 0 |
| Performance | 6 / 0 / 2 | 6 / 0 / 2 | 6 / 0 / 2 | 0 | 7 / 0 / 1 / 0 |
| Meeting | 0 / 6 / 2 | 4 / 2 / 2 | 3 / 3 / 2 | -1 | 7 / 0 / 0 / 1 |
| Hybrid | 2 / 5 / 1 | 4 / 3 / 1 | 4 / 3 / 1 | 0 | 4 / 0 / 3 / 1 |
| Adversarial / edge / semantic | 4 / 0 / 4 | 4 / 0 / 4 | 3 / 1 / 4 | -1 | 3 / 4 / 1 / 0 |

| Category | V2 correct | V2 incorrect | V2 needs review |
| --- | --- | --- | --- |
| Investment | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10 | - | INV-04, INV-06 |
| Performance | PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10 | - | PERF-06, PERF-07 |
| Meeting | MEET-01, MEET-04, MEET-08 | MEET-05, MEET-06, MEET-07 | MEET-02, MEET-10 |
| Hybrid | HYB-01, HYB-02, HYB-03, HYB-07 | HYB-06, HYB-09, HYB-10 | HYB-05 |
| Adversarial / edge / semantic | ADV-01, ADV-02, ADV-03 | ADV-10 | ADV-05, ADV-06, ADV-07, ADV-08 |

### All 40 questions (Baseline / V1 / V2)

| ID | Route | V2 meeting mode | Result: Baseline / V1 / V2 | V2 status | V2 primary failure | V2 latency |
| --- | --- | --- | --- | --- | --- | --- |
| INV-01 | investment | - | correct / correct / correct | ok | - | 18.2 s |
| INV-02 | investment | - | correct / correct / correct | ok | - | 13.9 s |
| INV-03 | investment | - | correct / correct / correct | ok | - | 12.8 s |
| INV-04 | investment | - | review / review / review | ok | - | 14.0 s |
| INV-06 | investment | - | review / review / review | ok | - | 13.2 s |
| INV-07 | investment | - | correct / correct / correct | ok | - | 12.1 s |
| INV-08 | investment | - | correct / correct / correct | ok | - | 12.2 s |
| INV-10 | investment | - | correct / correct / correct | ok | - | 12.5 s |
| PERF-01 | performance | - | correct / correct / correct | ok | - | 11.8 s |
| PERF-02 | performance | - | correct / correct / correct | ok | - | 13.0 s |
| PERF-04 | performance | - | correct / correct / correct | ok | - | 12.5 s |
| PERF-05 | performance | - | correct / correct / correct | unsupported | - | 1 ms |
| PERF-06 | performance | - | review / review / review | ok | - | 11.8 s |
| PERF-07 | performance | - | review / review / review | ok | - | 34.8 s |
| PERF-08 | performance | - | correct / correct / correct | ok | - | 11.6 s |
| PERF-10 | performance | - | correct / correct / correct | ok | - | 12.1 s |
| MEET-01 | meeting | aggregate | incorrect / correct / correct | ok | - | 12.2 s |
| MEET-02 | meeting | listing | review / review / review | ok | - | 10.4 s |
| MEET-04 | meeting | aggregate | incorrect / correct / correct | ok | - | 13.4 s |
| MEET-05 | meeting | aggregate | incorrect / correct / **incorrect** | ok to **error** | validation_false_positive | 12.2 s |
| MEET-06 | meeting | topical (fused) | incorrect / incorrect / incorrect | error to **ok** | meeting_retrieval | 14.5 s |
| MEET-07 | meeting | topical (fused) | incorrect / correct / **incorrect** | ok | other | 9.7 s |
| MEET-08 | meeting | topical (fused) | incorrect / incorrect / **correct** | ok | - | 9.6 s |
| MEET-10 | meeting | topical (fused) | review / review / review | ok | - | 8.8 s |
| HYB-01 | hybrid | listing | incorrect / correct / correct | ok | - | 12.6 s |
| HYB-02 | hybrid | aggregate | correct / correct / correct | ok | - | 18.0 s |
| HYB-03 | hybrid | listing | incorrect / correct / correct | ok | - | 13.1 s |
| HYB-05 | hybrid | listing | review / review / review | error to **ok** | - | 6.8 s |
| HYB-06 | hybrid | aggregate | incorrect / incorrect / incorrect | unsupported | hybrid_limitation | 14.9 s |
| HYB-07 | ambiguous | - | correct / correct / correct | unsupported | - | 1 ms |
| HYB-09 | hybrid | unsupported | incorrect / incorrect / incorrect | unsupported | unsupported_or_ambiguity_handled | 1 ms |
| HYB-10 | hybrid | aggregate | incorrect / incorrect / incorrect | error | validation_false_positive | 19.9 s |
| ADV-01 | investment | - | correct / correct / correct | clarification | - | 1 ms |
| ADV-02 | investment | - | correct / correct / correct | ok | - | 11.6 s |
| ADV-03 | ambiguous | - | correct / correct / correct | clarification | - | 0 ms |
| ADV-05 | investment | - | review / review / review | ok | - | 13.3 s |
| ADV-06 | meeting | listing | review / review / review | clarification | - | 1 ms |
| ADV-07 | investment | - | review / review / review | clarification | - | 0 ms |
| ADV-08 | performance | - | review / review / review | unsupported | - | 5.6 s |
| ADV-10 | meeting | topical (fused) | correct / correct / **incorrect** | ok | other | 10.0 s |

## 4. Component-level results

| Component | Baseline | V1 | V2 | Notes |
|---|---|---|---|---|
| Entity resolution | 28/28 | 28/28 | 28/28 | frozen |
| Routing | 40/40 | 40/40 | 40/40 | frozen |
| Structured query components correct | 18/18 | 17/18 | 17/18 | HYB-09 is refused before the structured side runs (V1 and V2) |
| Meeting retrieval: all expected ids in the returned list | MEET-02, MEET-07, MEET-08, MEET-10, ADV-10 | MEET-02, MEET-07, MEET-08, MEET-10, HYB-01, HYB-03, HYB-05, ADV-10 | MEET-02, MEET-07, MEET-08, MEET-10, HYB-01, HYB-03, HYB-05, ADV-10 | MEET-06: 1/14 in V1, 5/14 in V2; HYB-09 refused |
| Answer validation | 31/33 passed | 28/31 passed | 29/31 passed | V2 rejected: MEET-05, HYB-10 (V1: MEET-06, HYB-05, HYB-10; Baseline: MEET-07, HYB-01) |
| Validation false positives (analyst) | 1 | 2 | 2 | V2: MEET-05, HYB-10 |
| Semantic retrieval calls | 0 | 0 | 5 (all `ok`) | one query embedding per topical meeting question; median embedding request about 0.9 s (first call of the process about 4.5 s including the token fetch); vector scan 5-30 ms |

### Meeting-side mode and fusion in this run

| ID | Mode | Lexical terms (V1 text) | Semantic query text | Scope | Returned | Semantic candidates |
| --- | --- | --- | --- | --- | --- | --- |
| MEET-06 | topical | sanjay lópez attend | sanjay lópez attend | 20000 | 20 | 53 |
| MEET-07 | topical | absence independent directors board | absence independent directors board | 23 | 20 | 23 |
| MEET-08 | topical | concern full time finance chief relies outside providers cash management | concern no full time finance chief relies outside providers cash management | 18 | 18 | 18 |
| MEET-10 | topical | sensitivity 128 bps change growth | sensitivity 128 bps change growth | 23 | 20 | 23 |
| ADV-10 | topical | part income arrives dollars while costs paid local money | part income arrives dollars while costs paid local money | 23 | 20 | 23 |

Listing (MEET-02, HYB-01, HYB-03, HYB-05, ADV-06), aggregate (MEET-01, MEET-04, MEET-05, HYB-02, HYB-06, HYB-10), refused (HYB-09), structured and clarification questions made no embedding call.

## 5. Latency

| Measure | Baseline | V1 | V2 |
| --- | --- | --- | --- |
| Mean | 9.3 s | 9.2 s | 10.8 s |
| Median | 11.4 s | 11.3 s | 12.1 s |
| Max | 28.4 s (ADV-10) | 18.8 s (HYB-10) | 34.8 s (PERF-07) |

| Route | n | Baseline mean | V1 mean | V2 mean / median / max |
| --- | --- | --- | --- | --- |
| ambiguous | 2 | 0 ms | 1 ms | 1 ms / 1 ms / 1 ms |
| hybrid | 7 | 12.8 s | 11.2 s | 12.2 s / 13.1 s / 19.9 s |
| investment | 12 | 10.5 s | 10.4 s | 11.2 s / 12.7 s / 18.2 s |
| meeting | 10 | 6.8 s | 8.0 s | 10.1 s / 10.2 s / 14.5 s |
| performance | 9 | 9.7 s | 9.5 s | 12.6 s / 11.8 s / 34.8 s |

The V2 maximum (PERF-07, 34.8 s) is a performance-route question that does not use retrieval or embeddings; latency varies widely between runs on the Claude calls. The added V2 cost on a topical meeting question is one embedding request (about 0.9 s) and the scan.

## 6. Failure taxonomy

| Primary failure mode | Baseline | V1 | V2 | V2 IDs |
| --- | --- | --- | --- | --- |
| correct | 18 | 24 | 22 | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10, PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10, MEET-01, MEET-04, MEET-08, HYB-01, HYB-02, HYB-03, HYB-07, ADV-01, ADV-02, ADV-03 |
| entity_resolution | 0 | 0 | 0 | - |
| routing | 0 | 0 | 0 | - |
| sql_generation_or_query_correctness | 0 | 0 | 0 | - |
| meeting_retrieval | 2 | 2 | 1 | MEET-06 |
| hybrid_limitation | 5 | 1 | 1 | HYB-06 |
| answer_synthesis | 0 | 0 | 0 | - |
| validation_false_positive | 1 | 1 | 2 | MEET-05, HYB-10 |
| unsupported_or_ambiguity_handled | 0 | 1 | 1 | HYB-09 |
| other | 3 | 0 | 2 | MEET-07, ADV-10 |
| needs_review | 11 | 11 | 11 | INV-04, INV-06, PERF-06, PERF-07, MEET-02, MEET-10, HYB-05, ADV-05, ADV-06, ADV-07, ADV-08 |

`other` in V2 is the two lexical controls scored incorrect by the frozen cited-id equality rule (subtype `cited_non_match_from_extra_semantic_hits`).

## 7. Detailed cases: questions that are not correct in V2

### MEET-05 (meeting): validation_false_positive / unavailable_phrase_against_evidence
- **Question:** How many meetings are at the series B stage?
- **Route / mode / status:** meeting / aggregate / error (validation_failed)
- **Baseline / V1:** incorrect (ok) / correct (ok)
- **Why:** The aggregate evidence was correct (2,824 series_b meetings) and the answer gave the right number, but it also said the specific meetings and their dates 'are not available'; the frozen validator's contradiction check flags an 'unavailable' phrase while the structured evidence holds values, so the answer was withheld. V2 does not change this path's evidence; V2 changes only the synthesis prompt (one extra rule) and the LLM wording differs between runs, so a V2 effect and run-to-run variance cannot be separated.
- **Secondary:** passed in V1 on the same aggregate evidence; wording differs between runs

### MEET-06 (meeting): meeting_retrieval / encoding_and_top_k
- **Question:** Which meetings did Sanjay López attend?
- **Route / mode / status:** meeting / topical / ok
- **Baseline / V1:** incorrect (ok) / incorrect (error)
- **Why:** Expected meetings in the returned 20 rose from 1 of 14 (V1) to 5 of 14. The semantic list puts the sentence '- noted by Sanjay LÃ³pez.' at semantic rank 1 for several expected meetings (348, 1240, 19916 are in the returned list), but the fused top 20 also holds lexical-only meetings, and the answer states that 'noted by' does not prove attendance. Stored spelling is mojibake; only 20 of the candidates are returned.
- **Secondary:** semantic evidence shows the sentence '- noted by Sanjay LÃ³pez.' for expected meetings 348, 1240, 19916

### MEET-07 (meeting): other / cited_non_match_from_extra_semantic_hits
- **Question:** In client A12346's meetings, which ones flag the absence of independent directors on the board?
- **Route / mode / status:** meeting / topical / ok
- **Baseline / V1:** incorrect (error) / correct (ok)
- **Why:** All six expected meetings are at fused ranks 1-6 and the answer names exactly those six as matches. It also mentions meeting 4638 (a semantic-only hit at rank 20) and says it does not mention independent directors. The frozen scoring rule (cited ids must equal the expected set) therefore scores it incorrect; the answer's own statement is that 4638 is not a match. Same pattern as MEET-10, which stays needs_review under the frozen rule.
- **Secondary:** V1's evidence held only the 6 lexical matches; V2 always fills K=20, adding 14 semantic-only meetings that the model then discusses

### HYB-06 (hybrid): hybrid_limitation / cross_source_date_dependency
- **Question:** How many of client A12345's meetings took place after its latest performance snapshot, and what are the two dates?
- **Route / mode / status:** hybrid / aggregate / unsupported (hybrid_partial_failure)
- **Baseline / V1:** incorrect (ok) / incorrect (unsupported)
- **Why:** Unchanged from V1: the aggregate step reports that the snapshot date is not in meetings_meta and the sides are independent, so no count is produced.
- **Secondary:** no count is produced (same as V1)

### HYB-09 (hybrid): unsupported_or_ambiguity_handled / date_relation_unsupported
- **Question:** Which clients with a latest CI Total MOIC below 1.0x had a meeting since 2026-01-01 where it came up that nobody in the company holds a permanent finance-chief role?
- **Route / mode / status:** hybrid / unsupported / unsupported (date_relation_unsupported)
- **Baseline / V1:** incorrect (ok) / incorrect (unsupported)
- **Why:** Unchanged from V1: 'since 2026-01-01' is a date relation, unsupported by the approved rule; nothing was searched, so V2 semantic retrieval was not exercised for this question.

### HYB-10 (hybrid): validation_false_positive / row_count_of_one_side_not_grounded
- **Question:** For client D12376, list the deal names in its investment records and the sectors and companies in its meetings.
- **Route / mode / status:** hybrid / aggregate / error (validation_failed)
- **Baseline / V1:** incorrect (ok) / incorrect (error)
- **Why:** Unchanged from V1: both sides are correct but the answer states '16' (the aggregate's row count), which the validation wrapper does not ground because it merges the two sides' rows.
- **Secondary:** evidence-level correct; the answer text was correct and complete but withheld

### ADV-10 (adversarial_edge_semantic): other / cited_non_match_from_extra_semantic_hits
- **Question:** For client A12353, which meetings raised that part of the income arrives in dollars while costs are paid in local money?
- **Route / mode / status:** meeting / topical / ok
- **Baseline / V1:** correct (ok) / correct (ok)
- **Why:** The three expected meetings are at fused ranks 1-3 and are named as matches. The answer also mentions meeting 11707 (a semantic-only hit at rank 20) and says it is a different topic. The frozen cited-id equality rule scores it incorrect; the answer's own statement is that 11707 is not a match.
- **Secondary:** V2 adds 17 semantic-only meetings to the 3 lexical matches (K=20 is always filled)

### Needs review (not scored)

| ID | Status (V1 to V2) | V2 observation (facts only) |
| --- | --- | --- |
| INV-04 | ok | All 5 RMs and counts are given, but the answer says the result has no client_id column and does not show the counts relate to B12346, although the SQL was filtered to B12346 (the synthesizer is not shown the query). Same pattern as Baseline and V1. |
| INV-06 | ok | 192 clients with a Closed status is reported with a record-level caution; the answer says the evidence does not show whether distinct clients or records were counted (the SQL counted distinct clients); 12,488 is not reported. |
| PERF-06 | ok | Average 1.78125 within tolerance; the answer says the number of snapshots is not shown, so the 8-snapshot span is not stated (same as Baseline and V1). |
| PERF-07 | ok | The 8 values are listed in date order and described as rising and falling ('not consistently'); whether that meets the rule needs review. |
| MEET-02 | ok | Answer says action items are not recorded for meeting 4560 (listing path with an exact-date filter; no embedding call). |
| MEET-10 | ok | Meeting 5506 is retrieved at rank 1 and named in the answer as the match. The answer also cites meeting 11756 (rank 2) and states it is NOT a match (+/-183 bps, not 128). Automated citation-set equality would score this wrongly, so it is left for review. |
| HYB-05 | error to ok | An answer was delivered: no performance data for B12463 (stated without a [performance] citation), meeting 706 on 2026-01-12 with both recorded action items (newest-first listing, no embedding call). In V1 this answer was withheld for citing [performance]; the paths involved are unchanged by V2, so the difference is LLM wording. |
| ADV-05 | ok | Per-currency totals, no mixed-currency sum (same as Baseline and V1); a single USD total is not offered. |
| ADV-06 | clarification | Unchanged: clarification 'no_terms' (no searchable terms, no entity or date filter); no reference date, window or answer. |
| ADV-07 | clarification | Unchanged: the email is treated as an unknown entity (entity_not_found); no explanation that the email field is unreliable. |
| ADV-08 | unsupported | Status unsupported_question; message: 'Asks for a field definition; the data holds values, not documentation of column meanings.' The message states the meaning is not documented; whether that meets the rule is for review. |

## 8. Semantic-stress results

| ID | Baseline | V1 | V2 |
| --- | --- | --- | --- |
| MEET-08 | 3/3 expected in the returned 15 at ranks 4, 8, 12 (generic matched words); answer: concern not shown | unchanged: same ranks; answer: concern not shown | 3/3 at fused ranks 2 (id 5791), 4 (id 3664), 6 (id 9932); all three have **semantic rank 1** (score 0.788) and lexical ranks 4, 8, 12; the matched sentence was shown to the model; cited ids equal the expected three; **correct** |
| HYB-09 | 0 of 31 expected meetings; exact-date filter | refused (`date_relation_unsupported`) | refused (`date_relation_unsupported`): retrieval and the semantic side were not exercised |
| ADV-10 | 3/3 at ranks 1-3 on the shared word `local` | 3/3 at ranks 1-3 | 3/3 at fused ranks 1-3 (lexical 1-3, semantic rank 1 for all three, score 0.801); 17 semantic-only meetings fill ranks 4-20; the answer also mentions meeting 11707 as a different topic; scored **incorrect** by the frozen cited-id rule |

**Does MEET-08 remain semantic?** After V2 it is retrieved *because of* the semantic list: the question and the evidence sentence share no content word (the semantic query text was `concern no full time finance chief relies outside providers cash management`, with the negation kept), the three expected meetings have semantic rank 1 (the sentence ranks above the generic 'cash / management' sentences that dominated the lexical list, 0.788 against 0.761), and the sentence itself reached the synthesizer, whose answer named exactly the three meetings. As in V1 and Baseline, the client's scope (18 meetings) is smaller than K (20), so presence in the list is not selective; the rank, the shown sentence and the cited ids are the evidence. HYB-09 gives no retrieval evidence because the approved date-relation rule refuses it. ADV-10 still succeeds on lexical overlap, with the semantic list agreeing.

## 9. Comparison

### Movement per question (V1 to V2)

| ID | V1 | V2 | Path |
| --- | --- | --- | --- |
| MEET-05 | correct (ok) | incorrect (error) | path not changed by V2 (LLM wording) |
| MEET-06 | incorrect (error) | incorrect (ok) | topical retrieval (V2-changed path) |
| MEET-07 | correct (ok) | incorrect (ok) | topical retrieval (V2-changed path) |
| MEET-08 | incorrect (ok) | correct (ok) | topical retrieval (V2-changed path) |
| HYB-05 | needs_review (error) | needs_review (ok) | path not changed by V2 (LLM wording) |
| ADV-10 | correct (ok) | incorrect (ok) | topical retrieval (V2-changed path) |

### Counts
Correct 18 (Baseline) to 24 (V1) to 22 (V2); incorrect 11 to 5 to 7; needs review 11 to 11 to 11. Change in correct by category, V1 to V2: Investment 0, Performance 0, Meeting -1, Hybrid 0, Adversarial / edge / semantic -1; Baseline to V2: Investment 0, Performance 0, Meeting +3, Hybrid +2, Adversarial / edge / semantic -1.

### Failure clusters that disappeared (V2 against V1)
- **MEET-08 (semantic vocabulary distance):** now correct, with the expected sentence retrieved by meaning and shown to the model.

### New or changed outcomes
| Question | Measured change | Attribution |
|---|---|---|
| MEET-07, ADV-10 | correct to incorrect under the frozen scoring: the evidence still ranks all expected meetings first (6 of 6 at ranks 1-6; 3 of 3 at ranks 1-3), but V2 always returns 20 hits, so 14 and 17 semantic-only meetings follow them; each answer names the expected meetings and also mentions one extra meeting (4638, 11707) to say it is not a match, and the frozen cited-id equality rule scores that incorrect | V2 evidence volume interacting with the frozen scoring rule (same pattern as MEET-10, which the rules keep as needs_review). Documented as a scoring concern, not re-scored |
| MEET-05 | correct to incorrect (status error): the aggregate evidence was correct; the answer also said the meetings and dates 'are not available' and the validator's contradiction check withheld it | a path V2 does not change (aggregate evidence identical); the only V2 difference is the extra synthesis-prompt rule, so V2 effect and run-to-run wording variance cannot be separated |
| MEET-06 | still incorrect; expected meetings in the returned list 1 of 14 (V1) to 5 of 14; status error (V1) to ok | V2 retrieval (semantic list) and answer wording; the answer says attendance cannot be confirmed from 'noted by' |
| HYB-05 (needs review) | status error to ok: an answer with meeting 706 and both action items was delivered | a path V2 does not change (listing); LLM wording |

### Remaining failure clusters
| Cluster | Questions | V2 measurement |
|---|---|---|
| Date relations refused by rule | HYB-09 | unchanged |
| Cross-source date dependency | HYB-06 | unchanged |
| Answers withheld by the validation wrapper | HYB-10 (`16`), MEET-05 (an 'unavailable' phrase) | HYB-10 unchanged; MEET-05 new in V2 (see above) |
| Encoding and top-K | MEET-06 | expected evidence in the returned list 5 of 14 |
| Cited non-match under the frozen rule | MEET-07, ADV-10 (MEET-10 needs review) | new for MEET-07 and ADV-10 |
| Synthesis statements about the query | INV-04, MEET-04 (score unaffected) | unchanged |
| Rule-based questions with no answer | ADV-06, ADV-07 | unchanged |

- **Questions correct in Baseline but not in V2:** none.

## 10. Observed limitations

- Fusion always fills K = 20: for questions with few true matches (MEET-07, ADV-10, MEET-10) the evidence now includes many semantic-only neighbours, and the synthesizer mentions some of them.
- Repeated template sentences create semantic ties; MEET-06's expected meetings (14, one shared sentence) compete with lexical-only candidates for the 20 returned slots.
- One run per question; MEET-05 and HYB-05 changed on paths V2 does not alter, which shows the size of run-to-run wording variance on the LLM stages.
- The validation wrapper and the frozen validator withheld correct content for HYB-10 and MEET-05; no validation logic was changed.
- Statements about the query remain unreliable when the synthesizer is not shown the SQL (INV-04, MEET-04).
- Rule-based adversarial questions ADV-06 and ADV-07 still return no stated behavior; PERF-06 still lacks the snapshot count and span.
- Semantic retrieval depends on a network call to Vertex per topical question; all five calls in this run succeeded.
