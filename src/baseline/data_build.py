"""Build the Baseline SQLite analytical database from the assignment workbook.

read workbook -> normalise -> create tables -> populate -> index -> validate -> replace.
Standard library plus openpyxl (already a project dependency). The database is built
in a temporary file, validated by re-opening it read-only, and only then moved into
place, so a failed build never replaces a good database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .data_schema import INDEXES, IngestError, TABLE_SPECS, TableSpec, index_name, lineage_rows, transform_rows
from .data_validation import Check, run_checks
from .meeting_index import create_meeting_index

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKBOOK = PROJECT_ROOT / "data" / "Assignment_Data.xlsx"
DEFAULT_DB = PROJECT_ROOT / "data" / "derived" / "baseline.sqlite"
INGEST_VERSION = "0.1"


class ValidationError(Exception):
    def __init__(self, failed: list[Check]) -> None:
        self.failed = failed
        super().__init__("database validation failed: " + "; ".join(f"{c.name} ({c.detail})" for c in failed))


@dataclass(frozen=True)
class BuildReport:
    db_path: Path
    db_size_bytes: int
    row_counts: dict
    checks: tuple[Check, ...]
    workbook_sha256: str
    elapsed_s: float


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_workbook(path: Path) -> dict[str, tuple[list[Any], list[tuple]]]:
    """Sheet name -> (header, data rows), exactly as stored. Trailing blank rows are dropped; a blank row inside the data is an error."""
    import openpyxl  # imported here so the pure schema module has no dependency on it

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        missing = [s.sheet for s in TABLE_SPECS if s.sheet not in wb.sheetnames]
        if missing:
            raise IngestError(f"workbook is missing expected sheets: {missing} (found {wb.sheetnames})")
        out = {}
        for spec in TABLE_SPECS:
            rows_iter = wb[spec.sheet].iter_rows(values_only=True)
            header = list(next(rows_iter, ()))
            if not header or not all(isinstance(h, str) for h in header):
                raise IngestError(f"sheet {spec.sheet!r}: header row is missing or not all text")
            width, rows, blanks = len(header), [], 0
            for n, row in enumerate(rows_iter, start=2):
                row = tuple(row)
                if any(v is not None for v in row[width:]):
                    raise IngestError(f"sheet {spec.sheet!r} row {n}: values beyond the header width")
                row = row[:width] + (None,) * (width - len(row))
                if all(v is None for v in row):
                    blanks += 1
                    continue
                if blanks:
                    raise IngestError(f"sheet {spec.sheet!r}: blank row before row {n}")
                rows.append(row)
            if blanks:
                logger.info("sheet %s: dropped %d trailing blank rows", spec.sheet, blanks)
            out[spec.sheet] = (header, rows)
        return out
    finally:
        wb.close()


def _create_and_load(conn: sqlite3.Connection, spec: TableSpec, rows: list[tuple]) -> None:
    conn.execute(spec.ddl())
    marks = ", ".join("?" * len(spec.column_names()))
    conn.executemany(f"INSERT INTO {spec.table} ({', '.join(spec.column_names())}) VALUES ({marks})", rows)
    for cols in INDEXES[spec.table]:
        conn.execute(f"CREATE INDEX {index_name(spec.table, cols)} ON {spec.table} ({', '.join(cols)})")


def _write_meta(conn: sqlite3.Connection, workbook: Path, sha: str, counts: dict) -> None:
    conn.execute("CREATE TABLE _build_meta (key TEXT PRIMARY KEY, value TEXT)")
    meta = {"ingest_version": INGEST_VERSION, "workbook_file": workbook.name, "workbook_sha256": sha,
            "built_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **{f"rows_{t}": str(n) for t, n in counts.items()}}
    conn.executemany("INSERT INTO _build_meta VALUES (?, ?)", meta.items())
    conn.execute("CREATE TABLE _column_lineage (table_name TEXT, column_name TEXT, source_sheet TEXT, source_column TEXT, kind TEXT, note TEXT)")
    for spec in TABLE_SPECS:
        conn.executemany("INSERT INTO _column_lineage VALUES (?, ?, ?, ?, ?, ?)", lineage_rows(spec))


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def build_database(workbook_path: Path = DEFAULT_WORKBOOK, db_path: Path = DEFAULT_DB) -> BuildReport:
    """Build, validate and install the database. Raises IngestError or ValidationError; never leaves a half-built database in place."""
    workbook_path, db_path = Path(workbook_path), Path(db_path)
    start = time.monotonic()
    logger.info("build start: workbook=%s db=%s", workbook_path, db_path)
    if not workbook_path.is_file():
        raise IngestError(f"workbook not found: {workbook_path}")
    sha = _sha256(workbook_path)
    sheets = read_workbook(workbook_path)
    converted = {}
    for spec in TABLE_SPECS:
        header, raw = sheets[spec.sheet]
        converted[spec.table] = transform_rows(spec, header, raw)
        logger.info("normalised %s: %d rows", spec.table, len(converted[spec.table]))
    counts = {t: len(r) for t, r in converted.items()}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".building")
    for leftover in (tmp, Path(str(tmp) + "-journal")):
        if leftover.exists():
            leftover.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        for spec in TABLE_SPECS:
            _create_and_load(conn, spec, converted[spec.table])
        create_meeting_index(conn)           # derived from `meetings`; the analytical tables are not touched
        _write_meta(conn, workbook_path, sha, counts)
        conn.commit()
    except Exception:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()

    ro = _open_read_only(tmp)  # re-open: proves the file is readable and queryable after creation
    try:
        checks = run_checks(ro, roundtrip=converted)
    finally:
        ro.close()
    failed = [c for c in checks if not c.passed]
    logger.info("validation: %d checks, %d failed", len(checks), len(failed))
    if failed:
        tmp.unlink(missing_ok=True)
        raise ValidationError(failed)

    os.replace(tmp, db_path)
    final = _open_read_only(db_path)
    try:
        final_counts = {s.table: final.execute(f"SELECT COUNT(*) FROM {s.table}").fetchone()[0] for s in TABLE_SPECS}
    finally:
        final.close()
    if final_counts != counts:
        raise ValidationError([Check("installed database row counts", False, f"{final_counts} vs {counts}")])
    elapsed = round(time.monotonic() - start, 1)
    logger.info("build end: rows=%s size=%d bytes elapsed=%.1fs", counts, db_path.stat().st_size, elapsed)
    return BuildReport(db_path, db_path.stat().st_size, counts, tuple(checks), sha, elapsed)
