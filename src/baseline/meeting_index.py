"""Derived FTS5 index over the raw meeting text (see docs/architecture/meeting_retrieval.md).

`meetings_fts` is an external-content FTS5 table over `meetings` (nothing is copied or altered;
the analytical tables are unchanged). It is derived and rebuildable at any time from `meetings`.
Indexed fields: summary, attendees, action_items, company. Repetitive metadata (sector, region,
investment stage) and the deal-size text are not indexed. The tokenizer does not fold diacritics,
so clean and mojibake spellings stay different (encoding repair is deferred, A11).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

FTS_TABLE = "meetings_fts"
FTS_COLUMNS = ("summary", "attendees", "action_items", "company")
TOKENIZER = "unicode61 remove_diacritics 0"
# BM25 column weights, same order as FTS_COLUMNS. company is present in every summary, so it counts little.
COLUMN_WEIGHTS = (1.0, 1.0, 1.0, 0.1)


def create_meeting_index(conn: sqlite3.Connection) -> None:
    """Create and fill the index on a writable connection, then run FTS5's own integrity check."""
    conn.execute(f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5({', '.join(FTS_COLUMNS)}, content='meetings', content_rowid='source_row', tokenize='{TOKENIZER}')")
    conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
    conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('integrity-check')")


def rebuild_meeting_index(db_path: Path) -> None:
    """Drop and recreate the index in an existing database, from `meetings` only."""
    conn = sqlite3.connect(Path(db_path))
    try:
        conn.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
        create_meeting_index(conn)
        conn.commit()
    finally:
        conn.close()
