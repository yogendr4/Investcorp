# Baseline Entity Resolution

Implementation: [`src/baseline/entity_resolution.py`](../../src/baseline/entity_resolution.py). Tests: `tests/baseline/test_entity_resolution.py`. This is component C1 of [`baseline_contract.md`](baseline_contract.md) (section 5). It is deterministic, has no LLM, and reads the Baseline SQLite database read-only.

## 1. Principle

**Exact deterministic resolution is the Baseline.** An entity is resolved only by:

1. an exact canonical identifier,
2. an exact known name, or
3. an exact match after safe normalization (case, whitespace, punctuation).

**Partial, truncated or misspelled text is never used to resolve an entity.** It may be detected and reported as a diagnostic (an ambiguity with candidates), or it is `not_found`. No code path turns a partial match into a resolved entity, and a property test checks this over thousands of truncations, prefixes, suffixes, deletions and swaps of every known name and id.

## 2. Result

Every call returns a `Resolution` with one of three statuses:

| Status | Meaning |
|---|---|
| `resolved` | Exactly one entity matched exactly (or exactly after normalization). `canonical` holds it. |
| `ambiguous` | More than one candidate remains, or only a partial match exists. `canonical` is empty. Nothing is chosen. |
| `not_found` | No entity matched. Nothing is substituted. |

Fields: `entity_type`, `input_text`, `resolution_status`, `canonical`, `candidates` (at most 10, see 6), `match_rule`, `confidence` (`exact`, `normalized`, `none`), `reason` (matching evidence), `rationale` (why this status), `sources` (tables used), `details`. The contract's `UNRESOLVED` (for example an email used as an RM) is reported as `not_found` with the reason.

## 3. Entity types

Core entity types: **client, group, deal_id, deal_name, rm.**

| Type | Canonical value | Built from | Notes |
|---|---|---|---|
| `client` | client id, e.g. `A12345` | `client_id` in investments, performance, meetings | Known name forms: `client_name` (`Client_A12345`) and `account_name` (`Client A12345 Holdings`). `AccountName_org` is undocumented and is not a name form. `details` lists which sources hold the client and flags meeting-only clients. |
| `group` | group id, e.g. `346` | `group_id` in all three tables | Resolves the group identity only. `details` lists members per source and whether they differ. |
| `deal_id` | e.g. `DL100001` | investments | Independently resolvable. Its deal names are returned in `details` (see 5). |
| `deal_name` | e.g. `Orion Infrastructure I` | investments | Independent of `deal_id` (A5). `details.deal_ids` lists its ids. |
| `rm` | e.g. `Priya Sharma` | investments `account_rm` (`AccountRM`) only | Record-level; never implies one RM per client. |

Meeting companies and attendee names are **not** entity types. They are used only as diagnostic contexts so that a word such as `Summit` or `Rahul` can be reported as ambiguous. An exact company or attendee name is not resolvable: a typed request for one raises `ValueError`, and an untyped lookup returns `not_found` with `details.diagnostic_contexts`. `resolve_term` is for a mention whose type is unknown; a result that has no single domain type carries `entity_type = "untyped"`, which is a label and not an entity type.

## 4. Resolution stages

For a typed mention, in this order; the first stage that finds a match decides:

1. **Exact identifier.** The input, as given, equals a client id or deal id.
2. **Exact name or known name form.** The input, as given, equals a known name.
3. **Normalized match.** Equal after the normalization below. `confidence` is `normalized` and the rule is reported, so a trimmed or re-cased input is never presented as an exact match.
4. **Numeric suffix (clients).** A bare 5-digit number such as `12345` is not an identifier. Clients whose id ends in it are listed as candidates. Always `ambiguous`.
5. **Partial name (diagnostic).** The input's words appear, as consecutive whole words, inside known names. They are listed as candidates with rule `partial_name_diagnostic` and `confidence` `none`. Always `ambiguous`, even with one candidate. Never resolved.
6. **Otherwise** `not_found`.

**Normalization** (safe): Unicode NFC, case folding, trimming, collapsing whitespace, treating `_` and punctuation as word separators. **Not applied:** spelling correction, abbreviation, stemming, Roman/Arabic numeral conversion (`I` is not `1`), prefix or suffix completion, accent stripping, encoding repair (assumption A11). Two different values with the same normalized key would be reported as ambiguous, not merged.

**In a question** (`resolve_question`), mentions are found by whole-word exact matching of known names and identifiers (longest match first, no overlaps), plus: identifier-shaped words that are not in the data (`E12345`, `DL999999`, reported `not_found`), `group 346`, a bare 5-digit number that matches some client, an email (reported as an RM `not_found`, rule `non_authoritative_field`), and capitalized words that partly match a name (diagnostic ambiguity only). Lowercase words are never treated as names. An exact company or attendee name in a question is plain text and produces no mention.

## 5. Ambiguity rules and examples

| Input | Result |
|---|---|
| Bare `12345` | `ambiguous`: `A12345`, `B12345`, `C12345`, `D12345`. |
| `DL100001` | **`resolved`**, `entity_type = deal_id`, `canonical = DL100001`. `details.deal_names` holds both names (`NorthBridge Growth Fund` 8,356 records, `Orion Infrastructure IV` 594) with `maps_to_multiple_names = true`. `candidates` is empty. The names are never collapsed into one, and a deal name is never the canonical value of a deal id. |
| `DL100000` | `resolved`, one deal name in `details`. |
| `Orion Infrastructure I` | `resolved` to itself, never to `Orion Infrastructure IV`. Matching is exact, not by prefix. |
| `Orion Infrastructure` | `ambiguous` diagnostic: `I` and `IV`. |
| `Emerald` (one partial match) | `ambiguous` diagnostic with one candidate. Never `resolved`. |
| Typo (`Orion Infrastructur`, `Carlos Gomes`) | `not_found`. |
| `Summit` | `ambiguous`, `entity_type = untyped`. Candidates: deal name `Summit Credit Opportunities` and meeting company `Summit Advisors` (context `meeting_company`, no entity type). `details.candidate_contexts` counts candidates per context. |
| `Rahul` | `ambiguous`, untyped. 1 RM candidate plus 49 attendee names. |
| `Rahul Mehta` | `resolved` as an RM. `details.also_known_in_context` records that the same text is also an attendee name. |
| A meeting-only client id (`B12463`) | `resolved`; `details.meeting_only` is true. |
| An id absent everywhere (`E12345`) | `not_found`. Similar ids are not offered. |

## 6. Candidate cap

Public `candidates` is capped at **10** (`MAX_PUBLIC_CANDIDATES`). Ordering is deterministic: candidates with a core entity type first, then diagnostic contexts, each sorted by type, context and value. So the RM candidate is always shown for `Rahul`. `details` always reports `candidate_count`, `candidates_truncated`, `max_public_candidates` and `candidate_contexts` (counts per context), so a capped list still shows which contexts compete. The full list is kept internally in `Resolution.all_candidates` and is not part of `to_dict()`.

## 7. Explicit failure behavior

- A consumer must treat `ambiguous` and `not_found` as stops: no query or retrieval runs on that mention (contract section 5). The resolver never chooses a candidate.
- It never returns a canonical value for a partial match, typo, truncation or numeric-only input.
- A meeting-only client is resolved. That it has no investment or performance data is stated in `details`, not turned into "not found".
- RMs resolve only by `AccountRM` name. An email or alias is `not_found` with the reason (A3).
- Groups resolve by identity. Members per source are listed, with a flag when sources disagree; no membership definition is chosen (metadata open question 4).
- Missing database: `FileNotFoundError` with the build command. Unsupported entity type: `ValueError`.

## 8. Why fuzzy and partial matching are excluded from resolution

- **Identity, not similarity.** In this data, near-matches are usually different entities: `Orion Infrastructure I` and `IV`, `BluePeak Venture II` and `III`, `A12345` and `B12345`. A similarity or prefix rule would pick the wrong one with confidence, and every downstream number would be wrong.
- **Safe failure is measurable.** An explicit `ambiguous` or `not_found` can be evaluated and improved. A silent wrong guess cannot.
- **Contract and metadata.** Do not silently guess semantics, do not infer aliases, do not use `LIKE`. Embeddings are out of Baseline scope and are justified later only by evidence (contract section 19).
- **Small closed vocabulary.** 1,000 clients, 8 deal ids, 9 deal names and 5 RMs: exact lookup is enough.

## 9. Known limitations (later evaluation may justify changing)

- **Mojibake in attendee names.** Meeting text is used as stored (A11), so `Sanjay López` is stored as `Sanjay LÃ³pez`. Attendees are not entities, so an untyped lookup of either spelling returns `not_found` (with a note about mojibake for the clean spelling). In a question such a name produces no mention. Matching it is left to meeting retrieval.
- **No aliases.** A first name alone for an RM is only a partial match. Initials, nicknames and `Client A12345 Org` are not recognized.
- **Capitalization-dependent detection.** In a question, a partial name is only noticed when capitalized. `summit` in lowercase is ignored, and a multi-word capitalized phrase that matches nothing contiguous is ignored.
- **An RM name that is also an attendee name** (`Rahul Mehta`) resolves as the RM. The attendee context is only noted in `details`. Routing must not treat that as proof that the question is about the RM.
- **Client name forms exist only where the data has them.** Meeting-only clients have no name forms, and none are invented.
- **Only 5-digit numbers** are treated as bare client numbers, and a group needs the word `group` (or a typed call). A bare number typed as an untyped term is looked up as a group id.
- **Dictionaries are loaded once** when the resolver is created. Rebuild the database, then create a new resolver.
- **Which mentions need a stop** is a routing decision. For example, a deal id with two names is `resolved`, and the router decides whether a question needs one name.

## 10. Assumptions introduced by this slice

1. Input that needed normalization, even trimming, is reported as `normalized_match`, not exact.
2. A single partial match is `ambiguous` (one candidate), not `resolved` and not `not_found`.
3. Partial matching uses whole consecutive words and needs at least one word of 3 letters or more.
4. In a question, only capitalized partial names are noticed.
5. An email in a question is reported as an RM `not_found` mention.
6. Unknown identifier-shaped words (one letter plus 5 digits, `DL` plus digits) are reported as `not_found`.
7. `untyped` labels a mention with no single domain entity type.
