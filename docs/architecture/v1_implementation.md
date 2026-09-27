# V1 Implementation

Implements only the two changes approved in [`v1_contract.md`](v1_contract.md). Code is in `src/v1/`, tests in `tests/v1/`. The Baseline (git tag `baseline-v0.1`) is unchanged: V1 imports it and wraps it; a test compares `src/baseline` and `tests/baseline` with the tag. The 40-question benchmark has not been run on V1 and the holdout has not been read.

## 1. What V1 changed

| Change | Baseline failures that motivated it (from `v1_diagnosis.md`) | Files |
|---|---|---|
| **V1-1** Meeting-side evidence and query terms | HYB-01, HYB-03, HYB-05, HYB-10 (request and attribute words searched as text: `ci total`, `total company sector involve`, `sectors companies`); MEET-07 (the unmatched count 17 was rejected) | `meeting_terms.py`, `meeting_intent.py`, `synthesis.py` |
| **V1-2** Meeting aggregates | MEET-01, MEET-04, MEET-05 (count, date range, largest deal size, filtered count); HYB-10 (distinct values) | `meeting_aggregates.py`, `meeting_intent.py`, `synthesis.py` |
| Enabling adaptations (contract section 6) | The frozen validator cannot ground the new evidence | `validation.py`, `synthesis.py` |
| Orchestration | Uses the above; everything else is inherited | `service.py` |

### V1-1
- **Term derivation** wraps the frozen `derive_query` and removes, with a per-word audit (`original_terms`, `removed` with category and reason, `final_terms`, `phrases`):
  - R1 request words: `many date dates range after since largest estimated involve up two`, all seen as terms in failing questions;
  - R2 meetings-table field names: `company companies sector sectors region regions stage stages size series deal deals`;
  - R3 structured-side words, hybrid questions only: `ci re hf cop total current moic irr aum usd amount amounts investment investments performance snapshot snapshots names below above than`, `Nx` multiples, and the tokens of the router's investment/performance signals;
  - R4 cue words that the classifier consumes: `count number earliest oldest smallest biggest highest lowest maximum minimum distinct different unique period` (from the contract's cue vocabulary, section 5.4).
  Protected: quoted phrases, digits and their units (`128 bps`), mojibake and accents. No synonyms, stemming or reranking.
- **Listing fallback.** With no topical term and a filter, V1 calls the frozen retriever with an empty `query_text`, which lists the filtered meetings newest first (`rank_method = date_desc`, K = 20, unchanged). Synthesis is told the listing is not a topical match. Topical questions call the retriever with the cleaned terms.
- **Evidence to synthesis.** Meeting date and company are always shown. Sector, region, stage and deal-size estimate are shown only when the question names them. Lexical mode adds `in scope without a text match: (scope - matched)`, computed by the code; listing mode adds `showing N of M`.
- **Date relations are unsupported.** `since, after, before, until, prior to, between` (also `on or after / before`) applied to a date return `unsupported` (`date_relation_unsupported`) before any evidence step and before any Claude call, on meeting and hybrid routes. The date is never turned into an exact-date filter. A bare date ("the meeting on 2022-10-01") is still an exact-date filter.

### V1-2
- **Intent classifier** (deterministic cue lists, contract 5.4). Order: date relation, then topical (a topical term or quoted phrase remains; count cues are ignored, so "how many meetings mention X" stays a lexical search that reports its matched count), then aggregate cues (`how many`, `number of`, `count`, `earliest/oldest/first meeting`, `date range`, `distinct/unique/different`, plural attribute nouns, largest/smallest with deal size), then listing (`latest/most recent`, `action items`, or a filter). The frozen router's route stays authoritative.
- **`MeetingAggregateEngine`** follows the Baseline structured engine: entity gate, one Claude call, JSON `{sql, explanation}`, static scan plus authorizer (the frozen `SqlGuard`), `EXPLAIN` compile, read-only execution with a 50-row cap and a 10 s timeout, no retry, no repair. Extra rules: exactly one object (`meetings_meta`), any `JOIN` rejected, the resolved entity literal must appear in the SQL.
- **`meetings_meta`** (temporary view on a read-only connection): `meeting_id, client_id, group_id, meeting_date, company, sector, region, stage, deal_size_estimate_raw, deal_size_estimate_usd`. `deal_size_estimate_usd` is NULL unless the parse status is `ok`. No narrative text, attendees or action items. Only the view and these ten columns are approved; `meetings`, `meetings_fts`, `investments` and `performance` are denied.
- **Entity semantics** match the retriever: a client filters `client_id`; a group filters `group_id` with membership from the meetings source only (stated in the evidence); ambiguous or unknown entities stop before SQL; an RM or a deal is `unsupported`; a client with no meetings gives a qualified answer without Claude.
- **Hybrid.** The structured side is the frozen engine, unchanged and independent. The meeting side is aggregate, listing or topical. The two results are placed side by side; there is no row-level cross-source join and no cross-source intersection. One side failing gives `hybrid_partial_failure` with the good side kept.

### Enabling adaptations
- `synthesis.py`: the Baseline prompt plus rules for `[meetings]` (aggregate evidence only), listings, and shown fields; Baseline structured serialization is reused.
- `validation.py` calls the frozen `validate_answer`. It treats aggregate rows as grounded evidence, accepts `[meetings]` only when aggregate evidence was used, accepts `[meeting: id, date]` when that pair is an aggregate row, and grounds the code-computed unmatched count. No check is added or weakened; without new evidence it returns exactly what the frozen validator returns (tested).

## 2. Component boundaries

| Frozen from Baseline (imported, not modified) | New in V1 |
|---|---|
| Entity resolution, question router, entity gate, status mapping and failure handling (`BaselineService`, subclassed), structured SQL engine for investment/performance, `SqlGuard`, lexical retriever and FTS index, Claude CLI adapter, the validator's checks, the data layer | Term derivation and audit, intent classifier, `meetings_meta` aggregate engine, V1 synthesis extension, validation wrapper, `V1Service` and `V1Response` |

`V1Service` overrides only `_answer` (evidence, synthesis, validation for the meeting side) and adds fields to the response: `meeting_mode`, `meeting_intent` (with the term audit), `meeting_aggregate_evidence`, `unmatched_count`, `requested_fields`.

## 3. What V1 deliberately does not solve

Encoding (MEET-06); semantic misses (MEET-08, HYB-09); "since <date>" and every date range (unsupported by rule); relative-date windows and per-client windows (ADV-06); a structured date passed to the meeting side and set intersection across sources (HYB-06, HYB-09); the email response (ADV-07); investment/performance SQL prompts (PERF-06); showing the SQL explanation to the synthesizer (the deferred third change; see limitation 4); validator arithmetic and number-word checks (HYB-06).

## 4. Why embeddings are still deferred

The evidence showed two genuine semantic failures (MEET-08, HYB-09) from one evidence sentence, while ADV-10 succeeded lexically; the larger clusters were capability and query-formulation problems that V1 addresses without semantics. The gate in `v1_contract.md` section 8 requires the V1 measurement first: correct terms, correct filters, scope larger than K, and a still-missed equivalent sentence, on more than one paraphrase template, with encoding excluded.

## 5. Assumptions introduced by the implementation

1. **View column names follow your latest instruction** (`stage`, `deal_size_estimate_raw`, `deal_size_estimate_usd`, no parse-status column). The first draft of `v1_contract.md` (v0.1) listed `investment_stage`, `deal_size_estimate` and the parse status. After implementation the contract was finalized and synchronized with the code (v0.2; see its section 11), so section 5.2 now lists the implemented columns. That documentation update changed no V1 behavior.
2. The view has a no-op predicate (`WHERE meeting_id IS NOT NULL`). Without it, a bare `COUNT(*)` on the view produces a base-table read that the frozen guard cannot tell from a direct read of `meetings`. The Baseline views avoid this because they carry a filter.
3. Date-relation detection also covers `YYYY-MM`, bare years (19xx/20xx) and month-name dates after a relation word, and applies to meeting and hybrid routes only. A phrase such as "after 2024 budget" is treated as a date relation.
4. R4 cue words are a fourth removal class, taken from the contract's cue vocabulary, so that "smallest" or "earliest" does not make an aggregate question topical.
5. Any `JOIN` in aggregate SQL is rejected, including a self-join of the view.
6. `deal` or RM mentions make an aggregate `unsupported` (meetings have no such field); text-only ambiguous words (company or attendee names) do not block an aggregate, as in the retriever.
7. In a hybrid, the aggregate question is sent with a suffix asking for the meeting part only, as Baseline does for the structured side.
8. The V1 synthesis citation rule was clarified once after the first manual run (section 7).

## 6. Expected regression risks

- A generic word not in the lists keeps a listing or aggregate question topical: "which sectors **cover**", "action items **came out of**" (both hit while writing tests) then search for that word.
- A removed word that is topical in a meeting question (`deal`, `size`, `stage`, `different`, `period`).
- Bare years after `since/after/before` are refused, including non-date uses.
- Aggregate SQL is LLM-generated: wrong SQL or an unsupported question is reported once, not repaired. One more Claude call (about 6-7 s) on aggregate questions and a third on hybrids with an aggregate.
- HYB-02-type counts now use the aggregate path instead of the Baseline scope count.
- A listing may be read as topical evidence despite the note.

## 7. Manual validation (real Claude CLI; fresh questions, none from the benchmark)

Results after the citation-rule clarification; all verified against SQLite with hand-written SQL and an independent Python parse.

| # | Question | Mode | V1 answer | SQLite | Latency |
|---|---|---|---|---|---|
| 1 | How many meetings did client C12350 have? | aggregate | 19 `[meetings]` | 19 | 14.4 s |
| 2 | When was client C12346 most recently met? | listing (newest first) | 2026-01-27, meeting 11243 | 11243, 2026-01-27 | 6.2 s |
| 3 | Smallest estimated deal size across client C12347's meetings? | aggregate | 31,000 USD, meeting 2944 (2026-02-27) | 31k USD (31,000), meeting 2944; next 138k | 11.6 s |
| 4 | How many of client A12409's meetings mention hedging? | topical, terms `hedging` (`many` removed) | 15 of 23 match, 8 do not; 15 ids cited | 15 of 23 by independent token match; ids identical | 7.8 s |
| 5 | Client C12349's latest Total AUM and the company and sector of their most recent meeting? | hybrid, listing | 9,481,623.23 as of 2018-08-26; meeting 3703, IVC Capital, fintech | 9,481,623.23 (2018-08-26); 3703, IVC Capital, fintech | 11.7 s |

Findings from this run:
- **First run, question 4:** the answer was withheld because the model cited `[meetings]` for a count of lexical matches, which the contract reserves for aggregate evidence. The wrapper behaved as specified; the V1 prompt rule was ambiguous. It was clarified (counts from the meeting evidence block are cited with `[meeting: id, date]`) and all five were re-run.
- **Question 3 contains a false statement.** The value is right, but the answer says "only one meeting ... has a parsed deal size". All 19 meetings have a parsed value; the model read `LIMIT 1` as the number of meetings with a value. Validation cannot catch a statement about the query. This is the pattern the deferred third change (showing the query to the synthesizer) was about.
- Question 5's validation carries a warning for the year `2026` (small-integer/year rule).
