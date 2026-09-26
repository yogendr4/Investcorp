# Encoding Impact Test — Design Only (Baseline 0.1)

Status: DESIGN. Nothing has been run. No results are claimed here.

## 1. Question

Does repairing mojibake in meeting text materially improve retrieval, or can Baseline 0.1 run on RAW text?

Observed context (FACT, from `docs/metadata/data_dictionary_DRAFT.md` E1): 14,241 of 20,000 `summary` rows and 2,107 `attendees` rows contain UTF-8 text decoded as cp1252 (`â€”`, `Â±`, `Ã³`, `Ä`+U+008D). `action_items` and `company` are clean.

## 2. Paired design

Two arms, identical in everything except the text:

| Arm | Text indexed |
|---|---|
| RAW | `summary` and `attendees` exactly as in the workbook |
| REPAIRED | Same columns after mojibake repair, stored as separate columns. RAW is never overwritten. |

Held constant across arms: retriever type and settings, chunking, top-k = 5, tie-breaking, query set, random seeds, machine. The retriever itself is chosen by the baseline design, not by this test. The test is retriever-agnostic.

Repair must be verified before use: zero rows left containing the mojibake sequences; the only new non-ASCII characters are em dash, plus-minus, o-acute, c-caron; rows that cannot be repaired are counted and reported. A naive cp1252 round trip is known to fail on the U+008D sequences, so failures must be expected and counted.

## 3. Query set

Frozen before either arm is run. Each query has gold labels defined against the REPAIRED (canonical) text:

- `relevant_ids`: the `meeting_id`s that answer the query (1 to 5 per query, so Recall@5 can reach 1.0).
- `evidence`: a short span that a correct passage must contain (e.g. an attendee name, a stated figure, a phrase).

Two strata, reported separately and combined:

| Stratum | Content |
|---|---|
| Affected | Query answer depends on an affected token (e.g. attendee "Sanjay López", a "±" figure, an em-dash phrase). |
| Control | Answer does not touch an affected token. |

Proposed size (proposal, needs approval): at least 100 queries per stratum. Queries are generated deterministically from structured columns (client, company, sector, region, date) wherever possible, plus a hand-checked subset for affected tokens.

## 4. Metrics

Computed per query, averaged over the query set, for each arm.

| Metric | Definition |
|---|---|
| Recall@5 | `\|top5 ∩ relevant_ids\| / \|relevant_ids\|` |
| MRR@5 | `1 / rank` of the first relevant `meeting_id` in the top 5; 0 if none |
| Evidence Hit Rate@5 | 1 if at least one of the top 5 retrieved passages contains the `evidence` span, else 0. Matching is done after canonicalising both sides (same normalisation for both arms), so RAW is not penalised by the matcher itself, only by retrieval. |
| Latency (optional) | Indexing time and median per-query time, per arm, same machine, warm cache, median of repeated runs. Reported as a ratio. Not used in the decision rule. |

Paired comparison: for each query compute `delta = metric(REPAIRED) - metric(RAW)`. Report mean delta in percentage points and a 95% bootstrap interval over queries (information only).

## 5. Decision rule (from Yogendra)

Encoding repair is treated as NON-ESSENTIAL for baseline if both hold:

1. RAW Evidence Hit Rate@5 >= 90%
2. `Recall@5(REPAIRED) - Recall@5(RAW)` < 5 percentage points

Evaluated on the combined query set, using point estimates. Otherwise repair is not declared non-essential.

Notes:
- The affected-stratum numbers are always reported alongside, so a large loss on the small affected subset is visible even if the combined figure passes.
- If the rule fails and REPAIRED is also low, the cause is probably not encoding. Diagnose before adding repair.

## 6. Outputs (proposed locations, not created)

Query set and per-query results under `evaluation/`; a short result note recording Problem / Evidence / Change / Reason / Result / Remaining limitation (CLAUDE.md section 6).

## 7. Points needing approval

1. Query-set size and generation method (Section 3).
2. Whether the decision rule is applied to the combined set (as written) or to the affected stratum.
3. Retriever choice, which this test depends on and does not decide.
