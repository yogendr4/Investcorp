# Baseline Meeting Retrieval (lexical, FTS5 / BM25)

Implementation: `src/baseline/meeting_retrieval.py` (retriever), `src/baseline/meeting_index.py` (index). Tests: `tests/baseline/test_meeting_retrieval.py`. This is component C3b of [`baseline_contract.md`](baseline_contract.md) (section 8). It is deterministic: no embeddings, no LLM, no reranking, no fuzzy matching, no stemming. It returns evidence records; it does not answer the question.

## 1. Flow

```text
question + entity resolution (+ optional explicit query text / filters)
  1. entity gate       ambiguous or unknown entity, or an RM: explicit result, no search
  2. filters           client_id / group_id / ISO date on the meetings table (deterministic, before any text search)
  3. query terms       question words minus entity text, dates, stopwords and intent words
  4. FTS5 MATCH + BM25 over the filtered meetings, deterministic ordering, top-K
  5. MeetingEvidence   bounded hits with ids, dates, snippets, ranking metadata and counts
```

## 2. FTS5 index (`meetings_fts`)

- An **external-content** FTS5 table over `meetings` (`content='meetings'`, `content_rowid='source_row'`). Nothing is copied or altered; `meetings` and the other analytical tables are byte-for-byte unchanged (a fingerprint of the three tables was identical before and after adding the index).
- **Persistent** in `data/derived/baseline.sqlite`, built by `python -m src.baseline.build_db` (one extra call, `create_meeting_index`, and FTS validation checks). It is derived and rebuildable: `rebuild_meeting_index(db_path)` drops and recreates it from `meetings` only. Dependency: the retriever needs a database built with the index; otherwise it returns `index_missing`. The index adds about 15 MB and about 3 seconds to a build.
- **Tokenizer** `unicode61 remove_diacritics 0`: case-insensitive, splits on punctuation, no stemming, and no diacritic folding. So `López` and the stored mojibake `LÃ³pez` are different tokens (section 8).
- The build validates the index against an independent Python token count (`cfo` matches the same number of meetings both ways) and runs FTS5's own integrity check.

## 3. Searchable fields and weights

| Field | Indexed | BM25 weight | Why |
|---|---|---|---|
| `summary` | yes | 1.0 | the meeting text |
| `attendees` | yes | 1.0 | names are searched as text |
| `action_items` | yes | 1.0, or 5.0 when the question asks about action items | short, templated; only recorded values exist |
| `company` | yes | 0.1 | present in every summary, so weak evidence |
| `sector`, `region`, `investment_stage` | **no** | | repetitive metadata, named in every summary; they are structured filters, not evidence |
| `deal_size_estimate` | **no** | | raw text kept as a field; not searched, not parsed here |

Words that describe the request rather than the meeting (`what`, `meetings`, `client`, `mention`, `discussed`, `raised`, `came`, `latest`, ...) are removed from the query. Quoted text in a question becomes a phrase. An ISO date in the question becomes a `meeting_date` filter. A request for "action items" is a request for the field, so the words `action items` are not searched and the `action_items` weight is raised.

## 4. Entity-first filtering

| Entity | Behavior |
|---|---|
| Resolved client | `meetings.client_id = X`. Works for meeting-only clients. |
| Resolved group | `meetings.group_id = N`. **Membership is the meetings source's own.** Nothing is imported from investments or performance, and the result states this (`group_source: meetings.group_id`, the number of members in the meetings source, and a note when the sources disagree). |
| Ambiguous or unknown client, group, deal or RM mention | `clarification_needed` / `entity_not_found`. No search runs. |
| A resolved RM | `unsupported_rm_to_meeting`. Meetings have no RM field. The retriever never filters or attributes meetings by RM. This preserves the router's unsupported behavior. |
| A resolved deal id or deal name | Used only as search words. Meetings carry no deal field, so no deal filter and no deal relationship is created. The result notes this. |
| Ambiguous words that are only meeting text (`Rahul`, `Orchid`) | Searched as words, with a note that they do not identify one person or company. A word that is also a real entity (a deal name) still stops for clarification. |
| Meeting `company` and `deal_size_estimate` | Never used to identify an investment entity. |

There is no synthetic client or group mapping. With an entity filter, only that filter's meetings are candidates, so repetitive company, sector or region text cannot pull in other clients' meetings.

## 5. Ranking and top-K

- Ranking is SQLite `bm25()` with the column weights above, over an `OR` of the query terms. `score` in the result is `-bm25`, so higher is better. Ties are broken by `meeting_date` (newest first), then `meeting_id` (lowest first), so the order is stable across runs.
- If the question has no search terms but has a filter (for example "show the meetings for client X"), the filtered meetings are listed newest first, marked `rank_method: date_desc`, with no score. With no terms and no filter nothing is returned (`no_terms`).
- **Default K = 20, maximum 50.** The largest evidence set among the visible questions is 14 meetings, and a client has 12-23 meetings, so 20 reaches every meeting of a typical client without an unbounded list. A larger K is refused (`ValueError`).
- The result always reports `filtered_meeting_count` (meetings passing the filters), `matched_count` (of those, meetings with a lexical match), `fewer_than_k`, and a note when more than K matched. Each hit carries a snippet of about 30 tokens (matches marked `[[like this]]`), matched terms and fields, and `action_items` in full, so the evidence stays a few kilobytes.

## 6. Empty and weak results

Explicit outcomes: `ok`, `no_lexical_match` (meetings pass the filters but none contains the terms: the result says this does not mean the topic was not discussed), `no_meetings_for_filter`, `no_terms`, `clarification_needed`, `entity_not_found`, `no_meeting_data_for_entity`, `unsupported_rm_to_meeting`, `index_missing`. A retrieval with no hits is never presented as "no meetings exist".

## 7. Action items

`action_items IS NULL` is returned as `action_items_state: not_recorded` with `action_items: null`. A non-NULL value is `recorded` with its text. NULL is never turned into "no action item" (A8).

## 8. Encoding policy and its consequence

Text is indexed, matched and returned exactly as stored, mojibake included. No repair, and clean and mojibake spellings are not normalized to one value (assumption A11). Consequences, intentionally:

- `Sanjay López` (clean) matches nothing, because the data holds `Sanjay LÃ³pez`. Searching for the stored spelling finds it.
- A query containing `±` or an accented name in clean form will miss the raw text. Numbers next to a broken `Â±` still match as numbers.
- This is the known encoding limitation the repair test (`docs/encoding_impact_test_design.md`) is meant to measure.

## 9. Known limitations

- **Lexical only.** A paraphrase does not match. In the manual check, "the deal fell through and they walked away" (wording that never occurs) retrieved a meeting whose text says "We walked through the due diligence findings", and the meeting that says "elected to decline the opportunity" appeared in the top 20 only because it also happens to contain "walked through". The match came from unrelated words, not from meaning.
- **Templated corpus.** Summaries are built from a pool of repeated sentences, so many terms occur in a large share of meetings. BM25 inverse document frequency is then close to zero (scores such as `4e-06`), and the ranking among such matches rests mostly on column weights and length. Rare terms (such as `CFO`) rank clearly.
- **OR of terms.** Extra generic words in a question (for example `position`) add noise. There is no stemming, so `raised` does not match `raise`.
- **Filters are limited to** client, group and an exact ISO date, plus explicit date-range parameters. Sector, region, stage and company filters, and year or range phrases in a question, are not parsed.
- **Meetings have no RM link and no deal field.** RM-to-meeting questions are unsupported; deal names in text are text only.
- **Group membership** comes from the meetings source only (metadata question 4 stays open).
- **Term detection depends on the resolver:** an ambiguous mention that involves a real entity type stops the search even when the user meant the word.
- **Latency:** about 140-220 ms per query on the full index.

## 10. Embedding Decision Gate

Semantic embeddings are **not** part of Baseline and should be introduced only if evaluation shows meaningful lexical retrieval failures, especially on paraphrased meeting questions. The gate:

1. Run the same benchmark, with the same scoring, on this lexical retriever and record the results by failure mode.
2. The failures must be retrieval failures: the known evidence meeting is missing from, or ranked far down, the top-K, and this has been checked meeting by meeting. Answer-wording and routing failures do not count.
3. The failures must concentrate on paraphrased or semantic-stress questions, while questions that reuse the meeting text's own words succeed. That shows the pipeline works and the gap is vocabulary.
4. Lexical improvements inside Baseline have been tried and recorded (query terms, field weights, K, phrase handling), and the gap remains.
5. Encoding has been ruled out as the cause, using the encoding impact test.
6. A paired comparison on the same questions (Recall@K, MRR and Evidence Hit Rate, as in the encoding test design) then shows a measurable gain from embeddings, without loss on the lexical questions, and the holdout confirms it.

No numerical improvement threshold is set here; that is for you to define.

## 11. Assumptions introduced by this slice

1. `meetings_fts` lives in `baseline.sqlite` and is created by the build (a minimal pipeline change); analytical tables are unchanged.
2. `company` is indexed at weight 0.1; sector, region, stage and deal size are not indexed.
3. Tokenizer without diacritic folding or stemming.
4. Default K is 20 and the maximum is 50.
5. `OR` of terms with BM25; the `action_items` weight is 5.0 for action-item questions.
6. Intent words are removed from queries (a fixed list in code).
7. An ISO date in the question is a `meeting_date` filter.
8. Ambiguous company or attendee words are searched as text; ambiguity involving a real entity type stops the search.
9. A retrieval with no lexical match is a valid, explicit result, not an error.
