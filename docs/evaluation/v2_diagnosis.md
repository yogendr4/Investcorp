# V2 Diagnosis (read-only)

Diagnosis of the V2 benchmark result ([`v2_results.md`](v2_results.md), [`v2_results.json`](v2_results.json)) against V1 and Baseline. V2 is frozen: no production code, test, prompt, benchmark, scoring rule or architecture document was changed, the benchmark was not re-run, no Vertex or Claude call was made, no package was installed and the holdout was not read.

**Sources:** the stored raw V2 and V1 run output (per-hit ranks, scores and answers), `src/v2/` and `src/v1/`, the persisted embedding index and the SQLite database (read-only reads), and the visible questions' ground truth. The evidence text the synthesizer saw was rebuilt by running V2's own serialization function on the stored evidence (no LLM). One offline replay (section 8) uses only stored hits.

## 0. Summary

| Question | Answer, from the artifacts |
|---|---|
| Is the semantic embedding component working? | Yes on this benchmark: in all five topical questions the meetings at semantic rank 1 (the top-scoring cluster) are all expected meetings; it recovered MEET-08 and 4 more MEET-06 meetings. Its lower-ranked scores are not discriminating (unrelated template sentences score 0.71-0.75 against 0.77-0.80 for the answer) |
| Is the integration working as intended? | As specified, yes: scope isolation held, RRF is computed as specified, expected meetings were ranked first. The specification has no boundary between retrieval candidates and answer evidence, so the fused top 20 is passed whole to the synthesizer |
| Where is the main V2 regression? | **Evidence selection** (filler fills the list to K = 20), amplified by a **synthesis** rule that asks the model to describe semantic matches, and turned into a failure by a **scoring rule**. Not retrieval order, not validation |
| Smallest V2.1 change | Separate candidates from evidence: pass the synthesizer the lexical matches plus only the top semantic cluster. Replayed offline on the stored hits, it drops no expected meeting and removes both extra cited meetings |

## 1. Retrieval versus evidence selection (MEET-07, ADV-10)

| | MEET-07 | ADV-10 |
|---|---|---|
| Expected evidence | 4147, 5506, 11756, 12801, 13181, 17742 (one sentence: "Governance issues included the current board composition and the absence of independent directors...") | 7568, 19047, 19238 (one sentence: "Currency exposure is significant with ~30% of revenues denominated in USD but expenses largely local...") |
| V1 evidence | exactly those 6 (lexical matches) | exactly those 3 |
| V2 evidence | 20 hits: the same 6, then **14 semantic-only** | 20 hits: the same 3, then **17 semantic-only** |
| Expected meetings' V2 position | fused ranks 1-6 (lexical 1-6, semantic rank 1, score 0.765, rrf 0.0315-0.0328) | fused ranks 1-3 (lexical 1-3, semantic rank 1, score 0.801) |
| Extra meetings | semantic ranks 7-20, score 0.718-0.745, rrf 0.0125-0.0149, no lexical rank | semantic ranks 4-20, score 0.710-0.765, rrf 0.0125-0.0156, no lexical rank |
| Gap: lowest expected score minus highest extra | 0.020 | 0.035 |
| Answer | names the 6, adds meeting 4638 and says it does not mention independent directors | names the 3, adds meeting 11707 and says it is a different topic |

**Are the extras irrelevant or supporting?** None states the asked claim. In MEET-07 they are template sentences about a family-office setup (3), school-operations cash flow (4), a management Q&A action (1), a declined opportunity (1), a missing CFO and treasury outsourcing (4) and a governance-gaps remark (4638). The last five are topically adjacent (governance and finance) but are not evidence for "the absence of independent directors". In ADV-10, the 17 extras are 5 signature lines ("- noted by <name>") and 12 generic template sentences; the closest, 11707, mentions hedging alternatives in a seasonal cash-flow sentence. So they are filler with loose topical overlap, not supporting evidence.

**Cause.** The retrieval is doing what it was asked: the expected meetings are first. The lexical list had only 6 (and 3) matches, so the semantic list completes the fixed K = 20 with the next-ranked meetings whatever their similarity. The regression is created when those 14 and 17 meetings are **passed to synthesis as evidence**.

## 2. RRF behavior

Origin of the 20 returned hits (lexical and semantic / lexical only / semantic only), from the stored hits:

| Question | Scope | Lexical cand. | Both | Lexical only | Semantic only | Semantic-only score band (top in list) | Tie clusters among returned hits (semantic rank: size) |
|---|---|---|---|---|---|---|---|
| MEET-06 | 20,000 | 50 | 4 | 8 | 8 | 0.813-0.817 (0.817) | 1: 5, 6: 3, 9: 2, 25: 2 |
| MEET-07 | 23 | 6 | 6 | 0 | 14 | 0.718-0.745 (0.765) | 1: 6, 7: 3, 10: 4, 16: 4, ... |
| MEET-08 | 18 | 15 | 15 | 0 | 3 | 0.710-0.748 (0.788) | 1: 3, 4: 2, 6: 3, ... |
| MEET-10 | 23 | 18 | 18 | 0 | 2 | 0.767 (0.851) | 3: 4, 7: 3, 11: 4, ... |
| ADV-10 | 23 | 3 | 3 | 0 | 17 | 0.710-0.765 (0.801) | 1: 3, 6: 6, ... |

- **Interaction.** A semantic-only meeting at semantic rank r scores 1/(60+r); a lexical-only meeting at lexical rank r scores the same. Rank 7 gives 0.0149, rank 20 gives 0.0125, lexical rank 1 gives 0.0164, a meeting in both lists at rank 1 gives 0.0328. The similarity score is never used, only the rank, so a semantic list that always has 20 or more candidates in scope **always** completes the final list to K.
- **Weak semantic-only candidates enter the top 20?** Yes, by construction. In MEET-07 and ADV-10 they entered at scores 0.02-0.09 below the top cluster.
- **Template clusters.** Repeated sentences give exact ties: every meeting sharing a sentence has the same score and the same rank (in MEET-07 each extra's sentence is shared by 2,697-4,867 meetings; in ADV-10 the extras mix template sentences shared by 761-4,829 meetings with near-unique signature lines shared by 1-6). Tied meetings get equal semantic contributions and are ordered by the tie-break (lexical rank, then date, then id), which has nothing to do with relevance. Competition ranking makes ranks jump (1, 1, 1, 4...). The clusters are informative at the top (the top cluster was exactly the expected set) but noise in the tail.
- **Is RRF appropriate as a first choice?** The evidence does not point at the formula. It ranked the expected meetings first in MEET-07, ADV-10 and MEET-10 and at fused ranks 2, 4, 6 in MEET-08. Its cost is the missing cut-off (next section). One observed property: the formula is symmetric, so in MEET-08 meeting 8623 (lexical 1, semantic 4) and 5791 (lexical 4, semantic 1) tie at 0.032018 and the lexical-rank tie-break puts the lexical one first. No tuning is proposed.

## 3. Evidence boundary

There is **no boundary** between retrieval candidates and answer evidence. In code:

- `src/v2/retrieval.py:196` `order = fuse_order(cands, scope)[:k]` produces the top-K fused list, and it is returned as `V2Evidence.hits` (lines 208-218). K = 20 is both the retrieval cut-off and the evidence size.
- `src/v1/service.py:115` stores that object as the meeting evidence; `:155` `m_use = m_ev` passes all of it; `:161` sends it to synthesis; `:170` validates the answer against it.
- `src/v1/synthesis.py:55` `for h in ev.hits[:n_hits]` renders every hit (only a 16,000-character cap can trim, and it did not trigger: 8,948 and 8,133 characters).

What the synthesizer sees for MEET-07 (rebuilt): the header states `with a text match: 6; shown: 20`; six hit lines carry `matched summary`; 14 hit lines have no matched field and a `semantic match (0.745): "..."` sentence in their text. Ranks and RRF scores are not shown; the semantic score is only inside the sentence text. Prompt rule 12 says: "a 'semantic match' sentence was retrieved by meaning... say what that sentence says, not that the meeting is proven relevant."

The distinction exists in the data (`lexical_rank`, `semantic_rank`, `semantic_score`, the `semantic.candidates` counts) but is used by nothing downstream.

## 4. Synthesis: the regressed cases

| Case | Evidence sent | Why the extra meeting appears | Classification |
|---|---|---|---|
| MEET-07 | 20 hits (6 matched, 14 semantic-only), see section 3 | The answer says "One other meeting was retrieved by meaning only. Meeting 4638 includes a remark recommending a pause given 'current governance gaps'... It does not mention independent directors, so it is not shown to flag that issue," and cites it | (a) retrieval **introduced** the distractor (K-fill); (b) synthesis **chose** to describe and cite one of 14 (rule 12 asks it to describe semantic matches); (c) the frozen cited-id equality rule **rejected** an answer whose own text says the extra meeting is not a match |
| ADV-10 | 20 hits (3 matched, 17 semantic-only) | "The other 17 meetings shown were retrieved by semantic similarity only, and none of their retrieved sentences states this point. One (meeting 11707)... mentions hedging alternatives... a different topic," cited | same three components |
| MEET-05 | aggregate evidence, **byte-identical** to V1's (rebuilt) | Not a retrieval effect. The V2 prompt differs from V1's by one inserted rule (12) about hybrid retrieval; the V1 and V2 answers differ in wording ("cannot say more about which meetings are included" against "the specific meetings and their dates are not available") | (c) the validator's contradiction check on an "unavailable" phrase flagged a correct count; V2 effect and run-to-run wording variance cannot be separated |
| MEET-06 (still incorrect) | 20 hits (12 lexical, 8 semantic-only) | Not a regression: see section 5 | n/a |
| HYB-05 (status changed) | listing evidence, unchanged path | LLM wording (V1's answer cited `[performance]` for an absent source) | variance |

## 5. MEET-06: 1 of 14 to 5 of 14

The improvement is **genuine recovery, not candidate expansion.**
- Only 5 of the 14 expected meetings contain the name in their **summary** (348, 1240, 16485, 17056, 19916); the other 9 contain it only in the **attendees** field, which is not embedded. Database-wide, exactly those 5 summaries contain the sentence "- noted by Sanjay LÃ³pez.".
- V2 returned all 5 and all 5 form the semantic rank-1 cluster (score 0.817). V1's lexical top 20 held 1 (17056); meeting 16485 had lexical rank 40 and was lifted into the list by semantic rank 1; 348, 1240 and 19916 had no lexical rank in the returned list.
- Expansion noise exists too: of 8 semantic-only hits, 3 are expected (precision 3 of 8); the other 5 carry "noted by <another person>" lines at 0.813.
- The remaining 9 of 14 are outside what the sentence index can see. The lexical retriever does index attendees, but the OR of terms (`sanjay` matches 627 meetings) drowns them.

## 6. MEET-08: why V2 recovered it

| Meeting | V1 lexical rank (matched words) | V2 lexical / semantic rank | Semantic score | V2 fused rank |
|---|---|---|---|---|
| 5791 | 4 (`time, cash, management`) | 4 / **1** | 0.788 | 2 |
| 3664 | 8 (`cash, management`) | 8 / **1** | 0.788 | 4 |
| 9932 | 12 (`management`) | 12 / **1** | 0.788 | 6 |

- **Not absent lexically:** all three were in the lexical list (ranks 4, 8, 12 of 15) on generic words. What V1 lacked was ranking and visibility: the snippets showed unrelated text (5791's V1 snippet was a family-office sentence).
- **Why V2 recovered it:** (1) the semantic list isolates the three at 0.788 against the next cluster at 0.761, an exact-tie cluster of exactly the three expected meetings; (2) the matched sentence was appended to each hit's text (`semantic match (0.788): "...lacks a dedicated CFO and is outsourcing treasury functions..."`), so the synthesizer saw it and the answer cited exactly the three. The semantic query text was `concern no full time finance chief relies outside providers cash management`; the negation was kept, but no run without it exists, so its contribution was not measured.
- The client's scope (18) is smaller than K (20), so presence in the list is not selective; the rank, the shown sentence and the cited ids are the evidence.

## 7. Scoring and validation: what is what

| Item | Class | Reason |
|---|---|---|
| MEET-07, ADV-10 incorrect | **Evaluation artifact plus a genuine evidence-precision issue** | The answers name exactly the expected meetings and state that the extra one is not a match, but the frozen rule scores the cited ids. The genuine part: the evidence and the answer now carry 14 and 17 meetings unrelated to the question. The same pattern is why MEET-10 is needs-review, so the rule is inconsistent about a cited non-match |
| MEET-05 error | **Validator limitation** (plus wording variance) | A correct count withheld for an "unavailable" phrase about other details |
| HYB-10 error | **Validator limitation** (unchanged from V1) | Merged row counts |
| MEET-06 incorrect | **Genuine retrieval limitation, partly improved** | 9 of 14 not visible to the sentence index; lexical OR noise |
| HYB-06, HYB-09 | unchanged by V2 | date dependency; date relation refused |
| Validation of MEET-07 and ADV-10 | not involved | both answers passed validation |

No scoring rule was changed and nothing was re-scored.

## 8. V2.1 options (derived from the evidence; nothing implemented)

**Offline replay of option 1 on the stored hits (no calls):** evidence = hits that have a lexical rank, plus semantic-only hits with semantic rank 1.

| Question | Returned | Evidence | Expected recovered (returned to evidence) | Extra cited meeting dropped |
|---|---|---|---|---|
| MEET-06 | 20 | 15 | 5/14 to 5/14 | none cited |
| MEET-07 | 20 | 6 | 6/6 to 6/6 | 4638 dropped |
| MEET-08 | 18 | 15 | 3/3 to 3/3 | none cited |
| MEET-10 | 20 | 18 | 1/1 to 1/1 | 11756 stays (a lexical near-duplicate, as in V1) |
| ADV-10 | 20 | 3 | 3/3 to 3/3 | 11707 dropped |

Caveat: five questions, and I looked at these hits while forming the rule; the replay cannot show how the model responds. Counter-evidence from the earlier manual validation (`v2_semantic_retrieval.md` section 9): in the client C12572 stress question the top semantic cluster (7 tied meetings) was a *different* template sentence ("Compliance checks flagged potential tax structuring...", 0.772), and the target meeting sat below it at semantic rank 8 (0.764); the rule would admit that cluster and drop the target unless it also had a lexical rank (it did, so it would remain, but a target without lexical overlap would be lost).

| # | Change | Failures addressed | Component | Expected benefit | Regression risk | Metric to watch |
|---|---|---|---|---|---|---|
| 1 | **Separate candidates from evidence:** keep the fused top-K as the ranked candidate list; pass synthesis and validation an evidence set of lexical matches plus semantic-only hits in the top semantic cluster (semantic rank 1) | MEET-07, ADV-10 (extras enter as evidence) | a selection step after `fuse_order` in `src/v2/retrieval.py`, with candidates and evidence both stored in the evidence object | removes the filler; keeps MEET-08 and MEET-06's recovered meetings; the replay drops no expected meeting | the top cluster can be a wrong template (D12454 case) or very large for an unscoped query (thousands of tied meetings); depends on exact ties in a templated corpus; small sample | expected recall in evidence; number of non-expected meetings in evidence; extra cited meetings; the lexical controls and MEET-08/MEET-06 unchanged |
| 2 | **Synthesis grounding:** tag each hit as `lexical match` or `semantic-only` in the evidence lines and reword rule 12 so semantic-only sentences are described only if they support the question; the answer lists only matching meetings | the model's enumeration of extras (MEET-07, ADV-10) | `src/v2/synthesis.py` (rule text and hit-line tags) | reduces mentions of unrelated meetings without touching retrieval | prompt wording changes are LLM-dependent; the model might stop reporting genuine semantic matches (MEET-08 is the test) | MEET-08 still cites exactly the three; cited non-expected meetings across the five topical questions |
| 3 | **Relative semantic-score floor for semantic-only candidates** (not recommended on current evidence) | filler at lower scores | `src/v2/retrieval.py` | none demonstrated | the top-cluster gaps to the next cluster are 0.020, 0.027, 0.035, 0.010 and 0.004 (MEET-07, MEET-08, ADV-10, MEET-10, MEET-06), with no stable threshold; the extras' cosine similarity to the expected sentence reaches 0.83 | n/a |

RRF adjustment, a larger K and a different dimension are **not** indicated: the ordering was right in every question examined.

## 9. Decision

- **Semantic embedding component working?** Yes. Top-cluster precision was 100% across the five topical questions (5 of 5 meetings for MEET-06, 6 of 6, 3 of 3, 1 of 1, 3 of 3) and it recovered MEET-08. Limits: the tail scores are not discriminating, it cannot see attendees, and the manual paraphrase cases were mixed.
- **Integration working as intended?** Yes, as specified; the specification lacks a candidate/evidence boundary, which is the design gap this benchmark exposed.
- **Main regression location:** evidence selection (K-fill), then synthesis (rule 12), then a scoring rule; validation and retrieval order are not the cause. MEET-05 is separate (validator limitation and wording variance).
- **Smallest V2.1 change:** option 1, a selection step at the evidence boundary.

### Root-cause breakdown
| Observed change | Cause |
|---|---|
| MEET-07, ADV-10 correct to incorrect | evidence selection (K-fill), synthesis (rule 12), scoring rule (cited-id equality) |
| MEET-05 correct to error | validator limitation and LLM wording variance (identical evidence, one extra rule in the prompt) |
| MEET-08 incorrect to correct | semantic retrieval (semantic rank 1 plus the shown sentence) |
| MEET-06 1/14 to 5/14 | semantic retrieval (genuine recovery of the 5 summary-visible meetings) |
| HYB-05 status change | LLM wording (path unchanged) |

### Strongest evidence
1. MEET-07 and ADV-10: V1 evidence was exactly the expected set; V2 evidence is the same set plus 14 and 17 unrelated semantic-only meetings; the model discussed and cited one each.
2. In all five topical questions the semantic rank-1 cluster contained only expected meetings; the extras all sit at ranks 4-20 with scores 0.02-0.09 lower.
3. `retrieval.py:196` and `service.py:155,161`: one list serves as the retrieval result and as answer evidence.
4. MEET-06: exactly the 5 meetings whose summaries carry the sentence were returned; the other 9 have the name only in attendees.
5. MEET-05: byte-identical evidence in V1 and V2; the prompts differ by one inserted rule.

### Recommended V2.1 experiment
Option 1, staged so that each stage can stop it: (1) the offline replay above, already done, extended to the earlier manual cases (D12454 paraphrase, C12572 stress) using stored results where available; (2) implement the selection step, re-run only the five topical benchmark questions, compare expected recall in evidence, extra cited meetings and the lexical controls; (3) run the visible benchmark once, with the same frozen scoring; (4) only then, with your approval, a holdout check, because the rule was shaped by these five questions.

### Confirmation
No code, test, data, index, prompt, benchmark, scoring or evaluation artifact was modified; the persisted index and database were only read; no holdout read; no Claude or Vertex call; no package installed. The only new file is this document.
