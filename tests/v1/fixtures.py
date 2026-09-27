"""Shared fixtures for the V1 tests: a small synthetic database (real table definitions, real FTS index), a scripted
Claude adapter and fake structured evidence. No Claude CLI call is ever made by these tests.

Hand-computed facts about the synthetic meetings (used by the tests):
  A12345 (group 346): 4 meetings, 2023-11-15 .. 2025-02-20; sectors fintech / healthcare / edtech; largest deal 150M USD (meeting 3), smallest 209k USD (meeting 1)
  B12345 (group 346): 2 meetings;  group 346 has 6 meetings in the meetings source
  B12463 (group 464): 2 meetings (a meeting-only client);  A12350 (group 350): 1 meeting;  A12399 (group 399): 30 meetings
  series_b meetings overall: 2 (meetings 2 and 6);  C12345 has investments and performance rows but no meetings
"""
import datetime as dt
import sqlite3
from pathlib import Path

from src.baseline import data_build, data_schema as ds
from src.baseline import structured_query as sq
from src.baseline.claude_cli import CliResult
from src.baseline.meeting_index import create_meeting_index
from tests.baseline.test_data_layer import investment_row, meeting_row, performance_row, run


def m(mid, client, group, date, summary, action=None, company="Orchid Ventures", sector="fintech", region="EMEA", stage="seed", deal="209k USD"):
    return meeting_row(meeting_id=mid, client_id=client, group_id=group, date=dt.datetime.fromisoformat(date), summary=summary, action_items=action, company=company,
                       sector=sector, region=region, investment_stage=stage, deal_size_estimate=deal, attendees="Rahul Kaur", summary_char_length=len(summary))


MEETINGS = [
    m(1, "A12345", 346, "2024-01-10", "The sponsor lacks a dedicated CFO. Hedging concerns were raised about currency exposure.", None),
    m(2, "A12345", 346, "2024-03-01", "Orion Infrastructure I deal terms were reviewed.", "Action: introduce legal counsel.", company="Summit Advisors", region="APAC", stage="series_b", deal="75M USD"),
    m(3, "A12345", 346, "2023-11-15", "Runway concerns were raised.", None, sector="healthcare", deal="150M USD"),
    m(4, "A12345", 346, "2025-02-20", "Change-of-control clauses could complicate an exit.", "Action: review clauses.", sector="edtech", region="Americas", stage="growth", deal="2M USD"),
    m(5, "B12345", 346, "2024-02-01", "The sponsor lacks a dedicated CFO.", "Action: schedule a management Q&A.", deal="60M USD"),
    m(6, "B12345", 346, "2024-06-01", "Hedging policy requested.", None, sector="real estate", stage="series_b", deal="20M USD"),
    m(7, "B12463", 464, "2023-05-05", "Hedging policy requested. Currency exposure discussed at length.", None, sector="agritech", region="Global", deal="300k USD"),
    m(8, "B12463", 464, "2025-01-12", "Follow-up on currency exposure.", "Action: send model.", sector="agritech", region="Global", stage="growth", deal="5M USD"),
    m(9, "A12350", 350, "2024-04-01", "Change-of-control clauses could complicate an exit.", "Action: review clauses.", region="India", deal="10M USD"),
] + [m(100 + i, "A12399", 399, f"2024-05-{i + 1:02d}", f"Runway concerns were raised in session {i}.", None if i % 2 else f"Action: item {i}.",
       sector="logistics", region="APAC", stage="growth", deal="1M USD") for i in range(30)]


def build_db(path: Path) -> None:
    def inv(cid, gid, rm="Carlos Gomez"):
        return investment_row(Client_Id__c=cid, client_id=cid, **{"Client Name": f"Client_{cid}", "AccountName": f"Client {cid} Holdings", "Client_Group_Id_c": gid, "AccountRM": rm})
    investments = [inv("A12345", 346, "Priya Sharma"), inv("B12345", 346), inv("C12345", 346), inv("A12350", 350), inv("A12399", 399)]
    performances = [performance_row(Client_Id=c, Client_Name=f"Client_{c}", Client_Group_Id=g) for c, g in (("A12345", 346), ("B12345", 346), ("C12345", 346), ("A12350", 350), ("A12399", 399))]
    conn = sqlite3.connect(path)
    for spec, rows in ((ds.INVESTMENTS, investments), (ds.MEETINGS, MEETINGS), (ds.PERFORMANCE, performances)):
        data_build._create_and_load(conn, spec, run(spec, rows))
    create_meeting_index(conn)
    conn.commit()
    conn.close()


def cli_ok(text):
    return CliResult(success=True, result_text=text, model="claude-sonnet-5", cli_version="2.1.283", exit_code=0, timing={"wall_clock_ms": 1.0})


class ScriptedAdapter:
    """Returns the queued replies in order and records every prompt. Raises if called more often than scripted (no hidden retries)."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def run(self, prompt):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("the adapter was called more often than scripted")
        r = self.replies.pop(0)
        return r if isinstance(r, CliResult) else cli_ok(r)


class NoCalls:
    def run(self, prompt):
        raise AssertionError("Claude must not be called here")


def sql_reply(sql, explanation="test"):
    import json
    return json.dumps({"sql": sql, "explanation": explanation})


def s_ev(rows=((3,),), columns=("n",), source="performance", route="performance", outcome=sq.OK):
    return sq.StructuredEvidence("q", route, outcome, sql="SELECT ...", columns=columns, rows=tuple(rows), row_count=len(rows), source_tables=(source,),
                                 objects_used=(source,), rules_applied=("rule applied",))


class FakeEngine:
    def __init__(self, evidence):
        self.evidence, self.calls = evidence, []

    def run(self, question, route, resolution=None):
        self.calls.append((question, route))
        return self.evidence
