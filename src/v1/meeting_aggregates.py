"""V1-2: guarded, read-only aggregates over the meetings table (metadata only).

question + resolution -> entity gate -> Claude CLI SQL generation (one call, no retry)
  -> SQL validation (frozen static scan + authorizer; exactly one approved object: meetings_meta)
  -> read-only SQLite execution (bounded rows, timeout) -> MeetingAggregateEvidence

Same safety pattern as the Baseline structured engine, which is reused (guard, JSON parsing) and not modified.
The view exposes no narrative text (no summary, attendees or action_items). Text questions stay with retrieval.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.baseline.data_build import DEFAULT_DB
from src.baseline.entity_resolution import AMBIGUOUS as E_AMBIGUOUS, NOT_FOUND as E_NOT_FOUND, RESOLVED as E_RESOLVED, QuestionResolution
from src.baseline.sql_guard import Issue, SqlGuard, _mask
from src.baseline.structured_query import parse_llm_output

VIEW = "meetings_meta"
VIEW_COLUMNS = ("meeting_id", "client_id", "group_id", "meeting_date", "company", "sector", "region", "stage", "deal_size_estimate_raw", "deal_size_estimate_usd")
VIEW_DDL = (f"CREATE TEMP VIEW {VIEW} AS SELECT meeting_id, client_id, group_id, meeting_date, company, sector, region, investment_stage AS stage, "
            "deal_size_estimate AS deal_size_estimate_raw, "
            "CASE WHEN deal_size_estimate_parse_status = 'ok' THEN deal_size_estimate_usd END AS deal_size_estimate_usd FROM meetings "
            "WHERE meeting_id IS NOT NULL")     # no-op predicate: keeps every read attributed to the view (a bare COUNT(*) would otherwise look like a base-table read)
HYBRID_SUFFIX = " [Answer only the meeting part of this question; the other part is answered separately from another source.]"

OK, EMPTY_RESULT = "ok", "empty_result"
CLARIFICATION_NEEDED, ENTITY_NOT_FOUND, NO_MEETING_DATA = "clarification_needed", "entity_not_found", "no_meeting_data"
UNSUPPORTED_QUESTION = "unsupported_question"
LLM_FAILURE, MALFORMED_LLM_OUTPUT, MISSING_SQL = "llm_failure", "malformed_llm_output", "missing_sql"
SQL_REJECTED, EXECUTION_ERROR, TIMEOUT = "sql_rejected", "execution_error", "timeout"
SUCCESS_OUTCOMES = (OK, EMPTY_RESULT)


@dataclass(frozen=True)
class AggregateConfig:
    db_path: Path = DEFAULT_DB
    max_rows: int = 50
    query_timeout_s: float = 10.0


@dataclass(frozen=True)
class MeetingAggregateEvidence:
    question: str
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
        return {"question": self.question, "outcome": self.outcome, "success": self.success, "sql": self.sql, "explanation": self.explanation,
                "columns": list(self.columns), "rows": [list(r) for r in self.rows], "row_count": self.row_count, "truncated": self.truncated,
                "source_tables": list(self.source_tables), "objects_used": list(self.objects_used), "rules_applied": list(self.rules_applied),
                "entities_used": list(self.entities_used), "timing_ms": self.timing, "llm": self.llm, "error": self.error, "notes": list(self.notes)}


_SCHEMA = """Approved view (the ONLY object you may query): meetings_meta -- one row per meeting, metadata only
  meeting_id INTEGER            meeting identifier
  client_id TEXT                client identifier such as A12345
  group_id INTEGER              group identifier as stored in the meetings source (meeting-source membership)
  meeting_date TEXT             ISO date text YYYY-MM-DD (compare and order as text)
  company TEXT                  the company discussed in the meeting; NOT an investment deal
  sector TEXT                   one of: {sectors}
  region TEXT                   one of: {regions}
  stage TEXT                    one of: {stages}
  deal_size_estimate_raw TEXT   deal size as written, e.g. '150M USD'
  deal_size_estimate_usd REAL   the same value as a number in USD; NULL when it could not be parsed
Companies: {companies}
The view holds metadata only: nothing about what was said in a meeting, and no RM."""

_RULES = """- One SELECT (or WITH ... SELECT) statement over meetings_meta only. No comments. No joins to anything else.
- Supported shapes only: count of meetings; earliest / latest meeting date or date range (MIN, MAX); distinct values of sector, region, stage or company (SELECT DISTINCT or GROUP BY with COUNT(*)); largest or smallest deal_size_estimate_usd (ORDER BY ... LIMIT 1, tie-break meeting_id ASC); counts under filters.
- COUNT(*) counts MEETINGS. Match sector, region, stage and company with = and the exact value from the lists above.
- For a largest/smallest deal size return meeting_id, client_id, meeting_date, deal_size_estimate_raw and deal_size_estimate_usd, and ignore rows where deal_size_estimate_usd IS NULL.
- Use the resolved entity constraints exactly as written. Do not filter by deal, RM or anything not in the view.
- If the question asks about what was said in meetings (a topic), or needs any other source, output the unsupported form."""


def build_prompt(question: str, entity_lines: list[str], vocab: dict) -> str:
    entities = "\n".join(entity_lines) if entity_lines else "none (all meetings)"
    schema = _SCHEMA.format(sectors=", ".join(vocab["sector"]), regions=", ".join(vocab["region"]), stages=", ".join(vocab["stage"]), companies=", ".join(vocab["company"]))
    return f"""Task: write ONE read-only SQLite query that answers the question from the approved view.
Output: ONLY a JSON object, nothing else: {{"sql": "<the query>", "explanation": "<at most 15 words>"}}
If the approved view cannot answer the question, output {{"sql": null, "unsupported_reason": "<short reason>"}}.

Question: {question}

Resolved entities (already verified; use exactly these values):
{entities}

{schema}

Rules:
{_RULES}
"""


class MeetingAggregateEngine:
    def __init__(self, config: AggregateConfig = AggregateConfig(), adapter: Any = None) -> None:
        self.config = config
        self._adapter = adapter
        db = Path(config.db_path)
        if not db.is_file():
            raise FileNotFoundError(f"Baseline database not found: {db} (build it with: python -m src.baseline.build_db)")
        self.conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        self.conn.execute(VIEW_DDL)
        self.vocab = {col: [r[0] for r in self.conn.execute(f"SELECT DISTINCT {col} FROM {VIEW} WHERE {col} IS NOT NULL ORDER BY 1")] for col in ("sector", "region", "stage", "company")}
        self.conn.execute("PRAGMA query_only = ON")
        self.guard = SqlGuard(self.conn, {VIEW: set(VIEW_COLUMNS)}, {VIEW})     # only the view and its ten columns are approved; no base table
        self.guard.install()

    def close(self) -> None:
        self.conn.close()

    @property
    def adapter(self):
        if self._adapter is None:
            from src.baseline.claude_cli import ClaudeCliAdapter
            self._adapter = ClaudeCliAdapter()
        return self._adapter

    def run(self, question: str, resolution: Optional[QuestionResolution] = None) -> MeetingAggregateEvidence:
        t0 = time.monotonic()

        def out(outcome, stage=None, code=None, message=None, details=None, **kw):
            timing = dict(kw.pop("timing", {}))
            timing["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
            err = None if outcome in SUCCESS_OUTCOMES else {"stage": stage, "code": code or outcome, "message": message, "details": details or {}}
            return MeetingAggregateEvidence(question, outcome, timing=timing, error=err, **kw)

        used, gate = self._entity_gate(resolution)
        if gate:
            outcome, code, message, details = gate
            return out(outcome, "entities", code, message, details, entities_used=tuple(used))
        prompt = build_prompt(question, [f"- {u['entity_type']}: {u['constraint']}" + (f"  ({u['note']})" if u.get("note") else "") for u in used], self.vocab)
        tl = time.monotonic()
        result = self.adapter.run(prompt)
        llm = {"model": result.model, "cli_version": result.cli_version, "exit_code": result.exit_code, "timing": dict(result.timing),
               "error_class": result.error_class.value if result.error_class else None}
        timing = {"llm_ms": round((time.monotonic() - tl) * 1000, 1)}
        if not result.success:
            return out(LLM_FAILURE, "llm", llm["error_class"], result.error_message, None, entities_used=tuple(used), llm=llm, timing=timing)
        obj, problem = parse_llm_output(result.result_text)
        if obj is None:
            return out(MALFORMED_LLM_OUTPUT, "llm_output", "malformed_llm_output", problem, {"reply_start": (result.result_text or "")[:120]}, entities_used=tuple(used), llm=llm, timing=timing)
        sql, explanation = obj.get("sql"), obj.get("explanation")
        if sql is None and obj.get("unsupported_reason"):
            return out(UNSUPPORTED_QUESTION, "llm_output", "unsupported_question", str(obj["unsupported_reason"]), None, entities_used=tuple(used), llm=llm, timing=timing)
        if not isinstance(sql, str) or not sql.strip():
            return out(MISSING_SQL, "llm_output", "missing_sql", "the reply has no usable 'sql' field", {"keys": sorted(obj)}, entities_used=tuple(used), llm=llm, timing=timing)
        return self._validate_and_execute(sql, question, used, llm, timing, explanation if isinstance(explanation, str) else None, out)

    def execute_sql(self, sql: str, question: str = "", entities_used: tuple = ()) -> MeetingAggregateEvidence:
        """SQL only (no LLM): used by tests. Same validation and execution as `run`."""
        t0 = time.monotonic()

        def out(outcome, stage=None, code=None, message=None, details=None, **kw):
            timing = dict(kw.pop("timing", {}))
            timing["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
            err = None if outcome in SUCCESS_OUTCOMES else {"stage": stage, "code": code or outcome, "message": message, "details": details or {}}
            return MeetingAggregateEvidence(question, outcome, timing=timing, error=err, **kw)
        return self._validate_and_execute(sql, question, list(entities_used), {}, {}, None, out)

    def _validate_and_execute(self, sql, question, used, llm, timing, explanation, out) -> MeetingAggregateEvidence:
        tv = time.monotonic()
        v = self.guard.validate(sql)
        issues = list(v.issues)
        masked = _mask(sql.strip())[0] if isinstance(sql, str) else None
        if masked and re.search(r"\bjoin\b", masked, re.I):
            issues.append(Issue("join_not_allowed", "joins are not allowed: a meeting aggregate is a single query over one view"))
        if v.ok:
            if set(v.tables_read) != {VIEW}:
                issues.append(Issue("single_source_only", f"a query may read only {VIEW}; it read: {', '.join(v.tables_read) or 'nothing'}"))
            issues += [Issue("entity_constraint_missing", f"the query does not use the resolved {u['entity_type']} value {u['canonical']!r}") for u in used if not _uses_literal(sql, u)]
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
                return out(TIMEOUT, "execution", "query_timeout", f"the query exceeded {self.config.query_timeout_s:g} seconds", None, sql=sql, explanation=explanation, entities_used=tuple(used), llm=llm, timing=timing)
            return out(EXECUTION_ERROR, "execution", "execution_error", str(exc), None, sql=sql, explanation=explanation, entities_used=tuple(used), llm=llm, timing=timing)
        except sqlite3.Error as exc:
            timing["execution_ms"] = round((time.monotonic() - te) * 1000, 1)
            return out(EXECUTION_ERROR, "execution", "execution_error", str(exc), None, sql=sql, explanation=explanation, entities_used=tuple(used), llm=llm, timing=timing)
        finally:
            self.conn.set_progress_handler(None, 0)
        timing["execution_ms"] = round((time.monotonic() - te) * 1000, 1)
        truncated = len(fetched) > self.config.max_rows
        rows = tuple(tuple(r) for r in fetched[: self.config.max_rows])
        notes = [f"result truncated to the first {self.config.max_rows} rows"] if truncated else []
        for u in used:
            if u["entity_type"] == "group":
                notes.append("group membership is the meetings source's own group_id; nothing is imported from investments or performance")
        rules = ("aggregate over meeting metadata only (no meeting text)", "group membership from the meetings source only",
                 "deal size uses the parsed USD value; unparsed values are excluded", "read-only execution; single source; no cross-source join")
        return out(OK if rows else EMPTY_RESULT, sql=sql, explanation=explanation, columns=columns, rows=rows, row_count=len(rows), truncated=truncated,
                   source_tables=("meetings",), objects_used=(VIEW,), rules_applied=rules, entities_used=tuple(used), llm=llm, timing=timing, notes=tuple(notes))

    def _entity_gate(self, resolution: Optional[QuestionResolution]):
        used: list[dict] = []
        if resolution is None:
            return used, None
        for status, outcome, code, msg in ((E_AMBIGUOUS, CLARIFICATION_NEEDED, "ambiguous_entity", "an entity mention is ambiguous; nothing is guessed"),
                                           (E_NOT_FOUND, ENTITY_NOT_FOUND, "entity_not_found", "an entity mention was not found; nothing is substituted")):
            bad = [m for m in resolution.mentions if m.resolution.status == status
                   and not (status == E_AMBIGUOUS and not any(c.entity_type for c in (m.resolution.all_candidates or m.resolution.candidates)))]   # words that are only meeting text do not block
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
                if "meetings" not in r.details.get("sources_present", []):
                    return used, (NO_MEETING_DATA, "no_data_for_entity", f"client {val} has no meeting records", {"entity": val})
                used.append({"entity_type": "client", "text": m.text, "canonical": val, "constraint": f"client_id = '{val}'"})
            elif et == "group":
                if "meetings" not in r.sources:
                    return used, (NO_MEETING_DATA, "no_data_for_entity", f"group {val} has no meeting records", {"entity": val})
                used.append({"entity_type": "group", "text": m.text, "canonical": val, "constraint": f"group_id = {val}",
                             "note": "group membership is the meetings source's own"})
            elif et == "rm":
                return used, (UNSUPPORTED_QUESTION, "rm_meeting_link", "meetings have no RM field and no RM-to-meeting link is documented", {"rm": val})
            elif et in ("deal_id", "deal_name"):
                return used, (UNSUPPORTED_QUESTION, "deal_not_in_meetings", f"meetings carry no deal field, so {et} '{val}' cannot filter or attribute meetings", {"entity": val})
        return used, None


def _uses_literal(sql: str, u: dict) -> bool:
    val = u["canonical"]
    if u["entity_type"] == "group":
        return re.search(rf"(?<![\w.]){re.escape(val)}(?![\w.])", sql) is not None
    return "'" + val.replace("'", "''") + "'" in sql
