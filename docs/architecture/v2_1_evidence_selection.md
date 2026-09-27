# V2.1: Evidence Selection (candidates versus synthesis evidence)

Code: `src/v2/v21.py`. Tests: `tests/v2/test_evidence_selection.py` (19). Origin: [`docs/evaluation/v2_diagnosis.md`](../evaluation/v2_diagnosis.md). No V2, V1 or Baseline file was modified; V2.1 is a wrapper and a service subclass.

## Problem
V2 returns the fused top-K list and passes all of it to the synthesizer. When the lexical list has fewer than 20 matches, RRF completes the list with semantic-only meetings whatever their similarity (it uses only ranks), so the synthesizer received 14 and 17 unrelated meetings in MEET-07 and ADV-10, described and cited one of them, and the frozen cited-id rule scored both incorrect. Retrieval order was right; there was no boundary between retrieval candidates and answer evidence.

## Change
The fused top-20 stays as the ranked **candidate list**, unchanged, for diagnostics. Synthesis and validation receive only the **evidence**:

> **evidence = candidates that have a lexical rank, plus candidates without one whose `semantic_rank == 1`.**

- *Top semantic cluster* = the meetings tied at the highest semantic score inside the entity scope, i.e. rank 1 in V2's existing competition ranking (`semantic_rank`, already stored per candidate). No score threshold and no new ranking were introduced.
- Evidence keeps the candidates' fused order and rank numbers. The response's `meeting_evidence.hits` is the evidence, `meeting_evidence.candidates` is the fused list with an `in_evidence` flag on each, and `meeting_evidence.selection` records the counts and the dropped meeting ids.
- Because the frozen validator checks citations against the evidence it is given, a dropped candidate cannot be cited.
- Applies only to fused results (`rank_method == "rrf"`). Listings, entity/scope gates, no-terms results and the lexical-only result returned when the embedding call fails pass through unchanged.

Use: `V21Service` (a subclass of `V2Service`; `V2Service` is unchanged and still passes every candidate), or `EvidenceSelectingRetriever(V2Retriever(...))`.

## Unchanged
Gemini Embedding 2, 128 dimensions, RRF k = 60, K = 20 and the candidate depth 50; lexical retrieval; entity filtering and scope; the query text (including negation preservation); the V1 intent classifier, listing, aggregates, date-relation refusal and structured queries; synthesis prompts and validation; the embedding index; Baseline, V1 and their tests; the benchmark and scoring.

## Verification
Unit and integration tests cover: filler excluded (MEET-07/ADV-10 style, 6 lexical + 14 semantic-only gives 6 evidence); semantic-only top-cluster hits retained; lexical matches retained even with a poor semantic rank; candidate list, order, ranks, scores and fusion metadata identical to V2's; entity and group scope unchanged; non-fused results untouched; a dropped candidate cannot be cited; the synthesis prompt contains only evidence meetings; listing, aggregate, structured and refused paths make no embedding call. Applied offline to the stored V2 benchmark hits (no calls): MEET-07 20 to 6 candidates-to-evidence with 6 of 6 expected kept and 4638 outside; ADV-10 20 to 3 with 3 of 3 kept and 11707 outside; MEET-08 (3 of 3), MEET-06 (5 of 14) and MEET-10 (1 of 1) keep all their expected meetings.

## Known risks and limits
- **A semantic target below the top cluster can be excluded.** A relevant semantic-only meeting that is not tied for the highest score, and has no lexical overlap, is a candidate but not evidence. In the earlier manual validation (client C12572), a different template sentence formed the top cluster and the target sat at semantic rank 8; it survived only because it also had a lexical rank.
- The cluster can be a wrong template, or very large for an unscoped question (thousands of tied meetings); evidence is still bounded by the K = 20 candidates.
- Lexical hits keep the semantic sentence already appended to their text, as in V2.
- Evidence can be smaller than 20, or (in principle) empty when no candidate is lexical and none is in the top cluster.
- The rule was shaped by five topical benchmark questions; it has not been run through the benchmark or the holdout. Scoring concerns (the cited-id rule and MEET-10) are unchanged and documented separately.
