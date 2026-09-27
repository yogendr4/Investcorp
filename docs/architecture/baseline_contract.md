# Baseline Implementation Contract (v0.1)

Status: **Approved and implemented.** This contract was implemented as `src/baseline/`, measured on the 40-question visible benchmark ([`baseline_results.*`](../evaluation/baseline_results.md)), and frozen at git tag `baseline-v0.1`. It was subsequently updated in place (dictionary alignment + Abhishek's reconciliation, no benchmark or architecture change) and re-frozen at `baseline-v0.2`; see [`baseline_reference.md`](baseline_reference.md) for the historical-vs-current distinction. Superseded by [`v1_contract.md`](v1_contract.md), [`v2_semantic_retrieval.md`](v2_semantic_retrieval.md) and [`v2_1_evidence_selection.md`](v2_1_evidence_selection.md) for later behavior; this document remains the base layer all of them extend.

## 0. Sources of truth and precedence

| Source | Role |
|---|---|
| [`docs/metadata/data_dictionary_DRAFT.md`](../metadata/data_dictionary_DRAFT.md) | Data facts and agreed assumptions (A1-A18, extended after the official `Assignment_Data_Dictionary.xlsx` arrived). Wins over this document on any data question. |
| [`docs/evaluation/baseline_question_set.md`](../evaluation/baseline_question_set.md) and [`tests/evaluation/questions.json`](../../tests/evaluation/questions.json) | The 40 visible development/regression questions. Frozen. |
| `tests/evaluation/holdout/holdout_questions.json` | 10 held-out questions. **Not read while writing this contract and not to be read during implementation or tuning.** |
| [`CLAUDE.md`](../../CLAUDE.md) | Working rules (scope control, approvals, versioning). |

Governing principle (from the metadata): *never silently guess semantics. Use a documented definition, make an explicit assumption, or surface the ambiguity.* Where this contract cannot decide something safely, it is listed in section 13 and needs human approval before coding.

## 1. Scope

| In Baseline | Not in Baseline |
|---|---|
| Normalised local analytical data (raw values preserved) | Embeddings, semantic/vector retrieval |
| Deterministic entity resolution | LangGraph, multi-agent design, autonomous planning/replanning |
| Structured investment and performance querying | SQL self-repair loops, unlimited retries |
| Lexical meeting retrieval | Model consensus, memory, MCP, external web search |
| Hybrid questions (structured first, then meeting evidence) | Fine-tuning, production RBAC/auth, cloud deployment |
| LLM help only where deterministic logic is insufficient, through the Claude Code CLI (section 21) | Advanced observability, elaborate UI; any LLM provider SDK, API key or second LLM provider |
| Evidence-grounded answers and basic validation | Repairing meeting-text encoding (assumption A11) |
| Evaluation on the 40 visible questions | Resolving metadata questions 4-9 (section 13) |

## 2. Flow

```text
                    +---------------------------------------------+
   Workbook (read-only) --> Loader --> Local analytical store      |  built once, read-only at query time
                    |     raw columns kept + derived numeric cols  |
                    |     views: client / latest-per-client / group|
                    |     lexical index over raw meeting text      |
                    +----------------------+----------------------+
                                           |
User Question --> [C1 Entity Resolution] --+--> ambiguous / not found --> Response (clarify / not found)
                        | resolved entities
                        v
                  [C2 Question Routing] ---> unanswerable / undocumented / unavailable --> Response (decline)
                        | INVESTMENT | PERFORMANCE | MEETING | HYBRID
                        v
                  [C3 Evidence Retrieval]
                     |-- 3a Structured SQL (read-only, validated)
                     |-- 3b Lexical Meeting Retrieval (entity filter first)
                     '-- 3c Hybrid (3a per source -> aggregate -> 3b -> combine by client_id)
                        | Evidence Bundle
                        v
                  [C4 Answer Synthesis] --> [C5 Basic Validation] --> [C6 Response]
                                                   | fail: no retry loop; returns VALIDATION_FAILED with reason
```

Each arrow carries one typed object (section 4). Components do not call each other out of order and do not loop.

## 3. Data layer contract

Logical objects. Physical table names, the file location and the derived-column names are set by the implemented ingestion slice (D3). The performance views are created as read-only temporary views by the structured-query engine (`structured_query.md`).

| Object | Content | Rule (metadata ref) |
|---|---|---|
| `investments` | All 50,000 Investments rows, raw values untouched | Record-level RM and status (A1, A2, A4). USD field for money (dictionary 3.1). |
| `performance` | All 1,632 rows, raw values untouched | Group rows kept but never mixed into client aggregates (A6). |
| `meetings` | All 20,000 rows, raw text as stored | No repair of mojibake (A11). NULL `action_items` stays NULL (A8). |
| Derived MOIC numeric (8 columns) | Text before `x`, exact, unrounded; NULL plus parse-failure flag otherwise | A7, dictionary section 6. Raw column stays. |
| Derived deal size numeric | `<n>k`/`M`/`B` + `USD`, case-sensitive, exact decimal; NULL plus flag otherwise | A7, dictionary section 6. Raw column stays. |
| View: client performance | `performance` with `IsGroup_Flag='N'` | 1,536 rows, 192 clients. |
| View: latest per client | One row per client: maximum `As_Of_Date` among the client's own rows | A6. Never the global maximum. A tie for latest is surfaced, not resolved. |
| View: group performance | `IsGroup_Flag='Y'` rows (96) | Not derived from members (S3). |
| View: last met | `MAX(meetings.date)` per `client_id` | A13. Only source of "last met". |
| Availability table | Which metric exists for which product family | Metadata section 7. Drives "unavailable" (A9). |
| Lexical index | `meetings_fts`: a derived FTS5 external-content index over the raw text of `meetings` (summary, attendees, action_items, company) | Baseline retrieval is lexical only (section 8; `meeting_retrieval.md`). Rebuildable from `meetings`; the analytical tables are unchanged. |

Load-time checks (must pass, else loading fails): row counts 50,000 / 1,632 / 20,000; 192 clients in both structured sheets; 1,536 + 96 performance rows; 0 MOIC parse failures; 20,000 deal-size parses.

## 4. Interfaces (objects passed between components)

| Object | Fields |
|---|---|
| `ResolutionResult` | `status` (RESOLVED, AMBIGUOUS, NOT_FOUND, UNRESOLVED, NONE_NEEDED); `entities` (type, id, canonical name, matched text); `candidates` (when ambiguous); `sources_present` per client (investments, performance, meetings) |
| `RoutingDecision` | `route` (INVESTMENT, PERFORMANCE, MEETING, HYBRID); `reasons`; or `disposition` (CLARIFY, DECLINE) with reason |
| `EvidenceBundle` | `items` (source, object, filter/query, keys, values, `as_of_date`/meeting ids/dates); executed SQL text; retrieval hits with rank and score; `coverage` (start/end dates per source); `runtime_date`; `caveat_triggers` |
| `Answer` | `text`; `claims` (each tagged FACT, INTERPRETATION or CAVEAT, with evidence item ids) |
| `ValidationReport` | Per-check pass/fail with reasons |
| `Response` | `status` (ANSWERED, CLARIFICATION_NEEDED, NOT_FOUND, UNAVAILABLE, UNANSWERABLE, ERROR, VALIDATION_FAILED); `text`; `evidence`; `caveats`; `route`; `resolution`; `validation`; `runtime_date` |

## 5. C1 Entity resolution

| Aspect | Contract |
|---|---|
| Purpose | Turn mentions in the question into canonical entities, or fail explicitly. |
| Input | Question text; dictionaries built from the data (client ids and names, deal ids and names, RM names, meeting companies, sectors, regions, stages, attendee names, group ids). |
| Output | `ResolutionResult`. |
| Allowed | Exact and case-insensitive matching of documented forms; regex extraction of client-id tokens; listing candidates. Fully deterministic. No LLM. |
| Failure conditions | More than one candidate; no candidate for a mention that looks like an entity; a mention matching different entity types. |
| Must NOT | Choose between candidates; use prefix, `LIKE` or fuzzy matching for deal names; resolve via `AccountRMEmail`, `RM_Alias`, `AccountOwnerId` (A3); treat "absent from investments" as "does not exist"; guess a group's membership when sheets disagree. |

| Case | Required behaviour | Visible examples |
|---|---|---|
| Exact client id (`A12345`) | RESOLVED; record which sheets contain the client. | INV-01, INV-03 |
| Known client name forms: `Client_A12345` (`Client Name`), `Client A12345 Holdings` (`AccountName`) | RESOLVED to the same client id. `AccountName_org` is undocumented and is not a known form. | ADV-02 |
| Bare number (`12345`) or partial id | AMBIGUOUS when several ids share it (A/B/C/D). List the candidates. | ADV-01 |
| Deal name | Exact match only. An exact name always wins over a longer name that starts with it. A partial name (`Orion Infrastructure`) is AMBIGUOUS with the candidates listed. | INV-07 |
| Deal id | Exact match. A deal id is an entity in its own right and may carry several names (`DL100001`: two). Never reduced to one name (A5). | INV-02, INV-08 |
| Ambiguous term matching several entity types or people (`Summit`: deal and meeting company; a first name shared by many attendees) | AMBIGUOUS. List candidates by type. Do not merge. | ADV-03 |
| RM | Only the 5 `AccountRM` names. An email or alias is UNRESOLVED as an RM, with the reason. | ADV-07 |
| Meeting-only client (in meetings, not in the structured sheets) | RESOLVED with `sources_present = [meetings]`. Structured routes then answer "no investment/performance data for this client", never "client not found". | MEET-01, HYB-05 |
| Id absent from all three sheets | NOT_FOUND across all sources. Never substitute a similar id. | none visible |
| Group id (`group 346`) | RESOLVED to the group. Membership is reported per source; when the sheets disagree, both are shown (metadata question 4 is open, D7). | PERF-10 |
| No entity in the question (global aggregate) | NONE_NEEDED. | ADV-05, ADV-08 |

Any status other than RESOLVED or NONE_NEEDED ends the request with the matching response. No retrieval is run on an ambiguous or unresolved entity.

## 6. C2 Question routing

| Aspect | Contract |
|---|---|
| Purpose | Decide which evidence path to run. |
| Input | Question text; `ResolutionResult`. |
| Output | `RoutingDecision`. |
| Allowed | Rule-based classification on cue words and resolved entity types. An LLM classifier restricted to the four labels only when the rules find no cue at all, called through the CLI adapter (section 21). |
| Failure conditions | No cue and no working LLM (including a CLI failure, section 21); conflicting cues that fit no class; a request the data cannot answer. |
| Must NOT | Plan multiple steps; call tools; replan; add classes or sub-planners; decide entity ambiguity. |

Classes:

| Route | Chosen when | Evidence path |
|---|---|---|
| INVESTMENT | Cues about investment records, deals, amounts, currency, RM, `ClientStatus`, line of business | 3a on `investments` |
| PERFORMANCE | Cues about MOIC, IRR, AUM, snapshots, as-of, performance status, group performance | 3a on performance views |
| MEETING | Cues about meetings, attendees, action items, discussion topics, sector/region/stage, meeting deal size | 3b |
| HYBRID | Cues from two or more of the above, or one structured metric plus a meeting fact | 3c |

Terminal dispositions (not routes) decided here from documented facts, before any query: DECLINE when the question needs an RM-to-meeting link (meetings have no RM field), asks the meaning of an undocumented field (`L3Y_DIS`), or asks for a metric that does not exist for the family (COP MOIC, A9). A relative-date question is routed normally and handled by section 12.

## 7. C3a Structured SQL

| Aspect | Contract |
|---|---|
| Purpose | Retrieve exact structured facts at the correct grain. |
| Input | `RoutingDecision`, resolved entities, the approved schema (logical objects in section 3 only). |
| Output | Executed SQL text, result rows, row counts, as-of dates, coverage. |
| Allowed | **LLM-generated SQL is the primary mechanism (D2)**, produced through the CLI adapter (section 21) and constrained to the approved objects. There is no template layer and no template fallback. Data rules that do not depend on LLM judgement (latest-per-client, group-row exclusion, derived numeric columns, aggregation grain) are enforced outside the LLM, through the views it may query and through SQL validation. Aggregation, ranking, distinct counts. |
| Failure conditions | Validation rejects the SQL; execution error; empty result where a value was required; result larger than the row cap; timeout. |
| Must NOT | Write or alter anything; touch objects outside the approved list; join row-level tables from different sheets; use text MOIC or text deal size for ordering or comparison; use natural-currency amounts across currencies; use `LIKE` for deal names; retry or self-repair. |

Rules:

1. **Read-only.** The store is opened read-only. Exactly one `SELECT`/`WITH` statement. Anything else (DDL, DML, `PRAGMA`, `ATTACH`, multiple statements) is rejected before execution.
2. **Validate before execution.** Check the statement type and that every referenced object is in the approved list, and apply a row limit and a timeout.
3. **Correct grain.** Investments at record grain. Performance at client-latest, client-all-snapshots or group grain, chosen by the question. Meetings at meeting grain.
4. **Counts are explicit.** `COUNT(*)`, `COUNT(DISTINCT client_id)`, `COUNT(DISTINCT deal_id)` and `COUNT(DISTINCT deal_name)` are different questions and are not interchangeable (INV-01, INV-02).
5. **Money.** `Investment_Amount_USD_for_agg` for cross-record sums. Natural-currency sums only within a single currency (INV-03, INV-10, ADV-05).
6. **Performance.** Client questions use the client view; latest through the latest-per-client view; group rows only for group questions (PERF-02, PERF-04, PERF-08, PERF-10). MOIC comparisons use the derived numeric column (PERF-01, PERF-02).
7. **Unavailable metrics** are declined before SQL is generated (section 6, A9).
8. **Errors.** A validation or execution error ends the request with `ERROR` and the error class. There is no automatic retry and no regeneration.
9. **Evidence.** The executed SQL and its result are stored in the `EvidenceBundle`.

## 8. C3b Lexical meeting retrieval

| Aspect | Contract |
|---|---|
| Purpose | Find meeting records relevant to the question, using words only. |
| Input | Resolved entities, filters from the question (client, group, date range, company, sector, region, stage, attendee), remaining content words. |
| Output | Ranked meeting evidence with identifiers and dates. |
| Allowed | Deterministic filtering; lexical ranking (BM25-style) over raw meeting text; returning snippets; counting on the filtered set. |
| Failure conditions | Filter matches no meetings; no candidate scores above zero; a date filter outside meeting coverage. |
| Must NOT | Use embeddings; expand or rewrite the query with an LLM or synonyms (it would hide the lexical limits the semantic stress questions measure); repair encoding (A11); infer that missing evidence means the event did not happen. |

Steps, in this order:

1. **Resolve/filter first.** Apply entity and attribute filters from the structured columns (`client_id`, `group_id`, `date`, `company`, `sector`, `region`, `investment_stage`). Filter-only questions (counts by sector/region/year, stage) are answered from the filter, not from text ranking (MEET-05).
2. **Retrieve candidates.** The filtered set. With no filter, all 20,000 meetings, and the response says so.
3. **Rank lexically.** Rank candidates by lexical relevance to the remaining content words. The ranking function, indexed fields and top-k are undecided (D4).
4. **Return evidence.** Each hit carries `meeting_id`, `date`, `client_id`, `company`, `sector`, `region`, `investment_stage`, rank, score and a snippet of the raw text as stored.
5. **Action items.** Every returned meeting carries `action_items` as RECORDED (text) or NOT_RECORDED (NULL). NULL is never rendered as "none", "no actions" or an empty list (A8, MEET-02).

Also:
- Company, sector and region appear in every summary and the wording is templated, so text ranking cannot discriminate on them. They are used as filters only (MEET-07).
- `investment_stage` values never appear in the text. It is a filter only (MEET-05).
- Text is used exactly as stored. Names with accents and `±` figures may not match their clean forms (MEET-06, MEET-10). That is a known Baseline limitation, to be measured, not fixed.
- Deal size questions use the derived numeric value (MEET-04).
- Meeting `company` and `deal_size_estimate` are never joined to investment deals (A5, section 10).

## 9. C3c Hybrid

| Aspect | Contract |
|---|---|
| Purpose | Answer questions that need structured facts and meeting evidence together, without inflating numbers. |
| Input | Resolved entities and the route. |
| Output | An `EvidenceBundle` with separately aggregated structured results, meeting evidence and an explicit combination step. |
| Allowed | Running 3a once per source at that source's own grain, producing small client-level results; running 3b restricted to the client set; combining by `client_id` with set intersection, set union or side-by-side presentation. |
| Failure conditions | A structured part fails; the client set is empty; a source has no data for a client in the set. |
| Must NOT | Join row-level tables across sheets; sum or count after a cross-sheet join; combine Investments and performance amounts (S1); attribute meetings to an RM; treat `company` as a deal; reconcile conflicting dates or statuses. |

Rules:

1. **Aggregate first, combine after.** A client-level three-way row join produces 8,534,216 rows from 51,536 + 4,096 source rows; any sum or count after it is wrong (HYB-02, HYB-03).
2. **Population statement.** Only the 192 shared clients have all three sources. The 808 meeting-only clients are named as such, not dropped and not treated as having no investments (HYB-05).
3. **Dates differ by source** (investments 2024-11-05, performance 2025-01-22, meetings 2026-03-13). The answer states the date of each fact and never calls the performance value current at a later meeting date (HYB-01, HYB-06).
4. **Last met** is the latest meeting date from `meetings` (A13). `Client_Last_Met_Date` may appear only as a separately labelled field (HYB-01).
5. **Status.** `ClientStatus` and performance `Investment_Status_Name` are different fields. If both appear they are labelled separately (A4).
6. **Structured filter and text part are independent.** A hybrid filter such as "latest MOIC below 1.0x and a meeting that mentions X" is computed as two client sets, then intersected (HYB-09).
7. **No RM link.** Meetings have no RM field. Such questions are declined in C2 (HYB-07).

## 10. C4 Answer synthesis

| Aspect | Contract |
|---|---|
| Purpose | Turn the `EvidenceBundle` into a readable answer. |
| Input | Question, `EvidenceBundle`, caveat triggers. Nothing else (no raw sheets). |
| Output | `Answer` (text plus tagged claims). |
| Allowed | The LLM, called through the CLI adapter (section 21), or deterministic templates, for wording only. Rounding and formatting of numbers that are in the evidence. |
| Failure conditions | Evidence empty; a required caveat cannot be stated; the model output cannot be parsed into claims. |
| Must NOT | Introduce a fact, number or date that is not in the evidence; do arithmetic that is not in the evidence (any computed value comes from SQL); fill gaps from general knowledge; expand undocumented abbreviations; retry. |

The answer must:

- **Separate fact from interpretation.** Each claim is tagged FACT (with evidence item ids), INTERPRETATION or CAVEAT.
- **Identify the evidence.** Name the dataset and keys: sheet and filter, `As_Of_Date`, meeting ids and dates.
- **Preserve ambiguity.** Present all candidates or both source values. Never pick one to sound certain.
- **Not claim unavailable metrics.** State "unavailable" and name the missing column family. Never offer another metric as a substitute (PERF-05).
- **Not turn missing into negative.** NULL `action_items` is "not recorded". No performance rows for a meeting-only client is "no performance data", not zero.
- **Not present record-level attributes as client-wide.** RM and `ClientStatus` are stated as "on records", with counts. Never "the RM of client X" or "the client is Closed" (INV-04, INV-06).

Caveats added when triggered:

| Trigger | Caveat |
|---|---|
| Performance value shown | The `As_Of_Date` used (per client, A6). |
| Relative or current date | The reference date used, the window, and source coverage (section 12). |
| RM or status question | Record-level, with counts. |
| Meeting-only client involved | Which sources have data. |
| Undocumented field or unit involved | Says it is undocumented. |
| Amount shown | The field used; performance currency is unstated. |
| Meeting text quoted | Text is shown as stored. |

Which caveats must always appear is metadata question 9 and is open (D7).

## 11. C5 Basic validation

Runs after synthesis and before the response. Deterministic. A failed check gives `VALIDATION_FAILED` (or the status in the table). There is no regeneration loop.

| # | Check | Fails when | Response status |
|---|---|---|---|
| V1 | SQL errors | Validation rejected the SQL, execution raised an error, or a timeout or row cap was hit | ERROR |
| V2 | Entity resolution | Status is AMBIGUOUS, NOT_FOUND or UNRESOLVED but retrieval ran or the answer names one entity | CLARIFICATION_NEEDED / NOT_FOUND |
| V3 | Unavailable metric | The requested family/metric has no column (availability table) and the answer contains a value or names another metric as a substitute | UNAVAILABLE |
| V4 | Missing evidence | A meeting claim has no evidence id; a cited meeting id is not in the bundle; zero evidence is presented as "nothing happened" | VALIDATION_FAILED |
| V5 | Unsupported numbers | A number, count or date in the answer text is not present in the structured results or evidence (after allowed rounding) | VALIDATION_FAILED |
| V6 | Contradiction | A claim contradicts the evidence, or matches a forbidden pattern derived from the data rules: a single RM for a client; a client-wide status; "no action items" for NULL; a group row used in a client aggregate; `Client_Last_Met_Date` presented as last met | VALIDATION_FAILED |
| V7 | Required caveats | A triggered caveat (section 10) is absent | VALIDATION_FAILED |
| V8 | Status consistency | The response status does not match the resolution, routing and validation results | ERROR |
| V9 | LLM call | The CLI is unavailable, exits non-zero, times out, returns empty or unparseable output, or reports `is_error: true` (detected in the adapter, section 21, and reported here so the status is consistent) | ERROR |

These checks are minimums. They catch structural violations, not wrong-but-plausible answers. Correctness is measured by the benchmark (section 14).

### C6 Response

| Aspect | Contract |
|---|---|
| Purpose | Deliver the final result, its status and its evidence. |
| Input | `Answer`, `EvidenceBundle`, `ValidationReport`, `ResolutionResult`, `RoutingDecision`. |
| Output | `Response` (section 4): human-readable text plus a machine-readable record with the same content, for evaluation. |
| Allowed | Formatting; attaching evidence, caveats, route, resolution summary, validation summary and the runtime date used. Setting `status` from the pipeline outcome. |
| Failure conditions | Status and content disagree (V8); a required caveat or evidence item is missing from the output. |
| Must NOT | Change the answer after validation; drop caveats or evidence; return `ANSWERED` when any validation check failed; return a partial number on `ERROR`; hide a failure reason. |

Status precedence: `ERROR` (V1, V8) over `NOT_FOUND` / `CLARIFICATION_NEEDED` (V2) over `UNAVAILABLE` (V3) over `UNANSWERABLE` (C2 disposition) over `VALIDATION_FAILED` (V4-V7) over `ANSWERED`.

## 12. Relative dates and coverage

1. The reference for "recently", "last year", "current" and similar wording is the **runtime current date** (A14). The clock must be injectable and the date used must be recorded in the `Response`, so evaluation runs are reproducible.
2. A relative window has no definition in the data. The answer states the window it used. Baseline windows (D5), counted from the runtime date: **"recently"** = the previous 90 days; **"last year"** = the previous 365 days; **"this year"** = 1 January of the runtime year through the runtime date. Other relative terms (for example "last quarter") are not defined in Baseline; the answer states the window it used or surfaces the ambiguity. Whether the window boundaries are inclusive is not specified (section 13).
3. "Last met" and "most recent meeting" use the latest meeting date from `meetings` (A13). A runtime date after 2026-03-13 means the period exceeds meeting coverage. The answer says so and does not imply that meetings exist or do not exist after that date.
4. The latest date in any source is never treated as "today". Performance uses each client's own latest `As_Of_Date`, not the global maximum (A6).

## 13. Unresolved decisions (need your explicit approval before coding)

D1, D2, D5 and D10 are decided and kept here for traceability (D5 for three terms only). The remaining items are open, each with a recommendation, not a choice.

| # | Decision | Why it is open | Recommendation |
|---|---|---|---|
| D1 | **LLM interface: DECIDED. The Claude Code CLI, invoked by Python as a subprocess** (section 21). No API key, no Python LLM SDK, no other provider. | Decided by you. The CLI decisions L1-L10 are resolved in section 21.6, which also lists what remains open. | Which components call the LLM is unchanged: C2 routing fallback and C4 wording; SQL generation follows D2. |
| D2 | **SQL approach: DECIDED.** LLM-generated SQL is the primary structured-query mechanism, constrained to the approved objects and always validated (section 7). No template-plus-fallback architecture in Baseline. Deterministic data rules (latest-per-client, group-row exclusion, aggregation grain) may be enforced outside the LLM. | Decided by you. | None. |
| D3 | **Local store: IMPLEMENTED, names pending your approval.** SQLite (standard library) at `data/derived/baseline.sqlite`, gitignored. Tables `investments`, `meetings`, `performance` (plus `_build_meta`, `_column_lineage`). Derived columns: `<moic column>_num`, `deal_size_estimate_usd`, and `<column>_parse_status` (`ok`, `null`, `invalid`). The performance views are temporary and are created by the structured-query engine (not stored in the database file). | Built by `src/baseline/data_build.py` (`python -m src.baseline.build_db`); 92 validation checks pass (6 of them freeze the known data-quality inconsistencies against the official dictionary, A16-A18, data_dictionary_DRAFT.md decisions 2/5/7/8; decision 1's checks were removed once Abhishek clarified those performance columns are not usable application facts at all — see baseline_reference.md). Metadata question 8 (names for derived fields) is answered by these names only if you approve them. | Approve or rename. |
| D4 | **Lexical engine: IMPLEMENTED, settings pending your approval.** SQLite FTS5 with BM25 (`meetings_fts`, persistent in `baseline.sqlite`). Indexed fields: `summary`, `attendees`, `action_items`, `company` (weight 0.1). Tokenizer `unicode61 remove_diacritics 0` (no diacritic folding, no stemming); raw mojibake is indexed and returned as stored. Default top-k 20 (maximum 50), chosen because the largest visible evidence set is 14 meetings and a client has 12-23 meetings. | Implemented in `src/baseline/meeting_retrieval.py`; see `meeting_retrieval.md`. | Approve or change the weights, the top-k or the tokenizer. |
| D5 | **Relative-date windows: DECIDED for three terms.** "Recently" = previous 90 days from the runtime date. "Last year" = previous 365 days from the runtime date. "This year" = 1 January of the runtime year through the runtime date. Source-coverage limitations are always surfaced (section 12). | Decided by you. Still open: other terms (for example "last quarter"), and whether the start and end days are inclusive. | Until decided: other terms are stated or surfaced as ambiguous; treat both ends as inclusive. |
| D6 | **Candidate cap: DECIDED.** The public ambiguity result returns at most 10 candidates, in a deterministic order, and states the full count and that the list is truncated (implemented in the entity resolver; see `entity_resolution.md`). **Clarification style** in a single-turn system without memory (list candidates and stop) is still a recommendation. | Cap decided by you. Memory is out of scope. | Return the clarification and stop. |
| D7 | **Metadata questions that still affect behaviour, confirmed by Abhishek after the official dictionary arrived:** group membership when sheets differ (**decided**: source-specific, neither sheet overrides the other, data_dictionary_DRAFT.md decision 8) and `cod_lob`-to-prefix mapping (**decided**: PE=CI=Private Equity, HF=Hedge Fund, RE=Real Estate, COP=Credit Opportunity, INF=Infrastructure, A15) are resolved. Still open: units and currency of performance amounts; normalisation strictness; which caveats must always appear. | The two mapping questions are deliberately left open in the *source* metadata; Abhishek's confirmation is an application-level decision, not a claim that the source itself defines them. | Group questions still state which sheet's membership was used (A15/A18 do not change this). No unit or currency stated for performance amounts until decided. |
| D8 | **Evaluation execution:** how rule-based questions are scored (human review, keyword rubric or an LLM judge), where results are stored (proposed: `evaluation/`), and the git tag or record that freezes Baseline results. | Not decided. An LLM judge adds a second model. | Rubric checks for `must_include` / `must_not` plus human review of a sample. No LLM judge in Baseline. |
| D9 | **Code layout.** `CLAUDE.md` says production code is under `src/` and also names `baseline/`, `v1/`, `v2/` folders. | The two instructions conflict. | `src/baseline/`, later `src/v1/`, `src/v2/`. |
| D10 | **Dependencies: DECIDED.** `pytest` is required (already in `requirements.txt`, not yet installed). No LLM SDK, no `python-dotenv`, no API-key dependency. The Claude CLI is the LLM runtime dependency (section 21). pandas, openpyxl, numpy and sqlite3 are already present. | Decided by you. pytest's Python 3.13 compatibility is unverified. | Install pytest only when implementation starts and you give the go-ahead (CLAUDE.md). |
| D11 | **Baseline pass targets.** Whether any score is required of Baseline. | I will not invent a threshold. | None. Baseline is diagnostic (section 15). |

## 14. Evaluation contract

1. The **40 visible questions** are the development and regression set. They are evaluated on every version.
2. The **10 holdout questions** are not read during implementation or tuning. They are run only after a version is complete, by someone other than the tuning loop. Fixes are never shaped to a holdout question.
3. **Baseline results are recorded before any V1 change** and are then frozen.
4. **Every failure is classified** by (a) the benchmark `failure_mode_target` tag(s) and (b) the component that caused it: C1 resolution, C2 routing, C3a SQL, C3b retrieval, C3c hybrid, C4 synthesis, C5 validation, or data/metadata.
5. **V1 and V2 are evaluated on the same benchmark** with the same scoring. Nothing is re-picked.
6. **No question is removed** because Baseline fails it. A question changes only through a new benchmark version.
7. Each record follows CLAUDE.md sec. 6: Problem, Evidence, Change, Reason, Result, Remaining limitation, plus timestamp, version, test and result (sec. 10).
8. Scoring types come from the benchmark: numeric with tolerance, exact id sets, and rubric checks (`must_include`, `must_not`) for behaviour questions. `wrong_values` hits are tagged as known failures.
9. `ADV-06` depends on the runtime date. Its run records the injected date.

## 15. Baseline success criteria

Baseline is a diagnostic build (CLAUDE.md sec. 2). Success means the pipeline is built to this contract and measured, not that it scores well.

1. All 40 visible questions run end to end and return a `Response` with a status, evidence or an explicit reason, and a validation report. No crashes or empty responses.
2. Invariant tests pass: the SQL guard rejects any write or multi-statement query; 192 clients in the latest-per-client view; 1,536 client rows and 96 group rows; MOIC and deal-size parse with 0 failures; a bare `12345` is AMBIGUOUS; a meeting-only client is RESOLVED without structured data; the meeting retrieval returns `action_items` state for every hit.
3. Every failing visible question is classified by tag and component (section 14, item 4).
4. Baseline results are recorded and frozen before any V1 change.
5. No embedding, agent framework or excluded component is present.

## 16. Explicit non-goals

Everything in the right-hand column of section 1, plus: answering beyond the data, repairing or "fixing" synthetic inconsistencies, reconciling conflicting sources, inferring undocumented meanings, improving on the benchmark by editing it, and optimising latency or cost.

## 17. Expected failure categories (hypotheses to test, not results)

| Category | Likely tags | Visible questions to watch |
|---|---|---|
| Lexical recall on paraphrase | `SEM_PARAPHRASE` | MEET-08, HYB-09, ADV-10 |
| Encoding misses | `MEET_ENCODING` | MEET-06, MEET-10 |
| Top-k cap on set answers | `MEET_REPETITIVE_TEXT`, `MEET_FILTER_BEFORE_RETRIEVAL` | MEET-06, MEET-07 |
| Entity over-resolution | `ENT_*` | INV-07, ADV-01, ADV-03 |
| Record-level attributes stated as client-wide | `INV_RM_RECORD_LEVEL`, `INV_STATUS_RELATIONSHIP` | INV-04, INV-06 |
| SQL grain or join errors | `HYB_ROW_MULTIPLICATION`, `PERF_LATEST_PER_CLIENT`, `PERF_EXCLUDE_GROUP_ROWS` | HYB-02, HYB-03, PERF-02, PERF-04 |
| Text-typed numbers | `PERF_MOIC_TEXT`, `MEET_DEALSIZE_TEXT` | PERF-02, MEET-04 |
| Missing turned into negative | `MEET_NULL_ACTION_ITEMS` | MEET-02, HYB-05 |
| Over-answering | `PERF_UNAVAILABLE_METRIC`, `UNDOCUMENTED_SEMANTICS`, `PERF_TREND_AVERAGE` | PERF-05, PERF-06, PERF-07, ADV-08 |
| Date handling | `HYB_DATE_COVERAGE`, `TIME_ANCHOR` | HYB-01, HYB-06, ADV-06 |
| Synthesis overclaiming | any | validation checks V4-V7 |

## 18. What would justify V1

V1 is justified only by recorded Baseline failures, following CLAUDE.md sec. 2 and 6:

- A failure is reproducible and attributed to a specific component (section 14, item 4).
- A deterministic or narrow fix exists inside the Baseline scope (for example resolver dictionaries, SQL templates, grain rules, caveat triggers, filter-first logic, top-k, field weighting).
- The fix is written up as Problem, Evidence, Change, Reason and tested against the same visible questions, then confirmed on the holdout.
- Failures caused by data or metadata gaps (open metadata questions) go to a metadata decision, not to V1 code.

## 19. What would justify semantic embeddings later

All of the following, together:

1. The `SEM_PARAPHRASE` questions fail on retrieval: the known evidence meetings are not in the lexical results, checked meeting by meeting.
2. The lexical control (`MEET-07`) and the needle question (`MEET-10`) succeed after encoding is accounted for. This shows the pipeline works when words overlap, so the gap is vocabulary, not plumbing.
3. Lexical improvements inside Baseline (filters, field weighting, tokenisation, top-k) have been tried and recorded, and the gap remains.
4. The encoding impact test (`docs/encoding_impact_test_design.md`) has run, so encoding is separated from semantics.
5. A paired comparison, same benchmark and metrics (Recall@5, MRR@5, Evidence Hit Rate@5), shows a measurable gain on the semantic stratum, and no loss on the lexical control. The size of gain that counts is for you to set (not defined here).

## 20. Contract-to-benchmark map (validation aid)

Every failure-mode tag in the benchmark is handled by a section here.

| Tag | Section | Tag | Section |
|---|---|---|---|
| `ENT_AMBIGUOUS_ID`, `ENT_AMBIGUOUS_NAME`, `ENT_NAME_FORMS`, `ENT_DEAL_PREFIX`, `ENT_DEALID_MULTI_NAME`, `ENT_MEETING_ONLY` | 5 | `PERF_LATEST_PER_CLIENT`, `PERF_EXCLUDE_GROUP_ROWS`, `PERF_MOIC_TEXT`, `PERF_DATE_COVERAGE` | 3, 7 |
| `INV_ROW_VS_DISTINCT`, `INV_USD_VS_NATURAL`, `INV_RANKING` | 7 | `PERF_UNAVAILABLE_METRIC` | 6, 7, 11 (V3) |
| `INV_RM_RECORD_LEVEL`, `INV_STATUS_RELATIONSHIP` | 3, 10, 11 (V6) | `PERF_TREND_AVERAGE` | 10 |
| `MEET_FILTER_BEFORE_RETRIEVAL`, `MEET_REPETITIVE_TEXT`, `MEET_SPARSE_STAGE` | 8 | `MEET_NULL_ACTION_ITEMS` | 8, 10, 11 (V6) |
| `MEET_DEALSIZE_TEXT` | 3, 8 | `MEET_ENCODING` | 8 |
| `HYB_AGG_BEFORE_COMBINE`, `HYB_ROW_MULTIPLICATION`, `HYB_POPULATION_GAP` | 9 | `HYB_DATE_COVERAGE`, `TIME_ANCHOR` | 9, 12 |
| `SEM_PARAPHRASE` | 8, 17, 19 | `UNDOCUMENTED_SEMANTICS` | 6, 10 |

## 21. Claude CLI Runtime Dependency

### 21.1 Decision and boundary

D1 is decided: the Baseline application uses the **Claude Code CLI as its only LLM interface**. Python calls the CLI as a subprocess. There is no Anthropic, OpenAI or other SDK and no second provider.

```text
Python application  --(subprocess: args, prompt on stdin)-->  claude CLI  -->  Claude
       ^                                                          |
       '---- CliResult: stdout, stderr, exit code, timeout/failure ----'
```

All LLM calls go through one adapter: C2 routing fallback, C4 answer wording, and C3a SQL generation (D2: LLM-generated SQL is the primary mechanism). No other component builds CLI arguments or names a model.

### 21.2 Prerequisites and dependency

| Item | Contract |
|---|---|
| Prerequisite | Claude Code installed and authenticated in the environment where the application runs. |
| Version | **2.1.283** (`claude -v` printed `2.1.283 (Claude Code)`, exit 0, verified 2026-09-26). Recorded as an environment prerequisite. Executable on `PATH` at `C:\Users\singh\.local\bin\claude` on this machine. |
| Authentication check | `claude auth status --json` (exit 0 when logged in). Observed fields: `loggedIn: true`, `authMethod: "claude.ai"`, `apiProvider: "firstParty"`, `subscriptionType: "pro"`, plus account identifiers and directory paths. The application checks `loggedIn` and must not log or store the identifiers. |
| Runtime dependency | The application depends on the `claude` executable and its login being present at run time. |
| Python LLM SDK | None in Baseline. |
| API key | None required and none assumed. Verified: no `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set on this machine and both smoke-test calls succeeded on the subscription login. The application never reads, stores, passes or logs an API key. |
| Authentication and session | Owned by the CLI environment. The application never runs `claude auth login` or `logout`. |
| Runtime configuration not relied on | CLAUDE.md, hooks, MCP servers and auto-memory (`--safe-mode`, section 21.6). |
| Not to be used | `--bare` (its help says auth is then strictly `ANTHROPIC_API_KEY` or `apiKeyHelper`, and OAuth and keychain are never read, which contradicts the no-API-key decision). Also not `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions`. |

### 21.3 Verified invocation

Only flags listed in `claude --help` are used. Both shapes below were **executed successfully** (exit 0, empty stderr) on 2026-09-26 from the project root, with tiny prompts only:

```text
A. prompt as argument
claude -p --output-format json --model claude-sonnet-5 --tools "" --permission-prompts none \
       --no-session-persistence --safe-mode "Return exactly the word OK."

B. prompt on standard input (preferred, see 21.6 L3)
<prompt text on stdin> | claude -p --output-format json --model claude-sonnet-5 --tools "" \
       --permission-prompts none --no-session-persistence --safe-mode
```

Baseline configuration: `claude-sonnet-5` with `--effort low`. `claude --help` lists `--effort <level>` with the values `low, medium, high, xhigh, max`. This shape was executed once on 2026-09-26 (exit 0, empty stderr, `result: "EFFORT_LOW_OK"`):

```text
C. Baseline configuration (prompt on stdin)
<prompt text on stdin> | claude -p --output-format json --model claude-sonnet-5 --effort low \
       --tools "" --permission-prompts none --no-session-persistence --safe-mode
```

The adapter in `src/baseline/claude_cli.py` passes `--effort <configured effort>` on every inference call. Baseline defaults to `--effort low`. Model and effort remain configurable at the adapter boundary.

| Flag | Role | Verified |
|---|---|---|
| `-p` | Non-interactive print mode | Yes |
| `--output-format json` | One JSON object on stdout | Yes |
| `--model sonnet` | Pinned model alias, set in the adapter configuration | Alias accepted; served by `claude-sonnet-5` |
| `--effort low` | Effort level (help: low, medium, high, xhigh, max). Baseline default `low`; configurable in the adapter | Accepted with `-p` (shape C); the reply came from `claude-sonnet-5` |
| `--tools ""` | No tools | Accepted; no tool was used (not tested with a prompt that tries to use one) |
| `--permission-prompts none` | Prompts are denied, never asked | Accepted; `permission_denials: []` |
| `--no-session-persistence` | Nothing saved to disk | Yes: no session file exists for either session id, and no file appeared in the repository |
| `--safe-mode` | Ignore CLAUDE.md, hooks, skills, MCP, memory | Accepted and worked with normal authentication |

### 21.4 Verified response structure

One JSON document on stdout; stderr was empty in both calls; exit code 0.

| Field | Observed |
|---|---|
| `type` | `"result"` |
| `subtype` | `"success"` |
| `is_error` | `false` |
| `result` | The model's reply as a plain string (`"OK"`, `"STDIN_OK"`) |
| `terminal_reason`, `stop_reason` | `"completed"`, `"end_turn"` |
| `num_turns` | `1` |
| `permission_denials` | `[]` |
| `api_error_status` | `null` |
| `session_id`, `uuid` | Identifiers |
| `total_cost_usd` | 0.0081546 and 0.0047282 for the two calls (list-price basis) |
| `duration_ms`, `duration_api_ms`, `ttft_ms` and other timing fields | For example `duration_ms` 1,167 and `duration_api_ms` 1,969 (first call) |
| `usage` | Token counts (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, others) |
| `modelUsage` | Per-model tokens and cost. Keys observed: `claude-sonnet-5` and `claude-haiku-4-5-20251001` |
| Other keys | `fast_mode_state`, `subagent_stats`, `queued_turn_count`, `result_index`, `first_content_frame_ms`, `time_to_request_ms`, `ttft_stream_ms` |

### 21.5 Adapter contract

| Aspect | Contract |
|---|---|
| Purpose | The single place where Python talks to the CLI. |
| Input | Prompt text, optional system prompt, timeout, and configuration (executable, model, effort, working directory). |
| Output | `CliResult`: `stdout`, `stderr`, `exit_code`, `duration`, `timed_out`, the argument list used (no secrets), parsed JSON fields (`result`, `is_error`, `subtype`, `total_cost_usd`, `modelUsage` keys), and `error_class` if any. |
| Allowed | Run `claude` as a subprocess with an argument list (no shell), the prompt on stdin. Capture stdout and stderr separately. Enforce the timeout in Python and kill the process on expiry. Read the model from configuration. Run the startup checks (`claude -v`, `claude auth status --json`). |
| Failure conditions | Executable not found; non-zero exit code; timeout; empty output; output that is not one JSON object with a string `result`; `is_error` true or `subtype` other than `"success"`; `loggedIn` not true at startup. |
| Must NOT | Import or use any LLM SDK; read or require an API key; open an interactive session or depend on a terminal or prompt; pass `--bare` or a permission-bypass flag; enable tools; retry, loop or fall back to another model or provider; rely on CLAUDE.md, hooks, MCP or auto-memory; log secrets or account identifiers. |

Rules:

1. **Explicit failure.** A missing executable (`LLM_CLI_UNAVAILABLE`), a non-zero exit code (`LLM_CLI_ERROR`), a timeout (`LLM_CLI_TIMEOUT`), or empty, unparseable or `is_error` output (`LLM_CLI_BAD_OUTPUT`) each end the request with `ERROR` and that class (check V9). Nothing is answered from a failed call. A failed startup check (`LLM_CLI_UNAVAILABLE` or `LLM_CLI_NOT_AUTHENTICATED`) stops the application before any request.
2. **Everything captured.** `stdout`, `stderr` and the exit code are stored with the request record. `stderr` is preserved even on success.
3. **Non-interactive only.** No call waits on a terminal prompt. `--permission-prompts none` is always set.
4. **No retries.** One call per LLM step (section 11).
5. **Model at the boundary.** The model (default `claude-sonnet-5`) and the effort level (default `low`) are adapter configuration values, configurable at the adapter boundary; Baseline evaluation uses `claude-sonnet-5` with `--effort low`. No other component refers to a model or to CLI flags. The models actually used are recorded per call from `modelUsage`.
6. **Content limits.** The prompt contains only the question and the evidence bundle, or, for SQL generation, the question and the approved schema. It never includes workbook rows outside the evidence.
7. **Output handling.** The adapter returns the raw `CliResult`. Parsing `result` into claims or SQL (C4, C3a) and checking (C5) are outside the adapter.
8. **Working directory.** The trusted project root.

### 21.6 Decisions resolved

| # | Topic | Decision | Verified by the smoke test | Still open |
|---|---|---|---|---|
| L1 | Context isolation | `--safe-mode`. No reliance on CLAUDE.md, hooks, MCP or auto-memory at runtime. | Flag accepted; auth and model selection worked. | Replace (`--system-prompt`) or append the default system prompt was not tested. The default prompt is still sent (about 2.3k input tokens per call). |
| L2 | Tools | `--tools ""`. | Accepted; no tool call occurred; `permission_denials: []`. | That the model cannot use a tool was not tested. |
| L3 | Prompt transport | **Standard input is the preferred transport** for prompts and evidence. | Works with `-p`: `printf` piped in, `result: "STDIN_OK"`, exit 0, empty stderr. | Only a tiny ASCII prompt was tested. The maximum stdin size and non-ASCII text (accents, `±`, mojibake) are untested. If an argument is ever used instead, it must stay within the Windows command-line limit (about 32,000 characters), so evidence would have to be compacted or top-K. |
| L4 | Output format | `--output-format json`. | One JSON object; fields in 21.4. | `--json-schema` untested and not used. The model's reply is a plain string inside `result`. |
| L5 | Exit codes | Any non-zero exit is an explicit failure (`LLM_CLI_ERROR`). Also fail on `is_error` true or `subtype` other than `"success"`. | Exit 0 on success. | Failure exit codes and the JSON of a failed call are untested. |
| L6 | Determinism | No temperature or seed option exists in the help, so none is set. | Not tested. | Run-to-run variation is unmeasured. Raw outputs are recorded. Runs per question are undecided. |
| L7 | Timeout, retries, cost | Python enforces the timeout. No automatic retries. `--max-budget-usd` and `--fallback-model` not used. | Not exercised. | Timeout value is undecided. Wall-clock latency including process start was not measured. Subscription (`pro`) usage limits could throttle evaluation runs (untested). |
| L8 | Startup checks | `claude -v` and `claude auth status --json` before the first request. | Both exit 0 with the fields above. | Behaviour when logged out is untested. Version mismatch policy (fail or warn) is undecided. |
| L9 | Model and effort | Pinned in the adapter configuration. Baseline default: `claude-sonnet-5` with `effort=low`. Effort is configurable at the adapter boundary, but Baseline evaluation uses low. | The `sonnet` alias and the full name `claude-sonnet-5` were accepted, and `claude-sonnet-5` served the reply. `--effort low` was accepted with `-p` (shape C). | Implemented: the adapter passes `--effort <configured effort>`, defaulting to `low`; model and effort are configurable at the adapter boundary. The effect of effort is not observable in the CLI output (21.7). |
| L10 | Trust and session | Run from the trusted project root. `--no-session-persistence`. | Both calls ran from the project root; no session file and no repository change resulted. | None. |

### 21.7 Limitations discovered

- Both calls also billed a second model, `claude-haiku-4-5-20251001` (an internal use of the CLI). The reply came from Sonnet, but "one model only" cannot be claimed, and no confirmed flag turns this off.
- Fixed overhead of about 2.3k input tokens per call (2 + 1,766 + 518 and 2 + 846 + 1,446), even with `--safe-mode`, no tools and a six-word prompt.
- `result` is unstructured text. Structured outputs (SQL, claims) must be parsed from it and validated by the application.
- `--effort low` is accepted, but the JSON response has no effort field and `thinking_tokens` were 0 in every call, with and without the flag, so the effect of effort on model behaviour cannot be verified from the output.
- Latency: one adapter smoke call took about 6.9 s wall clock (adapter-measured, around the inference process) while the CLI itself reported `duration_ms` of 1,260, so process start-up adds several seconds per call.
- Failure behaviour, timeouts, large inputs, non-ASCII input and repeatability were not tested. The smoke test used exactly two tiny calls.
