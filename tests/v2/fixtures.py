"""Fixtures for the V2 tests: a small synthetic database, a deterministic fake embedder that simulates paraphrase similarity
(words of the same concept map to the same dimension), and scripted Vertex HTTP responses. No Vertex or Claude call is made.

Synthetic facts used by the tests:
  A12345 (group 346): m1 CFO+hedging, m2 legal+board, m3 runway+head of finance, m6 mojibake attendee/figure
  B12345 (group 346): m4 = the same CFO sentence as m1 and the same board sentence as m2 (duplicate sentences across meetings)
  A12350: m5 change-of-control;   C12399: m20..m25 share ONE identical sentence (exact semantic ties), different dates
"""
import datetime as dt
import json
import re
import sqlite3
import zlib
from pathlib import Path

import numpy as np

from src.baseline import data_build, data_schema as ds
from src.baseline.meeting_index import create_meeting_index
from src.v2.vertex import VertexConfig, VertexError
from tests.baseline.test_data_layer import investment_row, meeting_row, performance_row, run

DIM = 16
CFG = VertexConfig(dimensions=DIM)
CFO_SENT = "The sponsor currently lacks a dedicated CFO and is outsourcing treasury functions."
BOARD_SENT = "Board composition was reviewed at length."
MOJI_1 = "Sanjay LÃ³pez joined the review."
MOJI_2 = "Sensitivity to a Â±128 bps change in growth was shown."
TIE_SENT = "Currency exposure is significant with hedging requested."


def m(mid, client, group, date, summary, action=None):
    return meeting_row(meeting_id=mid, client_id=client, group_id=group, date=dt.datetime.fromisoformat(date), summary=summary, action_items=action,
                       attendees="Rahul Kaur", summary_char_length=len(summary))


MEETINGS = [
    m(1, "A12345", 346, "2024-01-10", f"{CFO_SENT} Hedging policy was requested."),
    m(2, "A12345", 346, "2024-03-01", f"Legal counsel will draft the shareholder agreement. {BOARD_SENT}", "Action: introduce legal counsel."),
    m(3, "A12345", 346, "2024-05-20", "Runway concerns were raised. The team plans to hire a head of finance."),
    m(4, "B12345", 346, "2024-02-01", f"{CFO_SENT} {BOARD_SENT}"),
    m(5, "A12350", 350, "2024-04-01", "Change-of-control clauses were flagged by counsel."),
    m(6, "A12345", 346, "2024-06-15", f"{MOJI_1} {MOJI_2}"),
] + [m(20 + i, "C12399", 399, f"2024-07-{i + 1:02d}", TIE_SENT) for i in range(6)]

CONCEPTS = [{"cfo", "finance", "treasury", "chief", "outsourcing", "outsources", "providers"}, {"hedging", "currency", "exposure"},
            {"legal", "shareholder", "agreement", "counsel"}, {"board", "governance", "composition", "directors"}, {"runway", "burn"}, {"clauses", "control"}]


def fake_vector(text: str) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for tok in re.findall(r"[^\W_]+", text.lower()):
        for i, group in enumerate(CONCEPTS):
            if tok in group:
                v[i] += 1.0
                break
        else:
            v[8 + zlib.crc32(tok.encode("utf-8")) % 8] += 0.05
    if not v.any():
        v[15] = 1.0
    return (v / np.linalg.norm(v)).astype(np.float32)


class FakeEmbedder:
    """Same interface as VertexEmbedder. Counts calls; never touches the network."""

    def __init__(self, fail_query=False, fail_docs_after=None):
        self.config = CFG
        self.requests, self.doc_calls, self.query_calls = 0, [], []
        self.fail_query, self.fail_docs_after = fail_query, fail_docs_after

    def embed_document(self, text):
        if self.fail_docs_after is not None and len(self.doc_calls) >= self.fail_docs_after:
            raise VertexError("http", "RESOURCE_EXHAUSTED: quota", 429)
        self.requests += 1
        self.doc_calls.append(text)
        return fake_vector(text)

    def embed_query(self, text):
        if self.fail_query:
            raise VertexError("http", "PERMISSION_DENIED: nope", 403)
        self.requests += 1
        self.query_calls.append(text)
        return fake_vector(text)


def build_db(path: Path) -> None:
    def inv(cid, gid):
        return investment_row(Client_Id__c=cid, client_id=cid, **{"Client Name": f"Client_{cid}", "AccountName": f"Client {cid} Holdings", "Client_Group_Id_c": gid, "AccountRM": "Carlos Gomez"})
    investments = [inv("A12345", 346), inv("B12345", 346), inv("C12345", 346), inv("A12350", 350), inv("C12399", 399)]
    performances = [performance_row(Client_Id=c, Client_Name=f"Client_{c}", Client_Group_Id=g) for c, g in (("A12345", 346), ("B12345", 346), ("C12345", 346), ("A12350", 350), ("C12399", 399))]
    conn = sqlite3.connect(path)
    for spec, rows in ((ds.INVESTMENTS, investments), (ds.MEETINGS, MEETINGS), (ds.PERFORMANCE, performances)):
        data_build._create_and_load(conn, spec, run(spec, rows))
    create_meeting_index(conn)
    conn.commit()
    conn.close()


def vertex_ok(values):
    return 200, json.dumps({"embedding": {"values": values}, "usageMetadata": {"promptTokenCount": 5}}).encode()
