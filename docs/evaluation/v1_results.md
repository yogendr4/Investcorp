# V1 Results (visible benchmark, 40 questions)

Measurement of the frozen V1 (`src/v1/`, contract [`v1_contract.md`](../architecture/v1_contract.md) v0.2) on the 40 visible questions of `tests/evaluation/questions.json`, compared with the stored Baseline results ([`baseline_results.md`](baseline_results.md) / [`.json`](baseline_results.json), unchanged). Raw data: [`v1_results.json`](v1_results.json). This document reports measured changes and attributed causes only; it does not rate either system.

## 1. Evaluation setup

- **Code:** V1 exactly as implemented and documented (Baseline tag `baseline-v0.1` untouched). No source, test, prompt, routing, retrieval, validation, benchmark or architecture change was made during or after the run, and nothing was tuned after a failure. One run, real Claude CLI (`claude-sonnet-5`, effort low), the same runner as the Baseline measurement with `V1Service` in place of `BaselineService`.
- **Questions:** the 40 visible questions only. The holdout file was not opened or read; no holdout content appears here.
- **Scoring:** the frozen Baseline rules, no LLM judge. Exact-answer questions are scored on the returned evidence (structured rows, aggregate rows, meeting hits) and require a final status of `ok` (or the expected refusal/clarification); id-set questions also require the cited ids to equal the expected set; rule-based questions are `needs_review`; **MEET-10 stays `needs_review`**.
- **Scorer additions, applied to both systems.** `v1_contract.md` section 7.3 specified, before implementation, evaluators that read aggregate evidence for MEET-01 (22, 2022-02-20, 2026-01-12), MEET-04 (meeting 17166 or 150,000,000), MEET-05 (2,824) and HYB-10 (the expected sectors and companies; the Baseline evaluator only required some meeting hits). They were coded while the V1 run was executing and **before any V1 result was inspected**. One further extension was not listed in the contract: HYB-02 accepts the count 22 from aggregate evidence as well as from the scope count (the contract names HYB-02 as a risk of the aggregate path). It was also decided before results were inspected. **Check:** re-scoring the stored Baseline raw responses with these evaluators gives the same 18 correct / 11 incorrect / 11 needs-review with identical per-question outcomes, so the two runs are directly comparable.
- **Failure-mode labels** are analyst judgements assigned after scoring (as in the Baseline measurement); each carries its reason in the JSON. The V1 manual validation had already led to one synthesis-rule clarification (`v1_contract.md` section 11.2); it is part of the V1 measured here. The known false-claim pattern on `LIMIT 1` queries was not fixed.
- **Caveats:** one run of a non-deterministic LLM per question; latency includes Claude CLI start-up. Post-hoc scoring concerns from `v1_diagnosis.md` section 4.7 (ADV-01/03 display rule, MEET-08 scope less than K, validator-withheld answers) are unchanged and documented separately; they are not applied here.

## 2. Aggregate results

| Measure | Baseline | V1 | Change |
| --- | --- | --- | --- |
| Questions | 40 | 40 | 0 |
| Correct (auto-scored) | 18 | 24 | +6 |
| Incorrect (auto-scored) | 11 | 5 | -6 |
| Needs review | 11 | 11 | 0 |
| Status `ok` | 31 | 28 | -3 |
| Status `clarification` | 4 | 4 | 0 |
| Status `unsupported` | 3 | 5 | +2 |
| Status `error` (all `validation_failed`) | 2 | 3 | +1 |

All 40 questions returned a response. Among the 18 questions correct in Baseline, **0 are not correct in V1**. Auto-scored questions: 29 of 40 (Baseline 29), of which 24 correct (Baseline 18).

## 3. Results by category

| Category | Baseline correct / incorrect / review | V1 correct / incorrect / review | Change in correct | V1 status: ok / clar. / unsupp. / error |
| --- | --- | --- | --- | --- |
| Investment | 6 / 0 / 2 | 6 / 0 / 2 | 0 | 8 / 0 / 0 / 0 |
| Performance | 6 / 0 / 2 | 6 / 0 / 2 | 0 | 7 / 0 / 1 / 0 |
| Meeting | 0 / 6 / 2 | 4 / 2 / 2 | +4 | 7 / 0 / 0 / 1 |
| Hybrid | 2 / 5 / 1 | 4 / 3 / 1 | +2 | 3 / 0 / 3 / 2 |
| Adversarial / edge / semantic | 4 / 0 / 4 | 4 / 0 / 4 | 0 | 3 / 4 / 1 / 0 |

| Category | V1 correct | V1 incorrect | V1 needs review |
| --- | --- | --- | --- |
| Investment | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10 | - | INV-04, INV-06 |
| Performance | PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10 | - | PERF-06, PERF-07 |
| Meeting | MEET-01, MEET-04, MEET-05, MEET-07 | MEET-06, MEET-08 | MEET-02, MEET-10 |
| Hybrid | HYB-01, HYB-02, HYB-03, HYB-07 | HYB-06, HYB-09, HYB-10 | HYB-05 |
| Adversarial / edge / semantic | ADV-01, ADV-02, ADV-03, ADV-10 | - | ADV-05, ADV-06, ADV-07, ADV-08 |

### All 40 questions (Baseline to V1)

| ID | Cat | Route | V1 meeting mode | Status (B to V1) | Result (B to V1) | V1 primary failure | V1 latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-01 | investment | investment | - | ok | correct | - | 14.8 s |
| INV-02 | investment | investment | - | ok | correct | - | 13.0 s |
| INV-03 | investment | investment | - | ok | correct | - | 12.2 s |
| INV-04 | investment | investment | - | ok | needs_review | - | 12.5 s |
| INV-06 | investment | investment | - | ok | needs_review | - | 13.0 s |
| INV-07 | investment | investment | - | ok | correct | - | 11.6 s |
| INV-08 | investment | investment | - | ok | correct | - | 11.5 s |
| INV-10 | investment | investment | - | ok | correct | - | 11.9 s |
| PERF-01 | performance | performance | - | ok | correct | - | 10.5 s |
| PERF-02 | performance | performance | - | ok | correct | - | 11.9 s |
| PERF-04 | performance | performance | - | ok | correct | - | 11.4 s |
| PERF-05 | performance | performance | - | unsupported | correct | - | 1 ms |
| PERF-06 | performance | performance | - | ok | needs_review | - | 11.3 s |
| PERF-07 | performance | performance | - | ok | needs_review | - | 12.7 s |
| PERF-08 | performance | performance | - | ok | correct | - | 11.1 s |
| PERF-10 | performance | performance | - | ok | correct | - | 11.5 s |
| MEET-01 | meeting | meeting | aggregate | ok | incorrect to **correct** | - | 11.8 s |
| MEET-02 | meeting | meeting | listing | ok | needs_review | - | 6.4 s |
| MEET-04 | meeting | meeting | aggregate | ok | incorrect to **correct** | - | 12.5 s |
| MEET-05 | meeting | meeting | aggregate | ok | incorrect to **correct** | - | 11.5 s |
| MEET-06 | meeting | meeting | topical | ok to **error** | incorrect | meeting_retrieval | 7.6 s |
| MEET-07 | meeting | meeting | topical | error to **ok** | incorrect to **correct** | - | 7.8 s |
| MEET-08 | meeting | meeting | topical | ok | incorrect | meeting_retrieval | 8.7 s |
| MEET-10 | meeting | meeting | topical | ok | needs_review | - | 6.7 s |
| HYB-01 | hybrid | hybrid | listing | error to **ok** | incorrect to **correct** | - | 12.1 s |
| HYB-02 | hybrid | hybrid | aggregate | ok | correct | - | 16.9 s |
| HYB-03 | hybrid | hybrid | listing | ok | incorrect to **correct** | - | 13.0 s |
| HYB-05 | hybrid | hybrid | listing | ok to **error** | needs_review | - | 6.7 s |
| HYB-06 | hybrid | hybrid | aggregate | ok to **unsupported** | incorrect | hybrid_limitation | 10.7 s |
| HYB-07 | hybrid | ambiguous | - | unsupported | correct | - | 1 ms |
| HYB-09 | hybrid | hybrid | unsupported | ok to **unsupported** | incorrect | unsupported_or_ambiguity_handled | 1 ms |
| HYB-10 | hybrid | hybrid | aggregate | ok to **error** | incorrect | validation_false_positive | 18.8 s |
| ADV-01 | adv | investment | - | clarification | correct | - | 2 ms |
| ADV-02 | adv | investment | - | ok | correct | - | 11.3 s |
| ADV-03 | adv | ambiguous | - | clarification | correct | - | 0 ms |
| ADV-05 | adv | investment | - | ok | needs_review | - | 12.6 s |
| ADV-06 | adv | meeting | listing | clarification | needs_review | - | 1 ms |
| ADV-07 | adv | investment | - | clarification | needs_review | - | 0 ms |
| ADV-08 | adv | performance | - | unsupported | needs_review | - | 5.2 s |
| ADV-10 | adv | meeting | topical | ok | correct | - | 7.4 s |

## 4. Component-level results

| Component | Baseline | V1 | Notes |
|---|---|---|---|
| Entity resolution | 28/28 | 28/28 | Unchanged (frozen component); 0 failures |
| Routing | 40/40 | 40/40 | Frozen router; route unchanged for all 40 |
| Structured query (SQL result) | 18/18 | 17/18 | The one component not scored correct is `HYB-09`: the structured side did not run because the question was refused for its date relation. All other structured components are correct |
| Meeting retrieval: expected evidence present | in top-K for MEET-02, MEET-07, MEET-08, MEET-10, ADV-10; none for MEET-04, HYB-01, HYB-03, HYB-05, HYB-09 | in top-K for MEET-02, MEET-07, MEET-08, MEET-10, HYB-01, HYB-03, HYB-05, ADV-10; none for HYB-09; partial: MEET-06 (1/14) | MEET-04 is now answered by the aggregate path (no hits expected); HYB-01, HYB-03, HYB-05 have the expected latest meeting at rank 1 |
| Meeting aggregates (new) | not available | MEET-01, MEET-04, MEET-05, HYB-02 exact at evidence level; HYB-10 correct at evidence level; HYB-06 reported unsupported | All aggregate SQL read only `meetings_meta`; no second-source query ran |
| Answer validation | 31/33 passed | 28/31 passed | Rejected: MEET-06, HYB-05, HYB-10 (Baseline: MEET-07, HYB-01) |
| Validation false positives | 1 (MEET-07) | 2 (HYB-10, HYB-05) | Answers whose content matches the expected facts but were withheld; HYB-05 is a needs-review question (analyst reading of the withheld text). MEET-06 was also withheld, but its content was incomplete regardless |

### Meeting-side mode classification (contract 5.4)

| ID | Question type | Mode chosen | Cue or reason |
| --- | --- | --- | --- |
| MEET-01 | meeting | aggregate | no topical term; aggregate cue(s): how_many, date_range |
| MEET-02 | meeting | listing | no topical term; record-lookup cue(s): action_items |
| MEET-04 | meeting | aggregate | no topical term; aggregate cue(s): extreme_deal_size |
| MEET-05 | meeting | aggregate | no topical term; aggregate cue(s): how_many |
| MEET-06 | meeting | topical | topical terms remain after removing request and field words |
| MEET-07 | meeting | topical | topical terms remain after removing request and field words |
| MEET-08 | meeting | topical | topical terms remain after removing request and field words |
| MEET-10 | meeting | topical | topical terms remain after removing request and field words |
| HYB-01 | hybrid | listing | no topical term; record-lookup cue(s): latest |
| HYB-02 | hybrid | aggregate | no topical term; aggregate cue(s): how_many |
| HYB-03 | hybrid | listing | no topical term; record-lookup cue(s): latest |
| HYB-05 | hybrid | listing | no topical term; record-lookup cue(s): latest, action_items |
| HYB-06 | hybrid | aggregate | no topical term; aggregate cue(s): how_many |
| HYB-09 | hybrid | unsupported | date relation 'since' applied to '2026-01-01' is not supported |
| HYB-10 | hybrid | aggregate | no topical term; aggregate cue(s): attribute_list |
| ADV-06 | adversarial_edge_semantic | listing | no topical term, no cue and no filter: the frozen retriever reports 'no terms' (clarification) |
| ADV-10 | adversarial_edge_semantic | topical | topical terms remain after removing request and field words |

The lexical controls (MEET-02, MEET-07, MEET-10, ADV-10) were never sent to the aggregate path. MEET-02 (exact date, action items) went to the listing path; MEET-07, MEET-10, ADV-10 to topical retrieval with the same topical terms as Baseline.

## 5. Latency

| Measure | Baseline | V1 |
| --- | --- | --- |
| Mean | 9.3 s | 9.2 s |
| Median | 11.4 s | 11.3 s |
| Max | 28.4 s (ADV-10) | 18.8 s (HYB-10) |

| Route | n | Baseline mean / median / max | V1 mean / median / max |
| --- | --- | --- | --- |
| ambiguous | 2 | 0 ms / 0 ms / 1 ms | 1 ms / 1 ms / 1 ms |
| hybrid | 7 | 12.8 s / 12.7 s / 16.9 s | 11.2 s / 12.1 s / 18.8 s |
| investment | 12 | 10.5 s / 12.1 s / 15.5 s | 10.4 s / 12.0 s / 14.8 s |
| meeting | 10 | 6.8 s / 6.6 s / 28.4 s | 8.0 s / 7.7 s / 12.5 s |
| performance | 9 | 9.7 s / 11.2 s / 13.4 s | 9.5 s / 11.3 s / 12.7 s |

Aggregate meeting answers use two Claude calls (SQL, then synthesis) and hybrids with an aggregate use three (HYB-10 was the slowest V1 response). Listing and topical answers use one call.

## 6. Failure taxonomy

| Primary failure mode | Baseline | V1 | V1 IDs |
| --- | --- | --- | --- |
| correct | 18 | 24 | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10, PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10, MEET-01, MEET-04, MEET-05, MEET-07, HYB-01, HYB-02, HYB-03, HYB-07, ADV-01, ADV-02, ADV-03, ADV-10 |
| entity_resolution | 0 | 0 | - |
| routing | 0 | 0 | - |
| sql_generation_or_query_correctness | 0 | 0 | - |
| meeting_retrieval | 2 | 2 | MEET-06, MEET-08 |
| hybrid_limitation | 5 | 1 | HYB-06 |
| answer_synthesis | 0 | 0 | - |
| validation_false_positive | 1 | 1 | HYB-10 |
| unsupported_or_ambiguity_handled | 0 | 1 | HYB-09 |
| other | 3 | 0 | - |
| needs_review | 11 | 11 | INV-04, INV-06, PERF-06, PERF-07, MEET-02, MEET-10, HYB-05, ADV-05, ADV-06, ADV-07, ADV-08 |

`unsupported_or_ambiguity_handled` in V1 is `HYB-09`: the question was refused under the approved date-relation rule. The four questions whose expected behavior is a refusal or clarification (PERF-05, HYB-07, ADV-01, ADV-03) are counted `correct`, as in Baseline.

## 7. Detailed cases: questions that are not correct

### MEET-06 (meeting): meeting_retrieval / encoding_and_top_k
- **Question:** Which meetings did Sanjay López attend?
- **Route / mode / status:** meeting / topical / error (validation_failed)
- **Baseline:** ok, incorrect
- **Why:** Unchanged cause: the query 'sanjay lopez attend' matches 627 meetings, only the top 20 are shown, and the stored spelling is mojibake. The answer cited 0 of the 14 expected meetings.
- **Secondary:** the answer was withheld by validation for '607' (matched 627 minus shown 20), a derived number; its content was honest but incomplete

### MEET-08 (meeting): meeting_retrieval / semantic_miss
- **Question:** In client D12349's meetings, which ones raised concern that the company has no full-time finance chief and relies on outside providers for cash management?
- **Route / mode / status:** meeting / topical / ok
- **Baseline:** ok, incorrect
- **Why:** Same as Baseline: question and evidence sentence share 0 content words; the 3 expected meetings are in the 15 returned only because the client has 18 meetings in scope and K=20; matched words are generic. V1 removed only 'company' from the terms.
- **Secondary:** retrieved in top-K only because scope (18) <= K (20); answer faithful to the visible snippets; validation passed

### HYB-06 (hybrid): hybrid_limitation / cross_source_date_dependency
- **Question:** How many of client A12345's meetings took place after its latest performance snapshot, and what are the two dates?
- **Route / mode / status:** hybrid / aggregate / unsupported (hybrid_partial_failure)
- **Baseline:** ok, incorrect
- **Why:** The meeting side was classified as an aggregate ('how many'); the generated SQL step returned 'unsupported': the snapshot date is not in meetings_meta and the two sides are independent. The whole question was reported as a hybrid partial failure. No count was produced.
- **Secondary:** Baseline produced a wrong self-contradicting count that passed validation; V1 reports the dependency as unsupported instead

### HYB-09 (hybrid): unsupported_or_ambiguity_handled / date_relation_unsupported
- **Question:** Which clients with a latest CI Total MOIC below 1.0x had a meeting since 2026-01-01 where it came up that nobody in the company holds a permanent finance-chief role?
- **Route / mode / status:** hybrid / unsupported / unsupported (date_relation_unsupported)
- **Baseline:** ok, incorrect
- **Why:** 'since 2026-01-01' is a date relation, unsupported by the approved V1 rule; nothing was searched and no structured query ran. The semantic and intersection parts of the question were therefore not exercised.
- **Secondary:** the structured side did not run; semantic retrieval and intersection remain untested

### HYB-10 (hybrid): validation_false_positive / row_count_of_one_side_not_grounded
- **Question:** For client D12376, list the deal names in its investment records and the sectors and companies in its meetings.
- **Route / mode / status:** hybrid / aggregate / error (validation_failed)
- **Baseline:** ok, incorrect
- **Why:** Both sides were correct: 8/8 deal names and the 16 distinct sector/company rows (all 10 sectors and 11 companies). The complete answer was withheld because it stated '16' (the aggregate's row count); the validator wrapper merges the two sides' rows and sums their counts (8 + 16), so 16 alone is not a grounded number.
- **Secondary:** evidence-level correct; the answer text was correct and complete but withheld

### Needs review (not scored)

| ID | Status (B to V1) | V1 observation (facts only) |
| --- | --- | --- |
| INV-04 | ok | All 5 RMs and counts are given, but the answer still says the result has 'no client_id column, so it can't be tied to B12346', although the SQL was filtered to B12346 (the synthesizer is not shown the query). Same pattern as Baseline. |
| INV-06 | ok | 192 clients with at least one Closed record is reported, with a record-level explanation; the count of Closed records (12,488) is not reported. |
| PERF-06 | ok | Average 1.78125 within tolerance; the answer states it does not know the number of snapshots or the values, so the 8-snapshot span is not stated (same as Baseline). |
| PERF-07 | ok | The 8 values are listed in date order and described as moving up and down; the answer also notes the latest is higher than the first. Whether that meets the rule needs review. |
| MEET-02 | ok | Answer says action items are not recorded for meeting 4560 (now through the newest-first listing path with an exact-date filter). |
| MEET-10 | ok | Meeting 5506 is retrieved at rank 1 and named in the answer as the match. The answer also cites meeting 11756 (rank 2) and states it is NOT a match (+/-183 bps, not 128). Automated citation-set equality would score this wrongly, so it is left for review. |
| HYB-05 | ok to error | No answer was delivered: it was withheld by validation because it cites [performance] to say there is no performance data while no performance evidence exists. The evidence itself holds meeting 706 at rank 1 with its date and both action items, and the withheld text (kept in the failure details) reports them. Baseline delivered an answer without the meeting. |
| ADV-05 | ok | Per-currency totals, no mixed-currency sum (same as Baseline); a single USD total is not offered. |
| ADV-06 | clarification | Unchanged: clarification 'no_terms' (no searchable terms, no entity or date filter); no reference date, window or answer. |
| ADV-07 | clarification | Unchanged: the email is treated as an unknown entity (entity_not_found); no explanation that the email field is unreliable. |
| ADV-08 | unsupported | Status unsupported_question; message: 'Definitions are not stored in the data; the schema has no field-meaning metadata.' The message states the meaning is not documented; whether that meets the rule is for review. |

The stored rules for these questions are unchanged in the JSON (`review.rule`). HYB-05 changed from an answer without the meeting (Baseline) to no delivered answer (V1) with the expected meeting in the evidence.

## 8. Semantic-stress results

`MEET-08` and `HYB-09` use the same evidence sentence (the CFO sentence); `ADV-10` succeeded lexically in both runs.

| ID | Baseline | V1 | Semantic after V1? |
| --- | --- | --- | --- |
| MEET-08 | 3/3 expected meetings in the returned 15 (ranks 4, 8, 12); answer says the concern is not shown | Same: 3/3 at ranks 4, 8, 12 of 15 returned; terms after V1: `concern full time finance chief relies outside providers cash management` (only `company` was removed); answer says the concern is not shown | **Yes.** No matched word occurs in the evidence sentence (matched: `cash`, `management`, `time`); scope 18 <= K 20, so retrieval was not selective. Correct terms and correct client filter, so the miss is vocabulary distance, not query formulation |
| HYB-09 | Structured side correct; 0 of 31 expected meetings; ISO date became an exact-date filter | Refused (`date_relation_unsupported`): 'since 2026-01-01'; nothing searched, structured side not run | **Not determinable from this run.** The approved rule refuses the question before any retrieval, so the semantic part was not exercised |
| ADV-10 | 3/3 at ranks 1-3 on the shared word `local` | 3/3 at ranks 1-3, matched word `local` | No: lexical overlap on the one shared word, as in Baseline |

After V1, the query-formulation defects that affected other questions (request words as search terms) do not affect MEET-08: its terms contain no request word besides `company`. Its failure therefore remains a lexical-vocabulary failure with correct filtering (visible set: one failing paraphrase template, used by MEET-08 and HYB-09). HYB-09 provides no retrieval evidence either way while date relations stay unsupported. No embeddings, semantic ranking or reranking were used.

## 9. Comparison with Baseline

### Movement per question

| ID | Baseline | V1 | V1 mode or attributed cause |
| --- | --- | --- | --- |
| MEET-01 | incorrect (ok) | correct (ok) | aggregate |
| MEET-04 | incorrect (ok) | correct (ok) | aggregate |
| MEET-05 | incorrect (ok) | correct (ok) | aggregate |
| MEET-06 | incorrect (ok) | incorrect (error) | Unchanged cause: the query 'sanjay lopez attend' matches 627 meetings, only the top 20 are shown, and the stored spelling is mojibake. The answer cite |
| MEET-07 | incorrect (error) | correct (ok) | topical |
| HYB-01 | incorrect (error) | correct (ok) | listing |
| HYB-03 | incorrect (ok) | correct (ok) | listing |
| HYB-05 | needs_review (ok) | needs_review (error) | No answer was delivered: it was withheld by validation because it cites [performance] to say there is no performance data while no performance evidenc |
| HYB-06 | incorrect (ok) | incorrect (unsupported) | The meeting side was classified as an aggregate ('how many'); the generated SQL step returned 'unsupported': the snapshot date is not in meetings_meta |
| HYB-09 | incorrect (ok) | incorrect (unsupported) | 'since 2026-01-01' is a date relation, unsupported by the approved V1 rule; nothing was searched and no structured query ran. The semantic and interse |
| HYB-10 | incorrect (ok) | incorrect (error) | Both sides were correct: 8/8 deal names and the 16 distinct sector/company rows (all 10 sectors and 11 companies). The complete answer was withheld be |

### Counts
Correct 18 to 24 (+6); incorrect 11 to 5 (-6); needs review 11 to 11 (0). By category, change in correct: Investment 0, Performance 0, Meeting +4, Hybrid +2, Adversarial / edge / semantic 0.

### Failure clusters that disappeared (as measured)
1. **No structured path over the meetings table** for MEET-01, MEET-04, MEET-05: now correct at evidence level and in the answer (aggregate path).
2. **Request or attribute words searched as text** for HYB-01 and HYB-03: now correct through the newest-first listing (latest meeting at rank 1; HYB-03's company and sector shown because the question named them). HYB-05's evidence now contains meeting 706 at rank 1 with its action items.
3. **MEET-07's answer withheld for the derived count 17:** delivered in V1, cited ids equal the expected six.
4. **HYB-06's wrong, self-contradicting count that passed validation:** no count is produced in V1 (see below).

### Failure clusters that remain or changed
| Cluster | Questions | V1 measurement |
|---|---|---|
| Lexical vocabulary distance | MEET-08 (HYB-09 not exercised) | unchanged |
| Encoding and top-K | MEET-06 | unchanged cause; the answer is now also withheld for the derived number 607 |
| Cross-source date dependency | HYB-06 | now reported as unsupported (a hybrid partial failure) instead of a wrong count |
| Date relations refused by rule | HYB-09 | refused, by design of the approved V1 rule |
| Answers withheld by the validation wrapper | HYB-10 (`16`), HYB-05 (`[performance]` cited without evidence), MEET-06 (`607`) | 3 withheld in V1 (Baseline: MEET-07 and HYB-01) |
| Synthesis statements about the query | INV-04 (needs review), MEET-04 (score unaffected) | unchanged pattern; MEET-04's answer says only one meeting was returned to compare (`LIMIT 1`) |
| Rule-based questions with no answer | ADV-06, ADV-07 | unchanged; ADV-08 now `unsupported_question` with a different message |
| Not exercised or unchanged | PERF-06, INV-06, ADV-05 | unchanged |

### Regressions and other measured changes in the wrong direction
- **Questions correct in Baseline that are not correct in V1: 0 of 18.**
- **HYB-10:** evidence became correct (all 8 deal names, all 10 sectors and 11 companies) but the complete answer was withheld: its `16` (the aggregate's row count) is not grounded, because the validation wrapper merges the two sides' rows and sums their counts. Baseline: status `ok`, wrong (no meeting side).
- **HYB-05 (needs review):** the answer is withheld because it cites `[performance]` to state that no performance data exists while no performance evidence was used. Baseline delivered an answer that omitted the meeting.
- **MEET-06:** status `ok` in Baseline, withheld in V1 (derived number 607); incorrect in both.
- **HYB-06, HYB-09:** status `ok` (incorrect) in Baseline, `unsupported` in V1 (incorrect).
- **Validator:** answers withheld 2 to 3; false positives 1 to 2 (definition above).
- **Latency:** mean about unchanged; meeting route mean 6.8 s to 8.0 s (aggregate adds a call); Baseline's slowest response (ADV-10, 28.4 s) is not repeated; V1's slowest is HYB-10 (18.8 s, three calls).

### Contract failure-evidence criteria (v1_contract 4.5 and 5.5), as measured
| Criterion | Measured |
|---|---|
| Lexical control (MEET-02/07/10, ADV-10) loses evidence or changes result | No: all keep their evidence; MEET-07 changes from withheld to delivered |
| HYB-01 or HYB-03 still lack the latest meeting although the terms are correct | No: both correct |
| More answers withheld by validation than Baseline (excluding MEET-07) | **Yes: 3 in V1 (MEET-06, HYB-05, HYB-10) against 1 in Baseline (HYB-01)** |
| An answer presents a listing as topical matches | Not observed in the listing answers (HYB-01, HYB-03, MEET-02 and the others describe the listing as ordered by date) |
| A date relation answered or filtered as an exact date | No: HYB-09 refused |
| MEET-01/04/05/HYB-10 not correct at evidence level, or a guard/LLM outcome error | No for all four (HYB-10's final answer was withheld after correct evidence) |
| A lexical control sent to the aggregate path | No |
| HYB-02 or an investment/performance question correct in Baseline becomes incorrect | No (HYB-02 now uses the aggregate path and is correct) |
| A query referencing a second source is executed | No (every executed aggregate query read only `meetings_meta`) |

## 10. Remaining failures and observed V1 limitations

- Semantic vocabulary distance (MEET-08) and encoding with top-K (MEET-06) are unchanged.
- HYB-06 needs a date from the other source; HYB-09 is refused under the date-relation rule; cross-source intersection is not attempted.
- The validation wrapper withheld correct content in HYB-10 (merged row counts) and HYB-05 (a citation for an absent source), and the frozen validator's derived-number rule withheld MEET-06's answer; no validation logic was changed for this run.
- Statements about the query remain unreliable when the synthesizer is not shown the SQL (INV-04, MEET-04).
- Rule-based adversarial questions ADV-06 and ADV-07 still return no stated behavior; PERF-06 still lacks the snapshot count and span.
- One run per question; LLM variance was not measured (no Baseline repeat, by decision D-V1-9).
