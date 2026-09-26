# Baseline Service (orchestration, synthesis, validation)

Implementation: `src/baseline/service.py` (orchestration), `synthesis.py` (Claude prompt and call), `answer_validation.py` (deterministic checks). Tests: `tests/baseline/test_baseline_service.py`. Entry point: `answer_question(question) -> BaselineResponse` (also `BaselineService().answer_question`). This composes the approved components of [`baseline_contract.md`](baseline_contract.md); it adds no SQL, retrieval or resolution logic.

## Baseline Runtime Flow

```text
question
  -> entity resolution      (EntityResolver, deterministic)
  -> entity gate            ambiguous / unknown entity -> clarification (<= 10 candidates)   [no Claude]
  -> routing                (route_question)  ambiguous -> clarification, unsupported assumption -> unsupported  [no Claude]
  -> evidence
       investment | performance : StructuredQueryEngine (one LLM SQL call, guarded)
       meeting                  : MeetingRetriever (FTS5 / BM25, no LLM)
       hybrid                   : both, independently, no row join
  -> evidence classification   evidence | no_data | clarification | unsupported | error
  -> synthesis              one Claude CLI call (claude-sonnet-5, effort low), bounded evidence only
  -> validation             deterministic; invalid -> answer withheld
  -> BaselineResponse
```

## 1. Component boundaries

| Concern | Owner |
|---|---|
| Entity identity | `EntityResolver`. The service only reads its result. |
| Route | `route_question`. The service never overrides it. |
| SQL | `StructuredQueryEngine` (guarded, read-only). |
| Meeting search | `MeetingRetriever`. |
| LLM transport | `ClaudeCliAdapter`, shared by SQL generation and synthesis. |
| Sequencing, status, failure mapping, timing | `BaselineService`. |

Components are injectable, so tests use fakes. The real ones are created lazily.

## 2. Evidence flow

- **Hybrid** runs the structured side with the question plus a fixed suffix telling the SQL generator to answer only the structured part, and the router's strong source cue picks investment or performance. If both sources are cued, the request is `unsupported` (`hybrid_two_structured_sources`).
- The meeting side searches with the question's terms minus the words that belong to the structured part (`_hybrid_meeting_query`). Neither side restricts or filters the other; they meet only through the client id both were given, and the prompt says so.
- Evidence sent to Claude is compact text: SQL source and rules applied, at most 50 rows, at most 20 meeting hits with snippets. If it exceeds 16,000 characters, the lowest-ranked meetings and then trailing rows are dropped and the prompt says so.
- Claude receives only: question, route, resolved entities, that evidence, and the rules. No tools, no database.

## 3. Synthesis rules (in the prompt)

Answer from evidence only; no new calculations; deterministic citations `[investments]`, `[performance]`, `[meeting: <id>, <YYYY-MM-DD>]`; RM and ClientStatus are record-level; NULL `action_items` is "not recorded"; meetings carry no RM and no deal link; state what is unavailable; hybrid facts in separate paragraphs; concise.

When there is nothing to summarize the answer is built in code, without Claude: a client with no data in the source, an entity with no meetings, or a retrieval with no text match (worded as "this does not mean the topic was not discussed").

## 4. Validation (no LLM judge)

Checks: answer not empty; required evidence present for the route and successful; `[investments]`/`[performance]` cite a source actually used; every `[meeting: id, date]` exists in the returned hits with that date; dates come from the evidence; numbers come from the evidence (rounding, scale words and percent tolerated; small integers and years are warnings only); the answer does not say a value is unavailable while using none of the values provided; NULL `action_items` is not turned into "no action items"; RM and status are not presented as client-wide or single-valued. Output: `valid`, `invalid`, `warnings`, `reasons`, per-check list. An invalid answer is **withheld** (`status=error`, `validation_failed`); the rejected text is kept in `failure.details.rejected_answer` for diagnosis.

## 5. Failure behavior

| Situation | Status | Claude synthesis |
|---|---|---|
| Ambiguous or unknown required entity | `clarification` (`ambiguous_entity`, `entity_not_found`), at most 10 candidates | no |
| Ambiguous route | `clarification` (route ambiguity code) | no |
| Unsupported assumption (e.g. RM-to-meeting), unavailable metric | `unsupported` | no |
| SQL rejected, execution error or timeout, LLM SQL failure | `error` (structured code) | no |
| Hybrid, one side failed | `error` `hybrid_partial_failure`; `partial_evidence` holds the good side; nothing is invented for the failed side | no |
| Explicit "no data for this entity", or empty retrieval | `ok`, `qualified=true` | no |
| Claude synthesis failure | `error` (`synthesis`) | it failed |
| Validation failure | `error` `validation_failed` | already done |
| Unexpected exception | `error` `internal_error` | n/a |

`BaselineResponse` carries: question, route, resolution, routing, status, answer, message, structured evidence, meeting evidence, validation, timing (`resolution_ms`, `routing_ms`, `structured_ms`, `meeting_ms`, `synthesis_ms`, `validation_ms`, `total_ms`), failure, clarification, qualified, partial evidence, notes.

## 6. Why no retries, agents or embeddings

- **No retries**: a repeated call hides the failure rate that the benchmark is meant to measure, and each Claude call costs 6-9 s. Failures are reported once.
- **No agents or planner**: the flow is fixed and deterministic. No demonstrated baseline limitation yet justifies agentic behavior.
- **No embeddings**: gated by the Embedding Decision Gate in [`meeting_retrieval.md`](meeting_retrieval.md).

## 7. Limitations (observed or by design)

- The validator is a set of pattern checks. It catches obvious violations, not every wrong claim, and it can reject correct answers. Observed: a meeting answer that said "17 other meetings had no match" (23 in scope minus 6 matched) was withheld because 17 is a derived number, not in the evidence.
- A number that appears in the evidence in another context still passes numeric grounding.
- Correctness of the generated SQL is not guaranteed; the validator checks the answer against the SQL's result, not the SQL against the question. For example "Total MOIC" returned the CI, HF and RE total-MOIC columns, and the answer said so.
- Snippets are about 30 tokens, so claims about a meeting rest on excerpts.
- Lexical retrieval fails on paraphrases; hybrid questions asking for a set intersection are not joined.
- One question per call; no conversation memory.
- Latency is about 10-17 s for structured or hybrid answers (two Claude calls) and about 8 s for meeting answers (one call); clarification and unsupported responses take milliseconds.
