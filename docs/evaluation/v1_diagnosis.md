# Baseline Failure Diagnosis and Smallest V1

Read-only analysis of [`baseline_results.md`](baseline_results.md) / [`baseline_results.json`](baseline_results.json). Nothing was implemented, no production file, benchmark, holdout or metadata was changed, no Claude call was made, no benchmark question was re-run, and the holdout was not read.

**What was run for this analysis (scratch script outside the repo):** read-only SQLite queries on `data/derived/baseline.sqlite`; the frozen `derive_query` / `_hybrid_meeting_query` on the question text; the frozen retriever called with an empty `query_text` (a counterfactual for a query-term fix; no LLM); and the frozen validator on the stored evidence and answers. These are diagnostics, not a re-run of the benchmark.

## 1. Diagnostic table (22 questions)

Type key: **CAP** capability gap, **RET** retrieval problem, **QI** query interpretation, **SYN** synthesis problem, **VAL** validation problem, **SCR** benchmark/scoring issue, **NONE** no defect observed. For the needs-review rows, "expected" is the benchmark rule and the last column is an analyst's *provisional reading of the rule against the stored answer*; it is not a score.

### 1a. The 11 auto-scored incorrect

| ID | Cat | Observed | Expected | Component | Root cause | Type | Smallest plausible fix | V1? |
|---|---|---|---|---|---|---|---|---|
| MEET-01 | meeting | Search for `many, date, range`; no match; "not found" | 22 meetings, 2022-02-20..2026-01-12 | meeting route | Retrieval-only route: count and min/max date have no path. Listing newest-first cannot help: 20 of 22 returned, the oldest 2 are cut | CAP | Guarded aggregate over the meetings table | yes (V1-2) |
| MEET-04 | meeting | Search `largest, estimated, deal, size`; no match | Meeting 17166, 150M USD | meeting route | Argmax over the numeric `deal_size_estimate_usd` (parse status `ok` for all 23); no path. Deal size is also not shown to the synthesizer | CAP | Same as MEET-01 | yes (V1-2) |
| MEET-05 | meeting | Search `many, series, stage`; no match | 2,824 series_b meetings | meeting route | Filtered count over the whole table; no entity filter and no topical term, so the retriever returns `no_terms`/no match | CAP | Same as MEET-01 | yes (V1-2) |
| MEET-06 | meeting | 627 text matches for `sanjay lópez attend`; top 20 shown; answer cites 1 of 14 | 14 meetings | retriever | Attendee is stored as mojibake `LÃ³pez` and no repair is applied (A11); OR-of-terms matches every "Sanjay". The stored spelling as a phrase returns exactly the 14 | RET (encoding + OR-of-terms) | Query-side variant of non-ASCII names, or repair; both are encoding-policy decisions | no (policy; encoding test not run) |
| MEET-07 | meeting | 6/6 retrieved at ranks 1-6; answer withheld for "17" | 6 meetings | validator | The answer's "17 other meetings" = scope 23 minus matched 6, both evidence fields; the validator requires each number to be in the evidence. Answer content was correct | VAL | Put the unmatched count in the evidence (or accept a difference of two evidence counts) | yes (folded into V1-1) |
| MEET-08 | meeting | 3/3 expected in the 15 returned (ranks 4, 8, 12), answer says the concern is not shown | 3 meetings | retriever | Question and evidence sentence share **0** content words; expected meetings appear only because 18 in scope ≤ K=20; snippets do not show the sentence | RET (semantic) | Semantic matching | no (see 5) |
| HYB-01 | hybrid | Meeting side searched `ci total`; answer withheld (citation `[meeting evidence]`) | MOIC 0.78; latest meeting 2026-03-13 (id 11640) | meeting-side query | Structured-attribute words became search terms. Counterfactual: with empty terms the frozen retriever returns 11640 at rank 1. The rejection itself is the validator working (notation) | QI (secondary SYN) | Drop request/attribute words; list newest-first when no topical term remains | yes (V1-1) |
| HYB-03 | hybrid | Search `total company sector involve`; no match | Meeting 8038 (2026-03-01), BlueOak Partners, manufacturing | meeting-side query + synthesis input | Same cause. Counterfactual: 8038 at rank 1 with company and sector, but `_meeting_lines` shows company only, not sector | QI + SYN input | V1-1 plus sector/region/stage/deal size in the evidence lines | yes (V1-1) |
| HYB-06 | hybrid | Counted 4 of 13 retrieved meetings; said "three" then "four"; passed validation | Snapshot 2024-10-25; 8 of 23 meetings later | hybrid architecture | The two sides are independent: the snapshot date cannot filter the meeting side. Terms `many after two dates` are noise. Counterfactual: newest-first listing contains all 8 later meetings, but the count is left to the model | CAP (hybrid) | Pass the structured date to the meeting side as `date_from`, or count deterministically | no (architecture decision; V1-1 may fix it by ordering, not guaranteed) |
| HYB-09 | hybrid | 22-client list correct; meeting side found 3 meetings (0 of 31 expected) | 6 clients | meeting side + hybrid | (i) 0 shared content words with the evidence sentence (semantic); (ii) "since 2026-01-01" became an exact-date filter (12 meetings on that day); (iii) no intersection is computed | RET (semantic) + QI + hybrid | Semantic matching first; date-range parsing and set intersection second | no |
| HYB-10 | hybrid | 8/8 deal names correct; `sectors companies` matched nothing | + 10 sectors, 11 companies | meeting-side query + synthesis input | Counterfactual: newest-first listing returns all 18 meetings (≤ K) containing all 10/11 values, but sector is not serialized | QI + SYN input | V1-1 | yes (V1-1) |

### 1b. The 11 needs-review

| ID | Cat | Observed | Expected (rule) | Component | Root cause | Type | Smallest plausible fix | V1? | Provisional reading |
|---|---|---|---|---|---|---|---|---|---|
| INV-04 | inv | All 5 RMs and counts given; says the result "is not filtered by client" | All 5 RMs with counts; no single RM | synthesis | The SQL *was* filtered (`client_id = 'B12346'`); `explanation` and `entities_used` exist in the evidence but synthesis does not show them | SYN | Show the query explanation and entity constraint | optional (V1-3) | must_include met; contains a false statement the rule does not test |
| INV-06 | inv | 192 reported with a record-level caution | 192 clients and/or 12,488 records, record-level statement | synthesis | SQL was correct (`COUNT(DISTINCT client_id) WHERE closed`); answer says it cannot tell what was counted | SYN | Same as INV-04 | optional (V1-3) | likely pass; weak wording |
| PERF-06 | perf | AVG only (1.78125), no count or date span | Average over 8 snapshots 2010-05-06..2024-10-25, with caveat | structured SQL | SQL returned only `AVG`; the rule needs the count and span in the answer | QI | Prompt guidance to return count/min/max date with averages | no (n=1, prompt tuning) | likely fail |
| PERF-07 | perf | 8 dated values in order, "fluctuating", no steady improvement | Ordered series; no consistent pattern | none | none | NONE | none | no | likely pass |
| MEET-02 | meeting | "not recorded ... does not mean there were none" | Not-recorded statement | none | none | NONE | none | no | likely pass |
| MEET-10 | meeting | 5506 named as the match; 11756 cited as **not** a match | Meeting 5506 | scoring | Citation-set equality cannot tell a match from a cited non-match | SCR | Score at evidence level; answer text by review | n/a | correct (retrieval rank 1; answer right) |
| HYB-05 | hybrid | No performance data stated (right); meeting side searched `ci total`, no meeting reported | + meeting 706, 2026-01-12, its action items | meeting-side query | Same as HYB-01. Counterfactual: 706 at rank 1, action items in the hit | QI | V1-1 | yes (V1-1) | fail now; likely fixed by V1-1 |
| ADV-05 | adv | Per-currency totals; refuses to add; no mixed sum | Refuse mixed sum; offer USD or per-currency totals | none | none | NONE | none | no | likely pass |
| ADV-06 | adv | Clarification `no_terms`, no answer | Reference date, stated window, latest meeting per client, coverage caveat (data ends 2026-03-13; runtime date 2026-09-27; 0 meetings in the last 90 days) | meeting route | Needs distinct clients / max date per client over a relative window; "recently" is a stopword so no terms remain | CAP (+QI) | Meeting aggregate path plus a relative-date rule | no (depends on V1-2; window rule needs a decision) | fail |
| ADV-07 | adv | `entity_not_found` for the email; no answer | Say the email is unreliable/contradicts AccountRM | resolver/service | An email is treated as an unknown entity, not as a known non-authoritative field | CAP (policy) | Deterministic email-pattern response | no (n=1; wording is a decision) | fail |
| ADV-08 | adv | `unsupported_question`: "data has no metadata" | State the meaning is not documented | structured engine | LLM classified it as unsupported; message meets the rule loosely | NONE | none | no | likely pass (relies on LLM wording) |

## 2. Failure counts by root cause

| Root cause type | Incorrect (11) | Needs review (11) | Total (22) | IDs |
|---|---|---|---|---|
| Capability gap | 4 | 2 | 6 | MEET-01, 04, 05, HYB-06 / ADV-06, ADV-07 |
| Retrieval problem | 3 | 0 | 3 | MEET-06, MEET-08, HYB-09 |
| Query interpretation | 3 | 2 | 5 | HYB-01, 03, 10 / HYB-05, PERF-06 |
| Synthesis problem | 0 | 2 | 2 | INV-04, INV-06 |
| Validation problem | 1 | 0 | 1 | MEET-07 |
| Benchmark / scoring issue | 0 | 1 | 1 | MEET-10 |
| No defect observed (likely pass) | 0 | 4 | 4 | PERF-07, MEET-02, ADV-05, ADV-08 |

## 3. Clusters

- **C1 Meeting-side evidence for requests that are not a topic** (HYB-01, 03, 05, 10, partly 06 and MEET-07): the meeting side searches the request words and returns nothing, although the frozen retriever's own newest-first listing would have returned the right evidence. Cheapest, deterministic.
- **C2 Aggregates over the meetings table** (MEET-01, 04, 05; ADV-06; HYB-06): counts, min/max, argmax, filtered counts, distinct clients. No deterministic listing suffices.
- **C3 Semantic mismatch** (MEET-08, HYB-09): one evidence sentence used by both questions.
- **C4 Encoding** (MEET-06): stored mojibake.
- **C5 Validator scope** (MEET-07, HYB-06).
- **C6 Synthesis context** (INV-04, INV-06; hedges in 12 of 17 structured `ok` answers, by a crude phrase match, almost all harmless): the synthesizer is not shown what the query did.
- **Isolated:** HYB-09 date phrase; ADV-07 email; PERF-06 count/span.

## 4. Investigations

### 4.1 Meeting analytics: exactly which visible questions

Fail because the meeting route only retrieves documents:

| Need | Questions | Satisfiable by the existing newest-first listing? |
|---|---|---|
| count + min/max date | MEET-01 | No: 20 of 22 returned; the oldest are cut |
| numeric argmax | MEET-04 | No: 23 in scope > K=20, and deal size is not shown to the synthesizer |
| filtered count over the whole table | MEET-05 | No: no filter and no terms, so `no_terms` |
| filtered count against another source's date | HYB-06 | Partly: all 8 later meetings are among the 20 newest, but the model must count |
| distinct values | HYB-10 | Yes if sector is shown (18 in scope ≤ K) |
| distinct clients by latest date in a window | ADV-06 | No |
| "latest meeting" and its attributes | HYB-01, 03, 05 | **Yes**: rank 1 was correct in all three; these are query-term failures, not analytics |

So 3 questions are pure analytics gaps (MEET-01, 04, 05), 2 more need it (ADV-06, HYB-06), and 3 (HYB-01, 03, 05) are wrongly attributed to it: they fail on query terms. Verified ground truth in SQLite for all: 22 / 2022-02-20 / 2026-01-12; 150M USD (id 17166); 2,824; 11640 (2026-03-13); 8038 BlueOak Partners manufacturing; 706 (2026-01-12); 8 of 23; 10 sectors and 11 companies; 0 meetings in the 90 days before the runtime date, 2026 latest date 2026-03-13.

### 4.2 Failures genuinely caused by lexical retrieval

Only three: **MEET-06** (encoding + OR-of-terms + K), **MEET-08** and **HYB-09** (zero shared content words). Everything else in the meeting and hybrid categories fails for another reason: capability (MEET-01/04/05), query terms (HYB-01/03/05/10), hybrid architecture (HYB-06), validation (MEET-07). Lexical retrieval *worked* on MEET-02 (date), MEET-07 (phrase), MEET-10 (number phrase) and ADV-10.

### 4.3 Query-term extraction

Terms actually derived: `many date range` (MEET-01); `largest estimated deal size` (04); `many series stage` (05); `ci total` (HYB-01, 05); `total company sector involve` (HYB-03); `many after two dates` (HYB-06); `sectors companies` (HYB-10); none for ADV-06 (`recently` is a stopword) and `ci total below 0x since up ...` plus an exact-date filter for HYB-09. They fall in three groups: request words (`many`, `date`, `range`, `after`, `two`, `dates`, `largest`, `involve`, `up`), attribute words of the structured side or of a meeting field (`ci`, `total`, `company`, `sector(s)`, `companies`, `size`, `series`, `stage`) and time words (`recently`, `since`).

A deterministic extraction change (no embeddings) **can** fix HYB-01 and HYB-05 (rank-1 latest meeting, action items included), and HYB-03/HYB-10 once sector is serialized. It **cannot** fix MEET-01/04/05 (an empty term set gives a listing or `no_terms`, not an aggregate), HYB-06 (needs the snapshot date), MEET-08/HYB-09 (their real topic words remain and do not match). Risk: `company`, `deal`, `size`, `stage` can be topical in a meeting question; the lexical controls MEET-07, MEET-10, ADV-10, MEET-02 contain none of them.

### 4.4 Semantic retrieval

| Question | Evidence exists | Lexical result | Genuinely semantic? |
|---|---|---|---|
| MEET-08 | yes, 3 meetings | in the returned list only because scope (18) ≤ K (20); matched words `cash`, `management`, `time` are not in the sentence; sentence not visible in snippets | **Yes**: 0 shared content words |
| HYB-09 | yes, 31 meetings | 0 of 31; also date-filter and term-noise defects | **Yes** for the meeting side (same sentence as MEET-08), but other defects would remain even with embeddings |
| ADV-10 | yes, 3 meetings | 3/3 at ranks 1-3 | **No**: one shared word `local` (declared by the benchmark) is decisive, since only these 3 of 23 contain it |
| MEET-07, MEET-10, MEET-02 | yes | success | No (exact words, phrase, date) |

Oracle check (the benchmark's evidence sentence is not something a user supplies): the phrase `"lacks a dedicated cfo"` in D12349's scope returns exactly the 3 expected meetings. So the meetings are findable by their own words; the failure is vocabulary distance. The visible set contains **two** distinct semantic templates (the CFO sentence, used by MEET-08 and HYB-09, and the currency sentence, which lexical overlap solved).

### 4.5 Validator

| Question | Answer correct? | Why accepted or rejected | Smallest deterministic change | Recommendation |
|---|---|---|---|---|
| MEET-07 | Yes, fully | Rejected: number `17` is not in the evidence; it is scope 23 minus matched 6 | Show `meetings in scope without a match: 17` in the evidence, or accept the difference of two evidence counts | Evidence field (in V1-1); leave the validator alone: the rule (no new calculations) did what it specified |
| HYB-01 | Content honest and correct ("cannot be determined") | Rejected: `[meeting evidence]` is not the citation notation | none; the model must comply | No change; with meeting ids in the evidence the model can cite |
| HYB-06 | **No** (3, then 4; expected 8 of 23) | Accepted: number *words* are not parsed; digits `13`, `23` grounded; dates and ids exist; no check that a count equals the number of listed items | Parse number words in count claims and compare with the evidence counts or the number of citations | Not V1: one case, and C1/C2 may remove the situation |
| MEET-08, MEET-06, MEET-01/04/05, HYB-03/09/10 | Honest "not found / not shown", not wrong | Accepted (nothing false was claimed) | none | The validator does not check completeness, by design |
| INV-04 | Contains a false statement about the query | Accepted: no check covers statements about the query | none | Fix by showing the synthesizer the query explanation (V1-3), not by a validator rule |

### 4.6 Hybrid

| Question | Caused by |
|---|---|
| HYB-01, 03, 05, 10 | **Meeting side** (query terms); HYB-03 and HYB-10 also a synthesis-input gap (sector). The structured side was correct in all four |
| HYB-06 | **Hybrid architecture** (independent sides cannot pass a date across) plus noisy terms |
| HYB-09 | **Meeting side** (semantic) and **architecture** (no intersection), plus a date-phrase defect |
| HYB-02, HYB-07 | correct |

The structured side of every hybrid was correct (6/6 components); no hybrid failure is a structured-query failure.

### 4.7 Scoring methodology

Post-hoc or ambiguous decisions in the Baseline scoring:

1. **MEET-10** was moved to needs-review after seeing the answer. Recommendation: score id-set questions at **evidence level** (recall@K, rank, precision in the returned list), decided before any run; score the answer text by rule review. Count MEET-10 as **correct** at evidence level (rank 1) and exclude it from the answer-level automated score.
2. **ADV-01 and ADV-03** were scored correct from `clarification.entities`, but the user-visible `message` does not list the candidates. Recommendation: **needs_review** until a display rule says the candidates are part of the response (or count them correct only if the message lists them).
3. **HYB-02** counts the meeting total from the evidence's scope count and required the answer to state it. Recommendation: correct, tagged "metadata-derived" (the retrieval matched nothing).
4. **MEET-08** shows evidence 3/3 in top-K only because scope ≤ K. Recommendation: report a retrieval hit **with the scope size and K**, and exclude questions where scope ≤ K from the "retrieval hit rate".
5. **MEET-07** is a correct answer that the system withheld. Recommendation: **incorrect** for the system outcome, tagged `validator_withheld_correct`, and reported separately from wrong answers.
6. **Failure-mode labels** were assigned by the analyst after scoring. Recommendation: fix a rubric (component that first deviates from the expected value) before the next run.
7. Needs-review questions should be reviewed by a human against the stored rule (in the JSON) and counted as **excluded from the automated score** until then. The provisional readings in 1b (MEET-10 correct; PERF-07, MEET-02, ADV-05, ADV-08 likely pass; INV-06 weak pass; INV-04 pass with a false statement; PERF-06, HYB-05, ADV-06, ADV-07 fail) are not scores.
8. Pre-decided and reasonable: components required per question (INV-10 needs the amounts, not the record count); `correct` requires status `ok` or an expected refusal.

Proposed reporting classes: **correct** (evidence-level match and the system outcome is right), **incorrect** (evidence or outcome wrong, including validator-withheld), **needs_review** (rule-based; ADV-01/03 pending display rule), **excluded from the automated score** (MEET-10 answer-level; questions with scope ≤ K for the retrieval hit rate).

## 5. V1 decision gate: the smallest V1

Three changes, in priority order. The first two are traced to the two largest observed clusters; the third is optional and small.

### V1-1 Meeting-side evidence for requests that are not a topic

- **Observed failures:** HYB-01, HYB-03, HYB-05, HYB-10 (query terms); MEET-07 (unmatched count); HYB-06 (partly).
- **Components:** `meeting_retrieval.derive_query` and `service._hybrid_meeting_query` (drop request, structured-attribute and time words so an empty term set falls back to the existing newest-first listing); `synthesis._meeting_lines` (add sector, region, stage, deal size and the count of in-scope meetings without a match).
- **Expected benefit:** up to +3 auto-scored (HYB-01, 03, 10), +1 needs-review (HYB-05), and MEET-07's answer no longer rejected. HYB-06 may improve through ordering but is not guaranteed.
- **Risks:** a dropped word may be topical in some meeting question; an unranked listing may be read as a match (the note says it is not); a larger prompt (bounded at 16,000 characters); more wording for the model to misread.
- **Measure:** the 40 visible questions paired against this baseline; the lexical controls MEET-02/07/10 and ADV-10 must not regress; prompt size and latency; per-question search terms before and after; number of answers rejected by validation.

### V1-2 Aggregates over the meetings table through the existing guarded structured path

- **Observed failures:** MEET-01, MEET-04, MEET-05 (pure); ADV-06 and HYB-06 (partly); HYB-10 (alternative to V1-1).
- **Components:** `structured_schema` (an approved `meetings` view without summary/attendees text), `structured_query` (a meetings source, reusing guard and prompts), `question_routing` (separating aggregate cues from topical retrieval), `service` (dispatch). This adds an approved data object and a routing distinction, so it is an **architecture decision for you** (CLAUDE.md section 3). A deterministic "scope facts" bundle attached to every meeting result was considered and rejected: it anticipates question shapes and would overfit the visible set.
- **Expected benefit:** +3 auto-scored (MEET-01, 04, 05); groundwork for ADV-06.
- **Risks:** routing confusion between "how many meetings mention X" (a text match count the engine cannot do) and metadata counts; LLM SQL errors on deal-size and stage vocabulary; a new SQL surface; investment/performance behavior must not move.
- **Measure:** exact-answer correctness on the three; routing outcomes for all 8 meeting questions and the hybrids; SQL guard rejections; investment and performance structured components still 18/18; latency (one more LLM call on this route).

### V1-3 (optional, smallest) Show the synthesizer what the query did

- **Observed failures:** INV-04 (false statement that the result is unfiltered); INV-06 (cannot say what was counted); a hedge about the query in 12 of 17 structured answers (crude phrase match).
- **Component:** `synthesis._structured_lines` adds the evidence's existing `explanation` and entity constraints (no raw SQL).
- **Expected benefit:** no auto-scored change; removes one factual misstatement and some hedging.
- **Risks:** small prompt growth; the model may quote the explanation; it does not fix PERF-06, which needs the count and span in the SQL result.
- **Measure:** INV-04 and INV-06 answers; the hedge count; validation warnings and rejections.

### Considered and not proposed for V1

| Item | Why not now |
|---|---|
| Embeddings | see A |
| Validator change | one false positive (fixed by evidence in V1-1), one false negative (HYB-06, one case) |
| Query-side mojibake variant (MEET-06) | an encoding-policy decision; the encoding impact test has not been run |
| Date phrases ("since", "recently") and the runtime-date window (HYB-09, ADV-06) | no scored gain alone; ADV-06 needs V1-2 and a window rule |
| Cross-source date passing and set intersection (HYB-06, HYB-09) | architecture decision; HYB-09 also needs semantic matching |
| ADV-07 email response, PERF-06 count/span | one question each; wording and prompt decisions |

### Answers

**A. Does the evidence justify semantic embeddings in V1?** No. Two of 40 questions (MEET-08, HYB-09) are genuine semantic failures and both use the same evidence sentence; ADV-10 succeeded lexically; the larger clusters (capability gap 6, query interpretation 5) are not semantic; HYB-09 would still fail on its date filter and missing intersection. The Embedding Decision Gate is not met: lexical improvements inside Baseline have not been tried (V1-1 is the first), and encoding has not been ruled out with the encoding impact test.

**B. What would justify embeddings in V2?** After V1 is measured: failures where (1) the expected meeting exists and is in scope; (2) the terms are topical only (no request words), the entity and date filters are right, and the scope exceeds K so a hit is not free; (3) the expected sentence shares no content word with the question; (4) this holds for **several different paraphrase templates**, not one sentence (the visible set has one); (5) the encoding impact test rules encoding out; and (6) the pre-registered holdout, opened only with your approval, shows the same pattern. A paired comparison (Recall@K, MRR, evidence hit rate) on the same questions, without loss on the lexical controls, would then decide.

**C. Frozen because they performed well:** entity resolution (28/28, including both ambiguity questions and the name form), routing (40/40; V1-2 would add one distinction inside the meeting route, not touch the rest), the structured engine and SQL guard on investment and performance (18/18 structured components correct), the data layer and FTS index, the Claude CLI adapter, and the service's status and failure mapping (every failure was reported honestly; none produced a fabricated answer). Lexical ranking itself stays frozen: it succeeded on MEET-02, MEET-07, MEET-10 and ADV-10.
