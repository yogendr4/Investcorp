"""SQL validation for the Baseline structured-query engine.

Two layers, both before any execution and neither ever rewrites or repairs the SQL:
  1. static scan of the text (one statement, SELECT/WITH only, no comments, no write/DDL/PRAGMA/ATTACH keywords);
  2. compilation with `EXPLAIN` under a SQLite authorizer that allows only approved tables/views, approved
     columns and a whitelist of functions. SQLite's own parser is the authority for what the statement reads.
The same authorizer stays active while the query runs.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

_FORBIDDEN = {"insert", "update", "delete", "drop", "alter", "create", "attach", "detach", "pragma", "vacuum", "reindex", "analyze", "begin",
              "commit", "rollback", "savepoint", "release", "explain", "load_extension", "truncate", "grant", "merge"}
ALLOWED_FUNCTIONS = {"count", "sum", "avg", "min", "max", "total", "round", "abs", "coalesce", "ifnull", "nullif", "iif", "lower", "upper", "length",
                     "substr", "substring", "trim", "ltrim", "rtrim", "instr", "date", "strftime", "group_concat", "like", "glob",
                     "row_number", "rank", "dense_rank", "lag", "lead", "first_value", "last_value"}
_ACTION_NAMES = {v: k for k, v in vars(sqlite3).items() if k.startswith("SQLITE_") and isinstance(v, int)}
_LIKE_ON_DEAL_NAME = re.compile(r"\bdeal_name\b\s*(?:not\s+)?(?:like|glob)\b", re.I)
_CTE_DEF = re.compile(r"([A-Za-z_]\w*)\s*(?:\([^()]*\))?\s+as\s+(?:(?:not\s+)?materialized\s+)?\(", re.I)
RESERVED_NAMES = {"investments", "performance", "meetings", "performance_client", "performance_latest", "performance_group", "sqlite_master",
                  "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema", "_build_meta", "_column_lineage"}


def cte_names(masked_sql: str) -> set[str]:
    """Names defined with WITH name AS (...). Found in the text with quoted content already blanked."""
    return {m.group(1) for m in _CTE_DEF.finditer(masked_sql)}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    issues: tuple[Issue, ...] = ()
    tables_read: tuple[str, ...] = ()
    functions_used: tuple[str, ...] = ()

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]


def _mask(sql: str) -> tuple[Optional[str], Optional[str]]:
    """Blank out quoted text so keywords, comments and semicolons are only looked for in code."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`") or ch == "[":
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if close != "]" and j + 1 < n and sql[j + 1] == close:   # doubled quote is an escape
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                return None, f"unterminated {ch} quote"
            out.append(ch + " " * (j - i - 1) + close)
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), None


def static_scan(sql: object, reserved: frozenset = frozenset()) -> list[Issue]:
    if not isinstance(sql, str) or not sql.strip():
        return [Issue("empty_sql", "no SQL text")]
    masked, err = _mask(sql.strip())
    if masked is None:
        return [Issue("unbalanced_quotes", err)]
    issues: list[Issue] = []
    if "--" in masked or "/*" in masked or "*/" in masked:
        issues.append(Issue("comment_not_allowed", "SQL comments are not allowed"))
    body = masked.rstrip()
    inner = body[:-1] if body.endswith(";") else body
    if ";" in inner:
        issues.append(Issue("multiple_statements", "exactly one statement is allowed"))
    first = re.match(r"\s*\(*\s*([A-Za-z_]+)", masked)
    if not first or first.group(1).lower() not in ("select", "with"):
        issues.append(Issue("not_a_select", "the statement must start with SELECT or WITH"))
    bad = sorted({w for w in re.findall(r"[A-Za-z_]+", masked.lower()) if w in _FORBIDDEN})
    if bad:
        issues.append(Issue("forbidden_keyword", f"write, DDL, PRAGMA, ATTACH and transaction keywords are not allowed: {', '.join(bad)}"))
    clash = sorted(n for n in cte_names(masked) if n.lower() in {r.lower() for r in reserved})
    if clash:
        issues.append(Issue("cte_name_collision", f"a WITH name may not reuse an existing table or view name: {', '.join(clash)}"))
    if _LIKE_ON_DEAL_NAME.search(masked):
        issues.append(Issue("like_on_deal_name", "deal_name must be matched exactly with '=', never LIKE or GLOB (prefix collisions)"))
    return issues


class SqlGuard:
    """Owns the authorizer policy for one connection."""

    def __init__(self, conn: sqlite3.Connection, approved_columns: dict[str, set[str]], views: set[str]) -> None:
        self.conn, self.approved, self.views = conn, approved_columns, views
        self.reserved = frozenset(RESERVED_NAMES | set(approved_columns) | set(views))
        self.ctes: set[str] = set()
        self.denied: list[tuple[str, str]] = []
        self.tables: set[str] = set()
        self.functions: set[str] = set()

    def install(self) -> None:
        self.conn.set_authorizer(self._authorize)

    def reset(self) -> None:
        self.denied, self.tables, self.functions = [], set(), set()

    def _authorize(self, action, arg1, arg2, dbname, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if source in self.views:                     # a read made inside one of our own view definitions
                self.tables.add(source)                  # COUNT(*) on a view emits no top-level read; the view name is only visible here
                return sqlite3.SQLITE_OK
            if dbname is None and arg1 in self.ctes:     # a WITH name defined by this query (collisions with real names are rejected)
                return sqlite3.SQLITE_OK
            if arg1 not in self.approved:
                self.denied.append(("table", arg1))
                return sqlite3.SQLITE_DENY
            if arg2 and arg2 not in self.approved[arg1]:
                self.denied.append(("column", f"{arg1}.{arg2}"))
                return sqlite3.SQLITE_DENY
            self.tables.add(arg1)
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            name = (arg2 or "").lower()
            if name in ALLOWED_FUNCTIONS:
                self.functions.add(name)
                return sqlite3.SQLITE_OK
            self.denied.append(("function", name))
            return sqlite3.SQLITE_DENY
        self.denied.append(("action", _ACTION_NAMES.get(action, str(action)).replace("SQLITE_", "").lower()))
        return sqlite3.SQLITE_DENY

    def validate(self, sql: object) -> ValidationResult:
        """Static scan, then compile under the authorizer. Never executes the statement."""
        issues = static_scan(sql, self.reserved)
        if issues:
            return ValidationResult(False, tuple(issues))
        self.reset()
        self.ctes = cte_names(_mask(sql.strip())[0])
        try:
            self.conn.execute("EXPLAIN " + sql.strip()).fetchall()
        except sqlite3.Error as exc:
            if self.denied:
                return ValidationResult(False, tuple(self._denial_issues()))
            return ValidationResult(False, (Issue("sql_compile_error", str(exc)),))
        if self.denied:
            return ValidationResult(False, tuple(self._denial_issues()))
        return ValidationResult(True, (), tuple(sorted(self.tables)), tuple(sorted(self.functions)))

    def _denial_issues(self) -> list[Issue]:
        out, seen = [], set()
        for kind, what in self.denied:
            if (kind, what) in seen:
                continue
            seen.add((kind, what))
            if kind == "table":
                out.append(Issue("unauthorized_table", f"table or view '{what}' is not approved"))
            elif kind == "column":
                out.append(Issue("unauthorized_column", f"column '{what}' is not approved (SELECT * is not allowed on investments; name the columns)"))
            elif kind == "function":
                out.append(Issue("unauthorized_function", f"function '{what}' is not allowed"))
            else:
                out.append(Issue("forbidden_operation", f"operation '{what}' is not allowed"))
        return out
