# V1 Implementation Contract (v0.1)

Status: **proposed, not implemented.** This contract is derived strictly from the approved [`docs/evaluation/v1_diagnosis.md`](../evaluation/v1_diagnosis.md), with the two approved changes and the exclusions you stated. It extends [`baseline_contract.md`](baseline_contract.md); where they differ, section 9 lists the point and asks for a decision. No code, benchmark, holdout, metadata or Baseline file is touched by this document.

## 0. Sources and precedence

1. Your latest instruction and CLAUDE.md.
2. `v1_diagnosis.md` (the evidence for what V1 may change).
3. `baseline_contract.md` (unchanged rules that V1 inherits: D1 Claude CLI only, D2 LLM-generated guarded SQL, D4 lexical engine, D5 relative dates, D6 candidate cap 10, A5, A8, A11, A13).
4. `docs/architecture/{entity_resolution,question_routing,structured_query,meeting_retrieval,baseline_service}.md` and `docs/evaluation/baseline_results.md` for what exists.

## 1. Scope

**Approved V1 changes (only these two):**

| # | Change |
|---|---|
| V1-1 | Meeting-side evidence and query-term handling |
| V1-2 | Guarded, read-only structured path for meeting aggregates |

**Not in V1:** the third proposed change (showing the SQL explanation and entity constraint to the synthesizer for structured evidence). It is recorded as deferred; nothing in this contract depends on it.

**Explicitly out of scope:** embeddings; a vector database; semantic retrieval; LLM reranking; LangGraph; multi-agent logic; memory; autonomous planning; any additional provider or model; benchmark redesign. Also out, because the diagnosis did not approve them: encoding repair or query-side mojibake variants (MEET-06), relative-date windows (ADV-06) and parsing of date relations such as "since/after/before <date>" (HYB-09; V1 refuses these questions explicitly, see 4.3 and D-V1-3), passing a structured date to the meeting side (HYB-06), set intersection across sources (HYB-09), the email response (ADV-07), and SQL prompt changes for investment/performance (PERF-06).

**Frozen (must not be edited):** all files under `src/baseline/`, the benchmark, the holdout, the metadata, the workbook. V1 code is new code that imports Baseline modules (section 9, D-V1-1).

## 2. Traceability: Baseline failures to V1 changes

| Change | Baseline failures it targets | Expected to move | Not expected to move (still fail after V1) |
|---|---|---|---|
| V1-1 | HYB-01, HYB-03, HYB-05 (latest-meeting lookups with wrong search terms); MEET-07 (withheld for the derived count 17) | HYB-01, HYB-03 incorrect to correct; HYB-05 needs-review, expected to satisfy its rule; MEET-07's answer no longer withheld | HYB-06 (may improve by ordering; not a target) |
| V1-2 | MEET-01, MEET-04, MEET-05 (aggregates); HYB-10 (distinct values) | those four incorrect to correct | ADV-06 (per-client window; relative-date rule unapproved), HYB-06 |
| neither | MEET-06 (encoding), MEET-08 (semantic), HYB-09 (semantic; becomes `unsupported` for its date relation, D-V1-3), INV-04, INV-06, PERF-06, ADV-07 | expected unchanged, still not correct | (they are the residual list for the V2 gate, section 8) |

Attribution rule for the two changes: **record lookups** (latest or most recent meeting and its attributes, action items) are V1-1 (existing newest-first listing); **values computed over the meeting set** (counts, earliest date, date range, distinct values, extremes) are V1-2. This resolves the diagnosis's note that HYB-10 could belong to either: it belongs to V1-2.

## 3. V1 architecture

```text
question
  -> entity resolution                (Baseline, frozen)
  -> routing                          (Baseline router, frozen)  investment | performance | meeting | hybrid | ambiguous
  -> entity gate                      (Baseline semantics: ambiguous/unknown -> clarification, no evidence, no Claude)
  -> per-side evidence
       structured side (investment/performance)  Baseline StructuredQueryEngine, frozen
       meeting side    V1 meeting-intent classifier (deterministic cue rules, section 5.4)
            |-- topical     V1-1 term derivation -> Baseline FTS5/BM25 retriever (unchanged)   -> MeetingEvidence
            |-- listing     V1-1 no topical term + filter -> Baseline newest-first listing       -> MeetingEvidence
            `-- aggregate   V1-2 guarded LLM SQL over approved view `meetings_meta`             -> MeetingAggregateEvidence
       (hybrid: structured side and meeting side run independently; no row join; combined only as separate evidence)
  -> synthesis (V1 prompt/serialization: adds meeting metadata, unmatched count, [meetings] source)   one Claude call
  -> validation (Baseline checks + V1 enabling adaptations, section 6)
  -> BaselineResponse shape (+ meeting_mode, term_audit, meeting_aggregate_evidence)
```

LLM calls per question: structured or topical/listing meeting question: as Baseline (0-2). A meeting aggregate adds one SQL-generation call; a hybrid with an aggregate meeting side makes three LLM calls.

## 4. V1-1: Meeting-side evidence and query-term handling

**Problem:** request, control and attribute words become search terms and match nothing or noise (`many date range`, `ci total`, `total company sector involve`, `many after two dates`, `sectors companies`), so a correct latest-meeting or attribute lookup returns "no lexical match".
**Evidence:** section 4.3 of the diagnosis; with empty terms the frozen retriever returns the correct latest meeting at rank 1 for HYB-01, HYB-03 and HYB-05 (verified, no LLM). MEET-07: "17" is scope 23 minus matched 6.
**Reason:** a deterministic fix inside the Baseline scope (baseline_contract section 18); no embeddings needed.

### 4.1 Component(s) changed

| Component | Change |
|---|---|
| New `derive_topical_terms` (V1 module, wraps the frozen `derive_query`) | Removes non-evidence-bearing words, records an audit |
| V1 replacement for `service._hybrid_meeting_query` | Uses the same derivation; still removes the structured route's signal tokens |
| Baseline `MeetingRetriever` | **Unchanged**; called with `query_text` (topical terms, possibly empty) and the existing filters |
| V1 synthesis serialization | Adds meeting metadata fields and the unmatched count (4.4) |

### 4.2 Term derivation rules

Applied to unquoted single words, after entity text, ISO dates and Baseline stopwords are removed (Baseline behavior is the starting point; V1 only removes more).

| Class | Removed | Initial contents (only words observed in failures; additions need recorded evidence) |
|---|---|---|
| R1 request/control | always (a question with a date relation applied to an ISO date never reaches term derivation: 4.3) | `many, date, dates, range, after, since, largest, estimated, involve, up, two` |
| R2 field names of the meetings table (and plurals) | always | `company, companies, sector, sectors, region, regions, stage, stages, size, series, deal, deals` |
| R3 structured-side vocabulary | hybrid questions only | `ci, re, hf, cop, total, current, moic, irr, aum, usd, amount, amounts, investment, investments, performance, snapshot, snapshots, names, below, above, than`, and multiples such as `0x`, `1.0x` |

**Protected (never removed):** quoted phrases (kept as phrases); numeric tokens and the unit next to them (`128`, `bps`, `%`), except R3 multiples in hybrids; tokens of the entity text are already removed by the Baseline logic; the action-items handling (the words `action items` are not searched and the weight rule is unchanged).

**Audit:** every derivation returns `term_audit = {kept: [...], removed: [{word, class}], phrases: [...]}`, stored in the response, so the evaluation can check that no evidence-bearing word was removed.

**Deterministic and lexical only:** no synonyms, no LLM rewriting, no stemming, no reranking (baseline_contract section 8 "Must NOT").

### 4.3 Empty-term behavior

| Situation | Behavior |
|---|---|
| At least one topical term or phrase remains | Baseline lexical search over the filtered meetings, unchanged |
| No topical term and an entity, group or date filter exists | **Existing deterministic newest-first listing** of the filtered meetings (`rank_method = date_desc`), K unchanged (20; maximum 50). The evidence states it is a listing, not a topical match, and `N of M` when M exceeds K |
| No topical term and no filter | Unchanged Baseline outcome (`no_terms`, clarification), unless V1-2 classifies it as an aggregate (5.4) |

**Date relations are unsupported in V1.** If a relation word (`since`, `after`, `before`, `until`, `prior to`, `between`, `on or after`, `on or before`) applies to an ISO date in the question, V1 returns status `unsupported` (code `date_relation_unsupported`) with the message that date ranges and relations are not supported, and states that nothing was searched. It never converts the date into the Baseline exact-date filter, and it never drops the relation word and keeps the date. No evidence is collected and no Claude call is made; for a hybrid question the whole question is unsupported (no partial answer). A bare ISO date with no relation word ("the meeting on 2022-10-01") remains an exact-date filter, as in Baseline (MEET-02). Detection is a fixed rule on the question text, before term derivation and before any evidence step.

Entity-first filtering is unchanged (client, group, exact date; ambiguity or unknown entity gate first; an RM is unsupported; a deal is search text only). Raw text and mojibake are preserved (A11). K and the FTS index are unchanged.

### 4.4 Evidence given to synthesis

Baseline already gives id, date, client, group, company, matched fields, `action_items` state and a snippet. V1 adds, deterministically:

| Item | Rule |
|---|---|
| `sector`, `region`, `investment_stage`, `deal_size_estimate` (raw text, and the normalized USD value where parse status is `ok`) | Included on each hit **only for the fields the question names** (R2 words: sector, region, stage/series, company, deal size). Meeting date and company stay always included |
| Unmatched count | In lexical mode: `meetings in scope without a text match: (scope - matched)`, computed by the code. In listing mode: `showing N of M meetings in scope` |
| Mode line | `search mode: lexical` or `newest-first listing, not ranked by relevance` |

The size cap (16,000 characters, lowest-ranked hits dropped first) is unchanged.

### 4.5 Expected benefit, risks, metrics, failure evidence

| | |
|---|---|
| Expected benefit | HYB-01 and HYB-03 correct; HYB-05 rule satisfied (meeting 706, date, action items); MEET-07's correct answer accepted |
| Regression risks | A removed word may be topical in some meeting question (`company`, `deal`, `size`, `stage`); an unranked listing may be read as a topical match; larger prompts; the model may cite `[meeting evidence]` again (the frozen notation rule stays) |
| Metrics vs Baseline | Per-question term lists before/after (from `term_audit`); topical meeting questions' terms identical to Baseline for MEET-02, MEET-07, MEET-10, ADV-10 (no evidence-bearing word lost); evidence correctness and final status for HYB-01/03/05; answers withheld by validation and their reasons; prompt characters and trimmed-evidence flag; latency |
| Evidence the change failed | A lexical control (MEET-02/07/10, ADV-10) loses evidence or changes result; HYB-01 or HYB-03 still lack the latest meeting although `term_audit` shows correct terms (the diagnosis would then be wrong); more answers withheld by validation than Baseline (excluding MEET-07); an answer presents a listing as topical matches; a question with "since/after/before <ISO date>" is answered or filtered as an exact date instead of returning `unsupported` |
| Result / remaining limitation | Filled in after the run (CLAUDE.md section 6) |

## 5. V1-2: Meeting aggregates

**Problem:** the meeting route only retrieves documents. Counts, date ranges, extremes and distinct values over meetings have no path.
**Evidence:** MEET-01 (count and range), MEET-04 (largest deal size), MEET-05 (count by stage), HYB-10 (distinct sectors and companies); the newest-first listing cannot answer them (oldest meetings cut, deal size not shown, no filter).
**Reason:** baseline_contract section 8 already says filter-only questions "are answered from the filter, not from text ranking" and allows "counting on the filtered set"; Baseline did not implement it. D2 makes guarded LLM SQL the structured mechanism.

### 5.1 Supported aggregate shapes (only those demonstrated by failures)

| Shape | Example failure | Allowed SQL constructs |
|---|---|---|
| A1 count of meetings under filters | MEET-01, MEET-05, HYB-02 | `COUNT(*)`, `WHERE` on approved columns |
| A2 earliest date, or date range (min and max) | MEET-01 | `MIN`, `MAX` on `meeting_date` |
| A3 distinct values of an attribute, with optional counts | HYB-10 | `COUNT(DISTINCT ...)`, `SELECT DISTINCT`, `GROUP BY` one approved attribute |
| A4 largest or smallest deal size, returning the meeting | MEET-04 | `ORDER BY deal_size_estimate_usd ... LIMIT 1`, or `MAX` |

Not supported in V1: per-client grouping or a relative-date window (ADV-06), counts of text matches ("how many meetings mention X": stays topical retrieval and reports `matched_count`), a filter by another source's value (HYB-06), any aggregate over meeting text.

### 5.2 Approved object

A read-only SQL view `meetings_meta` over `meetings` (created by the V1 engine on its own read-only connection, like the Baseline performance views; no change to the database file or the FTS index).

| Column | Notes |
|---|---|
| `meeting_id`, `client_id`, `group_id`, `meeting_date` | group membership is the meetings source's own (baseline_contract section 3) |
| `company`, `sector`, `region`, `investment_stage` | vocabularies given to the generator from the database: 15 companies, 12 sectors, 6 regions, 7 stages (`growth`, `late_stage`, `pre-seed`, `private_equity`, `seed`, `series_a`, `series_b`) |
| `deal_size_estimate` (raw text), `deal_size_estimate_usd`, `deal_size_estimate_parse_status` | numeric derived value, raw preserved; parse status is `ok` for all 20,000 rows today; a row with a non-`ok` status is excluded from A4 and counted |

**Not exposed:** `summary`, `attendees`, `action_items`, `summary_char_length`, `source_row`. Text is retrieval's job; aggregates are over metadata. (A record lookup that needs action items, such as HYB-05, is V1-1.)

### 5.3 Guard and engine

- A new V1 engine (`MeetingAggregateEngine`) follows the Baseline `StructuredQueryEngine` pattern: entity gate, one LLM call, JSON `{sql, explanation}`, static scan and authorizer guard, `EXPLAIN` compile, read-only execution with row cap 50 and timeout 10 s.
- It reuses the frozen `SqlGuard` and `static_scan` (their approved objects are parameters). If the guard cannot accept the new object without editing a Baseline file, implementation stops and reports **BLOCKED** (CLAUDE.md section 11).
- **Single object only:** exactly one approved object (`meetings_meta`) may be referenced. Any second object, join, subquery over another source, the FTS table, or a base table is rejected. This is the "no raw three-way joins" rule made mechanical.
- **No autonomous repair and no retry.** A rejected or failing query is reported once.
- **Outcomes** mirror Baseline: `ok`, `empty_result`, `clarification_needed`, `entity_not_found`, `no_meeting_data`, `unsupported_question`, `llm_failure`, `malformed_llm_output`, `missing_sql`, `sql_rejected`, `execution_error`, `timeout`.
- **Entity semantics** are the Baseline retriever's: resolved client sets `client_id = X` (a meeting-only client works); resolved group sets `group_id = N` with membership from the meetings source only (noted in the evidence); ambiguous or unknown entity stops before any SQL; an RM is `unsupported` (no RM field, A2/A3); a deal name or id is `unsupported` for aggregation (meetings carry no deal field, A5), not silently ignored.
- **Evidence object** `MeetingAggregateEvidence`: question, outcome, sql, explanation, columns, rows, row_count, truncated, `source_tables = ("meetings",)`, entities used, rules applied, timing, llm info, error.

### 5.4 Meeting-intent classification (keeps aggregation separate from topical retrieval)

The frozen router's route stays authoritative. For a `meeting` route, and for the meeting side of a `hybrid`, a deterministic V1 classifier (cue lists in code, no LLM) picks the mode. For a hybrid it looks at the question with the structured-side vocabulary (R3) removed.

Order of rules:

| # | Condition | Mode |
|---|---|---|
| 0 | A date relation applies to an ISO date (4.3) | **unsupported** (`date_relation_unsupported`); no evidence, no Claude call |
| 1 | A topical term or quoted phrase remains after V1-1 term derivation | **topical** retrieval (count cues are ignored; `matched_count` is reported as a lexical count with its caveat) |
| 2 | No topical term and an aggregate cue: `how many`, `number of`, `count`, `earliest`, `oldest`, `first meeting`, `date range`, `over what period`, `largest/biggest/highest/smallest/lowest` with `deal size`, plural attribute nouns after `which/what` (`sectors`, `regions`, `companies`, `stages`), `distinct`, `different`, `unique` | **aggregate** (V1-2) |
| 3 | No topical term and a record-lookup cue: `latest`, `most recent`, `last meeting`, `action items`, an explicit date, or a bare entity filter | **listing** (V1-1) |
| 4 | No topical term, no cue, no filter | unchanged Baseline outcome (`no_terms` clarification) |

Ambiguity between 2 and 3 is resolved by the cue table (latest is a lookup, earliest and range are aggregates) because the listing is newest-first, so the latest meeting is always in it and the earliest can be cut off when the scope exceeds K.

**Hybrid:** the structured side (Baseline engine) and the meeting side run independently; each aggregates or lists its own source; the results are placed side by side in the evidence. No row-level join and no joined sums or counts (baseline_contract section 9 rules 1 and 6). One side failing gives the Baseline `hybrid_partial_failure` (no invented other side).

### 5.5 Expected benefit, risks, metrics, failure evidence

| | |
|---|---|
| Expected benefit | MEET-01, MEET-04, MEET-05 and HYB-10 correct at evidence level |
| Regression risks | A topical question routed to the aggregate path (or the reverse); HYB-02 (correct in Baseline through the scope count) now goes through aggregation; LLM SQL error on deal-size or stage vocabulary; one more LLM call (about 5-9 s) on this route and a third call on hybrids; a new SQL surface; unchanged investment and performance behavior must stay unchanged |
| Metrics vs Baseline | Exact-answer correctness for MEET-01/04/05/HYB-10 against the benchmark's own expected values; mode classification (topical, listing, aggregate) for all meeting-touching questions with a table of expected against observed; guard rejections and outcomes; structured components for investment/performance still correct (Baseline 18 of 18); HYB-02 result; latency and LLM-call counts by route |
| Evidence the change failed | Any of MEET-01/04/05/HYB-10 not correct at evidence level with a valid SQL result (wrong SQL) or a guard/LLM outcome error; a lexical control (MEET-02, MEET-07, MEET-10, ADV-10) sent to the aggregate path; HYB-02 or any investment/performance question that was correct in Baseline becomes incorrect; a query that references a second source is executed |
| Result / remaining limitation | Filled in after the run |

## 6. Enabling adaptations (consequences of the two changes, not extra features)

The frozen validator and prompt cannot handle the new evidence as they are. These adaptations are written in new V1 modules and leave Baseline files untouched. **They go beyond the literal two changes and need your approval (D-V1-2).**

| Need | Why | Adaptation |
|---|---|---|
| `[meetings]` source citation | Aggregate answers must cite something. The frozen validator treats any bracket starting with `meeting` as a malformed meeting reference | V1 synthesis rule adds the `[meetings]` notation for aggregate evidence; the V1 validation wrapper accepts `[meetings]` only when aggregate evidence was used |
| Numbers from aggregate rows | Counts, dates and sums in aggregate rows must be grounded | The wrapper adds the aggregate rows (and their `row_count`) to the evidence numbers and dates |
| Unmatched count | V1-1 shows `scope - matched` to the model; the frozen validator would reject a number it did not know | The wrapper adds `unmatched_count` and the listing's `N of M` to the grounded numbers |
| Synthesis serialization | The prompt must show aggregate evidence and the new meeting fields | V1 `serialize_evidence` and `RULES` extend the Baseline ones (same 16,000-character bound) |

The wrapper calls the frozen `validate_answer` and only waives the numeric/citation reasons that these new evidence items explain. **No validation check is weakened or added** otherwise; in particular there is no derived-arithmetic acceptance, no number-word parsing (HYB-06 stays as a known false negative).

## 7. Evaluation contract

1. **Baseline is the frozen reference.** Its code and its recorded results (`baseline_results.md/json`) do not change. The reference state should be fixed by a git commit or tag, or by a SHA-256 manifest (D-V1-8).
2. **Same benchmark, same runner, same scorer.** Re-run the 40 visible questions with the identical runner and Baseline scoring rules. Question text and ground-truth definitions are not altered to improve V1.
3. **Scorer additions are pre-registered and applied to both systems.** Baseline's scorer returned `False` for MEET-01 and MEET-05 by construction (no structured meeting evidence existed). Before the V1 run, evaluators for aggregate evidence are fixed from the benchmark's own expected values: MEET-01 (22, 2022-02-20, 2026-01-12), MEET-04 (meeting 17166 or 150,000,000), MEET-05 (2,824), HYB-10 (the expected 10 sectors and 11 companies). They are applied to the Baseline results too (Baseline: incorrect, no evidence).
4. **MEET-10 is not retroactively scored correct.** It stays `needs_review` under the same rule in both runs. The post-hoc scoring concerns (diagnosis section 4.7: ADV-01/03 display rule, MEET-08's scope less than K, validator-withheld answers, failure-label rubric) are documented separately and presented as a *sensitivity view*; they do not change the headline comparison.
5. **Paired reporting:** a per-question table (Baseline result, V1 result, movement), category totals, component metrics (entity resolution, routing, structured component, meeting mode, retrieval hit with scope and K, validation), latency by route and LLM calls, and failure-mode counts under the diagnosis rubric.
6. **Regression rule:** any question correct in Baseline that is not correct in V1 is reported and explained; it counts as failure evidence for the responsible change unless a documented benchmark issue is the cause.
7. **Variance:** SQL and synthesis use a non-deterministic LLM. Investment, performance and structured-side paths are unchanged, so their evidence should match; differences are reported as variance, not as V1 effects. No repeat Baseline run is required (D-V1-9); the recorded Baseline results are the reference.
8. **Holdout:** untouched and unread during V1 implementation and tuning. It may be opened only after V1 is frozen and only with your explicit approval, as the confirmation step of baseline_contract section 18.
9. **Documentation per iteration** (CLAUDE.md section 6): Problem, Evidence, Change, Reason (sections 4 and 5), then Result and Remaining limitation in `docs/evaluation/v1_results.md`.
10. **Tests before the run:** `tests/v1/` with unit tests per change (term derivation and audit, listing fallback, cue classifier, view and guard rejections, evidence serialization, validator wrapper) using fakes, no Claude call; the 275 Baseline tests keep passing.

## 8. V2 embedding gate

Semantic embeddings become justified only if, after V1 is measured, there remain **reproducible retrieval failures** where all of the following hold:

1. **Relevant meeting evidence exists**, confirmed meeting by meeting against the benchmark's expected ids.
2. **Entity and filtering are correct** (the client, group and date filters returned the right scope), and the scope is larger than K, so a hit is not free (the MEET-08 situation).
3. **Query-term formulation is correct**: `term_audit` shows only topical terms, no request or attribute words, no lost quoted phrase.
4. **Lexical retrieval still misses or badly ranks semantically equivalent evidence**: the expected sentence shares no content word with the question, and the miss recurs on rerun (retrieval is deterministic, so reproducibility is checked by re-execution and by more than one paraphrase template, not only the CFO sentence used by MEET-08 and HYB-09).

Together with baseline_contract section 19: the encoding impact test has run (MEET-06 excluded), a paired comparison on the same questions shows a measurable gain without loss on the lexical controls (MEET-07, MEET-10, ADV-10 counted as controls), and any holdout confirmation is done only with your approval. No numeric improvement threshold is set here.

V1 is **not expected** to fix MEET-08 or HYB-09. HYB-09 will return `unsupported` for its date relation (D-V1-3), so its meeting side cannot serve as gate evidence until date relations are supported. If MEET-08 still fails after V1 with correct terms and correct filters, that is the first concrete evidence for this gate; it is not yet sufficient (the visible set has one failing paraphrase template).

## 9. Decisions requiring approval

| ID | Decision | Recommendation |
|---|---|---|
| D-V1-1 | **Code layout.** Baseline stays frozen, so V1 is a new package `src/v1/` (and `tests/v1/`) that imports Baseline modules and adds `meeting_terms`, `meeting_intent`, `meeting_aggregates`, `synthesis`, `validation`, `service`. This applies baseline_contract D9 (`src/baseline/`, later `src/v1/`) | Approve |
| D-V1-2 | **Enabling adaptations** (section 6): a V1 synthesis extension and a validation wrapper. They are outside the literal two changes but required: without them V1 evidence would be rejected by the frozen validator. The diagnosis said "leave the validator alone"; that holds for the checks, not for grounding new evidence | Approve, with the wrapper limited as stated |
| D-V1-3 | **DECIDED: date relations are explicitly unsupported in V1.** A question that applies `since`, `after`, `before`, `until`, `prior to` or `between` to an ISO date returns `unsupported` (`date_relation_unsupported`) with no evidence and no Claude call. The system must not silently reinterpret "since <date>" as an exact-date filter. A bare ISO date remains an exact-date filter (MEET-02). HYB-09 will therefore return `unsupported` in V1 (still not correct) | Decided by you; specified in 4.3 and 5.4 rule 0 |
| D-V1-4 | **Approved object** `meetings_meta`: name, the columns in 5.2, and the exclusion of text columns | Approve |
| D-V1-5 | **Classifier**: V1 wraps the frozen router with the cue rules of 5.4 instead of changing the router | Approve |
| D-V1-6 | **Listing size** stays K = 20 (maximum 50); scopes above 20 are shown as `N of M` | Keep |
| D-V1-7 | **Scoring**: headline uses the identical Baseline scoring plus the pre-registered aggregate evaluators (7.3); the sensitivity view (7.4) is separate; MEET-10 stays `needs_review` | Approve |
| D-V1-8 | **Fixing the Baseline reference.** `src/` is currently untracked in git. A commit and tag of the Baseline state needs your explicit approval; the alternative is a SHA-256 manifest of `src/baseline/` recorded under `docs/evaluation/` | Manifest now; commit and tag when you approve |
| D-V1-9 | **DECIDED: the optional Baseline variance re-run is not required.** The recorded Baseline results (`baseline_results.md/json`) are the reference; LLM variance on unchanged paths is reported as such (section 7, item 7) | Decided by you |
| D-V1-10 | **Initial term lists** (4.2) and **cue lists** (5.4), taken only from failures observed | Approve; additions require recorded evidence |

## 10. Consistency check

Against `v1_diagnosis.md`:

| Diagnosis statement | Contract | Status |
|---|---|---|
| V1-1 = query terms, listing fallback, metadata and unmatched count | section 4 | consistent |
| V1-2 = aggregates via the guarded path, `meetings` view, routing distinction, service dispatch | section 5 (view `meetings_meta`, classifier in a V1 wrapper) | consistent; the router is wrapped, not edited (D-V1-5) |
| V1-3 (explanation to synthesizer) optional | excluded | as instructed |
| MEET-07: "leave the validator alone" | section 6 adds a grounding wrapper | **differs in effect**; raised as D-V1-2 |
| HYB-10 could be V1-1 or V1-2 | assigned to V1-2 | resolved in section 2 |
| Expected gains (+HYB-01, 03, 10; MEET-01, 04, 05; HYB-05 review; MEET-07) | section 2 | consistent |
| Embeddings not in V1; gate criteria | sections 1 and 8 | consistent |
| HYB-09 date phrase listed as an isolated defect | explicit `date_relation_unsupported` refusal (4.3, D-V1-3) | consistent; stricter than "document as a limitation" per your decision |
| Scoring recommendations (MEET-10, ADV-01/03, MEET-08, MEET-07) | section 7.3-7.4 | consistent; applied as a separate sensitivity view, per your instruction |

Against `baseline_contract.md`:

| Baseline rule | V1 |
|---|---|
| D1 Claude CLI only; D2 guarded LLM SQL, no repair | same adapter; aggregate SQL guarded, no retry (5.3) |
| Section 8: filter first, lexical only, no synonyms or LLM rewriting, action items NULL = not recorded, deal size numeric, no encoding repair, deal names never joined | preserved (4.2, 4.3, 5.2); the "filter-only questions are answered from the filter (MEET-05)" and "counting on the filtered set" clauses are what V1-2 finally implements, a gap between contract and Baseline, not a contract change |
| Section 9: aggregate first, combine after, no row joins, no RM link, independent structured and text parts | preserved (5.3, 5.4); cross-source filtering and intersection remain out (HYB-06, HYB-09) |
| Section 10 and 11: citations, record-level semantics, validation | inherited; `[meetings]` and grounding additions are the only extensions (section 6) |
| Section 12 and D5: relative dates | unchanged; no window support in V1 |
| Section 18: V1 only from recorded failures, deterministic narrow fix, documented, tested on the visible questions, then holdout | followed (sections 2, 4, 5, 7) |
| Section 19: embeddings gate | restated and extended in section 8, no contradiction |
| Section 16: non-goals | all retained |
| D9 code layout | applied as D-V1-1 |
| Assumptions A2, A3, A5, A8, A11, A13 | preserved |
