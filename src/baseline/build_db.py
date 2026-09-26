"""Command: rebuild the Baseline SQLite database from the assignment workbook.

    python -m src.baseline.build_db [--workbook PATH] [--db PATH]

Exit code 0 on success; 1 if the workbook is unusable or validation fails
(the existing database is then left untouched).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .data_build import DEFAULT_DB, DEFAULT_WORKBOOK, ValidationError, build_database
from .data_schema import IngestError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Baseline SQLite analytical database.")
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        report = build_database(args.workbook, args.db)
    except ValidationError as exc:
        print(f"BUILD FAILED: {len(exc.failed)} validation check(s) failed:", file=sys.stderr)
        for c in exc.failed:
            print(f"  - {c.name}: {c.detail}", file=sys.stderr)
        return 1
    except IngestError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {report.db_path} ({report.db_size_bytes:,} bytes) rows={report.row_counts} "
          f"checks={len(report.checks)} passed in {report.elapsed_s}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
