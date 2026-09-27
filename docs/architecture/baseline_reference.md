# Baseline Reference: Historical Tag vs. Current Dictionary-Aligned Baseline

## Purpose

There are now two distinct Baseline reference points. This document names both explicitly so
that "Baseline is frozen" is never ambiguous, and points at the test that enforces the
distinction (`tests/v1/test_v1_service.py::TestBaselineIsFrozen`).

## 1. Historical: `baseline-v0.1`

- Annotated tag `baseline-v0.1`, tag object `36b85f87d06d7d776e001a90c2d05b608920a9aa`,
  pointing at commit `3ee53fb1efa82af8fc8e53ed7ef73811e019f4f5`.
- This is the exact state that was measured in `docs/evaluation/baseline_results.md/json` and
  used as the fixed comparison point for the V1, V2, and V2.1 evaluations.
- It is immutable: nothing in this or any future task moves the tag or rewrites the commit it
  points to. `TestBaselineIsFrozen.test_baseline_v0_1_tag_itself_is_unmoved` checks this
  directly against the two SHAs above, independent of the working tree.

## 2. Current: dictionary-aligned Baseline

- The working tree's `src/baseline/` and `tests/baseline/` as they exist today, after (a) the
  official `data/Assignment_Data_Dictionary.xlsx` arrived and 8 decisions were aligned into the
  code, and (b) Abhishek's subsequent reconciliation of the open data questions, which
  superseded some of (a)'s framing (see `docs/metadata/data_dictionary_DRAFT.md` decisions
  1-8 and assumptions A15-A18 — A15's LOB mapping and decision 1 reflect Abhishek's answers,
  not the earlier guesses).
- This differs from `baseline-v0.1` in exactly these 5 files, and no others:
  - `src/baseline/data_schema.py` — `id_CapitalCall` column note updated (flag, not identifier; A16).
  - `src/baseline/data_validation.py` — 6 frozen facts + checks (`client_last_met_date` vs.
    `as_of_date`/meetings, `total_aum_amount` vs. LOB sum, `id_capital_call` value set,
    group-membership overlap) documenting decisions 2/5/7/8. (Decision 1's checks were removed:
    Abhishek confirmed performance First_*/Last_*_Investment_Date are not usable application
    facts at all, so there is nothing left to freeze as a fact worth tracking.)
  - `src/baseline/structured_schema.py` — SQL-generation prompt text carries the same facts
    (non-additive AUM, no cross-source name join, confirmed canonical LOB codes) and now
    **excludes** performance First_*/Last_*_Investment_Date from the approved query surface
    entirely, rather than exposing them with a caveat (decision 1).
  - `tests/baseline/test_data_layer.py` — regression tests for the 4 remaining frozen facts,
    plus a test that the excluded investment-date columns are still physically stored but not
    queryable.
  - `tests/baseline/test_structured_query.py` — regression tests asserting the new prompt
    text and column exclusions (and that the existing route-isolation guarantees still hold).
- Scope of the behavior change: no query routing, entity resolution, SQL-generation *logic*,
  retrieval, or synthesis code was touched. `structured_schema.py`'s approved-column surface did
  change (performance First_*/Last_*_Investment_Date removed) — no current route queries those
  columns, so no observable answer changes today, but this is a real (if inert) change to what
  the SQL-generation LLM is permitted to select, not documentation-only.
- `TestBaselineIsFrozen.test_baseline_files_differ_from_the_tag_only_where_explicitly_approved`
  enforces that the working tree may diverge from `baseline-v0.1` in exactly this named set of
  files, and fails on any other, unapproved drift in `src/baseline`, `tests/baseline`, or
  `tests/evaluation/questions.json`.

## 3. What happens next

No new tag exists yet for the dictionary-aligned Baseline. Per the current approval, a new tag
(e.g. `baseline-v0.2`) is only cut once the 40-question benchmark has been re-run against this
current tree and the result is approved — not as part of this cleanup.
