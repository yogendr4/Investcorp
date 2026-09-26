# Baseline Results (visible benchmark, 40 questions)

Measurement of the frozen Baseline (entity resolution, routing, guarded LLM SQL, lexical FTS5/BM25 meeting retrieval, one Claude synthesis call, deterministic validation) on the 40 visible questions of `tests/evaluation/questions.json`. Raw data: [`baseline_results.json`](baseline_results.json) (one object per question). This document reports observations; it does not rate the Baseline and proposes no change.

## 1. Evaluation setup

- **Code:** the production code exactly as it stood after the end-to-end service slice. No production file, prompt, metadata, benchmark or holdout was changed for this run. No retries, no manual correction of SQL, entities, routes, retrieval queries or answers.
- **Questions:** the 40 visible questions only (`split == visible`). The holdout file was not opened or read, and no holdout question or answer appears here.
- **Run:** one pass through `BaselineService.answer_question`, real Claude CLI (`claude-sonnet-5`, effort low) for SQL generation and synthesis, one call per LLM stage, on the built `data/derived/baseline.sqlite`. Every run used the runtime date. Account-email and org fields were redacted from the stored responses.
- **Scoring (no LLM judge):**
  - *Exact-answer questions* (25 scored on evidence, plus 4 questions whose expected behavior is a refusal or clarification): the structured evidence (SQL result rows) or the returned meeting evidence is compared with the expected value. The generated prose is not scored, except that a response only counts as `correct` if its final status is `ok` (or is the expected refusal/clarification) and, for id-set questions, if the meeting ids the answer cites equal the expected set. `HYB-02` also requires the answer to state the meeting count, because that number exists only in the meeting evidence's scope count.
  - *Required components* are the ones the question asks for (for example `INV-10` asks for the GBP and USD totals, not the record count).
  - *Refusal/clarification questions* decided from structured response fields: `PERF-05` (unavailable metric), `HYB-07` (RM-to-meeting), `ADV-01` (the four candidates), `ADV-03` (both Summit candidates).
  - *Rule-based questions* are `needs_review`. Their `must_include` / `must_not` / reference facts are preserved in the JSON, with non-scoring keyword hints and a factual observation. `MEET-10` is also `needs_review`: the retrieval is scored (rank 1) but its answer names 5506 as the match and cites 11756 only to say it is not one, which citation-set equality cannot score. That rule was set after seeing this answer; it is disclosed here.
- **Failure-mode labels** (primary, plus secondary observations) were assigned by the analyst from the raw responses, after scoring. They are judgements, and each carries its reason in the JSON.
- **Caveats:** one run, one sample of non-deterministic LLM output per question; latency includes the two Claude calls and the Claude CLI start-up.

## 2. Aggregate results

| Measure | Value |
| --- | --- |
| Questions | 40 |
| Completed (a response returned, no crash) | 40 |
| Correct (auto-scored) | 18 |
| Incorrect (auto-scored) | 11 |
| Needs review | 11 |
| Final status `ok` | 31 |
| Final status `clarification` | 4 |
| Final status `unsupported` | 3 |
| Final status `error` | 2 (both `validation_failed`) |
| Auto-scored questions | 29 of 40: 18 correct, 11 incorrect |

Correct, of the 29 auto-scored: 18/29. Not counted as correct or incorrect: the 11 `needs_review` questions, of which 3 produced no answer text (ADV-06, ADV-07, ADV-08; see section 6) and 1 (HYB-05) did not report the meeting fact the rule requires. A clarification or `unsupported` status is counted as correct only where the benchmark expects exactly that (PERF-05, HYB-07, ADV-01, ADV-03).

## 3. Results by category

| Category | Total | Correct | Incorrect | Needs review | ok | clarification | unsupported | error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Investment | 8 | 6 | 0 | 2 | 8 | 0 | 0 | 0 |
| Performance | 8 | 6 | 0 | 2 | 7 | 0 | 1 | 0 |
| Meeting | 8 | 0 | 6 | 2 | 7 | 0 | 0 | 1 |
| Hybrid | 8 | 2 | 5 | 1 | 6 | 0 | 1 | 1 |
| Adversarial / edge / semantic | 8 | 4 | 0 | 4 | 3 | 4 | 1 | 0 |

| Category | Correct | Incorrect | Needs review |
| --- | --- | --- | --- |
| Investment | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10 | - | INV-04, INV-06 |
| Performance | PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10 | - | PERF-06, PERF-07 |
| Meeting | - | MEET-01, MEET-04, MEET-05, MEET-06, MEET-07, MEET-08 | MEET-02, MEET-10 |
| Hybrid | HYB-02, HYB-07 | HYB-01, HYB-03, HYB-06, HYB-09, HYB-10 | HYB-05 |
| Adversarial / edge / semantic | ADV-01, ADV-02, ADV-03, ADV-10 | - | ADV-05, ADV-06, ADV-07, ADV-08 |

### All 40 questions

| ID | Cat | Diff | Route | Status | Result | Primary failure | Latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-01 | investment | medium | investment | ok | correct | - | 15.5 s |
| INV-02 | investment | medium | investment | ok | correct | - | 13.2 s |
| INV-03 | investment | medium | investment | ok | correct | - | 11.8 s |
| INV-04 | investment | hard | investment | ok | needs_review | - | 12.2 s |
| INV-06 | investment | hard | investment | ok | needs_review | - | 12.1 s |
| INV-07 | investment | hard | investment | ok | correct | - | 12.1 s |
| INV-08 | investment | hard | investment | ok | correct | - | 12.0 s |
| INV-10 | investment | medium | investment | ok | correct | - | 12.3 s |
| PERF-01 | performance | medium | performance | ok | correct | - | 10.9 s |
| PERF-02 | performance | hard | performance | ok | correct | - | 11.9 s |
| PERF-04 | performance | medium | performance | ok | correct | - | 11.4 s |
| PERF-05 | performance | hard | performance | unsupported | correct | - | 1 ms |
| PERF-06 | performance | hard | performance | ok | needs_review | - | 11.7 s |
| PERF-07 | performance | hard | performance | ok | needs_review | - | 13.4 s |
| PERF-08 | performance | medium | performance | ok | correct | - | 11.2 s |
| PERF-10 | performance | hard | performance | ok | correct | - | 10.8 s |
| MEET-01 | meeting | easy | meeting | ok (qualified) | incorrect | other | 6 ms |
| MEET-02 | meeting | hard | meeting | ok | needs_review | - | 6.4 s |
| MEET-04 | meeting | hard | meeting | ok (qualified) | incorrect | other | 3 ms |
| MEET-05 | meeting | medium | meeting | ok (qualified) | incorrect | other | 3 ms |
| MEET-06 | meeting | hard | meeting | ok | incorrect | meeting_retrieval | 7.9 s |
| MEET-07 | meeting | medium | meeting | error | incorrect | validation_false_positive | 9.0 s |
| MEET-08 | meeting | hard | meeting | ok | incorrect | meeting_retrieval | 9.1 s |
| MEET-10 | meeting | hard | meeting | ok | needs_review | - | 6.7 s |
| HYB-01 | hybrid | hard | hybrid | error | incorrect | hybrid_limitation | 12.7 s |
| HYB-02 | hybrid | hard | hybrid | ok | correct | - | 12.2 s |
| HYB-03 | hybrid | hard | hybrid | ok | incorrect | hybrid_limitation | 12.6 s |
| HYB-05 | hybrid | hard | hybrid | ok | needs_review | - | 6.8 s |
| HYB-06 | hybrid | medium | hybrid | ok | incorrect | hybrid_limitation | 14.8 s |
| HYB-07 | hybrid | hard | ambiguous | unsupported | correct | - | 1 ms |
| HYB-09 | hybrid | hard | hybrid | ok | incorrect | hybrid_limitation | 16.9 s |
| HYB-10 | hybrid | medium | hybrid | ok | incorrect | hybrid_limitation | 13.6 s |
| ADV-01 | adv | hard | investment | clarification | correct | - | 1 ms |
| ADV-02 | adv | medium | investment | ok | correct | - | 11.4 s |
| ADV-03 | adv | hard | ambiguous | clarification | correct | - | 0 ms |
| ADV-05 | adv | medium | investment | ok | needs_review | - | 13.4 s |
| ADV-06 | adv | hard | meeting | clarification | needs_review | - | 0 ms |
| ADV-07 | adv | hard | investment | clarification | needs_review | - | 0 ms |
| ADV-08 | adv | medium | performance | unsupported | needs_review | - | 5.8 s |
| ADV-10 | adv | hard | meeting | ok | correct | - | 28.4 s |

## 4. Component-level results

| Component | Result | Notes |
|---|---|---|
| Entity resolution | 28/28 where the benchmark defines an expected entity; 0 failures; 12 not applicable | Includes `ADV-01` (4 candidates for a bare number), `ADV-02` (name form resolved to A12345), `ADV-03` (2 candidates for `Summit`), `HYB-07` (RM resolved). Not applicable (no expected entity): INV-06, INV-10, PERF-02, PERF-04, PERF-08, MEET-05, MEET-06, HYB-09, ADV-05, ADV-06, ADV-07, ADV-08. `MEET-06` names an attendee, which is not an entity type. |
| Routing | 40/40 | Route compared with the route implied by `expected_source`. `HYB-07` and `ADV-03` routed to `ambiguous` (then `unsupported` / `clarification`), which is the expected behavior. Routing to `meeting` was right for `MEET-01/04/05`, but the meeting route has no aggregate path (section 6). |
| Structured query (SQL result) | 18/18 structured components correct | Investment, performance and the structured side of hybrids and `ADV-02`. Rule-based structured questions are excluded. Traps handled: USD field for sums, exact deal-name match, latest per client, group rows excluded, group row read directly. |
| Meeting retrieval, expected evidence in top-K | all expected ids in top-K: MEET-02, MEET-07, MEET-08, MEET-10, ADV-10; none: MEET-04, HYB-01, HYB-03, HYB-05, HYB-09; partial: MEET-06 (1/14) | Per question in the JSON (`retrieval`). For `MEET-08` the hit is not evidence of relevance (section 7). |
| Answer validation | 31/33 answers passed; 2 rejected (MEET-07, HYB-01) | Warnings on MEET-06, HYB-01, HYB-03. |
| Validation false positives | 1 (`MEET-07`) | The answer's evidence was complete (6/6 meetings, ranks 1-6); it was withheld for the derived number 17 (23 in scope minus 6 matched). `HYB-01` was rejected for a non-notation citation `[meeting evidence]`; that is the rule working, not a false positive. |
| Validation false negatives (a wrong claim passed) | 1 clear case (`HYB-06`) | The answer counted 4 of 13 retrieved meetings after the snapshot, said "three", then "four"; the expected count was 8 of 23. The other 8 answers that passed and are incorrect are honest "not found / not shown" answers over missing evidence: MEET-01, MEET-04, MEET-05, MEET-06, MEET-08, HYB-03, HYB-09, HYB-10. |

### Latency (end-to-end, per response)

Mean 9.3 s, median 11.4 s, min 0 ms, max 28.4 s (ADV-10). The 9 responses without a Claude call (clarification, unsupported, and three meeting routes that returned an empty retrieval) took 0.4-6 ms, which pulls the mean down.

| Route | n | Mean | Median | Max |
| --- | --- | --- | --- | --- |
| ambiguous | 2 | 0 ms | 0 ms | 1 ms |
| hybrid | 7 | 12.8 s | 12.7 s | 16.9 s |
| investment | 12 | 10.5 s | 12.1 s | 15.5 s |
| meeting | 10 | 6.8 s | 6.6 s | 28.4 s |
| performance | 9 | 9.7 s | 11.2 s | 13.4 s |

Investment, performance and hybrid answers use two Claude calls (SQL, then synthesis) at about 10-17 s. Meeting answers use one (synthesis) at about 6-9 s, except `ADV-10` (28.4 s; one synthesis call, the slowest in the run, cause not investigated).

## 5. Failure taxonomy

| Primary failure mode | Questions | IDs |
| --- | --- | --- |
| correct | 18 | INV-01, INV-02, INV-03, INV-07, INV-08, INV-10, PERF-01, PERF-02, PERF-04, PERF-05, PERF-08, PERF-10, HYB-02, HYB-07, ADV-01, ADV-02, ADV-03, ADV-10 |
| entity_resolution | 0 | - |
| routing | 0 | - |
| sql_generation_or_query_correctness | 0 | - |
| meeting_retrieval | 2 | MEET-06, MEET-08 |
| hybrid_limitation | 5 | HYB-01, HYB-03, HYB-06, HYB-09, HYB-10 |
| answer_synthesis | 0 | - |
| validation_false_positive | 1 | MEET-07 |
| unsupported_or_ambiguity_handled | 0 | - |
| other | 3 | MEET-01, MEET-04, MEET-05 |
| needs_review | 11 | INV-04, INV-06, PERF-06, PERF-07, MEET-02, MEET-10, HYB-05, ADV-05, ADV-06, ADV-07, ADV-08 |

`unsupported_or_ambiguity_handled` is 0 because the four questions where refusal or clarification was the expected behavior are counted `correct`. `other` is used for the three meeting questions that need a structured aggregate over the meetings table, which Baseline has no path for (subtype `no_structured_meeting_path`); it is not a routing or retrieval defect in the strict sense, so it is kept separate.

**Recurring clusters**

1. **No structured path over the meetings table** (7 incorrect: `MEET-01`, `MEET-04`, `MEET-05`, `HYB-01`, `HYB-03`, `HYB-06`, `HYB-10`; also affects `HYB-05`, `needs_review`). Counts, min/max dates, the latest meeting, the largest deal size and distinct sector/company values are aggregates over meetings. The meeting route runs a lexical search only, and the search terms come from the request wording (`many, date, range`; `ci, total`; `sectors, companies`), which match nothing or noise. In every one of these the structured side (when present) was correct and the answer honestly said the meeting fact was not available.
2. **Lexical retrieval limits** (`MEET-06`, `MEET-08`, `HYB-09`): the attendee spelling is stored as mojibake and only the top 20 of 627 matches are shown (`MEET-06`); the paraphrased concern is not matched by meaning (`MEET-08`, `HYB-09`).
3. **Validator behavior** (`MEET-07` false positive; `HYB-06` false negative; `HYB-01` notation): a derived count was rejected, a self-contradicting wrong count passed.
4. **Rule-based adversarial questions with no answer** (`ADV-06`, `ADV-07`, `ADV-08`, `needs_review`): the system stopped with a clarification or `unsupported` where the benchmark asks for a stated behavior.

**Isolated:** `HYB-09` also treated "since 2026-01-01" as an exact-date filter; `INV-04` (`needs_review`) has an answer that contradicts the SQL filter.

## 6. Detailed failure cases

### MEET-01 (meeting, easy): other / no_structured_meeting_path
- **Question:** How many meetings are recorded for client B12463, and over what date range?
- **Route / status:** meeting / ok
- **Why:** Route 'meeting' is lexical retrieval only: it searched the words 'many, date, range' and found no match. A count and min/max date over a client's meetings has no path in Baseline.
- **Meeting evidence:** no_lexical_match, terms ['many', 'date', 'range'], matched 0 of 22 in scope

### MEET-04 (meeting, hard): other / no_structured_meeting_path
- **Question:** Which of client A12345's meetings had the largest estimated deal size, and what was it?
- **Route / status:** meeting / ok
- **Why:** Largest deal_size_estimate needs a numeric aggregate over meetings; lexical search on 'largest, estimated, deal, size' matched nothing.
- **Meeting evidence:** no_lexical_match, terms ['largest', 'estimated', 'deal', 'size'], matched 0 of 23 in scope
- **Expected evidence in top-K:** 0 of 1

### MEET-05 (meeting, medium): other / no_structured_meeting_path
- **Question:** How many meetings are at the series B stage?
- **Route / status:** meeting / ok
- **Why:** Count of series_b meetings needs a deterministic filter and count; retrieval searched 'many, series, stage' and matched nothing.
- **Meeting evidence:** no_lexical_match, terms ['many', 'series', 'stage'], matched 0 of 20000 in scope

### MEET-06 (meeting, hard): meeting_retrieval / encoding_and_top_k
- **Question:** Which meetings did Sanjay López attend?
- **Route / status:** meeting / ok
- **Why:** Query 'sanjay lopez attend' (OR of terms) matched 627 meetings; only the top 20 are shown and the stored spelling is mojibake. The answer cited 1 meeting; recall of the 14 expected is in the retrieval block.
- **Meeting evidence:** ok, terms ['sanjay', 'lópez', 'attend'], matched 627 of 20000 in scope
- **Expected evidence in top-K:** 1 of 14
- **Secondary:** encoding: clean 'lopez' vs stored mojibake (documented assumption A11)

### MEET-07 (meeting, medium): validation_false_positive / derived_count_rejected
- **Question:** In client A12346's meetings, which ones flag the absence of independent directors on the board?
- **Route / status:** meeting / error (validation_failed)
- **Why:** All 6 expected meetings were retrieved (ranks 1-6), but the answer mentioned '17' (23 in scope minus 6 matched), a derived number not in the evidence, so the validator withheld the answer.
- **Meeting evidence:** ok, terms ['absence', 'independent', 'directors', 'board'], matched 6 of 23 in scope
- **Expected evidence in top-K:** 6 of 6
- **Secondary:** evidence and answer otherwise complete; 6/6 at ranks 1-6

### MEET-08 (meeting, hard): meeting_retrieval / semantic_miss
- **Question:** In client D12349's meetings, which ones raised concern that the company has no full-time finance chief and relies on outside providers for cash management?
- **Route / status:** meeting / ok
- **Why:** The 3 expected meetings were in the returned 15 (ranks 4, 8, 12) only because the client has 18 meetings in scope and K=20; they matched on generic words, and the snippets do not show the expected sentence, so the answer (faithful to the snippets) says the concern is not shown.
- **Meeting evidence:** ok, terms ['concern', 'company', 'full', 'time', 'finance', 'chief', 'relies', 'outside', 'providers', 'cash', 'management'], matched 15 of 18 in scope
- **Expected evidence in top-K:** 3 of 3
- **Secondary:** retrieved in top-K only because scope (18) <= K (20); answer faithful to visible snippets; validation passed

### HYB-01 (hybrid, hard): hybrid_limitation / meeting_side_lexical_only
- **Question:** For client A12345, what is the latest CI Total MOIC and when was the client's most recent meeting?
- **Route / status:** hybrid / error (validation_failed)
- **Why:** MOIC 0.78 as of 2024-10-25 was correct. 'Most recent meeting' needs a max-date lookup; the meeting side searched 'ci, total' (no match). The synthesis also used a non-notation citation '[meeting evidence]', so validation withheld the answer.
- **Structured evidence:** ok, rows `[["A12345", "2024-10-25", 0.78]]`
- **Meeting evidence:** no_lexical_match, terms ['ci', 'total'], matched 0 of 23 in scope
- **Expected evidence in top-K:** 0 of 1
- **Secondary:** citation '[meeting evidence]' violated the required notation (validator rejected)

### HYB-03 (hybrid, hard): hybrid_limitation / meeting_side_lexical_only
- **Question:** What is the total USD investment amount for client C12355, and which company and sector did their most recent meeting involve?
- **Route / status:** hybrid / ok
- **Why:** Total USD 437,086,022.21 was correct. The latest meeting's company and sector need a latest-meeting lookup; the search for 'total, company, sector, involve' matched nothing.
- **Structured evidence:** ok, rows `[[437086022.21]]`
- **Meeting evidence:** no_lexical_match, terms ['total', 'company', 'sector', 'involve'], matched 0 of 19 in scope
- **Expected evidence in top-K:** 0 of 1
- **Secondary:** no structured meeting path (latest meeting lookup); validator warning: 'unavailable' while other values present

### HYB-06 (hybrid, medium): hybrid_limitation / cross_source_date_comparison
- **Question:** How many of client A12345's meetings took place after its latest performance snapshot, and what are the two dates?
- **Route / status:** hybrid / ok
- **Why:** Snapshot date correct. The count of meetings after it (8 of 23) was not computed over the full meeting set; the answer counted lexically retrieved meetings (4 of 13) and contradicted itself ('three' then 'four'). Validation passed.
- **Structured evidence:** ok, rows `[["A12345", "2024-10-25"]]`
- **Meeting evidence:** ok, terms ['many', 'after', 'two', 'dates'], matched 13 of 23 in scope
- **Secondary:** validator passed a self-contradicting wrong count (false negative)

### HYB-09 (hybrid, hard): hybrid_limitation / intersection_and_semantic_meeting_side
- **Question:** Which clients with a latest CI Total MOIC below 1.0x had a meeting since 2026-01-01 where it came up that nobody in the company holds a permanent finance-chief role?
- **Route / status:** hybrid / ok
- **Why:** The 22-client structured list was correct. The meeting side treated the ISO date 2026-01-01 as an exact-date filter (not 'since'), searched noisy terms and found 3 meetings (none of the 31 expected); no intersection is computed. The answer reports no support.
- **Structured evidence:** ok, rows `[["A12416", "Client_A12416", 0.71], ["A12372", "Client_A12372", 0.72], ["B12346", "Client_B12346", 0.73]]`
- **Meeting evidence:** ok, terms ['ci', 'total', 'below', '0x', 'since', 'up', 'nobody', 'company', 'holds', 'permanent', 'finance', 'chief', 'role'], matched 3 of 12 in scope
- **Expected evidence in top-K:** 0 of 31
- **Secondary:** ISO date 'since 2026-01-01' became an exact meeting_date filter; semantic retrieval did not run across the intended scope

### HYB-10 (hybrid, medium): hybrid_limitation / meeting_side_lexical_only
- **Question:** For client D12376, list the deal names in its investment records and the sectors and companies in its meetings.
- **Route / status:** hybrid / ok
- **Why:** 8/8 deal names correct. Sectors and companies need distinct values over the client's meetings; the search for 'sectors, companies' matched nothing, so nothing was listed.
- **Structured evidence:** ok, rows `[["Atlas Private Equity II"], ["BluePeak Venture II"], ["BluePeak Venture III"]]`
- **Meeting evidence:** no_lexical_match, terms ['sectors', 'companies'], matched 0 of 18 in scope
- **Secondary:** no structured meeting path (distinct values)

### Needs review (not scored)

| ID | Status | Observation (facts only) | Keyword hints (not a score) |
| --- | --- | --- | --- |
| INV-04 | ok | Structured evidence lists all 5 RMs with counts (the SQL is filtered to B12346), but the answer says the evidence 'is not filtered by client and contains no client_id column' and declines to attribute the counts to B12346: inconsistent with the SQL, which the synthesizer is not shown. | `Sara Khan`: yes; `Carlos Gomez`: yes; `Rahul Mehta`: yes; `Daniel Lee`: yes; `Priya Sharma`: yes |
| INV-06 | ok | 192 distinct clients with a Closed record is reported; the count of Closed records (12,488) is not. | `192`: yes; `12,488`: no; `record-level|relationship-level|record level`: yes |
| PERF-06 | ok | Structured value 1.78125 is within tolerance of 1.7812; whether the answer states 8 snapshots and the date span needs review. | `1.78`: yes; `8 snapshots|eight snapshots`: no; `2010-05-06`: no; `2024-10-25`: no |
| PERF-07 | ok | Values were returned ordered by as_of_date; whether the trend statement satisfies the rule needs review. | `0.93`: yes; `1.33`: yes; `no consistent|not a steady|no steady|up and down|not consistently`: yes |
| MEET-02 | ok | Answer says action items are not recorded (meeting 4560). | `not recorded|missing`: yes |
| MEET-10 | ok | Meeting 5506 is retrieved at rank 1 and named in the answer as the match. The answer also cites meeting 11756 (rank 2) and states it is NOT a match (+/-183 bps, not 128). Automated citation-set equality would score this wrongly, so it is left for review. | - |
| HYB-05 | ok | States no performance data for B12463. The meeting search ('ci, total') matched nothing, so meeting 706, its date and its action item were not reported. | `no performance data|not available|no data`: yes; `706`: no; `2026-01-12`: no; `legal counsel`: no |
| ADV-05 | ok | Per-currency totals returned, no mixed-currency sum; a single USD total was not offered. | `cannot be added|not add|mix`: yes; `USD`: yes; `125,633,546,451`: no |
| ADV-06 | clarification | No answer: clarification 'no_terms' (no searchable terms, no entity or date filter). Reference date and window are not stated. | `reference date|today|current date`: no; `90|days|window`: no |
| ADV-07 | clarification | No answer: the email was treated as an unknown entity (entity_not_found); the response does not explain that the email field is unreliable. | `unreliable|not authoritative|cannot identify|contradict`: no; `Carlos Gomez`: no |
| ADV-08 | unsupported | No answer: unsupported_question; the message says the data has no metadata for the field. | `not documented|undocumented|no metadata|no documentation`: yes |

The `must_include`, `must_not`, reference facts and expected summary for each of these are in the `review.rule` object of the JSON. `ADV-06`, `ADV-07` and `ADV-08` produced no answer text, so the behaviors the rules ask for (reference date and window; the email being unreliable; the field being undocumented) were not stated in an answer; whether the `ADV-08` message counts as the required disclosure is for review.

## 7. Semantic-stress results

Three visible questions are flagged `semantic_stress`: `MEET-08`, `ADV-10` and `HYB-09`. `MEET-07` is their lexical control (same client, shared words with the question) and was retrieved 6/6.

| ID | Expected evidence | Retrieved? | Ranks (returned/scope/K) | Matched terms | Right reason or coincidence? |
| --- | --- | --- | --- | --- | --- |
| MEET-08 | meetings 3664, 5791, 9932 (client D12349): "the sponsor currently lacks a dedicated CFO and is outsourcing treasury functions" | Yes, 3/3 in the returned list | ranks 4, 8, 12; 15 returned of 18 in scope, K=20 | management, cash, time (generic) | **Neither semantic nor lexical.** No matched term occurs in the evidence sentence. They were returned because the client filter leaves 18 meetings and K=20, so almost the whole scope is returned. The snippets (about 30 tokens around matches) do not show the sentence; the answer said the concern is not shown. |
| ADV-10 | meetings 7568, 19047, 19238 (client A12353): "~30% of revenues denominated in USD but expenses largely local" | Yes, 3/3 | ranks 1, 2, 3 (3 returned, 23 in scope) | local | **Lexical overlap on one word.** The benchmark declares `local` as the one word shared between question and evidence; only these 3 of the client's 23 meetings contain it, so BM25 isolated them. The other question words (`income`, `dollars`, `costs`, `money`) matched nothing. It succeeded by coincidence of vocabulary, not by understanding the paraphrase. |
| HYB-09 | 31 meetings since 2026-01-01 containing the CFO sentence; expected clients A12351, A12356, A12413, C12348, C12375, D12345 | No: 0 of 31 (the structured side contained all 6 expected clients) | 3 returned (6750, 10313, 18002) of 12 in scope | noisy terms (ci, total, below, up, holds ...) | Not retrieved. The ISO date became an exact-date filter (12 meetings on 2026-01-01), and the terms did not include the sentence's words. The paraphrase was not bridged; no intersection was computed. |

On these three, lexical retrieval reached the expected meeting only where the question shared a word with the evidence (`ADV-10`) or where the entity filter made the scope smaller than K (`MEET-08`). The top-K hit rate for `MEET-08` overstates retrieval quality; `ADV-10` is a lexical success by the benchmark's own declared overlap. The encoding impact test (`docs/encoding_impact_test_design.md`) has not been run, so encoding cannot be excluded as a contributor to `MEET-06`, which is not a semantic-stress question.

## 8. Observed Baseline limitations

- Meetings can only be searched by words; facts about the meetings table as a whole (counts, dates, extremes, distinct values, latest meeting) are not reachable (7 incorrect, 1 `needs_review`).
- The query terms come from the question's wording, so words that describe the request (`many`, `date`, `after`, `sectors`) become search terms and produce empty or noisy results.
- Top-K hit rate can look successful when the entity filter leaves fewer meetings than K (`MEET-08`).
- Paraphrased meeting evidence was not found by meaning (`MEET-08`, `HYB-09`).
- Attendee spelling with an accent does not match the stored mojibake, and only 20 of 627 text matches are shown (`MEET-06`).
- Hybrid questions that need a combined result (set intersection, comparing dates across sources) are not combined (`HYB-06`, `HYB-09`).
- The validator rejected a correct answer for a derived count (`MEET-07`), passed a self-contradicting one (`HYB-06`), and checks citation notation strictly (`HYB-01`).
- The synthesizer is not shown the SQL or its filter, which is consistent with `INV-04`'s answer saying the result "is not filtered by client" when the SQL was.
- A date phrase such as "since 2026-01-01" is read as an exact date.
- Rule-based adversarial questions `ADV-06`, `ADV-07`, `ADV-08` end with a clarification or `unsupported` and no stated behavior.
- Latency is about 10-17 s for structured and hybrid answers (two Claude calls).

## 9. Candidate V1 directions (not implemented)

Derived only from the observations above; each is a hypothesis to test, in no priority order.

- The seven meeting-aggregate failures (`MEET-01/04/05`, `HYB-01/03/06/10`) suggest that a deterministic, guarded query path over the meetings table may be worth testing.
- Empty or noisy searches from request words (`many`, `after`, `sectors`) suggest that query-term derivation for meeting questions may need refinement.
- Semantic meeting misses (`MEET-08`, `HYB-09`) suggest embeddings may be worth testing, under the Embedding Decision Gate; `MEET-06` should first be checked with the encoding impact test, and `ADV-10` shows that a lexical hit can come from one shared word.
- The `MEET-07` false positive and the `HYB-06` false negative suggest the validator's numeric-grounding logic may need refinement (for derived counts and internal contradictions).
- The `INV-04` answer suggests that showing the synthesizer the SQL filter or scope may help, subject to review of that answer.
- The `HYB-09` result suggests that combining a structured client list with meeting evidence, and reading "since <date>" as a range, may be worth examining.
- Top-K reporting that accounts for scope size (`MEET-08`) may give a more honest retrieval metric.
- `ADV-06`, `ADV-07`, `ADV-08` suggest reviewing how ambiguity and unsupported handling should state the behavior the rules require, once those rule-based questions are reviewed.

None of these has been implemented or tested.
