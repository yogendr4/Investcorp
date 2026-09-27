# Exploratory 20-Question Stress Test (Baseline / V1 / V2 / V2.1)

## Status and scope

**This is a one-off exploratory experiment, run once, outside the CLAUDE.md staged-development
process.** It is not part of the 40-question historical development/evaluation set
(`tests/evaluation/questions.json`, [`baseline_question_set.md`](baseline_question_set.md)) and
its results are **not directly comparable** to the historical Baseline/V1/V2/V2.1 scores in
[`baseline_results.md`](baseline_results.md), [`v1_results.md`](v1_results.md),
[`v2_results.md`](v2_results.md), [`v21_results.md`](v21_results.md) — different questions,
different (smaller) sample size, single run per version, no LLM judge. No repository file was
changed to run it; the question set, raw responses, and scoring script exist only in a session
scratchpad, not in this repository, and are not reproducible from anything checked in here.

## Purpose

The 40-question historical set scored V1 and V2.1 identically (24 correct / 5 incorrect / 11
needs-review each), with V2 in between at 22/7/11. That aggregate parity leaves open whether V2's
semantic meeting retrieval and V2.1's evidence-selection hardening add real capability that the
40-question set's specific wording simply doesn't exercise. This experiment tests that on 20 new,
realistic questions phrased in natural language (not schema terminology), built from entities and
facts verified directly against `data/derived/baseline.sqlite` before running.

## Composition

- 10 natural structured (e.g. "What is the latest CI multiple reported for client A12360?")
- 5 semantic-meeting: paraphrased away from the meeting text's own wording, intended to stress
  lexical vs. semantic retrieval
- 3 hybrid: one structured fact plus meeting evidence, phrased naturally
- 2 adversarial: one legitimate entity-ambiguity case, one refusal/unsupported case

## Aggregate results (out of 20, single run each)

| Version | Correct | Incorrect / Withheld |
|---|---|---|
| Baseline | 17 | 3 |
| V1 | 19 | 1 |
| V2 | 19 | 1 |
| V2.1 | 19 | 1 |

## Findings

1. **Baseline → V1 showed a clear improvement** on this new set (17→19), consistent with the
   historical result. The one Baseline-only failure that V1/V2/V2.1 all fixed (`HYB-03`) is a
   structured-group-membership-plus-meeting-evidence question: Baseline's synthesis both missed
   the relevant meeting evidence and mishandled a `NULL` action-items field; V1's hybrid handling
   answered it correctly, and V2/V2.1 preserved that.
2. **V2 and V2.1 did not improve the aggregate score beyond V1** on this set (19/19/19, flat).
3. **The 5 semantic-meeting questions were all retrievable by lexical BM25 as well as by
   semantic/fused retrieval.** Despite paraphrasing away from the source sentences, this
   experiment did not isolate a measurable semantic-retrieval advantage — every ground-truth
   meeting was found by the plain lexical retriever too.
4. This is consistent with a documented, pre-existing limitation of the meeting corpus
   ([`meeting_retrieval.md`](../architecture/meeting_retrieval.md) §9, "templated corpus"): a
   limited pool of boilerplate sentences is reused across many meetings, so a natural paraphrase
   still shares enough vocabulary with the underlying sentence for term-overlap retrieval to reach
   it. This experiment did not construct a case that gets past that limitation.
5. **`HYB-03`** demonstrates the structured+meeting hybrid path's value: Baseline failed, V1/V2/V2.1
   succeeded (see finding 1).
6. **`SEM-03`** exposed a V2.1 validator false positive: V2.1's answer correctly identified the
   relevant meetings as semantic-only matches (explicitly distinguishing them from lexical
   matches) and cited all of them with dates, but was withheld by the frozen number-grounding
   check over an incidental number in the explanatory prose, unrelated to the cited evidence.
   Meanwhile V2 answered the same question correctly. This is a genuine single-question regression
   from V2 to V2.1, caused by validator interaction with more explicit hedging language, not by
   evidence selection itself.
7. **`SEM-01`** had a V2-only Claude CLI timeout (the larger fused evidence payload increased
   generation time past the 60-second limit on this run); V2.1 answered the same question
   correctly on its run. Not evidence of a structural V2 defect on a single run.
8. **`NS-02`** (a record-level relationship-manager question) was withheld by the same validator
   rule on Baseline and V1, and answered on V2 and V2.1. No V1→V2 code change touches this path,
   so this is most plausibly LLM output-format variance across runs, not proof that V2 introduced
   a structural capability. It is reported for completeness, not as a V2 finding.

## Conclusion

**This experiment does not support a claim that V2 or V2.1 outperforms V1 on realistic language.**
It supports the narrower claim that **V2 and V2.1 preserved V1's capabilities** (no aggregate or
per-category regression versus V1 on this set) **while introducing semantic retrieval and
surfacing additional evidence-handling and validation behaviors** — one genuine, specific,
fixable validator false positive (`SEM-03`), and one latency/timeout side-effect (`SEM-01`) that
did not recur in the following version. It does not demonstrate a semantic-retrieval advantage,
because none of the 5 semantic-meeting questions in this set turned out to require one.
