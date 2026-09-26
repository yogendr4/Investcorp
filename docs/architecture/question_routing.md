# Baseline Question Routing

Implementation: [`src/baseline/question_routing.py`](../../src/baseline/question_routing.py). Tests: `tests/baseline/test_question_routing.py`. This is component C2 of [`baseline_contract.md`](baseline_contract.md) (section 6). It is rule-based: no LLM, no embeddings, no fuzzy matching.

## 1. What it does

`route_question(question, resolution=None)` returns a `RoutingResult` with exactly one route:

| Route | Meaning |
|---|---|
| `investment` | Answer from the investments table. |
| `performance` | Answer from the performance table (latest per client, client rows unless a group is asked for). |
| `meeting` | Answer from meeting records (filter first, then lexical retrieval). |
| `hybrid` | Needs structured investment or performance facts **and** meeting evidence. |
| `ambiguous` | The route cannot be chosen safely. Nothing is defaulted. |

Result fields: `route`, `status` (`routed` or `ambiguous`), `confidence` (`high`, `medium`, `none`), `rule` (the routing rule that decided), `signals` (every vocabulary match with family, strength and role), `source_cues` (signals grouped by family), `entity_cues`, `entities_needing_attention`, `ambiguity` (code, kind, message), `rationale` and `notes`.

## 2. Stages

1. **Signal extraction.** The vocabulary table `SIGNAL_RULES` is applied in order, longest phrases first. Text claimed by an earlier rule cannot match a later one. This is how `estimated deal size` and `latest meeting` are kept from being read as an investment "deal" or a performance "latest".
2. **Role assignment.** An investment or performance word is marked `topic` when it is the subject matter of a meeting question, for example "what was discussed about MOIC" (section 5). Topic words do not count as structured signals.
3. **Entity awareness.** Entity-resolution results are recorded as cues and never change the route (section 7).
4. **Routing rules.** `ROUTING_RULES` are applied in the precedence order below. The first rule that applies decides, and its id is returned.

## 3. Signal categories

| Family | Strong signals (examples) | Weak signals (examples) |
|---|---|---|
| Investment | `investment(s)`, `invested`, `investment records`, `deal`, `deal ID`, `deal name`, `RM`, `relationship manager`, `account manager`, `client status` | `amount`, `USD`, `currency`, currency codes, `records`, `commitment`, `exposure`, `line of business`, `capital call` |
| Performance | `MOIC`, `IRR`, `AUM`, `performance`, `snapshot`, `as of`, `receivables`, `distribution`, `performance status` | `return(s)`, `multiple(s)`, `latest`, `current`, `trend`, `improving`, `over time` |
| Meeting | `meeting(s)`, `met`, `latest/most recent meeting`, `discussed`, `conversation`, `said`, `raised`, `mentioned`, `flagged`, `concern(s)`, `action items`, `topics`, `attendee/attended`, `notes`, `summary`, `estimated deal size` | `sector`, `region`, `stage`, `company`, `investment stage` |
| Status words | `Closed` (exists only in investments ClientStatus). `Active`, `Dormant`, `Prospect` (exist in both investments and performance). Handled by their own rule (R6). | |

`commitment` and `exposure` are weak because they are not documented fields (metadata section 3.1). Weak words are generic or belong to more than one source, so they never decide a route alone.

## 4. Rule precedence

| Order | Rule | Applies when | Result |
|---|---|---|---|
| 1 | `R1_NO_SIGNALS` | No vocabulary signal at all | `ambiguous` (insufficient intent) |
| 2 | `R2_RM_MEETING_LINK` | An RM cue (the word, or a resolved RM entity) and meeting signals, and no other structured signal | `ambiguous` (unsupported assumption): meetings have no RM field (A2, A3) |
| 3 | `R3_HYBRID` | A strong structured signal (investment or performance) **and** a strong meeting signal | `hybrid`, high |
| 4 | `R4_MULTI_STRUCTURED` | Strong investment **and** strong performance signals, no meeting signal | `ambiguous` (conflict) |
| 5 | `R5_SINGLE_FAMILY` | Strong signals from exactly one family | That route. `high`, or `medium` if weak words from another family are also present |
| 6 | `R6_STATUS` | Only status words | `Closed` alone: `investment`, medium. Otherwise `ambiguous` (status exists in two sources) |
| 7 | `R7_WEAK_ONLY` | Only weak signals | Two or more from one family: that route, medium. Otherwise `ambiguous` |

## 5. Hybrid detection

Hybrid means the answer needs both a structured fact and meeting evidence, for example:

- a performance filter plus a meeting topic ("clients with MOIC below 1x who raised concerns in meetings");
- an investment figure plus a meeting question ("total investment and what was discussed at the latest meeting");
- a performance threshold plus meeting activity ("latest IRR above 10% and had a meeting in 2025");
- counts from two sources ("how many investment records and how many meetings").

Hybrid is **not** chosen because an entity appears in several sources, and not when a structured word is only the topic of a meeting question. A word counts as a topic when it follows a speech or discussion word within four words ("said about", "discussed", "concerns about", "raised") or is followed by a discussion noun or passive ("MOIC discussions", "deals were mentioned"). "What did the client say about MOIC in the meeting?" is `meeting`. "Which clients with MOIC above 2x discussed liquidity?" is `hybrid`, because `MOIC` there is a filter on clients.

## 6. Ambiguity behavior

`ambiguous` is returned, with a code and a kind, when no route is safe:

| Code | Kind | Example |
|---|---|---|
| `no_intent_signals` | `insufficient_intent` | "Tell me about Client A12345." |
| `weak_signal_only` | `insufficient_intent` | "What is the total amount?" |
| `conflicting_weak_signals` | `conflict` | "What is the latest return amount?" |
| `multiple_structured_sources` | `conflict` | "What is the total investment and the latest MOIC?" |
| `status_source_unclear` | `conflict` | "How many clients are Active?" |
| `rm_meeting_link` | `unsupported_assumption` | "How many meetings were held by relationship manager X?" |

The router never defaults to investment. A later stage decides what to do: `insufficient_intent` and `conflict` call for a clarification, and `unsupported_assumption` matches the contract's DECLINE disposition (contract section 6).

## 7. Entity awareness

Entity-resolution results are passed in and preserved:

- Every mention appears in `entity_cues` with its status. Ambiguous and not-found mentions are copied to `entities_needing_attention` unchanged. The router does not resolve or fix them.
- A resolved `deal_id` does not make a question investment-only ("what was discussed about DL100001" is `meeting`).
- A meeting-only client does not force the meeting route: "B12463's latest MOIC" is `performance`, with a note that the client has no performance data. The consumer reports the missing data.
- The only entity effect on the route is the RM cue in R2, because an RM name is the same kind of evidence as the words "relationship manager".

## 8. Why deterministic routing is the Baseline

- **Inspectable.** Every route names the rule and the matched words, so a wrong route can be diagnosed and fixed with a rule change.
- **Documented rules matter here.** The data has specific traps that keywords must respect (`deal size` is a meeting field, `latest meeting` is not a performance signal, `Closed` exists in one source only).
- **Contract.** Do not use an LLM where deterministic code is sufficient (CLAUDE.md section 8). Agentic or model-based routing needs a demonstrated Baseline limitation.
- **A baseline to measure against.** V1 can be compared with these rules on the same questions.

## 9. Known limitations

- **Vocabulary coverage.** A question that expresses intent in words outside the tables (for example "portfolio value", "how did it do", "who talked to us") gets `no_intent_signals` or a weak-only result. It is safely ambiguous, not wrong, but it costs a clarification.
- **Generic words.** `performance`, `deal`, `met` and `meeting` are strong signals even when used loosely ("meeting the criteria", "performance of the process").
- **Topic detection is a window rule.** A structured word more than four words after a discussion word, or phrased in an unexpected way, is read as structured and can produce `hybrid` instead of `meeting`.
- **Two structured sources.** A question about investments and performance together, without meetings, is `ambiguous` (R4). Baseline has no route for it.
- **Sector, region, stage and company** are weak, so questions about them alone ("Which sector has the most companies?") are ambiguous unless a meeting word is present.
- **Status words.** "Investment status" is read as the performance field name (`Investment_Status_Name`), which may not be what the user meant.
- **Compound questions.** Each clause is not routed separately. The whole question gets one route.
- **English only, no negation handling, no spelling correction, and dates and numbers are not used.**

## 10. Evidence that would justify LLM-assisted routing in V1

- Recorded Baseline results (same benchmark, same scoring) show routing errors or false-ambiguous results caused by **vocabulary and paraphrase gaps**, not by rule bugs.
- Adding rules gives diminishing returns: each new rule fixes few questions or breaks others.
- The share of clear-intent questions returned as `ambiguous` (R1, R7) is high enough to hurt the user experience, and the questions are answerable once a route is chosen.
- An LLM classifier restricted to the four routes plus `ambiguous`, run through the CLI adapter, beats the rules on a paired comparison over the same questions, **and** does not turn explicit ambiguity (R2, R4, R6) into a confident route.
- The holdout confirms the gain. The size of gain that counts is for you to set (not defined here).

## 11. Assumptions introduced by this slice

1. An RM plus meetings question is `ambiguous` (`unsupported_assumption`), not `hybrid` and not a decline. The consumer maps the kind to a decline.
2. Two structured sources without meetings are `ambiguous`, because hybrid is defined as structured plus meeting evidence.
3. `Closed` routes to investments because the value exists only there. `Active`, `Dormant` and `Prospect` are ambiguous unless a source is named. "Client status" is investments and "performance status" or "investment status" is performance.
4. Weak words never decide alone. Two or more from one family give a `medium` route.
5. Multi-word phrases (`estimated deal size`, `latest meeting`, `investment stage`) are matched before their single words.
6. Topic detection uses a four-word window after a discussion word, and a few noun and passive forms after the term.
7. `status` is `routed` or `ambiguous`, and `confidence` is `high`, `medium` or `none`. These labels are not calibrated probabilities.
8. The contract's optional LLM fallback for "no cue at all" is not used. It would attach to `R1_NO_SIGNALS` if enabled later.
