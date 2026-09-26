# Data Dictionary — DRAFT (Baseline 0.1 source of truth)

Status: DRAFT, revised after review. Not yet approved as final. Source: `data/Assignment_Data.xlsx`, profiled 2026-09-26. No data was modified.

## 0. Governing principle and how to read this document

> **Never silently guess semantics. Either use a documented field definition, make an explicit assumption, or surface the ambiguity.**

Every statement here is one of three kinds:

| Tag | Meaning |
|---|---|
| **FACT** | Observed directly in the workbook by a reproducible profiling check. |
| **INFERENCE** | Our reading of what something probably means. Not confirmed by any source document. |
| **ASSUMPTION** | A decision taken by Yogendra (Section 1). Nothing else counts as an assumption. |

Column headers in the field tables carry the tag for their column. Confidence (HIGH / MEDIUM / LOW) applies to the *proposed meaning* only. Where a meaning is not documented, the table says "undocumented" and does not fill the gap.

## 1. Decided assumptions (ASSUMPTION)

| # | Assumption | Observed data behaviour (FACT) |
|---|---|---|
| A1 | `AccountRM` is the authoritative RM field. | Identical to `AccountRM_org` in all 50,000 rows. |
| A2 | RM association is **record/relationship-level**. Do not infer one managing RM per client. | All 192 clients have records under all 5 RMs (about 47-59 rows per RM for client A12345). No RM field exists in the performance or meeting sheets. |
| A3 | `AccountRMEmail`, `AccountRMEmail_org`, `RM_Alias`, `AccountOwnerId` are non-authoritative/noisy and are ignored for chatbot logic. | Each RM name pairs with all 5 emails and all 5 aliases in near-equal counts, so these fields contradict `AccountRM`. `AccountOwnerId` has 8,961 distinct values. |
| A4 | `ClientStatus` is **relationship/investment-record-level**. It is not a single client status and is never collapsed into one client-wide status. Performance `Investment_Status_Name` is a separate field and source and is never substituted for `ClientStatus`. If both are presented, each is clearly labelled separately (Section 8). | All 192 clients carry all 4 statuses (Active, Dormant, Prospect, Closed) across their records. Performance `Investment_Status_Name` has no `Closed` value. |
| A5 | `deal_id` and `deal_name` are independent identifiers. Do not assume a strict 1:1 mapping. | 8 `deal_id` vs 9 `deal_name`. `DL100001` maps to 2 names. |
| A6 | Current/latest performance = the latest `As_Of_Date` **per client** (Section 5). | Latest dates per client range 2017-07-30..2025-01-22; no ties for latest among the 192. |
| A7 | MOIC and textual monetary fields get deterministic numeric analytical versions (Section 6). Raw values are preserved. | 8 MOIC columns hold text like `2.26x`; `deal_size_estimate` holds text like `15M USD`. |
| A8 | Missing `action_items` (NULL) means "not recorded". It is never read as "no action item" or "no meeting". | 5,008 of 20,000 meetings have NULL `action_items`. |
| A9 | If a requested metric does not exist for the relevant data, answer that it is unavailable. Do not substitute another metric (Section 7). | MOIC and IRR exist only for some product families. |
| A10 | Synthetic-data inconsistencies are documented, never silently corrected (Section 9). | See Section 9. |
| A11 | Meeting-text encoding artifacts are documented, not repaired. Repair is deferred until the retrieval-impact test (`docs/encoding_impact_test_design.md`) has run. | 71.2% of summaries and 2,107 attendee lists contain mojibake. |
| A12 | Hybrid queries aggregate each structured source at its own grain first, then combine with meeting evidence (Section 10). | A row-level three-way join of the three sheets on `client_id` produces 8,534,216 rows from 51,536 + 4,096 source rows (Section 2.3). |
| A13 | For a user question asking "last met" or "most recent meeting", the answer is the latest `date` in `meeting_notes_20k` for that client. `performance.Client_Last_Met_Date` is never silently substituted. It may be shown only as a separately labelled performance field (Section 8). | The two fields disagree: for all 192 shared clients, the latest-snapshot `Client_Last_Met_Date` is earlier than the client's latest meeting date (T6). |
| A14 | Relative or current-date questions ("recently", "last year", "current") use the **runtime current date** as the reference. The answer must not imply the data runs through that date, must not treat the latest source date as "today", and must explicitly say when the requested period extends beyond source coverage (Section 5). | The sources end on different dates: `Investments.dat_MinInvested` 2024-11-05, `performance.As_Of_Date` 2025-01-22, `meeting_notes.date` 2026-03-13 (Section 5, T5). |

## 2. Workbook structure (FACT unless tagged)

### 2.1 Sheets

| Sheet | Rows | Cols | Nulls |
|---|---|---|---|
| `Investments_data_50k` | 50,000 | 24 | none |
| `meeting_notes_20k` | 20,000 | 13 | `action_items`: 5,008 (25.0%) |
| `performance_data` | 1,632 | 65 | `Client_Id`, `Client_Name`: 96 each (5.9%) — exactly the group rows |

### 2.2 Grain

No primary keys are documented for any sheet. None are invented here. What was observed:

| Sheet | Observed structure (FACT) | Inference (INFERENCE) |
|---|---|---|
| Investments | 50,000 rows, no exact duplicate rows. 216-306 rows per client (mean 260.4). 1,472 distinct (`client_id`, `deal_id`) pairs; 1,654 distinct (`client_id`, `deal_name`) pairs. For client A12345, one (`deal_name`, `cod_lob`) combination has 1 to 15 rows. 0 duplicates on (`client_id`, `deal_id`, `dat_MinInvested`, `Investment_Amount_Natural_Currency`), an observation only, not a declared key. | Each row is one investment-related record. What exactly a row represents (a commitment, a call, a transaction) is undocumented. |
| Performance | 1,632 rows = 1,536 client rows (192 clients x 8 rows) + 96 group rows (`IsGroup_Flag=Y`, one per `Client_Group_Id`, `Client_Id` null). (`Client_Id`, `As_Of_Date`) is **not** unique: clients C12351 and C12366 have 7 distinct dates in 8 rows. | Each client row is a dated snapshot. Group rows are group-level snapshots whose relation to member clients is undocumented (0 of 96 groups equal the sum of members' latest `Total_AUM_Amount`). |
| Meetings | 20,000 rows. `meeting_id` is unique in this data. 12-23 meetings per client (mean 20.0). | Each row is one meeting record. |

### 2.3 Join cardinalities

| Join key | Investments | Performance | Meetings | Consequence (FACT) |
|---|---|---|---|---|
| `client_id` | 216-306 rows per client | 8 rows per client | 12-23 rows per client (18-23 for the 192 shared clients, mean 21.3) | A row-level three-way join on `client_id` over the 192 shared clients produces 8,534,216 rows in total (per client: min 33,984, mean 44,449, max 56,304), from 50,000 Investments + 1,536 performance client rows + 4,096 meeting rows. Two-way joins: Investments x Performance 400,000 rows; Investments x Meetings 1,066,777 rows; Performance x Meetings 32,768 rows. Any SUM or COUNT over such a join is multiplied. |
| Group id | 96 groups | 96 groups (1 group row + client rows) | 250 groups | See 2.4. |

### 2.4 Population and group-membership differences (FACT)

| Aspect | Investments / Performance | Meetings |
|---|---|---|
| Clients | 192 (identical set in both sheets) | 1,000. Only the 192 overlap; all 192 have meetings. **808 clients have meetings but no investment or performance rows.** |
| Client ID range | A12345..D12376 | A12345..D12594 |
| Groups | 96 | 250. 96 shared; **154 exist only in meetings**. |
| Clients per group | 64 groups have 1 client (all `A`-prefixed); 32 groups have 4 clients (one each of A, B, C, D, all with the same numeric suffix) | **Every one of the 250 groups has exactly 4 clients** |
| Group id per client | 1 per client | 1 per client; agrees with Investments for all 192 shared clients |
| Arithmetic | — | 808 meeting-only clients = 154 meeting-only groups x 4 (616) + 3 extra clients in each of the 64 groups that Investments shows with 1 client (192) |

So the same `group_id` denotes a 1-client group in one sheet and a 4-client group in the other for 64 groups. Group-level answers must state which sheet the membership comes from.

## 3. Field tables

### 3.1 `Investments_data_50k`

| Column | Observed dtype (FACT) | Examples (FACT) | Cardinality (FACT) | Proposed meaning (INFERENCE) | Conf | Relationships (FACT) | Caveat / anomaly (FACT) | Handling (ASSUMPTION) |
|---|---|---|---|---|---|---|---|---|
| `Client_Id__c` | str | C12355, A12345 | 192 | Client identifier. Origin of the `__c` suffix is undocumented. | MEDIUM | Identical to `client_id` in all rows | Redundant | Use `client_id` |
| `client_id` | str | C12355, A12345 | 192 | Client identifier | HIGH | Joins to `performance_data.Client_Id` (192/192) and `meeting_notes_20k.client_id` (192 of that sheet's 1,000). Prefix A/B/C/D; same numeric suffix within multi-client groups | Prefix meaning undocumented | — |
| `Client Name` | str | Client_C12355 | 192 | Display name of the client | HIGH | Always `"Client_" + client_id` | Redundant | — |
| `Client_Group_Id_c` | int | 356, 346, 421 | 96 | Group identifier. What a group represents is undocumented. | MEDIUM | Each client has exactly 1 group. Matches `performance_data.Client_Group_Id` (96/96, client-to-group agreement 100%) | Group membership differs from meetings (2.4) | — |
| `deal_id` | str | DL100000, DL100013 | 8 | Deal identifier | MEDIUM | `DL100001` maps to 2 `deal_name` values. Not tied to `cod_lob` | 8 ids vs 9 names | A5 |
| `deal_name` | str | Orion Infrastructure I | 9 | Deal name | MEDIUM | Each name maps to one `deal_id`. Each name appears under all 5 `cod_lob` values. Rare names: `Orion Infrastructure IV` (594 rows), `Atlas Private Equity III` (229), `BluePeak Venture II` (757) | Some names are prefixes of others (`…I` / `…IV`, `…II` / `…III`). No overlap with `meeting_notes_20k.company` | A5 |
| `id_CapitalCall` | int | 0, 1 | 2 | Undocumented 0/1 flag. The name suggests a capital-call marker (from the name only). | LOW | 49,027 = 1; 973 = 0 | Meaning of 0/1 undocumented | Not used in baseline logic |
| `Investment_Amount_USD_for_agg` | float/int mix | 12568.75 | 49,947 | Amount in USD intended for aggregation (per column name). Whether it is committed, called or invested is undocumented. | MEDIUM | Equals natural amount x exchange rate (max abs diff 0.004; 0 rows differ by more than 0.01) | Range 127.03..6,249,643.75; total 91.12 bn. Per-client totals 367M-584M | The amount field for aggregation; state its undocumented nature when asked |
| `Investment_Amount_Natural_Currency` | int | 10055 | 49,744 | Amount in the row's original currency | HIGH | See above | Not summable across currencies | — |
| `Natural_Currency_Code` | str | GBP, EUR, USD | 6 | Currency code | HIGH | 1:1 with `Investment_Exchange_Rate` | AED, EUR, GBP, INR, SGD, USD | — |
| `Investment_Exchange_Rate` | float/int | 1.25, 1.08, 1 | 6 | Rate applied to reach USD | HIGH | AED 0.27, EUR 1.08, GBP 1.25, INR 0.012, SGD 0.74, USD 1.00 | One fixed rate per currency across all dates | — |
| `dat_MinInvested` | datetime | 2022-07-16 | 2,501 | A date field. What "Min" refers to is undocumented. | LOW | Range 2018-01-01..2024-11-05 | Meaning undocumented | Report as "the date in `dat_MinInvested`" |
| `cod_lob` | str | COP, RE, PE | 5 | Line-of-business code as labelled | MEDIUM | 1:1 with `nam_lob` | Values COP, HF, INF, PE, RE. Code expansions undocumented | — |
| `nam_lob` | str | Credit, Real Assets | 5 | Line-of-business name as labelled | MEDIUM | COP=Credit, HF=Venture, INF=Secondaries, PE=Private Markets, RE=Real Assets | Shown as-is; code-to-name expansion undocumented | Use as given |
| `flg_Realised` | bool | True, False | 2 | Boolean flag named "realised". Definition undocumented. | LOW | 25,062 True / 24,938 False | Relation to `id_CapitalCall` and to performance "Realised" columns not established | Do not equate with performance "Realised" metrics |
| `ClientStatus` | str | Prospect, Closed, Dormant | 4 | Status of the client relationship as recorded on the row | MEDIUM | Active 12,417 / Dormant 12,577 / Prospect 12,518 / Closed 12,488. Every client has all 4 | A client has no single status | A4: relationship/record level; never substituted by performance `Investment_Status_Name` |
| `AccountName` | str | Client C12355 Holdings | 192 | Name string. What "account" denotes is undocumented. | LOW | 1:1 with client; differs from `AccountName_org` in all 50,000 rows | — | — |
| `AccountName_org` | str | Client C12355 Org | 192 | Undocumented. The `_org` suffix has no documented meaning. | LOW | 1:1 with client | — | Ignored |
| `AccountRM` | str | Carlos Gomez | 5 | RM name associated with the record | MEDIUM | Identical to `AccountRM_org`. Every client appears under all 5 RMs | RM is a row attribute, not a client attribute | **A1, A2: authoritative, record-level** |
| `AccountRM_org` | str | Carlos Gomez | 5 | Undocumented | LOW | Identical to `AccountRM` in 50,000 rows | Redundant | Ignored |
| `AccountOwnerId` | str | OWN2109 | 8,961 | Undocumented identifier | LOW | 215-301 distinct values per client; unrelated to RM | — | A3: non-authoritative, ignored |
| `AccountRMEmail` | str | cgomez@investcorp.com | 5 | Undocumented email field | LOW | Each `AccountRM` value pairs with all 5 emails | Contradicts `AccountRM` | A3: non-authoritative, ignored |
| `AccountRMEmail_org` | str | cgomez@investcorp.com | 5 | Undocumented | LOW | Identical to `AccountRMEmail` | Same contradiction | A3 |
| `RM_Alias` | str | cgomez | 5 | Undocumented alias field | LOW | 1:1 with `AccountRMEmail`; contradicts `AccountRM` | Same contradiction | A3 |

### 3.2 `meeting_notes_20k`

| Column | Observed dtype (FACT) | Examples (FACT) | Cardinality (FACT) | Proposed meaning (INFERENCE) | Conf | Relationships (FACT) | Caveat / anomaly (FACT) | Handling (ASSUMPTION) |
|---|---|---|---|---|---|---|---|---|
| `meeting_id` | int | 1, 2, 3 | 20,000 | Meeting identifier | HIGH | Unique in this data | No documented key statement | — |
| `date` | datetime | 2022-09-01 | 1,533 | Meeting date | HIGH | Range 2022-01-01..2026-03-13 | Ends about 16 months after the last investment date (2024-11-05) and about 13 months after the last performance snapshot (2025-01-22). 5,311 meetings postdate 2025-01-22 | A13: source of "last met" / "most recent meeting" (latest per client). A14: coverage ends 2026-03-13 |
| `attendees` | str | "Zoya Ivanov; Rahul Kaur" | 19,892 | Attendee names, `;`-separated | MEDIUM | 1-5 names per row. Not linked to any client or RM field. RM full names appear as attendees in 12-15 meetings each | 2,107 rows contain mojibake (Section 9, E1) | Raw, not repaired (A11) |
| `company` | str | Orchid Ventures | 15 | Company named in the meeting record. Its role is undocumented. | MEDIUM | Named in the summary of 20,000/20,000 meetings. No exact overlap with `deal_name`; word overlap exists (Summit Advisors / Summit Credit Opportunities) | Not a client in the other sheets | Do not join to deals |
| `sector` | str | agritech | 12 | Sector label | HIGH | Named in the summary of 20,000/20,000 meetings | Includes "education (private schools)" | — |
| `region` | str | Global, EMEA | 6 | Region label | HIGH | Named in the summary of 20,000/20,000 meetings | Categories overlap (Global, APAC, India, Southeast Asia, EMEA, Americas) | — |
| `investment_stage` | str | seed, growth | 7 | Stage label | HIGH | Stage text appears in only 1,101 of 20,000 summaries | Values: late_stage, pre-seed, series_a, seed, series_b, growth, private_equity | Use the column, not the text |
| `deal_size_estimate` | str | 2M USD, 209k USD | 510 | Estimated size as text. What deal it refers to is undocumented. | MEDIUM | 20,000/20,000 match `<n>M USD` (14,274) or `<n>k USD` (5,726); 2,210 have decimals. No `B` unit observed | Text, not numeric. Text sort order is wrong (`"95M USD" > "200M USD"`) | A7, Section 6 |
| `summary` | str | "Rahul and the team met to summarize…" | 20,000 | Free-text meeting summary | HIGH | 991-1,508 chars (median 1,416); equals `summary_char_length` in all rows. Begins with an attendee's first name in 20,000/20,000 | 14,241 rows (71.2%) contain mojibake. Highly templated wording | Raw, not repaired (A11) |
| `action_items` | str | "Action: introduce legal counsel…" | 85 distinct (14,992 non-null) | Follow-up action text | HIGH | 85 templated sentences | **NULL in 5,008 rows (25.0%). NULL = not recorded.** | **A8: NULL is missingness. Do not read it as "no action item".** Keep "not recorded" and "no action item" as separate states; the data cannot express "no action item" |
| `summary_char_length` | int | 1380 | 444 | Length of `summary` | HIGH | Equals `len(summary)` in all rows | Derived, redundant | — |
| `client_id` | str | B12463 | 1,000 | Client identifier | HIGH | 192 exist in the other sheets; 808 do not (15,904 meetings) | Orphan clients (2.4) | — |
| `group_id` | int | 464 | 250 | Group identifier | HIGH | 1 per client; agrees with Investments for the 192 shared clients | 154 groups exist only here; every group has 4 clients (2.4) | — |

### 3.3 `performance_data`

Families of identically-behaving columns are listed with their exact column names.

| Column(s) | Observed dtype (FACT) | Examples (FACT) | Cardinality (FACT) | Proposed meaning (INFERENCE) | Conf | Relationships (FACT) | Caveat / anomaly (FACT) | Handling (ASSUMPTION) |
|---|---|---|---|---|---|---|---|---|
| `Client_Group_Id` | int | 346 | 96 | Group identifier | HIGH | Same 96 groups as Investments | — | — |
| `Client_Id`, `Client_Name` | str | A12345, Client_A12345 | 192 (+96 null) | Client identifier / name | HIGH | Same 192 clients as Investments | Null on all 96 group rows | — |
| `IsGroup_Flag` | str | N, Y | 2 | Marks a group row (Y) versus a client row (N) | HIGH | Y <=> `Client_Id` null <=> all `*_Status_Name` = "Group" (96/96) | What group metrics represent is undocumented | Filter `N` for client questions |
| `As_Of_Date` | datetime | 2024-10-25 | 1,418 | Snapshot date of the row's metrics | MEDIUM | 8 rows per client, not in date order. Ranges in Section 5 | Metrics jump between snapshots (A12345 `CI_Current_MOIC`: 3.0x, 0.93x, 1.33x…) | **A6: latest per client** |
| 8 MOIC cols: `CI_Current_MOIC`, `CI_Total_MOIC`, `CI_Realised_MOIC`, `HF_Total_MOIC`, `RE_Core_MOIC`, `RE_Current_MOIC`, `RE_Total_MOIC`, `RE_Realised_MOIC` | str | 3.0x, 2.26x | 279-281 each | Multiple-on-invested-capital style metric (INFERENCE from the name) | MEDIUM | All 1,632 values in all 8 columns match `^\d+(\.\d+)?x$`. Range 0.70..3.50. 159 one-decimal and 1,473 two-decimal forms coexist in `CI_Current_MOIC` | Text. String sort/MAX is wrong (`"1.5x" > "1.55x"`). The definitions of Current, Total, Realised and Core are undocumented | A7, Section 6 |
| 12 IRR cols: `CI_Current_IRR`, `CI_Total_IRR`, `CI_Realised_IRR`, `CI_Since_2001_IRR`, `HF_Total_IRR`, `RE_Core_IRR`, `RE_Current_IRR`, `RE_Total_IRR`, `RE_Realised_IRR`, `COP_Total_IRR`, `COP_Realised_IRR`, `COP_Current_IRR` | float | 0.0684, -0.0314 | about 1,230-1,295 each | Rate-of-return metric (INFERENCE from the name) | MEDIUM | Range -0.05..0.25 across all 12 | Unit undocumented; values are consistent with fractions (INFERENCE) | Report as stored; do not label as a percentage unless the unit is confirmed |
| 19 amount cols: `CI_CY_FR_Amount`, `CI_FR_SI_Amount`, `CI_L3Y_DIS_Amount`, `CI_L3Y_FR_Amount`, `RE_CY_FR_Amount`, `RE_FR_SI_Amount`, `RE_L3Y_DIS_Amount`, `RE_L3Y_FR_Amount`, `COP_FR_SI_Amount`, `HF_AUM_Amount`, `Mena_AUM_Amount`, `Pref_Shares_AUM_Amount`, `RE_AUM_Amount`, `Tech_AUM_Amount`, `CI_AUM_Amount`, `Total_AUM_Amount`, `Receivables_Amount`, `Call_Account_Balance_Amount`, `Future_Distribution_Amount` | float | 18957828.56 | about 1,632 each | Money amounts. `AUM` is presumed to abbreviate assets under management (INFERENCE). CY, FR, SI, L3Y, DIS are undocumented. | LOW | All positive; range 11,771.67..19,999,856.76 across the sheet's 22 amount columns (these 19 plus the three `Last_*_Investment_Amount` columns). | Currency not stated. Latest `Total_AUM_Amount` per client is 0.10M-19.92M against 367M-584M of Investments per client: the two are not reconcilable | Do not expand undocumented abbreviations; do not compare with Investments amounts |
| `Total_AUM_Amount` | float | 10508678.11 | 1,632 | Total of AUM (INFERENCE from the name) | LOW | Differs from the sum of the six `*_AUM_Amount` columns in 1,632 of 1,632 rows. In row 0 it is smaller than `CI_AUM_Amount` alone | How it is composed is undocumented | Do not derive or reconcile |
| `Product_Count_Number` | int | 2, 4, 10 | 10 | Count of products | MEDIUM | Range 1..10 | Definition undocumented | — |
| `Investment_Status_Name` | str | Active, Dormant, Prospect, Group | 4 | Status of the client on this snapshot | MEDIUM | Latest snapshots (192 clients): Dormant 73, Prospect 62, Active 57. Changes across snapshots (169 clients show 3 distinct values, 23 show 2) | **No `Closed` value**, although Investments has `Closed` | A4, Section 8: a separate field/source; never a substitute for `ClientStatus`; label separately if both are shown |
| `CI_Status_Name`, `RE_Status_Name`, `INF_Status_Name`, `ICM_Status_Name` | str | Active, Stopped, Group | 3 each | Status per named family | MEDIUM | 17 distinct combinations of the four | `INF` and `ICM` have no metric columns; `COP`, `HF` have no status column | — |
| `Client_Last_Met_Date` | datetime | 2018-09-18 | 1,401 | A date named "client last met" | MEDIUM | Snapshot-specific: 8 distinct values across a client's 8 rows for 191 of 192 clients (7 for one). Range 2010-01-03..2025-01-16. Later than `As_Of_Date` in 832 of 1,632 rows | Disagrees with meetings for 192 of 192 clients (Section 8) | A13, Section 8: not the last-met source; show only as a separately labelled performance field |
| `First_HF/CI/RE_Investment_Date`, `Last_HF/CI/RE_Investment_Date` | datetime | 2020-03-01 | about 1,400 each | First / last investment date per family | MEDIUM | All range 2010-01-01..2025-01-21 | Contradictory (Section 9, T3, T4) | Report as stored |
| `First_CI_Investment_Name`, `Last_CI_Investment_Name`, `First_RE_Investment_Name`, `Last_RE_Investment_Name`, `Last_COP_Investment_Name` | str | CI Fund 19, RE Fund 4 | 10-20 each | Fund names | LOW | Do not match `Investments.deal_name` | — | Do not join to deals |
| `Last_CI_Investment_Amount`, `Last_RE_Investment_Amount`, `Last_COP_Investment_Amount` | float | 1201427.39 | about 1,632 each | Amount of the last investment per family | LOW | Unrelated to `Investment_Amount_USD_for_agg` | Currency not stated | — |

## 4. Entity identifiers (FACT)

| Entity | Forms in the data | Note |
|---|---|---|
| Client | `A12345`; `Client_A12345` (`Client Name`); `Client A12345 Holdings` (`AccountName`); `Client A12345 Org` (`AccountName_org`) | Same numeric suffix appears with A, B, C and D (32 groups). A bare number is ambiguous. |
| Group | integer id, e.g. 346 | Group 346 = A12345, B12345, C12345, D12345 |
| Deal | `deal_id` DL…; `deal_name` | Names that are prefixes of others: `Orion Infrastructure I`/`IV`, `Atlas Private Equity II`/`III`, `BluePeak Venture II`/`III` |
| RM | 5 names in `AccountRM` | Emails and aliases are non-authoritative (A3) |
| Meeting counterparty | `company` (15 values) | Not a client, not a deal |
| Person names | `attendees`; first name opens each `summary` | RM full names appear as attendees in 12-15 meetings each |

## 5. Latest performance (A6) and observed date ranges

**Rule (ASSUMPTION):**
1. For client questions, use rows with `IsGroup_Flag='N'` and the client's `Client_Id`. The client's latest snapshot is the row with the maximum `As_Of_Date` **among that client's own rows**.
2. Never use the global maximum date (2025-01-22) as a client's latest date. Clients' latest dates differ by up to 7.5 years.
3. If two rows share the client's maximum date, surface the ambiguity. None occurs among the 192 clients.
4. Group questions use the group row (`IsGroup_Flag='Y'`), which has its own `As_Of_Date`. Do not derive it from member clients.
5. Answers should state the `As_Of_Date` used.

**Observed ranges (FACT):**

| Field | Range |
|---|---|
| `Investments.dat_MinInvested` | 2018-01-01..2024-11-05 |
| `performance.As_Of_Date`, all rows | 2010-01-03..2025-01-22 |
| `performance.As_Of_Date`, latest per client (192 clients) | 2017-07-30..2025-01-22 |
| `performance.As_Of_Date`, group rows | 2010-01-03..2024-12-30 |
| `performance.Client_Last_Met_Date` | 2010-01-03..2025-01-16 |
| `performance.First/Last_*_Investment_Date` | 2010-01-01..2025-01-21 |
| `meeting_notes.date` | 2022-01-01..2026-03-13 |

**Source coverage (FACT):** there is no common end date across the sources. The latest `Investments.dat_MinInvested` is 2024-11-05, the latest `performance.As_Of_Date` is 2025-01-22 and the latest `meeting_notes.date` is 2026-03-13. Illustration: at the profiling date, 2026-09-26, meeting coverage ended 197 days earlier.

**Relative and current dates (A14, ASSUMPTION):**
1. The reference for "recently", "last year", "current" and similar wording is the **runtime current date**.
2. Do not treat the latest date in any source as "today", and do not imply the data runs through the runtime date.
3. When the requested period extends beyond the coverage of the source used, say so explicitly. The source used for meeting questions is `meeting_notes.date` (coverage ends 2026-03-13); for performance questions it is `As_Of_Date` (Section 5, A6).
4. A relative window such as "recently" has no definition in the data. The answer states the window it uses.
5. "Last met" comes from `meeting_notes.date` (A13), not from `Client_Last_Met_Date`.

## 6. Deterministic normalisation (A7)

Raw columns are never modified or overwritten. Numeric values go in separate derived fields (naming to be decided at implementation).

**MOIC** (8 columns):
- Accept only a raw value that exactly matches `^\d+(\.\d+)?x$`. The numeric value is the text before `x`, converted exactly, without rounding.
- Anything else (NULL, empty, different format, sign, other suffix) gives numeric NULL and a parse-failure flag. Do not coerce, guess or impute.
- Observed: 0 failures across all 8 columns x 1,632 rows at profiling time.

**Deal size** (`deal_size_estimate`):
- Accept only `<number><unit> <currency>` where `<number>` is a decimal, `<unit>` is `k` (x 1,000), `M` (x 1,000,000) or `B` (x 1,000,000,000), case-sensitive, and `<currency>` is `USD`. Use exact decimal arithmetic.
- Observed: only `k` and `M`, only `USD`; `B` is supported by the rule but never occurs. 20,000/20,000 parse.
- Any other unit letter or case, other currency, missing part or extra text gives numeric NULL and a parse-failure flag. No default unit, no guessing.

**Both:** every answer that uses a derived value keeps the raw value available and must report unparsed rows as unknown, not zero.

## 7. Metric availability (A9)

Availability by column name (FACT). "None" means no column exists.

| Family prefix | MOIC | IRR | AUM amount | Status column | Investment dates / names |
|---|---|---|---|---|---|
| `CI` | Current, Total, Realised | Current, Total, Realised, Since_2001 | `CI_AUM_Amount` | `CI_Status_Name` | First/Last date, First/Last name, Last amount |
| `HF` | Total | Total | `HF_AUM_Amount` | none | First/Last date only |
| `RE` | Core, Current, Total, Realised | Core, Current, Total, Realised | `RE_AUM_Amount` | `RE_Status_Name` | First/Last date, First/Last name, Last amount |
| `COP` | **none** | Total, Realised, Current | none | none | Last name, Last amount |
| `INF`, `ICM` | none | none | none | Status only | none |
| `PE` | none | none | none | none | none (appears only as an Investments `cod_lob` value) |
| `Mena`, `Tech`, `Pref_Shares` | none | none | one AUM amount each | none | none |

Rules (ASSUMPTION):
- If the requested metric has no column for the requested family, answer "unavailable". Do not substitute another metric (for example IRR for MOIC, or `Total` for `Current`).
- The mapping between Investments `cod_lob` values (COP, HF, INF, PE, RE) and performance prefixes (CI, HF, RE, COP, INF, ICM) is undocumented. `CI` and `ICM` are absent from `cod_lob`; `PE` is absent from performance. Do not assume a mapping.
- Not every client has every family; the data does not say whether a family value is meaningful for a client.

## 8. Source-precedence guidance

No conflicting values are reconciled anywhere in this document.

| Topic | Sources and observed disagreement (FACT) | Guidance |
|---|---|---|
| **RM** | One source: `Investments.AccountRM`. Performance and meetings have no RM. Other RM-like Investments fields contradict it (A3). | `AccountRM` only (A1). Record-level (A2). Never state "the RM of client X". State the RMs found on that client's records. |
| **Status** | Two sources: `Investments.ClientStatus` (4 values incl. `Closed`, per record, every client has all 4) and `performance.Investment_Status_Name` (Active/Dormant/Prospect/Group, per snapshot, no `Closed`, changes over time). The two do not agree in vocabulary. Because every client has all 4 Investments statuses, agreement cannot be tested. | **Decided (A4).** `ClientStatus` is relationship/record-level and is never collapsed into one client-wide status. `Investment_Status_Name` is a separate field and source and is never substituted for `ClientStatus`. If both are presented, label each separately with its field name. Do not merge or reconcile them. |
| **Last-met date** | Two sources: `performance.Client_Last_Met_Date` (snapshot-specific) and the maximum `meeting_notes.date` per client. For all 192 shared clients the latest-snapshot performance value is **earlier** than the latest meeting (example A12345: 2018-09-18 versus 2026-03-13). Only 1 of 192 latest-snapshot values equals any meeting date of that client. The 808 meeting-only clients have no performance value. | **Decided (A13).** For "last met" / "most recent meeting", use the latest `meeting_notes.date` for the client and name `meeting_notes_20k` as the source. Do not silently substitute `Client_Last_Met_Date`. The two fields can disagree; the performance field may be shown only as a separately labelled performance field, without calling either value wrong. |

## 9. Known synthetic-data and cross-sheet inconsistencies (documented, not repaired — A10)

| ID | Observation (FACT) |
|---|---|
| T1 | 810 of 1,536 client snapshots have `As_Of_Date` earlier than that client's earliest `dat_MinInvested`. 809 snapshots predate 2018-01-01, the earliest investment record. Among the 192 latest snapshots, 1 predates it. |
| T2 | `Client_Last_Met_Date` is later than `As_Of_Date` in 832 of 1,632 rows. |
| T3 | `First_*_Investment_Date` is later than `Last_*_Investment_Date` in 809 (HF), 846 (CI) and 782 (RE) of 1,632 rows. |
| T4 | `Last_HF/CI/RE_Investment_Date` is later than `As_Of_Date` in 792 / 806 / 842 rows; `First_CI_Investment_Date` in 831 rows. |
| T5 | Meeting dates run to 2026-03-13; the last investment date is 2024-11-05 and the last performance snapshot 2025-01-22. |
| T6 | `Client_Last_Met_Date` and meeting dates disagree for 192 of 192 clients (Section 8). |
| S1 | Investments total 367M-584M USD per client; performance `Total_AUM_Amount` is 0.10M-19.92M per client. |
| S2 | `Total_AUM_Amount` does not equal the sum of the six `*_AUM_Amount` columns in any row. |
| S3 | Group-row `Total_AUM_Amount` equals neither the sum of members' latest values nor the sum of all their snapshots for any of the 96 groups. Tested on `Total_AUM_Amount` only. |
| S4 | Performance metrics change widely between a client's snapshots with no visible pattern. |
| S5 | Investments `ClientStatus` has `Closed`; performance status does not. |
| S6 | `AccountRMEmail`, `RM_Alias` contradict `AccountRM`. |
| S7 | The same `group_id` has 1 client in Investments and 4 in Meetings for 64 groups (2.4). |
| S8 | Investments rows carry every RM and every status for every client (A2, A4). |
| E1 | Encoding: 14,241 of 20,000 `summary` rows (71.2%) and 2,107 `attendees` rows contain UTF-8 text decoded as cp1252. All non-ASCII characters in these two columns are affected; none are correct. Sequences: `â€”` (18,210 occurrences), `Â±` (4,706), `Ã³` (182 in summaries, 1,102 in attendees), `Ä`+U+008D (190 in summaries, 1,116 in attendees). `action_items` and `company` are clean. A plain cp1252-to-UTF-8 decode fails for 177 summaries (U+008D is undefined in cp1252). |

## 10. Hybrid queries and meeting retrieval

**Hybrid queries (A12):**
1. Aggregate each structured source at its own grain first: Investments per client (or record set), performance per client latest snapshot (Section 5), meetings per client.
2. Only then combine the small results with meeting evidence. Do not join row-level tables: a row-level three-way join on `client_id` produces 8,534,216 rows in total (Section 2.3), inflating any SUM or COUNT.
3. Populations differ (2.4). A question spanning sheets covers only the 192 shared clients. Say so, and do not silently drop the 808 meeting-only clients or treat them as having no investments.
4. Amounts from Investments and performance are not reconcilable (S1). Never compare or add them.
5. `company` and `deal_size_estimate` in meetings are not the client's deals. Meetings carry no RM, so questions such as "meetings held by RM X" are not answerable from the data.

**Meeting retrieval considerations:**
1. Deterministic filtering comes first: client, group, date, company, sector, region, stage from the structured columns. Text retrieval runs over the filtered set.
2. `company`, `sector` and `region` are named in every one of the 20,000 summaries, and the wording is highly templated. Text similarity on these terms alone matches thousands of near-identical summaries and cannot discriminate.
3. `investment_stage` appears in the text of only 1,101 of 20,000 summaries. Use the column.
4. Encoding artifacts exist (E1). They affect accented and unaccented name matching and phrases with dashes. Raw text is the baseline. **Repair is deferred** until the retrieval-impact test (`docs/encoding_impact_test_design.md`) has run.
5. `action_items` has 85 templated values and 25% NULL (A8).
6. `deal_size_estimate` is text; range or ordering questions need the numeric version (Section 6).

## 11. Open questions for Yogendra

1. **Status precedence — resolved (A4, Section 8).** `ClientStatus` is relationship-level; performance `Investment_Status_Name` is a separate source, never a substitute, and is labelled separately if both are shown.
2. **Last-met precedence — resolved (A13, Section 8).** Latest `meeting_notes.date` per client; `Client_Last_Met_Date` only as a separately labelled performance field.
3. **Time anchor — resolved (A14, Section 5).** Runtime current date, with explicit qualification when the requested period exceeds source coverage.
4. **Group membership:** for the 64 groups that differ (S7), which sheet defines membership?
5. **Product mapping:** any documented mapping between `cod_lob` and the CI/HF/RE/COP/INF/ICM prefixes? Until then, none is assumed.
6. **Undocumented fields:** meaning of `id_CapitalCall`, `flg_Realised`, `dat_MinInvested`, and the abbreviations CY, FR, SI, L3Y, DIS.
7. **Units:** currency of the performance amounts, and whether IRR is a fraction or a percentage.
8. **Normalisation strictness:** confirm that case-sensitive parsing (`k`, `M`, `B`) with NULL on anything else is acceptable, and the names for the derived numeric fields.
9. **Answer-time caveats:** which of the caveats above (record-level RM, as-of date, unavailable metrics, meeting-only clients) must always appear in the answer text.
