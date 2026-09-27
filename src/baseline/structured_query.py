"""Baseline structured-query engine (investment and performance routes).

question + route + entity resolution
  -> gates (route, entities, data availability, metric availability)
  -> compact schema context + prompt
  -> Claude CLI SQL generation (one call, no retry)
  -> SQL validation (static scan + authorizer compile; never repaired)
  -> read-only SQLite execution (bounded rows, timeout)
  -> StructuredEvidence

See docs/architecture/structured_query.md.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .data_build import DEFAULT_DB
from .entity_resolution import AMBIGUOUS as E_AMBIGUOUS, NOT_FOUND as E_NOT_FOUND, RESOLVED as E_RESOLVED, QuestionResolution
from .sql_guard import SqlGuard, ValidationResult
from .structured_schema import (INVESTMENT_ROUTE, INVESTMENTS_TABLE, PERF_VIEWS, PERFORMANCE_ROUTE, VIEW_BASE_TABLE, approved_columns,
                                build_schema_context, check_metric_availability, view_ddl)

# outcomes
OK, EMPTY_RESULT = "ok", "empty_result"
CLARIFICATION_NEEDED, ENTITY_NOT_FOUND, NO_STRUCTURED_DATA = "clarification_needed", "entity_not_found", "no_structured_data"
UNSUPPORTED_ROUTE, UNAVAILABLE_METRIC, UNSUPPORTED_QUESTION = "unsupported_route", "unavailable_metric", "unsupported_question"
LLM_FAILURE, MALFORMED_LLM_OUTPUT, MISSING_SQL = "llm_failure", "malformed_llm_output", "missing_sql"
SQL_REJECTED, EXECUTION_ERROR, TIMEOUT = "sql_rejected", "execution_error", "timeout"
SUCCESS_OUTCOMES = (OK, EMPTY_RESULT)


@dataclass(frozen=True)
class EngineConfig:
    db_path: Path = DEFAULT_DB
    max_rows: int = 50            # rows kept in the evidence object
    query_timeout_s: float = 10.0


@dataclass(frozen=True)
class StructuredEvidence:
    question: str
    route: str
    outcome: str
    sql: Optional[str] = None
    explanation: Optional[str] = None
    columns: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = ()
    row_count: int = 0
    truncated: bool = False
    source_tables: tuple[str, ...] = ()
    objects_used: tuple[str, ...] = ()
    rules_applied: tuple[str, ...] = ()
    entities_used: tuple[dict, ...] = ()
    timing: dict = field(default_factory=dict)
    llm: dict = field(default_factory=dict)
    error: Optional[dict] = None
    notes: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return self.outcome in SUCCESS_OUTCOMES

    def to_dict(self) -> dict:
        return {"question": self.question, "route": self.route, "outcome": self.outcome, "success": self.success, "sql": self.sql,
                "explanation": self.explanation, "columns": list(self.columns), "rows": [list(r) for r in self.rows], "row_count": self.row_count,
                "truncated": self.truncated, "source_tables": list(self.source_tables), "objects_used": list(self.objects_used),
                "rules_applied": list(self.rules_applied), "entities_used": list(self.entities_used), "timing_ms": self.timing, "llm": self.llm,
                "error": self.error, "notes": list(self.notes)}


# ---------------------------------------------------------------- prompt
_COMMON_RULES = """- One SELECT (or WITH ... SELECT) statement. No comments. No semicolon except optionally at the very end.
- Use only the approved tables/views and columns above. Do not use SELECT * on investments.
- COUNT(*) counts ROWS. For clients or deals use COUNT(DISTINCT client_id), COUNT(DISTINCT deal_id) or COUNT(DISTINCT deal_name), whichever the question asks for.
- Match names and ids with = and the exact value given. Never LIKE on deal_name.
- Use one source only. Do not join investments to performance.
- Put ORDER BY ... and LIMIT where a ranking or top-N is asked; break ties deterministically."""
_ROUTE_RULES = {
    INVESTMENT_ROUTE: """- RM: use account_rm. It is per RECORD: never say a client 'has one RM'; group by account_rm to show them.
- client_status is per RECORD, not per client.
- Totals across records: SUM(investment_amount_usd_for_agg). Add up investment_amount_natural_currency only within one natural_currency_code.
- deal_id and deal_name are independent identifiers; one deal_id can have several deal_names. Do not treat one as the other.""",
    PERFORMANCE_ROUTE: """- 'Latest' or 'current' performance: use performance_latest (one row per client). Never build 'latest' yourself with MAX(as_of_date) on performance_client.
- Use performance_client only for history across snapshots, and performance_group only for group questions.
- MOIC columns are numeric: compare and order them as numbers (2.0 means 2.0x).
- Snapshot values vary widely over time: do not average or describe trends unless the question asks.
- If the question needs a metric that does not exist, do not substitute another one.""",
}


def build_prompt(question: str, route: str, entity_lines: list[str]) -> str:
    entities = "\n".join(entity_lines) if entity_lines else "none"
    return f"""Task: write ONE read-only SQLite query that answers the question from the approved schema.
Output: ONLY a JSON object, nothing else: {{"sql": "<the query>", "explanation": "<at most 15 words>"}}
If the approved schema cannot answer the question, output {{"sql": null, "unsupported_reason": "<short reason>"}}.

Route: {route}
Question: {question}

Resolved entities (already verified; use exactly these values):
{entities}

Approved schema:
{build_schema_context(route)}

Rules:
{_COMMON_RULES}
{_ROUTE_RULES[route]}
"""


def parse_llm_output(text: str) -> tuple[Optional[dict], Optional[str]]:
    """Strict: one JSON object, optionally inside a single ``` fence. The SQL inside is never touched."""
    t = (text or "").strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    if m:
        t = m.group(1)
    try:
        obj = json.loads(t)
    except ValueError:
        return None, "the reply is not valid JSON"
    if not isinstance(obj, dict):
        return None, "the reply is JSON but not an object"
    return obj, None


# ---------------------------------------------------------------- engine
class StructuredQueryEngine:
    """Opens one SQLite connection (and its authorizer-bound SqlGuard) per thread, lazily, on
    first use. A sqlite3.Connection may only be used by the thread that created it (SQLite's own
    rule); the engine itself is a plain object that may legitimately be constructed on one thread
    (for example a cached UI resource) and then called from others, so the connection cannot be
    opened once in __init__ and shared. threading.local() gives each calling thread its own
    connection and guard, opened once per thread and reused for that thread's later calls.
    """

    def __init__(self, config: EngineConfig = EngineConfig(), adapter: Any = None) -> None:
        self.config = config
        self._adapter = adapter
        self._db = Path(config.db_path)
        if not self._db.is_file():
            raise FileNotFoundError(f"Baseline database not found: {self._db} (build it with: python -m src.baseline.build_db)")
        self._local = threading.local()

    def _open(self) -> tuple[sqlite3.Connection, "SqlGuard"]:
        conn = sqlite3.connect(f"{self._db.resolve().as_uri()}?mode=ro", uri=True)
        for ddl in view_ddl():                       # deterministic rules live in these views
            conn.execute(ddl)
        conn.execute("PRAGMA query_only = ON")
        guard = SqlGuard(conn, approved_columns(), set(PERF_VIEWS))
        guard.install()
        return conn, guard

    @property
    def conn(self) -> sqlite3.Connection:
        if getattr(self._local, "conn", None) is None:
            self._local.conn, self._local.guard = self._open()
        return self._local.conn

    @property
    def guard(self) -> "SqlGuard":
        if getattr(self._local, "guard", None) is None:
            self._local.conn, self._local.guard = self._open()
        return self._local.guard

    def close(self) -> None:
        """Closes this thread's connection only, if this thread ever opened one."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
            self._local.guard = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def adapter(self):
        if self._adapter is None:
            from .claude_cli import ClaudeCliAdapter
            self._adapter = ClaudeCliAdapter()
        return self._adapter

    # ---- main entry
    def run(self, question: str, route: str, resolution: Optional[QuestionResolution] = None) -> StructuredEvidence:
        t0 = time.monotonic()

        def out(outcome, stage=None, code=None, message=None, details=None, **kw):
            timing = dict(kw.pop("timing", {}))
            timing["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
            err = None if outcome in SUCCESS_OUTCOMES else {"stage": stage, "code": code or outcome, "message": message, "details": details or {}}
            return StructuredEvidence(question, route, outcome, timing=timing, error=err, **kw)

        if route not in (INVESTMENT_ROUTE, PERFORMANCE_ROUTE):
            return out(UNSUPPORTED_ROUTE, "route", message=f"the structured engine handles only investment and performance routes, not {route!r}")

        used, gate = self._entity_gate(route, resolution)
        if gate:
            outcome, code, message, details = gate
            return out(outcome, "entities", code, message, details, entities_used=tuple(used))

        if route == PERFORMANCE_ROUTE:
            miss = check_metric_availability(question)
            if miss:
                return out(UNAVAILABLE_METRIC, "metric_availability", "metric_not_available", miss["message"], miss, entities_used=tuple(used))

        prompt = build_prompt(question, route, [f"- {u['entity_type']}: {u['constraint']}" + (f"  ({u['note']})" if u.get("note") else "") for u in used])
        tl = time.monotonic()
        result = self.adapter.run(prompt)
        llm = {"model": result.model, "cli_version": result.cli_version, "exit_code": result.exit_code, "timing": dict(result.timing),
               "error_class": result.error_class.value if result.error_class else None}
        timing = {"llm_ms": round((time.monotonic() - tl) * 1000, 1)}
        if not result.success:
            return out(LLM_FAILURE, "llm", llm["error_class"], result.error_message, None, entities_used=tuple(used), llm=llm, timing=timing)

        obj, problem = parse_llm_output(result.result_text)
        if obj is None:
            return out(MALFORMED_LLM_OUTPUT, "llm_output", "malformed_llm_output", problem, {"reply_start": (result.result_text or "")[:120]},
                       entities_used=tuple(used), llm=llm, timing=timing)
        sql, explanation = obj.get("sql"), obj.get("explanation")
        if sql is None and obj.get("unsupported_reason"):
            return out(UNSUPPORTED_QUESTION, "llm_output", "unsupported_question", str(obj["unsupported_reason"]), None,
                       explanation=None, entities_used=tuple(used), llm=llm, timing=timing)
        if not isinstance(sql, str) or not sql.strip():
            return out(MISSING_SQL, "llm_output", "missing_sql", "the reply has no usable 'sql' field", {"keys": sorted(obj)},
                       entities_used=tuple(used), llm=llm, timing=timing)
        return self._validate_and_execute(sql, question, route, used, llm, timing, explanation if isinstance(explanation, str) else None, out)

    # ---- SQL only (used by run, and directly by tests)
    def execute_sql(self, sql: str, route: str, question: str = "", entities_used: tuple = ()) -> StructuredEvidence:
        t0 = time.monotonic()

        def out(outcome, stage=None, code=None, message=None, details=None, **kw):
            timing = dict(kw.pop("timing", {}))
            timing["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
            err = None if outcome in SUCCESS_OUTCOMES else {"stage": stage, "code": code or outcome, "message": message, "details": details or {}}
            return StructuredEvidence(question, route, outcome, timing=timing, error=err, **kw)
        return self._validate_and_execute(sql, question, route, list(entities_used), {}, {}, None, out)

    def _validate_and_execute(self, sql, question, route, used, llm, timing, explanation, out) -> StructuredEvidence:
        tv = time.monotonic()
        v: ValidationResult = self.guard.validate(sql)
        issues = list(v.issues)
        if v.ok:
            families = {("investments" if t == INVESTMENTS_TABLE else "performance") for t in v.tables_read}
            if len(families) > 1:
                issues.append(_issue("cross_source_query", "one query may use investments or performance, not both (row multiplication)"))
            elif families and families != ({"investments"} if route == INVESTMENT_ROUTE else {"performance"}):
                issues.append(_issue("route_source_mismatch", f"the {route} route may only query {'investments' if route == INVESTMENT_ROUTE else 'the performance views'}"))
            issues += [_issue("entity_constraint_missing", f"the query does not use the resolved {u['entity_type']} value {u['canonical']!r}")
                       for u in used if not _uses_literal(sql, u)]
        timing = dict(timing, validation_ms=round((time.monotonic() - tv) * 1000, 1))
        if issues:
            return out(SQL_REJECTED, "sql_validation", issues[0].code, "; ".join(i.message for i in issues),
                       {"codes": [i.code for i in issues], "issues": [{"code": i.code, "message": i.message} for i in issues]},
                       sql=sql, explanation=explanation, entities_used=tuple(used), llm=llm, timing=timing)

        te = time.monotonic()
        deadline = te + self.config.query_timeout_s
        self.conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5000)
        try:
            cur = self.conn.execute(sql.strip())
            columns = tuple(d[0] for d in cur.description or ())
            fetched = cur.fetchmany(self.config.max_rows + 1)
        except sqlite3.OperationalError as exc:
            timing["execution_ms"] = round((time.monotonic() - te) * 1000, 1)
            if "interrupt" in str(exc).lower():
                return out(TIMEOUT, "execution", "query_timeout", f"the query exceeded {self.config.query_timeout_s:g} seconds", None,
                           sql=sql, explanation=explanation, entities_used=tuple(used), llm=llm, timing=timing)
            return out(EXECUTION_ERROR, "execution", "execution_error", str(exc), None, sql=sql, explanation=explanation,
                       entities_used=tuple(used), llm=llm, timing=timing)
        except sqlite3.Error as exc:
            timing["execution_ms"] = round((time.monotonic() - te) * 1000, 1)
            return out(EXECUTION_ERROR, "execution", "execution_error", str(exc), None, sql=sql, explanation=explanation,
                       entities_used=tuple(used), llm=llm, timing=timing)
        finally:
            self.conn.set_progress_handler(None, 0)
        timing["execution_ms"] = round((time.monotonic() - te) * 1000, 1)
        truncated = len(fetched) > self.config.max_rows
        rows = tuple(tuple(r) for r in fetched[: self.config.max_rows])
        objects = v.tables_read
        base = tuple(sorted({VIEW_BASE_TABLE.get(o, o) for o in objects}))
        notes = []
        if truncated:
            notes.append(f"result truncated to the first {self.config.max_rows} rows")
        for u in used:
            if u["entity_type"] == "group" and u.get("membership_differs"):
                notes.append("group membership differs across sources; the query uses group_id as stored in the queried table")
        return out(OK if rows else EMPTY_RESULT, sql=sql, explanation=explanation, columns=columns, rows=rows, row_count=len(rows), truncated=truncated,
                   source_tables=base, objects_used=objects, rules_applied=_rules(route, objects), entities_used=tuple(used), llm=llm,
                   timing=timing, notes=tuple(notes))

    # ---- entity gate
    def _entity_gate(self, route: str, resolution: Optional[QuestionResolution]):
        used: list[dict] = []
        if resolution is None:
            return used, None
        required = "investments" if route == INVESTMENT_ROUTE else "performance"
        for status, outcome, code, msg in ((E_AMBIGUOUS, CLARIFICATION_NEEDED, "ambiguous_entity", "an entity mention is ambiguous; nothing is guessed"),
                                           (E_NOT_FOUND, ENTITY_NOT_FOUND, "entity_not_found", "an entity mention was not found; nothing is substituted")):
            bad = [m for m in resolution.mentions if m.resolution.status == status]
            if bad:
                return used, (outcome, code, msg, {"entities": [{"text": m.text, "entity_type": m.resolution.entity_type, "reason": m.resolution.reason,
                                                                   "candidates": [c.value for c in m.resolution.candidates],
                                                                   "candidate_count": m.resolution.details.get("candidate_count", len(m.resolution.candidates))} for m in bad]})
        for m in resolution.mentions:
            r = m.resolution
            if r.status != E_RESOLVED:
                continue
            et, val = r.entity_type, r.canonical
            if et == "client":
                present = r.details.get("sources_present", [])
                if required not in present:
                    return used, (NO_STRUCTURED_DATA, "no_data_for_entity", f"client {val} has no {required} data (present in: {', '.join(present)})",
                                  {"entity": val, "sources_present": present})
                u = {"entity_type": "client", "text": m.text, "canonical": val, "constraint": f"client_id = '{val}'"}
            elif et == "group":
                if required not in r.sources:
                    return used, (NO_STRUCTURED_DATA, "no_data_for_entity", f"group {val} has no {required} data", {"entity": val, "sources": list(r.sources)})
                differs = bool(r.details.get("membership_differs_across_sources"))
                u = {"entity_type": "group", "text": m.text, "canonical": val, "constraint": f"group_id = {val}", "membership_differs": differs,
                     "note": "group identity only; membership differs across sources" if differs else "group identity only"}
            elif et in ("deal_id", "deal_name", "rm"):
                if required != "investments":
                    return used, (NO_STRUCTURED_DATA, "not_in_source", f"{et} exists only in investments, not in {required}", {"entity": val})
                if et == "deal_id":
                    names = list(r.details.get("deal_names", {}))
                    u = {"entity_type": "deal_id", "text": m.text, "canonical": val, "constraint": f"deal_id = '{val}'",
                         "note": f"carries {len(names)} deal names ({', '.join(names)}); query by deal_id, do not collapse into one deal_name"}
                elif et == "deal_name":
                    u = {"entity_type": "deal_name", "text": m.text, "canonical": val, "constraint": f"deal_name = '{val.replace(chr(39), chr(39) * 2)}'"}
                else:
                    u = {"entity_type": "rm", "text": m.text, "canonical": val, "constraint": f"account_rm = '{val}'", "note": "per-record attribute"}
            else:
                continue
            used.append(u)
        return used, None


def _issue(code: str, message: str):
    from .sql_guard import Issue
    return Issue(code, message)


def _uses_literal(sql: str, u: dict) -> bool:
    val = u["canonical"]
    if u["entity_type"] == "group":
        return re.search(rf"(?<![\w.]){re.escape(val)}(?![\w.])", sql) is not None
    return "'" + val.replace("'", "''") + "'" in sql


def _rules(route: str, objects: tuple[str, ...]) -> tuple[str, ...]:
    rules = []
    if route == INVESTMENT_ROUTE:
        rules += ["RM comes from account_rm (record-level)", "client_status is record-level", "row count is distinguished from distinct client/deal counts",
                  "USD field used for cross-record sums"]
    else:
        rules += ["MOIC is compared as a numeric value"]
        if "performance_latest" in objects:
            rules.append("latest snapshot per client (each client's own maximum as_of_date)")
        if "performance_client" in objects:
            rules.append("client snapshot rows only (group rows excluded)")
        if "performance_group" in objects:
            rules.append("group rows only")
    rules.append("read-only execution; single source; no row-level cross-source join")
    return tuple(rules)
