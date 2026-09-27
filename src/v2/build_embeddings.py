"""Build the V2 embedding index:  python -m src.v2.build_embeddings [options]

  --test-batch N   embed only the first N distinct sentences into a temporary directory, verify dimension / finiteness / id mapping, print the result
  --workers W      concurrent requests (default 16)
  --restart        discard an existing index or partial progress first
  --dry-run        count sentences and estimate the work; no API call

Requires: gcloud authenticated (gcloud auth login), project investcorp-assignment with aiplatform.googleapis.com enabled, and the
Baseline database (python -m src.baseline.build_db). No Python package beyond numpy is needed.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from src.baseline.data_build import DEFAULT_DB
from .embedding_index import BuildError, DEFAULT_INDEX_DIR, build_index, collect_corpus
from .vertex import VertexConfig, VertexEmbedder, VertexError, find_gcloud


def _gcloud_version() -> str:
    try:
        return subprocess.run([find_gcloud(), "--version"], capture_output=True, text=True, timeout=60).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, VertexError, IndexError):
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--chunk-size", type=int, default=400)
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--test-batch", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    corpus = collect_corpus(Path(a.db))
    print(f"meetings {corpus.meetings}; sentence occurrences {corpus.occurrences}; distinct sentences {len(corpus.sentences)}; source fingerprint {corpus.fingerprint[:16]}...")
    if a.dry_run:
        print(f"dry run: {len(corpus.sentences)} embedContent requests would be made (about {len(corpus.sentences) / max(a.workers, 1) * 0.85 / 60:.0f} min at {a.workers} workers, if latency stays near 0.85 s)")
        return 0
    emb = VertexEmbedder(VertexConfig())
    if a.test_batch:
        tmp = Path(tempfile.mkdtemp(prefix="v2_testbatch_"))
        try:
            m = build_index(Path(a.db), tmp, emb, workers=min(a.workers, a.test_batch), chunk_size=max(a.test_batch, 1), limit=a.test_batch, restart=True)
            v = np.fromfile(tmp / "vectors.f32", dtype="<f4").reshape(m["vectors"]["shape"])
            ok_norm = np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-3)
            import sqlite3
            c = sqlite3.connect(tmp / "index.sqlite")
            texts = [r[0] for r in c.execute("SELECT text FROM sentences ORDER BY sentence_id")]
            map_ok = texts == corpus.sentences[:a.test_batch]
            print(f"test batch: {a.test_batch} sentences | shape {v.shape} | finite {bool(np.isfinite(v).all())} | unit norm {ok_norm} | id mapping ok {map_ok} | requests {emb.requests}")
            c.close()
            return 0 if (np.isfinite(v).all() and map_ok and v.shape[1] == emb.config.dimensions) else 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    t0 = time.monotonic()
    try:
        m = build_index(Path(a.db), Path(a.out), emb, workers=a.workers, chunk_size=a.chunk_size, restart=a.restart, software={"gcloud": _gcloud_version()},
                        log=lambda s: print(s, flush=True))
    except (BuildError, VertexError) as exc:
        print("BUILD FAILED:", exc, file=sys.stderr)
        return 1
    print(f"built {m['counts']['sentences']} vectors ({m['dimensions']}-d) in {time.monotonic() - t0:.0f}s; API requests {m['build']['api_requests']}; "
          f"vectors {m['vectors']['bytes']} bytes; manifest {Path(a.out) / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
