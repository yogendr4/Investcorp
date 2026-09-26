"""Deterministic validation of the Baseline SQLite analytical database.

Expected values come from docs/metadata/data_dictionary_DRAFT.md (FACT items).
Nothing here relies on undocumented business semantics. Each check returns a
Check; the build fails if any check fails.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from .data_schema import INDEXES, INVESTMENTS, MEETINGS, PERFORMANCE, TABLE_SPECS, index_name

ISO_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


@dataclass(frozen=True)
class Expected:
    rows: dict = field(default_factory=lambda: {"investments": 50_000, "meetings": 20_000, "performance": 1_632})
    structured_clients: int = 192          # investments and performance
    meeting_clients: int = 1_000
    meeting_only_clients: int = 808
    structured_groups: int = 96
    meeting_groups: int = 250
    meeting_only_groups: int = 154
    performance_group_rows: int = 96
    performance_client_rows: int = 1_536
    null_action_items: int = 5_008
    summary_non_ascii_rows: int = 14_241   # E1: all non-ASCII characters are mojibake
    attendees_non_ascii_rows: int = 2_107
    deal_size_k: int = 5_726
    deal_size_m: int = 14_274
    deal_size_min_usd: float = 10_000.0
    deal_size_max_usd: float = 200_000_000.0
    date_ranges: dict = field(default_factory=lambda: {
        ("investments", "dat_min_invested"): ("2018-01-01", "2024-11-05"),
        ("meetings", "meeting_date"): ("2022-01-01", "2026-03-13"),
        ("performance", "as_of_date"): ("2010-01-03", "2025-01-22"),
        ("performance", "client_last_met_date"): ("2010-01-03", "2025-01-16"),
    })
    distinct: dict = field(default_factory=lambda: {
        ("investments", "deal_id"): 8, ("investments", "deal_name"): 9, ("investments", "account_rm"): 5,
        ("investments", "client_status"): 4, ("investments", "natural_currency_code"): 6,
    })


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def _set(conn: sqlite3.Connection, sql: str) -> set:
    return {r[0] for r in conn.execute(sql)}


def run_checks(conn: sqlite3.Connection, roundtrip: Optional[dict[str, list[tuple]]] = None, expected: Expected = Expected()) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, bool(ok), detail))

    def eq(name: str, got: Any, want: Any) -> None:
        add(name, got == want, f"got {got!r}, expected {want!r}")

    # -- structure
    tables = _set(conn, "SELECT name FROM sqlite_master WHERE type='table'")
    needed = {s.table for s in TABLE_SPECS} | {"_build_meta", "_column_lineage"}
    add("tables present", needed <= tables, f"missing {sorted(needed - tables)}")
    if not needed <= tables:
        return checks
    eq("integrity_check", _one(conn, "PRAGMA integrity_check"), "ok")
    for spec in TABLE_SPECS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({spec.table})")]
        eq(f"{spec.table}: columns", cols, spec.column_names())
    have_idx = _set(conn, "SELECT name FROM sqlite_master WHERE type='index'")
    want_idx = {index_name(t, c) for t, lst in INDEXES.items() for c in lst}
    add("indexes present", want_idx <= have_idx, f"missing {sorted(want_idx - have_idx)}")
    fts = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'meetings_fts'").fetchone()
    add("meetings_fts: FTS5, external content over meetings, no diacritic folding",
        fts is not None and all(s in fts[0] for s in ("fts5", "content='meetings'", "remove_diacritics 0")), str(fts))

    # -- row counts
    for table, n in expected.rows.items():
        eq(f"{table}: row count", _one(conn, f"SELECT COUNT(*) FROM {table}"), n)
    eq("meetings: meeting_id unique", _one(conn, "SELECT COUNT(DISTINCT meeting_id) FROM meetings"), expected.rows["meetings"])

    # -- canonical client id
    for table in ("investments", "meetings", "performance"):
        bad = _one(conn, f"SELECT COUNT(*) FROM {table} WHERE client_id IS NOT NULL AND client_id NOT GLOB '[A-D][0-9][0-9][0-9][0-9][0-9]'")
        eq(f"{table}: client_id format", bad, 0)
        raw = _one(conn, f"SELECT COUNT(DISTINCT client_id) FROM {table}")
        canon = _one(conn, f"SELECT COUNT(DISTINCT UPPER(TRIM(client_id))) FROM {table}")
        eq(f"{table}: no case/whitespace duplicate client ids", canon, raw)
        many = _one(conn, f"SELECT COUNT(*) FROM (SELECT client_id FROM {table} WHERE client_id IS NOT NULL GROUP BY client_id HAVING COUNT(DISTINCT group_id) > 1)")
        eq(f"{table}: one group per client", many, 0)
    eq("investments: client_name = 'Client_' + client_id", _one(conn, "SELECT COUNT(*) FROM investments WHERE client_name <> 'Client_' || client_id"), 0)
    eq("performance: client_name = 'Client_' + client_id where present",
       _one(conn, "SELECT COUNT(*) FROM performance WHERE client_id IS NOT NULL AND client_name <> 'Client_' || client_id"), 0)
    eq("investments: no client-wide status column", [c for c in spec_cols("investments") if c in ("status", "client_wide_status")], [])

    # -- populations
    inv, perf = _set(conn, "SELECT client_id FROM investments"), _set(conn, "SELECT client_id FROM performance WHERE client_id IS NOT NULL")
    meet = _set(conn, "SELECT client_id FROM meetings")
    eq("investments clients", len(inv), expected.structured_clients)
    eq("performance clients", len(perf), expected.structured_clients)
    eq("investments and performance client sets identical", inv == perf, True)
    eq("meeting clients", len(meet), expected.meeting_clients)
    eq("meeting-only clients", len(meet - inv), expected.meeting_only_clients)
    eq("structured clients all present in meetings", inv <= meet, True)
    ig, pg, mg = _set(conn, "SELECT group_id FROM investments"), _set(conn, "SELECT group_id FROM performance"), _set(conn, "SELECT group_id FROM meetings")
    eq("structured groups", (len(ig), len(pg), ig == pg), (expected.structured_groups, expected.structured_groups, True))
    eq("meeting groups", len(mg), expected.meeting_groups)
    eq("meeting-only groups", len(mg - ig), expected.meeting_only_groups)
    mismatch = _one(conn, """SELECT COUNT(*) FROM (SELECT DISTINCT client_id, group_id FROM investments) i
                             JOIN (SELECT DISTINCT client_id, group_id FROM performance WHERE client_id IS NOT NULL) p ON p.client_id = i.client_id AND p.group_id <> i.group_id""")
    eq("client-to-group agrees between investments and performance", mismatch, 0)
    mismatch = _one(conn, """SELECT COUNT(*) FROM (SELECT DISTINCT client_id, group_id FROM investments) i
                             JOIN (SELECT DISTINCT client_id, group_id FROM meetings) m ON m.client_id = i.client_id AND m.group_id <> i.group_id""")
    eq("client-to-group agrees between investments and meetings", mismatch, 0)

    # -- performance group rows kept
    eq("performance: group rows kept", _one(conn, "SELECT COUNT(*) FROM performance WHERE is_group_flag = 'Y'"), expected.performance_group_rows)
    eq("performance: client rows", _one(conn, "SELECT COUNT(*) FROM performance WHERE is_group_flag = 'N'"), expected.performance_client_rows)
    eq("performance: is_group_flag domain", _set(conn, "SELECT DISTINCT is_group_flag FROM performance"), {"Y", "N"})
    eq("performance: group rows have NULL client_id and client rows do not",
       _one(conn, "SELECT COUNT(*) FROM performance WHERE (is_group_flag='Y') <> (client_id IS NULL)"), 0)
    eq("performance: snapshots not collapsed (8 per client)",
       _set(conn, "SELECT COUNT(*) FROM performance WHERE is_group_flag='N' GROUP BY client_id"), {8})
    latest = conn.execute("""WITH c AS (SELECT * FROM performance WHERE is_group_flag='N'),
        l AS (SELECT c.client_id FROM c JOIN (SELECT client_id cid, MAX(as_of_date) md FROM c GROUP BY client_id) m ON c.client_id=m.cid AND c.as_of_date=m.md)
        SELECT COUNT(*), COUNT(DISTINCT client_id) FROM l""").fetchone()
    eq("latest-per-client rule: one row per client, no ties", tuple(latest), (expected.structured_clients, expected.structured_clients))

    # -- MOIC
    for c in PERFORMANCE.stored():
        if c.kind != "moic":
            continue
        n = c.name
        eq(f"{n}: all parse ok", _one(conn, f"SELECT COUNT(*) FROM performance WHERE {n}_parse_status='ok'"), expected.rows["performance"])
        off = _one(conn, f"SELECT COUNT(*) FROM performance WHERE {n}_num IS NULL OR ABS({n}_num - CAST(REPLACE({n}, 'x', '') AS REAL)) > 1e-9")
        eq(f"{n}: numeric equals raw text before x", off, 0)

    # -- deal size
    ds = "deal_size_estimate"
    eq("deal_size: all parse ok", _one(conn, f"SELECT COUNT(*) FROM meetings WHERE {ds}_parse_status='ok'"), expected.rows["meetings"])
    eq("deal_size: k / M counts", (_one(conn, f"SELECT COUNT(*) FROM meetings WHERE {ds} LIKE '%k USD'"), _one(conn, f"SELECT COUNT(*) FROM meetings WHERE {ds} LIKE '%M USD'")),
       (expected.deal_size_k, expected.deal_size_m))
    off = _one(conn, f"""SELECT COUNT(*) FROM meetings WHERE ABS({ds}_usd - CAST(REPLACE(REPLACE({ds}, ' USD', ''), CASE WHEN {ds} LIKE '%k USD' THEN 'k' ELSE 'M' END, '') AS REAL)
                         * CASE WHEN {ds} LIKE '%k USD' THEN 1000.0 ELSE 1000000.0 END) > 1e-6""")
    eq("deal_size: numeric equals number x unit", off, 0)
    eq("deal_size: min / max USD", tuple(conn.execute(f"SELECT MIN({ds}_usd), MAX({ds}_usd) FROM meetings").fetchone()), (expected.deal_size_min_usd, expected.deal_size_max_usd))

    # -- raw text and NULL preservation
    eq("meetings: NULL action_items preserved", _one(conn, "SELECT COUNT(*) FROM meetings WHERE action_items IS NULL"), expected.null_action_items)
    eq("meetings: no empty-string action_items introduced", _one(conn, "SELECT COUNT(*) FROM meetings WHERE action_items = ''"), 0)
    s_rows = a_rows = 0
    for summary, attendees in conn.execute("SELECT summary, attendees FROM meetings"):
        s_rows += not summary.isascii()
        a_rows += not attendees.isascii()
    eq("meetings: mojibake left as loaded (non-ASCII summaries / attendees)", (s_rows, a_rows), (expected.summary_non_ascii_rows, expected.attendees_non_ascii_rows))

    # -- FTS index agrees with an independent computation over the raw text
    if fts is not None:
        import re
        eq("meetings_fts: indexed rows", _one(conn, "SELECT COUNT(*) FROM meetings_fts"), expected.rows["meetings"])
        n_py = sum(1 for row in conn.execute("SELECT summary, attendees, action_items, company FROM meetings")
                   if any("cfo" in re.findall(r"[^\W_]+", (v or "").casefold()) for v in row))
        eq("meetings_fts: MATCH 'cfo' equals a Python token count", _one(conn, "SELECT COUNT(*) FROM meetings_fts WHERE meetings_fts MATCH '\"cfo\"'"), n_py)

    # -- dates
    for spec in TABLE_SPECS:
        for col in spec.date_columns():
            bad = _one(conn, f"SELECT COUNT(*) FROM {spec.table} WHERE {col} IS NOT NULL AND {col} NOT GLOB '{ISO_DATE_GLOB}'")
            eq(f"{spec.table}.{col}: ISO date format", bad, 0)
    for (table, col), (lo, hi) in expected.date_ranges.items():
        eq(f"{table}.{col}: range", tuple(conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {table}").fetchone()), (lo, hi))
    for (table, col), n in expected.distinct.items():
        eq(f"{table}.{col}: distinct values", _one(conn, f"SELECT COUNT(DISTINCT {col}) FROM {table}"), n)

    # -- traceability tables
    eq("lineage table populated", _one(conn, "SELECT COUNT(DISTINCT table_name) FROM _column_lineage"), len(TABLE_SPECS))
    add("build meta has workbook checksum", _one(conn, "SELECT COUNT(*) FROM _build_meta WHERE key='workbook_sha256'") == 1)

    # -- every stored value equals the converted source value
    if roundtrip is not None:
        for spec in TABLE_SPECS:
            want = roundtrip[spec.table]
            got = conn.execute(f"SELECT {', '.join(spec.column_names())} FROM {spec.table} ORDER BY source_row").fetchall()
            if got == want:
                add(f"{spec.table}: every stored value equals the source value", True)
                continue
            detail = f"row count {len(got)} vs {len(want)}"
            if len(got) == len(want):
                for i, (g, w) in enumerate(zip(got, want)):
                    if g != w:
                        j = next(k for k in range(len(w)) if g[k] != w[k])
                        detail = f"first difference at source_row {w[0]}, column {spec.column_names()[j]}"
                        break
            add(f"{spec.table}: every stored value equals the source value", False, detail)
    return checks


def spec_cols(table: str) -> list[str]:
    return next(s for s in TABLE_SPECS if s.table == table).column_names()
