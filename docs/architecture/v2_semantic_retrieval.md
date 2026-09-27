# V2: Semantic Meeting Retrieval (Gemini Embedding 2 + lexical, fused by RRF)

Code: `src/v2/` (`vertex.py`, `embedding_index.py`, `semantic.py`, `retrieval.py`, `synthesis.py`, `service.py`, `build_embeddings.py`). Tests: `tests/v2/` (37, no network, no Claude). Baseline (`baseline-v0.1`) and V1 are unchanged; V2 wraps them. The 40-question benchmark has not been run on V2 and the holdout has not been read.

## 1. Why V2 exists

The Baseline measurement (`docs/evaluation/baseline_results.md`) and the V1 measurement (`v1_results.md`) show one failure that lexical retrieval cannot remove: **MEET-08** (and the meeting side of HYB-09). The question ("no full-time finance chief ... outside providers ... cash management") and the evidence sentence ("the sponsor currently lacks a dedicated CFO and is outsourcing treasury functions") share **no content word**. In V1 the three expected meetings were in the returned list only because the client's scope (18 meetings) was smaller than K (20); the words that matched (`cash`, `management`, `time`) were generic, and the snippets did not show the sentence, so the answer said the concern was not shown. V1 cleaned the query terms and the failure remained, which isolates it as vocabulary distance. V2 adds a semantic list to the same retrieval so that such paraphrases can be found by meaning.

## 2. Architecture

```text
question -> entity resolution -> routing -> V1 intent classifier            (all frozen / V1, unchanged)
   topical meeting question only:
     scope = meetings passing client / group / exact-date filters            (read back from the lexical evidence's own filters)
        |- lexical  : frozen FTS5/BM25 retriever, top 50 candidates          -> list L (lexical_rank)
        |- semantic : ONE query embedding (Gemini Embedding 2, query format)
        |             cosine vs the sentence vectors of the meetings in scope
        |             meeting score = its best sentence                       -> list S (semantic_rank, depth 50)
        `- RRF      : score(m) = 1/(60 + rank_L) + 1/(60 + rank_S)  (absent list = 0)
     order: rrf desc, lexical rank asc, meeting date desc, meeting_id asc  -> top K = 20
   listing / aggregate / date relations / structured / clarification         (V1 paths: no embedding call)
```

`V2Service` subclasses `V1Service` and replaces only the retriever and the synthesizer. V1 already sends a listing to the retriever with an empty query text (V2 delegates that unchanged) and never sends aggregates, refused date relations or structured questions to it. Entity gates in the lexical retriever (ambiguous or unknown entity, RM, no meetings) return before any embedding call.

## 3. Embedding model and interface

- **Choice:** Google Vertex AI `gemini-embedding-2`, project `investcorp-assignment`, location `global`, `outputDimensionality = 128` (kept unless a quality problem is shown by a focused test; none was run, see section 9). Cloud embedding was chosen after a feasibility study; it needs no local model, no new Python package and no GPU on the old laptop.
- **Endpoint:** `POST https://aiplatform.googleapis.com/v1/projects/investcorp-assignment/locations/global/publishers/google/models/gemini-embedding-2:embedContent`, body `{"content": {"parts": [{"text": ...}]}, "outputDimensionality": 128}`. There is no `taskType`: the task is part of the text.
- **Formats (per the Gemini API embeddings documentation):** document `title: none | text: {sentence}`; query `task: search result | query: {text}`.
- **One text per request.** `:predict` with several instances returned HTTP 404 for this model (probe, 1 call), and no server-side batching is documented in the sources this project could read, so requests are single and concurrent.
- **Responses** are strictly validated: JSON, `embedding.values` list, exactly 128 finite numbers; anything else is a `malformed` error. Returned vectors are unit-length.
- **Authentication:** an OAuth access token from `gcloud auth print-access-token` (the logged-in gcloud user), cached in memory for 40 minutes, never printed or written to disk. Application Default Credentials are not used.
- **Dependencies:** none new. HTTP is `urllib` (standard library); numpy was already installed. `requirements.txt` was not changed.
- **Failures:** every failure is an explicit `VertexError` (`auth`, `http` with the status, `network`, `malformed`). **There are no automatic retries**: a transient-failure retry policy could not be verified from documentation available to this project. Recovery is manual and safe: the build is resumable.

## 4. Sentence-level indexing

- The 20,000 summaries hold 193,890 sentence occurrences but only **26,407 distinct sentences** (7.3x fewer texts; about 1M instead of about 7M tokens). Each distinct sentence is embedded once, exactly as stored (raw text, mojibake included, never repaired), with only the document prefix added.
- Splitting: `re.split((?<=[.!?])\s+)` on `meetings.summary`, strip, drop empty; exact-text de-duplication. `meeting_sentences(meeting_id, pos, sentence_id)` keeps every meeting-to-sentence relationship.
- A meeting's semantic score is the best cosine among its sentences; ties between sentences take the lowest position. The best sentence becomes evidence.
- Repeated template sentences (100 sentences occur in 100 or more meetings) produce **exact ties** between meetings. Semantic ranks use competition ranking (equal scores share a rank), and the final order breaks ties deterministically (section 5).

## 5. Fusion

`rrf(m) = sum over the two lists of 1 / (60 + rank)`; a meeting missing from a list adds 0. Both candidate lists have depth 50 (the frozen lexical retriever's maximum, applied to the semantic list for symmetry). Final order: (1) RRF score descending, (2) lexical rank ascending (absent last), (3) meeting date descending, (4) meeting_id ascending. Final K = 20 (unchanged). Lexical retrieval is not replaced: the frozen retriever supplies list L, the filters and the scope check (the semantic scope must equal the lexical `filtered_meeting_count`, otherwise the request fails).

Evidence per hit (`V2Hit`, an extension of the frozen `MeetingHit`): the V1 fields plus `lexical_rank`, `semantic_rank`, `semantic_score`, `semantic_sentence`, `rrf_score`, `lexical_score`. The response also carries `semantic` (status, model, dimensions, query text, scope size, candidate counts, embed/search milliseconds) and `fusion` (formula, k, depths, tie-break). The best semantic sentence is appended to the hit's text (`semantic match (0.812): "..."`) so the synthesizer sees it and the frozen validator can ground numbers and dates in it. Metadata (sector, region, and so on) is still shown only when the question names it (V1 rule). The full sentence corpus is never exposed.

**Query text.** The text embedded is the V1 topical query (the terms and quoted phrases left after entity, date, request-word and field-word removal), so entity identifiers are never the semantic content and the entity filters stay separate. **Negation words are preserved** (`semantic_query_text`): V1's lexical terms drop the stop words "no" and "not" and split contractions ("doesn't" became "doesn"), which could reverse a query's meaning, so the semantic text keeps every negation word of the question in its original position (no, not, never, without, nor, neither, cannot, none, nobody, nothing, nowhere, and any word ending in n't, kept whole). Other stop words are still dropped. The lexical query text passed to the frozen retriever is unchanged, and with no negation the semantic text equals the earlier behavior (the V1 terms, then the phrases). Example: "...has no full-time finance chief" is embedded as `no full time finance chief` (before: `full time finance chief`).

**If the embedding call fails** the request degrades explicitly to the lexical result: `semantic.status = "unavailable"` with the error kind and HTTP status, a note in the evidence, `rank_method = "bm25"`. It never fails silently.

## 6. Storage and index lifecycle

`data/derived/v2_embeddings/` (gitignored with `data/derived/`):

| File | Content |
|---|---|
| `index.sqlite` | `sentences(sentence_id, text)`, `meeting_sentences(meeting_id, pos, sentence_id)` |
| `vectors.f32` | float32, shape (26,407, 128), row i = sentence id i (13.5 MB) |
| `manifest.json` | model, project/location, dimensions, document/query formats, counts, source fingerprint (SHA-256 over the meetings' id and summary), checksums of both files, generation time, build statistics, versions (Python, numpy, gcloud) |
| `progress/` | resumable 400-sentence chunks and a config file while a build runs; removed on success |

Build: `python -m src.v2.build_embeddings [--workers 16] [--restart] [--test-batch N] [--dry-run]`. It de-duplicates before any API call, persists each finished chunk atomically, stops at the first failed request without publishing an index, and resumes from the finished chunks when re-run. Embeddings are never mixed: a different model, dimension, format, source fingerprint or sentence count is refused (`--restart` is the only way to discard progress or an index). Loading validates the manifest, checksums, vector finiteness and shape, sentence count, and optionally the source fingerprint and configuration; a partial (limited) build cannot be used for retrieval. No query vector is stored anywhere; the retriever keeps none.

## 7. Cost and quota

- Volume: one request per distinct sentence, about 27,000 requests in total for this build (26,407 needed, plus one 400-sentence chunk re-done after a quota stop, some cancelled in-flight requests, and about 120 exploratory requests). Roughly 40 tokens per request including the prefix, so about 1M tokens. At the Gemini API list price of $0.20 per 1M tokens (standard; the Vertex price was not verified) the build costs about $0.20.
- Runtime: one embedding request per topical meeting question, about 0.8-0.9 s (the first call of a process adds about 4 s to obtain the gcloud token) and 5-30 ms for the scan. The Claude synthesis call dominates a question's latency.
- **Quota:** the first build run made about 19 requests/s and was stopped by HTTP 429 `RESOURCE_EXHAUSTED` after about 21,600 sentences (about 18 minutes). It failed explicitly, kept its progress, and finished when re-run at 8 workers. The manifest records only the final run's request count (4,807).

## 8. Runtime dependency and reproducibility

Retrieval needs a network call to Vertex (one per topical question), a logged-in `gcloud`, and the enabled `aiplatform.googleapis.com` API with billing. The corpus vectors are persisted, so a re-run of the benchmark does not re-embed documents, but query embedding still needs the service. Reproducibility rests on the recorded model, dimension, formats and checksums; embeddings from a future model version would differ, so the manifest must be checked and a rebuild is the remedy. The index is rebuildable from the SQLite database.

## 9. Manual validation (fresh questions, retrieval only, real index; expected evidence by SQL substring match)

| Question type | Client, expected | Lexical only | V2 fused | Notes |
|---|---|---|---|---|
| Paraphrase 1 (regulatory licensing sentence) | D12454, 4 meetings | expected positions 10, 1, 4, 8 (2 of top 4) | 11, 1, 9, 10 (1 of top 4) | The closest sentence by meaning was a different template ("Compliance checks flagged potential tax structuring ...", score 0.772) shared by many meetings; the target sentence scored lower |
| Paraphrase 2 (forecast reconciliation) | B12475, 5 meetings | 11, 19, 20, 9, 5 (1 of top 5) | 4, 9, 11, 3, 1 (3 of top 5) | All five share one sentence, so all have semantic rank 1; their fused order comes from lexical rank (the two with lexical ranks 19 and 20 stay at positions 9 and 11) |
| Lexical control 1 (exact wording) | B12529, 4 meetings | ranks 1-4 | ranks 1-4, identical order | All four at lexical and semantic rank 1-4 |
| Lexical control 2 (exact figure "231 bps") | B12480, 1 meeting | rank 1 | rank 1 | Semantic rank 2, lexical rank 1 |
| Entity-filter stress (same query as Paraphrase 1) | C12572, 2 meetings; the same sentence exists in 5,610 meetings database-wide | positions 1, 2 | positions 1, 8 | **0 out-of-scope hits** in all five questions; scope size 18 |

Observations, not tuning: scope isolation and the lexical controls behave as specified. The paraphrase results are mixed: fusion improved paraphrase 2 and moved expected meetings down for paraphrase 1 and the stress question, where a tied group of seven meetings on a semantically similar but different template sentence outranked one expected meeting. Whether the 128-dimension output, the bag-of-terms query text or the RRF weighting is responsible was not tested; the dimension was not compared with a larger one.

## 10. Known limitations and what V2 does not solve

- **Unsupported date relations** (`since`, `after`, `before`, `until`, `prior to`, `between`) stay unsupported and refused before retrieval; V2 does not touch them, so HYB-09 remains refused.
- **Cross-source intersections** and cross-source date dependencies (HYB-06, HYB-09) are not solved; the structured and meeting sides stay independent.
- **Validation false positives and negatives** are not addressed: the frozen validator and the V1 wrapper are unchanged (HYB-10 and HYB-05 withholdings and the HYB-06 pattern remain).
- **Encoding repair** is not done: mojibake is embedded and returned as stored, so text such as `LÃ³pez` is not matched to `López` by design (MEET-06 is unaffected by construction).
- Query embedding drops stop words other than negation words; the semantic list contains ties from template sentences, so its ordering inside a tie comes from the fusion tie-break, not from meaning.
- Semantic search covers topical questions only; aggregates and listings are unchanged. Sentences are English text embedded at 128 dimensions.
- Lexical and semantic candidate depth is 50; a meeting outside both lists is never returned.
- Cloud dependency: availability, quota and model-version changes of the Vertex service.

## 11. Assumptions introduced

1. Semantic query text is the V1 topical query plus the question's negation words in place; document format `title: none | text: ...`, query format `task: search result | query: ...`.
2. Competition ranking for semantic ties; candidate depth 50 for both lists; RRF k = 60.
3. An embedding failure degrades to the lexical result with an explicit status; it is not an error and not silent.
4. No automatic retries; a failed build is resumed by re-running it.
5. Index and manifest are derived data under the ignored `data/derived/`.
