"""V2 semantic index: validated loading and per-meeting best-sentence scoring inside an explicit scope.

The scope (meeting ids that passed the entity/group/date filters) is decided before this module runs and is the only
set of meetings it can return. A meeting's score is the highest cosine similarity among its distinct sentences; the
best sentence is the first (lowest position) sentence with that score. Nothing about a query is stored.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from .embedding_index import INDEX_FORMAT, file_sha256


class IndexError_(Exception):
    """The index is missing, inconsistent with its manifest, or built for another configuration/source."""


@dataclass(frozen=True)
class SemanticMatch:
    meeting_id: int
    score: float
    sentence_id: int
    sentence_pos: int


class SemanticIndex:
    """`sentence_text()` is called at answer time (one lookup per cited sentence), so its
    connection is opened lazily, once per calling thread, the same way as MeetingRetriever and
    StructuredQueryEngine (a sqlite3.Connection may only be used by the thread that created it,
    and this index may be built once and then queried from several threads, e.g. a cached UI
    resource). `load()`'s own validation connection is a short-lived local, closed before this
    constructor ever runs, so it never needs this treatment."""

    def __init__(self, directory: Path, manifest: dict, vectors: np.ndarray, offsets: dict[int, tuple[int, int]], sids: np.ndarray, poss: np.ndarray, sentence_db_path: Path) -> None:
        self.directory, self.manifest, self.vectors = directory, manifest, vectors
        self._offsets, self._sids, self._poss, self._sentence_db_path = offsets, sids, poss, sentence_db_path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        if getattr(self._local, "conn", None) is None:
            self._local.conn = sqlite3.connect(f"{self._sentence_db_path.resolve().as_uri()}?mode=ro", uri=True)
        return self._local.conn

    @property
    def dimensions(self) -> int:
        return int(self.manifest["dimensions"])

    @classmethod
    def load(cls, directory: Path, *, expected_config: Optional[dict] = None, expected_fingerprint: Optional[str] = None, allow_partial: bool = False,
             verify_checksums: bool = True) -> "SemanticIndex":
        d = Path(directory)
        mpath = d / "manifest.json"
        if not mpath.is_file():
            raise IndexError_(f"no embedding index at {d} (build it with: python -m src.v2.build_embeddings)")
        m = json.loads(mpath.read_text(encoding="utf-8"))
        if m.get("config", {}).get("index_format") != INDEX_FORMAT:
            raise IndexError_("unsupported index format")
        if m.get("partial") and not allow_partial:
            raise IndexError_("the index is a partial (limited) build and cannot be used for retrieval")
        if expected_config is not None:
            for k, v in expected_config.items():
                if m["config"].get(k) != v:
                    raise IndexError_(f"the index was built with {k}={m['config'].get(k)!r}, not {v!r}; embeddings are never mixed across configurations")
        if expected_fingerprint is not None and m["source"]["fingerprint_sha256"] != expected_fingerprint:
            raise IndexError_("the index was built from a different meetings table (source fingerprint differs); rebuild it")
        vpath, spath = d / m["vectors"]["file"], d / m["index_sqlite"]["file"]
        for p in (vpath, spath):
            if not p.is_file():
                raise IndexError_(f"missing index file {p.name}")
        if verify_checksums and (file_sha256(vpath) != m["vectors"]["sha256"] or file_sha256(spath) != m["index_sqlite"]["sha256"]):
            raise IndexError_("an index file does not match the checksum in its manifest")
        n, dim = m["vectors"]["shape"]
        raw = np.fromfile(vpath, dtype="<f4")
        if raw.size != n * dim:
            raise IndexError_(f"vector file holds {raw.size} values, expected {n} x {dim}")
        vectors = raw.reshape(n, dim).astype(np.float32)
        if not np.isfinite(vectors).all():
            raise IndexError_("the vector file contains non-finite values")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms == 0).any():
            raise IndexError_("the vector file contains a zero vector")
        vectors = vectors / norms                                  # cosine similarity == dot product
        conn = sqlite3.connect(f"{spath.resolve().as_uri()}?mode=ro", uri=True)     # scratch connection: used only for this validation, closed below
        try:
            count = conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
            if count != n or m["counts"]["sentences"] != n:
                raise IndexError_(f"the sentence table has {count} rows, the vectors {n}")
            rows = conn.execute("SELECT meeting_id, pos, sentence_id FROM meeting_sentences ORDER BY meeting_id, pos").fetchall()
            if rows and max(r[2] for r in rows) >= n:
                raise IndexError_("a meeting refers to a sentence id without a vector")
        finally:
            conn.close()
        mids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        sids = np.fromiter((r[2] for r in rows), dtype=np.int64, count=len(rows))
        poss = np.fromiter((r[1] for r in rows), dtype=np.int64, count=len(rows))
        offsets: dict[int, tuple[int, int]] = {}
        if len(rows):
            bounds = np.flatnonzero(np.diff(mids)) + 1
            starts = np.concatenate(([0], bounds))
            ends = np.concatenate((bounds, [len(rows)]))
            offsets = {int(mids[s]): (int(s), int(e)) for s, e in zip(starts, ends)}
        return cls(d, m, vectors, offsets, sids, poss, spath)

    def close(self) -> None:
        """Closes this thread's sentence-lookup connection only, if this thread ever opened one."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def sentence_text(self, sentence_id: int) -> str:
        row = self._conn().execute("SELECT text FROM sentences WHERE sentence_id = ?", (int(sentence_id),)).fetchone()
        if row is None:
            raise IndexError_(f"unknown sentence id {sentence_id}")
        return row[0]

    def has_meeting(self, meeting_id: int) -> bool:
        return int(meeting_id) in self._offsets

    def search(self, query_vector: np.ndarray, scope_meeting_ids: Iterable[int]) -> list[SemanticMatch]:
        """Best sentence per meeting for the meetings in scope (and only those). Unordered; ranking is done by the caller."""
        q = np.asarray(query_vector, dtype=np.float32)
        if q.shape != (self.dimensions,) or not np.isfinite(q).all():
            raise ValueError(f"the query vector must be {self.dimensions} finite values")
        nq = float(np.linalg.norm(q))
        if nq == 0:
            raise ValueError("the query vector is zero")
        q = q / nq
        scope = [int(m) for m in scope_meeting_ids if int(m) in self._offsets]
        if not scope:
            return []
        needed = np.unique(np.concatenate([self._sids[self._offsets[m][0]:self._offsets[m][1]] for m in scope]))
        scores = np.zeros(self.vectors.shape[0], dtype=np.float32)
        scores[needed] = self.vectors[needed] @ q                    # one score per distinct sentence: identical sentences share one exact value
        out = []
        for m in scope:
            s, e = self._offsets[m]
            local = scores[self._sids[s:e]]
            k = int(np.argmax(local))                                # first maximum = lowest position: deterministic
            out.append(SemanticMatch(m, float(local[k]), int(self._sids[s + k]), int(self._poss[s + k])))
        return out
