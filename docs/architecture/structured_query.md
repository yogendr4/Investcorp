# Baseline Structured Query Engine

Implementation: `src/baseline/structured_query.py` (engine), `structured_schema.py` (approved schema, views, metric availability, prompt context), `sql_guard.py` (validation). Tests: `tests/baseline/test_structured_query.py`. This is component C3a of [`baseline_contract.md`](baseline_contract.md) (section 7), for the `investment` and `performance` routes.

## 1. Component boundary

```text
question + route (question_routing) + entity resolution (entity_resolution)
   -> gates (route, entities, data availability, metric availability)      no LLM call if a gate stops
   -> schema context + prompt
   -> Claude CLI adapter: ONE call, model claude-sonnet-5, effort low, no tools
   -> parse the reply (strict JSON)
   -> SQL validation (static scan + authorizer compile)                    never repaired
   -> read-only SQLite execution (bounded rows, timeout)
   -> StructuredEvidence
```

Not in this component: meeting retrieval, hybrid orchestration, answer synthesis, retries, any Claude call after SQL generation. Meetings are not queryable here.

**Output** (`StructuredEvidence`): `outcome`, `sql`, `explanation`, `columns`, `rows` (at most 50), `row_count`, `truncated`, `source_tables` (base tables), `objects_used` (tables/views), `rules_applied`, `entities_used`, `timing` (`llm_ms`, `validation_ms`, `execution_ms`, `total_ms`), `llm` (model, CLI version and timing), `error` (stage, code, message, details) and `notes`. `success` is true only for `ok` and `empty_result`.

## 2. Prompt and context supplied to the LLM

About 3-4 thousand characters, built per request:

1. Task and output format: return only `{"sql": "...", "explanation": "..."}`, or `{"sql": null, "unsupported_reason": "..."}` if the schema cannot answer. Never substitute another metric.
2. The route and the question.
3. Resolved entities as exact constraints (`client_id = 'A12345'`, `group_id = 346`, `deal_id = 'DL100001'` with its deal names and "do not collapse", `deal_name = '...'`, `account_rm = '...'`).
4. The approved schema for the route only (table/view names, columns, types, short descriptions taken from the data dictionary, relationships and undocumented meanings marked as undocumented). Investments and performance are never shown together.
5. Rules (section 4).

The LLM gets no data rows, no tools, no shell, no file access, and no write access. Only the question, the route, the entity values and the schema go into the prompt.

## 3. SQL validation (before execution, never repairing)

**Layer 1: static scan of the text** (quoted text is blanked first, so a keyword inside a string is harmless):

- exactly one statement (one trailing `;` is tolerated);
- it starts with `SELECT` or `WITH`;
- no comments (`--`, `/* */`);
- no `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, `VACUUM`, `REINDEX`, `ANALYZE`, transaction keywords, `EXPLAIN` or `load_extension`;
- no `LIKE` or `GLOB` on `deal_name` (prefix collisions);
- no `WITH` name that reuses an existing table or view name (this stops a CTE from impersonating an approved view).

**Layer 2: compile under a SQLite authorizer.** The statement is compiled with `EXPLAIN` while an authorizer is active. SQLite's own parser reports every table, column and function the statement touches, so this is authoritative and not a text guess. Only these are allowed:

- tables and views on the approved list, and only their approved columns (hidden: `nonauth_*`, `account_name_org`, `source_row`, raw MOIC text, parse-status columns, `client_last_met_date`, `is_group_flag`);
- a whitelist of functions (aggregates, `round`, `coalesce`, `lower`, `substr`, `date`, `strftime`, window functions and a few others);
- reads made inside our own view definitions, and the names of the query's own CTEs.

Anything else (unapproved table or column, `PRAGMA`, `ATTACH`, a write action, an unknown function such as `random` or `load_extension`) is denied. The same authorizer stays on while the query runs.

**Layer 3: engine rules on the compiled result.** One source family only (no `investments` joined to `performance`, which would multiply rows); the route decides the family; and every resolved entity's canonical value must appear in the SQL as a literal (`entity_constraint_missing` otherwise).

Rejections carry codes such as `forbidden_keyword`, `multiple_statements`, `comment_not_allowed`, `not_a_select`, `unauthorized_table`, `unauthorized_column`, `unauthorized_function`, `sql_compile_error`, `cross_source_query`, `route_source_mismatch`, `like_on_deal_name`, `cte_name_collision`, `entity_constraint_missing`.

## 4. Deterministic data rules

Enforced outside the LLM, by the schema objects the LLM is allowed to query:

| Rule | How it is enforced |
|---|---|
| RM comes from `account_rm` | Only that RM column is exposed; the noisy RM fields are hidden. RM and `client_status` are described as per-record. |
| Client performance excludes group rows | Performance can only be read through views. `performance_client` and `performance_latest` contain client rows only; the base table is not approved. |
| Latest performance is per client | `performance_latest` is the client's own maximum `as_of_date` (temp view). The prompt forbids rebuilding it. |
| Group rows | Only in `performance_group`. |
| MOIC is compared numerically | The views expose MOIC as a numeric value under its natural name. The raw text column is not exposed, so a text comparison cannot be written. |
| Unavailable metrics stay unavailable | Before any LLM call, a mention such as `COP MOIC` or `HF Current MOIC` is checked against the metric columns that exist (derived from the schema). A missing one returns `unavailable_metric` with no substitute. |
| Grain | Counts distinguish rows from distinct clients, deals and names (prompt rule). Cross-source queries are rejected, so no row-level multiplication. |
| Amounts | USD field for sums across records; natural-currency amounts only within one currency (prompt rule). |
| Entities | Ambiguous mention: stop with `clarification_needed`. Unknown: `entity_not_found`. A client or group with no data in the source: `no_structured_data`. A deal id is queried by `deal_id` and its several names are never collapsed. Group membership differences are noted, not chosen. |
| Deal-size numeric value | Inert in this slice, because meetings are not queryable here. It is reserved for the hybrid slice. |

## 5. Failure behavior

Every failure is an explicit outcome with a stage and a code, and none is repaired or retried:

`unsupported_route`, `clarification_needed`, `entity_not_found`, `no_structured_data`, `unavailable_metric`, `llm_failure` (CLI error class passed through), `malformed_llm_output`, `missing_sql`, `unsupported_question` (the LLM said it cannot answer), `sql_rejected`, `execution_error`, `timeout`. A valid query with no rows is `empty_result` (a success, not an error).

## 6. Read-only execution

- The database is opened with `mode=ro` (SQLite URI) and `PRAGMA query_only = ON`. The temp views are created before `query_only` is switched on.
- A write attempt fails at the database level even if the authorizer allowed it (tested).
- A progress handler stops any query after 10 seconds (`timeout`).
- At most 50 rows are kept (`max_rows`); `truncated` says if more existed. Nothing large is passed to the LLM, and there is no LLM step after execution.

## 7. Why there is no SQL repair loop

A repair loop would send a rejected query and its error back to the model and run the result. That makes the final SQL unaudited, hides the model's failure rate (which is what V1 has to be justified by), can turn a safety rejection into a workaround, and costs another 5-8 seconds per call. Baseline records the first failure and stops. Contract section 7 rule 8 and the "no self-repair" scope decision say the same.

## 8. Known limitations

- **One call, one chance.** A malformed reply or a wrong query is a failed request.
- **The LLM decides the SQL.** The guard checks safety, sources and entities, not correctness. A well-formed but wrong query (wrong aggregation, wrong filter) executes. Correctness is measured by the benchmark.
- **Prompt rules are advice.** Only the view design, the column list, the source and entity checks and the LIKE rule are enforced in code. Row-versus-distinct counting and currency handling depend on the model following the prompt.
- **Metric availability uses a pattern.** It recognizes `CI/HF/RE/COP/INF/ICM/PE/Mena/Tech/Pref Shares` next to `MOIC/IRR/AUM`; a metric named in other words is not pre-checked (the LLM may still return `unsupported_reason`).
- **A constrained entity must appear as a literal.** A question that mentions an entity only in passing can be rejected for not filtering on it.
- **Row cap.** Long result lists are cut at 50 rows. Aggregate in SQL.
- **Latency.** Each request costs about 6-9 seconds, nearly all in the Claude CLI call.
- **Ties for latest.** `performance_latest` would return every row tied at a client's maximum date; none exist in this data.
- **Meetings, cross-source and multi-step questions are out of scope.** They return `unsupported_route` or need the hybrid slice.

## 9. Evidence that would justify V1 changes

- A measurable share of Baseline failures are `malformed_llm_output`, `missing_sql` or `sql_rejected` on questions that were answerable, which would justify a structured-output mechanism, a better prompt, or a single guarded regeneration (evaluated as a separate change).
- Executed but wrong queries cluster on one pattern (for example distinct-versus-row counts, ranking ties, currency), which would justify deterministic templates for that pattern or a stricter view.
- Questions blocked by the entity-literal rule or the metric pre-check that a human would accept.
- Latency or cost that makes evaluation runs impractical.
- Each change must be written up as Problem, Evidence, Change, Reason, Result, Remaining limitation, and tested against the same benchmark, then confirmed on the holdout.

## 10. Assumptions introduced by this slice

1. Temporary SQLite views (`performance_client`, `performance_latest`, `performance_group`) carry the performance rules, created per connection and never stored.
2. Raw MOIC text is not exposed; MOIC is numeric under its natural name.
3. `client_last_met_date` and `is_group_flag` are hidden.
4. Meetings are not part of the approved schema.
5. A query must use the canonical value of every resolved entity as a literal.
6. One query may not use both investments and performance.
7. A CTE may not reuse a real table or view name.
8. Ambiguous or unknown entity mentions stop the request before any LLM call.
9. The evidence keeps at most 50 rows; the timeout is 10 seconds.
10. The LLM may answer `unsupported_reason` instead of SQL.
