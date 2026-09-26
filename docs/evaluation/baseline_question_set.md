# Baseline Evaluation Question Set (v0.1)

Status: DRAFT for approval. Documentation and test data only; no application code exists yet.

Machine-readable set: [`tests/evaluation/questions.json`](../../tests/evaluation/questions.json). This page lists the 40 visible questions. The 10 holdout questions are kept in a separate file (see section 4) and are deliberately not described here.

## 1. Purpose

One fixed, deliberately hard question set to compare **Baseline -> V1 -> V2** on the same footing. It targets known data realities from [`docs/metadata/data_dictionary_DRAFT.md`](../metadata/data_dictionary_DRAFT.md), not just happy paths. Questions that need a meaning the metadata marks as undocumented either test that the system says so, or avoid that meaning.

## 2. Composition

| Category | Count | Main failure modes targeted |
|---|---|---|
| Structured investment | 10 | Row vs distinct counts; record-level RM and status; `deal_id`/`deal_name` mismatch and prefix collision; USD vs natural currency; near-tied rankings |
| Structured performance | 10 | Per-client latest snapshot; group rows; text MOIC; unavailable metrics; noisy trends/averages; date coverage |
| Meeting retrieval | 10 | Filter-before-retrieval; repetitive text; missing stage text; NULL `action_items`; text deal size; mojibake; meeting-only clients; paraphrase retrieval |
| Hybrid | 10 | Aggregate-then-combine; row multiplication; different date coverage; population gaps; unanswerable links (RM to meetings) |
| Adversarial / edge / semantic | 10 | Ambiguous ids and names; name forms; mixed-currency sums; time anchor; non-authoritative fields; undocumented terms; non-existent client; paraphrase |
| **Total** | **50** | |

The full benchmark has 50 questions: 40 visible here and 10 holdout (2 per category). Counts above are for all 50.

Difficulty: easy 1, medium 18, hard 31. Answer type: 34 with a computed ground-truth value and 16 with a deterministic validation rule only (see section 7); HYB-01 has a computed value plus a behaviour rule. Semantic stress tests: 4.

## 3. Ground truth and verification

- Workbook: `data/Assignment_Data.xlsx`, SHA-256 `998cc8a57067a1f566af3112b3401674ad0310d895a6cce55fc320d455f7a1c4`. If the workbook changes, the ground truth must be regenerated.
- The three tightened questions (`INV-06`, `HYB-01`, `ADV-06`) changed expected behaviour only; no value or ground truth changed.
- Every value was computed read-only from the workbook with pandas.
- The values were then recomputed a second, independent way, by loading the three sheets into SQLite and running SQL (and raw-text `LIKE` on the mojibake strings). 43 of 43 checks matched.
- Meeting evidence sets are exact-match sets. For accent and `±` questions the ground truth uses the repaired text; the pipeline itself still uses raw text (assumption A11). Repair is used only to define what a correct answer is.
- Each entry lists known wrong values (`wrong_values`) where a common mistake gives a specific different number, so failures can be tagged by cause.

## 4. Regression principles

1. **Same benchmark, every version.** Baseline, V1 and V2 are all scored on the same 50 questions with the same scoring rules.
2. **Known failures stay.** A question that failed in an earlier version remains in the set after it is fixed, so regressions are caught.
3. **No re-picking.** Improvement is measured on the existing questions. Do not swap in new questions to show a gain; additions go in as a new versioned set (v0.2) and never replace a v0.1 question.
4. **Holdout.** 10 questions (2 per category) are kept apart in [`tests/evaluation/holdout/holdout_questions.json`](../../tests/evaluation/holdout/holdout_questions.json). Implementation and tuning work should not need to read that file. Fix against the visible 40 questions; evaluate on the holdout after a change, not while tuning. Their definitions and ground truth are unchanged from the original 50-question set. The separation is by file, not access control, so it relies on discipline.
5. **Failure-mode tags, not just pass/fail.** Each result should record which `failure_mode_target` tags were hit, so V1/V2 changes can be tied to specific failure modes.
6. **Frozen definitions.** A change to the metadata that alters an expected answer requires a new benchmark version, not a silent edit.

## 5. Failure-mode tags and coverage

Question lists show visible questions only. "holdout only" means the tag is covered only by holdout questions.

| Tag | Meaning | Questions |
|---|---|---|
| `ENT_AMBIGUOUS_ID` | Bare or ambiguous client id (12345 exists as A/B/C/D) | ADV-01 |
| `ENT_NAME_FORMS` | Multiple name forms for one entity (id, `Client_X`, `Client X Holdings`, email) | ADV-02, ADV-07 |
| `ENT_DEAL_PREFIX` | Deal-name prefix collision (`Orion Infrastructure I` / `IV`) | INV-07 |
| `ENT_DEALID_MULTI_NAME` | `deal_id` and `deal_name` not 1:1 (`DL100001`) | INV-02, INV-08 |
| `ENT_AMBIGUOUS_NAME` | Ambiguous word or name (Summit, Rahul) | ADV-03 |
| `ENT_MEETING_ONLY` | Client present in meetings only, or absent everywhere | MEET-01, HYB-05 |
| `INV_ROW_VS_DISTINCT` | Row count versus distinct client/deal counts; repeated records | INV-01, INV-02 |
| `INV_RM_RECORD_LEVEL` | RM is record-level, several RMs per client (`AccountRM`) | INV-04, HYB-07, ADV-07 |
| `INV_STATUS_RELATIONSHIP` | `ClientStatus` is per record, not per client | INV-06 |
| `INV_USD_VS_NATURAL` | USD field versus natural-currency amounts | INV-03, INV-10, ADV-05 |
| `INV_RANKING` | Ranking / top-N over near-tied counts | holdout only |
| `PERF_LATEST_PER_CLIENT` | Latest snapshot per client, not global | PERF-01, PERF-02, PERF-04, HYB-01, HYB-09 |
| `PERF_EXCLUDE_GROUP_ROWS` | Group rows excluded from client-level answers | PERF-02, PERF-04, PERF-08, PERF-10 |
| `PERF_MOIC_TEXT` | MOIC stored as text; needs numeric normalisation | PERF-01, PERF-02, PERF-06, HYB-09 |
| `PERF_UNAVAILABLE_METRIC` | Metric does not exist for the family: answer unavailable | PERF-05 |
| `PERF_TREND_AVERAGE` | Trend or average across noisy snapshots | PERF-06, PERF-07 |
| `PERF_DATE_COVERAGE` | Different latest dates across clients | holdout only |
| `MEET_FILTER_BEFORE_RETRIEVAL` | Deterministic entity/attribute filtering before text retrieval | MEET-04, MEET-05, MEET-07, MEET-08, ADV-10 |
| `MEET_REPETITIVE_TEXT` | Company/sector/region repeated in every summary | MEET-07 |
| `MEET_SPARSE_STAGE` | `investment_stage` absent from the text | MEET-05 |
| `MEET_NULL_ACTION_ITEMS` | NULL `action_items` = not recorded | MEET-02 |
| `MEET_DEALSIZE_TEXT` | `deal_size_estimate` is text (`k`/`M`) | MEET-04 |
| `MEET_ENCODING` | Mojibake in attendees or summary | MEET-06, MEET-10 |
| `HYB_AGG_BEFORE_COMBINE` | Aggregate each source at its own grain, then combine | HYB-02, HYB-03, HYB-07, HYB-09, HYB-10 |
| `HYB_ROW_MULTIPLICATION` | Row-level join inflates counts and sums | HYB-02, HYB-03 |
| `HYB_DATE_COVERAGE` | Sources end on different dates | HYB-01, HYB-06, ADV-06 |
| `HYB_POPULATION_GAP` | Populations differ (192 shared, 808 meeting-only) | HYB-05 |
| `SEM_PARAPHRASE` | Paraphrase with little word overlap (semantic stress test) | MEET-08, HYB-09, ADV-10 |
| `UNDOCUMENTED_SEMANTICS` | Undocumented meaning must not be invented | PERF-10, HYB-07, HYB-10, ADV-07, ADV-08 |
| `TIME_ANCHOR` | Relative time with no defined 'today' | ADV-06 |

## 6. Semantic stress tests (lexical vs embeddings)

These are answerable from the data and have exact evidence, but the wording avoids the words in the evidence sentence. They are meant to show whether lexical retrieval is sufficient and whether embeddings later add measurable value. One more semantic stress question is in the holdout set. `MEET-07` is the lexical control (uses the summary's own words) and `MEET-10` is a needle question with an exact number.

| ID | Question | Evidence sentence in the meeting text | Content words shared with the evidence |
|---|---|---|---|
| MEET-08 | In client D12349's meetings, which ones raised concern that the company has no full-time finance chief and relies on outside providers for cash management? | Operational readiness was discussed at length: the sponsor currently lacks a dedicated CFO and is outsourcing treasury functions, which increases execution risk. | none |
| HYB-09 | Which clients with a latest CI Total MOIC below 1.0x had a meeting since 2026-01-01 where it came up that nobody in the company holds a permanent finance-chief role? | Operational readiness was discussed at length: the sponsor currently lacks a dedicated CFO and is outsourcing treasury functions, which increases execution risk. | none |
| ADV-10 | For client A12353, which meetings raised that part of the income arrives in dollars while costs are paid in local money? | Currency exposure is significant with ~30% of revenues denominated in USD but expenses largely local; a hedging policy and scenario P&L were requested. | `local` |

The last column lists the content words (stop-words removed, words of 3+ letters) that the question shares with the evidence sentence. It was measured mechanically. A low overlap makes lexical retrieval likely to struggle; it does not prove it, and whether embeddings close the gap is an experiment for later versions. `HYB-09` also requires the structured MOIC filter, so its retrieval part is only one component.

## 7. Questions that are rule-based rather than a single value

These cannot be reduced to one value because the correct behaviour is to disambiguate, decline, qualify, or report a set of values. Each has a deterministic rubric (`must_include` / `must_not`) plus reference facts computed from the workbook. Scoring needs a rubric or reviewer, not string equality.

| ID | Why not a single value |
|---|---|
| INV-04 | Correct answer is 'no single RM' plus a record-level breakdown |
| INV-06 | Must be qualified as relationship/record-level; performance status only if shown separately |
| PERF-05 | Correct answer is 'unavailable' |
| PERF-06 | A number is accepted only with a stated caveat |
| PERF-07 | Correct answer is a series and a no-trend statement |
| MEET-02 | Correct answer is 'not recorded' |
| HYB-01 | A computed value plus a rule: last met must come from meeting_notes_20k, not Client_Last_Met_Date |
| HYB-05 | Mixed: unavailable performance plus a found meeting |
| HYB-07 | Correct answer is 'not answerable' |
| ADV-01 | Ambiguity must be surfaced |
| ADV-03 | Ambiguity must be surfaced |
| ADV-05 | Mixed-currency sum must be declined |
| ADV-06 | Runtime current date as reference; window stated; coverage qualification required |
| ADV-07 | Non-authoritative field must not be used |
| ADV-08 | Meaning is undocumented |

## 8. Questions

### Structured investment

| ID | Diff. | Question | Expected answer / rule | Targets | Split |
|---|---|---|---|---|---|
| INV-01 | medium | How many investment records does client A12345 have for the Emerald Real Estate Fund? | 48 records | `INV_ROW_VS_DISTINCT` | visible |
| INV-02 | medium | How many investment records does client C12355 have, and how many distinct deal names and distinct deal IDs do those records cover? | 251 records; 9 deal names; 8 deal IDs | `INV_ROW_VS_DISTINCT`, `ENT_DEALID_MULTI_NAME` | visible |
| INV-03 | medium | What is the total USD amount across all of client A12345's investment records? | 472,004,607.02 USD | `INV_USD_VS_NATURAL` | visible |
| INV-04 | hard | Which relationship manager manages client B12346? | (rule) No single RM: Sara Khan 53, Rahul Mehta 44, Carlos Gomez 44, Daniel Lee 39, Priya Sharma 36 | `INV_RM_RECORD_LEVEL` | visible |
| INV-06 | hard | How many clients are Closed? | (rule) 192 clients have >=1 Closed record (12,488 records); status is relationship/record-level; performance status (no Closed value) only if shown separately | `INV_STATUS_RELATIONSHIP` | visible |
| INV-07 | hard | What is the total USD amount for the Orion Infrastructure I deal? | 14,184,184,822.55 USD (7,794 records); prefix match would give 15,272,474,828.30 | `ENT_DEAL_PREFIX` | visible |
| INV-08 | hard | How many investment records carry deal ID DL100001, and which deal names appear on them? | 8,950 records; NorthBridge Growth Fund 8,356; Orion Infrastructure IV 594 | `ENT_DEALID_MULTI_NAME` | visible |
| INV-10 | medium | What is the total original-currency amount of all GBP-denominated investment records, and what is that in USD? | 20,880,959,986 GBP = 26,101,199,982.50 USD (8,370 records) | `INV_USD_VS_NATURAL` | visible |

### Structured performance

| ID | Diff. | Question | Expected answer / rule | Targets | Split |
|---|---|---|---|---|---|
| PERF-01 | medium | What is client A12345's latest CI Current MOIC? | 1.33x (as of 2024-10-25) | `PERF_LATEST_PER_CLIENT`, `PERF_MOIC_TEXT` | visible |
| PERF-02 | hard | Which client has the highest CI Total MOIC in its latest performance snapshot? | A12350, 3.5x (as of 2021-06-20) | `PERF_LATEST_PER_CLIENT`, `PERF_EXCLUDE_GROUP_ROWS`, `PERF_MOIC_TEXT` | visible |
| PERF-04 | medium | What is the average latest Total AUM amount across all clients? | 9,971,615.68 (no currency stated in data) | `PERF_LATEST_PER_CLIENT`, `PERF_EXCLUDE_GROUP_ROWS` | visible |
| PERF-05 | hard | What is client A12345's latest COP MOIC? | (rule) Unavailable: there is no COP MOIC column | `PERF_UNAVAILABLE_METRIC` | visible |
| PERF-06 | hard | What is client A12345's average CI Current MOIC across its snapshots? | (rule) Mean 1.7812 only with caveat (8 snapshots 2010-05-06..2024-10-25); latest is 1.33x | `PERF_TREND_AVERAGE`, `PERF_MOIC_TEXT` | visible |
| PERF-07 | hard | Has client A12345's CI Current MOIC been improving over time? | (rule) No consistent trend: 0.93x, 3.0x, 1.29x, 1.52x, 2.26x, 1.94x, 1.98x, 1.33x | `PERF_TREND_AVERAGE` | visible |
| PERF-08 | medium | How many clients have performance data, and how many performance snapshots do those clients have in total? | 192 clients; 1,536 snapshots (group rows excluded) | `PERF_EXCLUDE_GROUP_ROWS` | visible |
| PERF-10 | hard | What is the Total AUM amount for group 346? | 4,040,540.43 (group row, as of 2012-04-26) | `PERF_EXCLUDE_GROUP_ROWS`, `UNDOCUMENTED_SEMANTICS` | visible |

### Meeting retrieval

| ID | Diff. | Question | Expected answer / rule | Targets | Split |
|---|---|---|---|---|---|
| MEET-01 | easy | How many meetings are recorded for client B12463, and over what date range? | 22 meetings, 2022-02-20..2026-01-12 (meeting-only client) | `ENT_MEETING_ONLY` | visible |
| MEET-02 | hard | What were the action items from client A12345's meeting on 2022-10-01? | (rule) Not recorded (NULL) - must not be read as 'no actions' | `MEET_NULL_ACTION_ITEMS` | visible |
| MEET-04 | hard | Which of client A12345's meetings had the largest estimated deal size, and what was it? | Meeting 17166: 150M USD (150,000,000 USD) | `MEET_DEALSIZE_TEXT`, `MEET_FILTER_BEFORE_RETRIEVAL` | visible |
| MEET-05 | medium | How many meetings are at the series B stage? | 2,824 meetings | `MEET_SPARSE_STAGE`, `MEET_FILTER_BEFORE_RETRIEVAL` | visible |
| MEET-06 | hard | Which meetings did Sanjay López attend? | 14 meetings: [2, 348, 1100, 1240, 2050, 2334, 3560, 6041, 8716, 13658, 16485, 17056, 19270, 19916] | `MEET_ENCODING` | visible |
| MEET-07 | medium | In client A12346's meetings, which ones flag the absence of independent directors on the board? | 6 meetings: [4147, 5506, 11756, 12801, 13181, 17742] | `MEET_FILTER_BEFORE_RETRIEVAL`, `MEET_REPETITIVE_TEXT` | visible |
| MEET-08 | hard | In client D12349's meetings, which ones raised concern that the company has no full-time finance chief and relies on outside providers for cash management? | 3 meetings: [3664, 5791, 9932] | `SEM_PARAPHRASE`, `MEET_FILTER_BEFORE_RETRIEVAL` | visible |
| MEET-10 | hard | In client A12346's meetings, which one discusses sensitivity to a ±128 bps change in growth? | Meeting 5506 (2025-08-10) | `MEET_ENCODING` | visible |

### Hybrid

| ID | Diff. | Question | Expected answer / rule | Targets | Split |
|---|---|---|---|---|---|
| HYB-01 | hard | For client A12345, what is the latest CI Total MOIC and when was the client's most recent meeting? | MOIC 0.78x as of 2024-10-25; last met = latest meeting 2026-03-13 (id 11640) from meeting_notes_20k, not Client_Last_Met_Date (+ rule: last met from meetings) | `HYB_DATE_COVERAGE`, `PERF_LATEST_PER_CLIENT` | visible |
| HYB-02 | hard | How many investment records and how many meetings does client B12346 have? | 216 investment records; 22 meetings | `HYB_ROW_MULTIPLICATION`, `HYB_AGG_BEFORE_COMBINE` | visible |
| HYB-03 | hard | What is the total USD investment amount for client C12355, and which company and sector did their most recent meeting involve? | 437,086,022.21 USD; latest meeting 8038 (2026-03-01): BlueOak Partners, manufacturing | `HYB_ROW_MULTIPLICATION`, `HYB_AGG_BEFORE_COMBINE` | visible |
| HYB-05 | hard | What is the latest CI Total MOIC for client B12463, and what action items were recorded at their most recent meeting? | (rule) No performance data (meeting-only client); latest meeting 706 on 2026-01-12 | `ENT_MEETING_ONLY`, `HYB_POPULATION_GAP` | visible |
| HYB-06 | medium | How many of client A12345's meetings took place after its latest performance snapshot, and what are the two dates? | Snapshot 2024-10-25; 8 of 23 meetings are later (latest 2026-03-13) | `HYB_DATE_COVERAGE` | visible |
| HYB-07 | hard | How many meetings were held by relationship manager Priya Sharma? | (rule) Not answerable: meetings have no RM field | `HYB_AGG_BEFORE_COMBINE`, `UNDOCUMENTED_SEMANTICS`, `INV_RM_RECORD_LEVEL` | visible |
| HYB-09 | hard | Which clients with a latest CI Total MOIC below 1.0x had a meeting since 2026-01-01 where it came up that nobody in the company holds a permanent finance-chief role? | 6 clients: ['A12351', 'A12356', 'A12413', 'C12348', 'C12375', 'D12345'] | `SEM_PARAPHRASE`, `HYB_AGG_BEFORE_COMBINE`, `PERF_LATEST_PER_CLIENT`, `PERF_MOIC_TEXT` | visible |
| HYB-10 | medium | For client D12376, list the deal names in its investment records and the sectors and companies in its meetings. | 8 deal names; 10 sectors; 11 companies | `HYB_AGG_BEFORE_COMBINE`, `UNDOCUMENTED_SEMANTICS` | visible |

### Adversarial / edge / semantic

| ID | Diff. | Question | Expected answer / rule | Targets | Split |
|---|---|---|---|---|---|
| ADV-01 | hard | Show me the investments for client 12345. | (rule) Ambiguous: A12345, B12345, C12345, D12345 all exist | `ENT_AMBIGUOUS_ID` | visible |
| ADV-02 | medium | How many investment records does Client A12345 Holdings have? | 258 records (same as client A12345) | `ENT_NAME_FORMS` | visible |
| ADV-03 | hard | Tell me about Summit. | (rule) Ambiguous: deal 'Summit Credit Opportunities' vs meeting company 'Summit Advisors' | `ENT_AMBIGUOUS_NAME` | visible |
| ADV-05 | medium | What is the total investment amount across all records in the original currency? | (rule) Refuse mixed-currency sum; USD total = 91,123,356,224.62 | `INV_USD_VS_NATURAL` | visible |
| ADV-06 | hard | Which clients did we meet recently? | (rule) Reference date = runtime current date; window stated; last met from meeting_notes_20k; qualify when the window exceeds coverage (meetings end 2026-03-13) | `TIME_ANCHOR`, `HYB_DATE_COVERAGE` | visible |
| ADV-07 | hard | Who is cgomez@investcorp.com and how many investment records do they manage? | (rule) Email is non-authoritative; it maps to all five AccountRM names | `ENT_NAME_FORMS`, `INV_RM_RECORD_LEVEL`, `UNDOCUMENTED_SEMANTICS` | visible |
| ADV-08 | medium | What does the L3Y_DIS field mean in the performance data? | (rule) Undocumented: state so; do not invent an expansion | `UNDOCUMENTED_SEMANTICS` | visible |
| ADV-10 | hard | For client A12353, which meetings raised that part of the income arrives in dollars while costs are paid in local money? | 3 meetings: [7568, 19047, 19238] | `SEM_PARAPHRASE`, `MEET_FILTER_BEFORE_RETRIEVAL` | visible |

## 9. Known limitations of this benchmark

- **Small samples per failure mode.** Most modes are covered by one to four questions, so results are indicative, not statistically strong.
- **Client concentration.** Several questions use clients A12345, A12346 and B12346, and one client each for the others. Behaviour on other clients is not sampled.
- **Semantic questions are templated.** The meeting text is assembled from a pool of repeated sentences. Retrieval results here may not transfer to varied real text.
- **Rule-based scoring needs judgement.** Section 7 questions cannot be scored by string match.
- **Holdout is separated by file, not access control.** Anyone with repository access can read it (principle 4).
- **Rules agreed for three ambiguous questions.** `INV-06` (status is relationship-level; performance status only shown separately), `HYB-01` (last met = latest meeting date in `meeting_notes_20k`, not `Client_Last_Met_Date`) and `ADV-06` (runtime current date as reference, with an explicit coverage qualification when the period exceeds the data) now encode these rules. These three questions (status precedence, last-met precedence and the time anchor) are now resolved in the data dictionary (`docs/metadata/data_dictionary_DRAFT.md`: A4, A13, A14, sections 5 and 8, and marked resolved in section 11). The other open metadata questions in section 11 remain intentionally unresolved. `ADV-06` cannot have a fixed answer because it depends on the runtime date.
- **Ground truth for accent and `±` questions** depends on the repair used for evidence only. The raw text and the repaired text define different exact-match behaviour by design.

## 10. Change log

- **v0.1:** holdout questions moved to `tests/evaluation/holdout/holdout_questions.json`; expected behaviour of `INV-06`, `HYB-01` and `ADV-06` tightened. No question text, answer or ground truth was changed otherwise.
