"""V2 embedding index: distinct meeting sentences, their meeting mapping, persisted vectors and a manifest.

Layout (default data/derived/v2_embeddings/, gitignored, rebuildable):
  index.sqlite    sentences(sentence_id, text), meeting_sentences(meeting_id, pos, sentence_id)
  vectors.f32     little-endian float32, shape (sentence_count, dimensions), row i = sentence_id i
  manifest.json   model, project/location, dimensions, formats, counts, source fingerprint, checksums, timestamps, versions
  progress/       resumable chunks while a build is running (config.json + chunk_NNNNNN.npy); removed on success

Sentences are embedded exactly as stored (raw text, mojibake included) once each; only the document format prefix is added.
An index or a partial build is never mixed across configurations: a different model/dimension/format/source is refused.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from src.baseline.data_build import DEFAULT_DB
from .vertex import DOC_FORMAT, QUERY_FORMAT, VertexError

INDEX_FORMAT = 1
DEFAULT_INDEX_DIR = Path(DEFAULT_DB).parent / "v2_embeddings"
_SPLIT = re.compile(r"(?<=[.!?])\s+")
SPLITTER = r"re.split((?<=[.!?])\s+) on meetings.summary, strip, drop empty; exact-text de-duplication (v1)"


class BuildError(Exception):
    pass


def split_sentences(summary: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(summary or "") if s.strip()]


@dataclass(frozen=True)
class Corpus:
    sentences: list[str]                         # distinct raw sentences, ids 0..N-1 in first-seen order
    rows: list[tuple[int, int, int]]             # (meeting_id, pos, sentence_id) for every occurrence
    fingerprint: str                             # sha256 over (meeting_id, summary) of the source table
    meetings: int

    @property
    def occurrences(self) -> int:
        return len(self.rows)


def collect_corpus(db_path: Path) -> Corpus:
    conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        data = conn.execute("SELECT meeting_id, summary FROM meetings ORDER BY meeting_id").fetchall()
    finally:
        conn.close()
    h = hashlib.sha256()
    ids: dict[str, int] = {}
    sentences: list[str] = []
    rows: list[tuple[int, int, int]] = []
    for mid, summary in data:
        h.update(f"{mid}\t{summary}\n".encode("utf-8"))
        for pos, s in enumerate(split_sentences(summary)):
            sid = ids.get(s)
            if sid is None:
                sid = ids[s] = len(sentences)
                sentences.append(s)
            rows.append((int(mid), pos, sid))
    return Corpus(sentences, rows, h.hexdigest(), len(data))


def source_fingerprint(db_path: Path) -> str:
    return collect_corpus(db_path).fingerprint


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def index_config(vertex_config, corpus: Corpus, limit: Optional[int]) -> dict:
    return {"index_format": INDEX_FORMAT, **vertex_config.as_dict(), "splitter": SPLITTER, "source_fingerprint": corpus.fingerprint,
            "sentence_count": len(corpus.sentences) if limit is None else min(limit, len(corpus.sentences)), "partial": limit is not None}


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_index_sqlite(path: Path, corpus: Corpus, count: int) -> None:
    tmp = path.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript("CREATE TABLE sentences (sentence_id INTEGER PRIMARY KEY, text TEXT NOT NULL);"
                           "CREATE TABLE meeting_sentences (meeting_id INTEGER NOT NULL, pos INTEGER NOT NULL, sentence_id INTEGER NOT NULL, PRIMARY KEY (meeting_id, pos));"
                           "CREATE INDEX idx_ms_sentence ON meeting_sentences (sentence_id);")
        conn.executemany("INSERT INTO sentences VALUES (?, ?)", list(enumerate(corpus.sentences[:count])))
        conn.executemany("INSERT INTO meeting_sentences VALUES (?, ?, ?)", [r for r in corpus.rows if r[2] < count])
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)


def build_index(db_path: Path, out_dir: Path, embedder: Any, *, workers: int = 16, chunk_size: int = 400, restart: bool = False, limit: Optional[int] = None,
                software: Optional[dict] = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Build (or resume) the index. Every API failure stops the build with a BuildError; nothing is retried; finished chunks are kept."""
    out_dir = Path(out_dir)
    corpus = collect_corpus(db_path)
    config = index_config(embedder.config, corpus, limit)
    n = config["sentence_count"]
    manifest_path, progress = out_dir / "manifest.json", out_dir / "progress"
    if restart and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("config") == config and (out_dir / "vectors.f32").exists():
            log("index is already built for this configuration")
            return existing
        raise BuildError("an index for a different configuration exists in " + str(out_dir) + "; embeddings are never mixed: rebuild with restart=True")
    cfg_file = progress / "config.json"
    if progress.exists() and cfg_file.exists() and json.loads(cfg_file.read_text(encoding="utf-8")) != {"config": config, "chunk_size": chunk_size}:
        raise BuildError("partial progress belongs to a different configuration; embeddings are never mixed: rebuild with restart=True")
    progress.mkdir(exist_ok=True)
    _atomic_write(cfg_file, json.dumps({"config": config, "chunk_size": chunk_size}, indent=1).encode("utf-8"))
    write_index_sqlite(out_dir / "index.sqlite", corpus, n)

    t0 = time.monotonic()
    starts = list(range(0, n, chunk_size))
    resumed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for start in starts:
            end = min(start + chunk_size, n)
            chunk_file = progress / f"chunk_{start:06d}.npy"
            if chunk_file.exists():
                arr = np.load(chunk_file)
                if arr.shape == (end - start, config["dimensions"]) and np.isfinite(arr).all():
                    resumed += 1
                    continue
                chunk_file.unlink()
            futures = {pool.submit(embedder.embed_document, corpus.sentences[i]): i for i in range(start, end)}
            vecs: dict[int, np.ndarray] = {}
            failure: Optional[tuple[int, VertexError]] = None
            for fut in as_completed(futures):
                try:
                    vecs[futures[fut]] = fut.result()
                except VertexError as exc:
                    failure = (futures[fut], exc)
                    for other in futures:
                        other.cancel()
                    break
            if failure is not None:
                raise BuildError(f"the embedding request for sentence {failure[0]} (chunk {start}-{end}) failed ({failure[1]}); finished chunks are kept in {progress}; re-run to resume")
            arr = np.vstack([vecs[i] for i in range(start, end)]).astype(np.float32)
            if arr.shape != (end - start, config["dimensions"]) or not np.isfinite(arr).all():
                raise BuildError(f"chunk {start}-{end} has an invalid shape or non-finite values")
            tmp = chunk_file.with_suffix(".tmp.npy")
            np.save(tmp, arr)
            os.replace(tmp, chunk_file)
            log(f"chunk {start}-{end} done ({end}/{n} sentences, {time.monotonic() - t0:.0f}s)")

    matrix = np.vstack([np.load(progress / f"chunk_{s:06d}.npy") for s in starts]).astype("<f4")
    if matrix.shape != (n, config["dimensions"]) or not np.isfinite(matrix).all():
        raise BuildError("the assembled matrix has an invalid shape or non-finite values")
    vec_path = out_dir / "vectors.f32"
    tmp = vec_path.with_suffix(".f32.tmp")
    matrix.tofile(tmp)
    os.replace(tmp, vec_path)
    manifest = {"config": config, "model": config["model"], "vertex": {"project": config["project"], "location": config["location"]},
                "dimensions": config["dimensions"], "doc_format": DOC_FORMAT, "query_format": QUERY_FORMAT,
                "counts": {"sentences": n, "sentence_occurrences": len([r for r in corpus.rows if r[2] < n]), "meetings": corpus.meetings},
                "source": {"table": "meetings", "fingerprint_sha256": corpus.fingerprint, "database": Path(db_path).name},
                "vectors": {"file": "vectors.f32", "dtype": "float32-le", "shape": [n, config["dimensions"]], "bytes": vec_path.stat().st_size, "sha256": file_sha256(vec_path)},
                "index_sqlite": {"file": "index.sqlite", "sha256": file_sha256(out_dir / "index.sqlite")},
                "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "build": {"api_requests": getattr(embedder, "requests", None), "workers": workers, "chunk_size": chunk_size, "resumed_chunks": resumed, "chunks": len(starts),
                          "seconds": round(time.monotonic() - t0, 1)},
                "software": {"python": sys.version.split()[0], "numpy": np.__version__, **(software or {})},
                "partial": limit is not None}
    _atomic_write(manifest_path, json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8"))
    shutil.rmtree(progress, ignore_errors=True)
    return manifest
