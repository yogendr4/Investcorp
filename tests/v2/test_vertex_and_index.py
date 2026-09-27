"""V2 tests: Vertex client (mocked HTTP) and the embedding index build/manifest/resume rules (fake embedder).

Run from the project root:  python -m unittest tests.v2.test_vertex_and_index -v
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.v2 import embedding_index as ei
from src.v2 import vertex as vx
from src.v2.semantic import IndexError_, SemanticIndex
from tests.v2.fixtures import CFG, CFO_SENT, DIM, FakeEmbedder, MEETINGS, MOJI_1, MOJI_2, build_db, vertex_ok


class Recorder:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def embedder(post, dims=DIM):
    return vx.VertexEmbedder(vx.VertexConfig(dimensions=dims), vx.TokenProvider(runner=lambda: "SECRET-TOKEN"), post=post)


class TestVertexClient(unittest.TestCase):
    def test_document_request_format_endpoint_and_no_task_type(self):
        rec = Recorder(vertex_ok([0.1] * DIM))
        v = embedder(rec).embed_document("Raw sentence.")
        url, headers, body = rec.calls[0]
        self.assertEqual(url, "https://aiplatform.googleapis.com/v1/projects/investcorp-assignment/locations/global/publishers/google/models/gemini-embedding-2:embedContent")
        self.assertEqual(body, {"content": {"parts": [{"text": "title: none | text: Raw sentence."}]}, "outputDimensionality": DIM})
        self.assertNotIn("taskType", json.dumps(body))
        self.assertEqual(headers["Authorization"], "Bearer SECRET-TOKEN")
        self.assertEqual(v.shape, (DIM,))

    def test_query_request_format(self):
        rec = Recorder(vertex_ok([0.1] * DIM))
        embedder(rec).embed_query("finance chief")
        self.assertEqual(rec.calls[0][2]["content"]["parts"][0]["text"], "task: search result | query: finance chief")

    def test_default_configuration_is_128_dimensions_global(self):
        c = vx.VertexConfig()
        self.assertEqual((c.dimensions, c.location, c.project, c.model), (128, "global", "investcorp-assignment", "gemini-embedding-2"))

    def test_malformed_responses_are_rejected(self):
        cases = [(200, b"not json"), (200, b"{}"), (200, json.dumps({"embedding": {}}).encode()), (200, json.dumps({"embedding": {"values": "abc"}}).encode()),
                 vertex_ok([0.1] * (DIM - 1)), vertex_ok([0.1] * (DIM + 1)), vertex_ok(["a"] * DIM), vertex_ok([True] * DIM), vertex_ok([None] * DIM),
                 (200, b'{"embedding": {"values": [' + b"NaN," * (DIM - 1) + b'NaN]}}'), (200, b'{"embedding": {"values": [' + b"Infinity," * (DIM - 1) + b'1.0]}}')]
        for resp in cases:
            with self.subTest(resp=resp[1][:40]):
                with self.assertRaises(vx.VertexError) as cm:
                    embedder(Recorder(resp)).embed_document("x")
                self.assertEqual(cm.exception.kind, "malformed")

    def test_api_failure_is_explicit_and_not_retried(self):
        rec = Recorder((403, json.dumps({"error": {"status": "PERMISSION_DENIED", "message": "API disabled"}}).encode()))
        with self.assertRaises(vx.VertexError) as cm:
            embedder(rec).embed_document("x")
        self.assertEqual((cm.exception.kind, cm.exception.status), ("http", 403))
        self.assertIn("API disabled", str(cm.exception))
        self.assertEqual(len(rec.calls), 1)                                     # no retry, ever
        rec = Recorder((429, b"{}"))
        with self.assertRaises(vx.VertexError):
            embedder(rec).embed_document("x")
        self.assertEqual(len(rec.calls), 1)

    def test_network_error_and_auth_error(self):
        with self.assertRaises(vx.VertexError) as cm:
            embedder(Recorder(vx.VertexError("network", "timed out"))).embed_document("x")
        self.assertEqual(cm.exception.kind, "network")

        def boom():
            raise vx.VertexError("auth", "no token")
        e = vx.VertexEmbedder(vx.VertexConfig(dimensions=DIM), vx.TokenProvider(runner=boom), post=Recorder(vertex_ok([0.1] * DIM)))
        with self.assertRaises(vx.VertexError) as cm:
            e.embed_query("x")
        self.assertEqual(cm.exception.kind, "auth")

    def test_token_is_cached_and_never_leaks_into_errors(self):
        calls = []
        tp = vx.TokenProvider(runner=lambda: calls.append(1) or "SECRET-TOKEN")
        e = vx.VertexEmbedder(vx.VertexConfig(dimensions=DIM), tp, post=Recorder((500, b"oops")))
        for _ in range(3):
            with self.assertRaises(vx.VertexError) as cm:
                e.embed_document("x")
            self.assertNotIn("SECRET-TOKEN", str(cm.exception))
        self.assertEqual(len(calls), 1)
        calls.clear()
        tp0 = vx.TokenProvider(runner=lambda: calls.append(1) or "t", ttl_s=-1)
        tp0.get(); tp0.get()
        self.assertEqual(len(calls), 2)                                          # refreshed when older than the ttl

    def test_embed_documents_keeps_order_with_concurrency(self):
        def post(url, headers, body, timeout):
            text = json.loads(body)["content"]["parts"][0]["text"]
            n = float(text.split("item")[1])
            return vertex_ok([n] + [0.0] * (DIM - 1))
        e = embedder(post)
        out = e.embed_documents([f"item{i}" for i in range(20)], workers=8)
        self.assertEqual([float(v[0]) for v in out], [float(i) for i in range(20)])
        self.assertEqual(e.requests, 20)


class TestCorpusAndIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="v2_idx_"))
        cls.db = cls.tmp / "t.sqlite"
        build_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def out(self, name):
        p = self.tmp / name
        if p.exists():
            shutil.rmtree(p)
        return p

    def test_sentence_splitting_and_raw_preservation(self):
        self.assertEqual(ei.split_sentences("One sentence. Two! Three?  "), ["One sentence.", "Two!", "Three?"])
        self.assertEqual(ei.split_sentences(""), [])
        c = ei.collect_corpus(self.db)
        self.assertIn(MOJI_1, c.sentences)                                        # mojibake kept exactly, never repaired
        self.assertIn(MOJI_2, c.sentences)
        self.assertFalse(any("Ã³" not in s and "López" in s for s in c.sentences))

    def test_deduplication_and_mapping(self):
        c = ei.collect_corpus(self.db)
        self.assertEqual(len(set(c.sentences)), len(c.sentences))
        self.assertEqual(c.sentences.count(CFO_SENT), 1)                          # used by meetings 1 and 4, stored once
        sid = c.sentences.index(CFO_SENT)
        self.assertEqual(sorted(r[0] for r in c.rows if r[2] == sid), [1, 4])
        self.assertEqual(c.occurrences, sum(len(ei.split_sentences(row["summary"])) for row in MEETINGS))
        self.assertGreater(c.occurrences, len(c.sentences))
        self.assertEqual(c.meetings, len(MEETINGS))
        pos = {(r[0], r[1]): r[2] for r in c.rows}
        self.assertEqual(c.sentences[pos[(1, 0)]], CFO_SENT)

    def test_build_embeds_each_distinct_sentence_once_and_writes_the_manifest(self):
        emb, d = FakeEmbedder(), self.out("full")
        m = ei.build_index(self.db, d, emb, workers=1, chunk_size=5)
        c = ei.collect_corpus(self.db)
        self.assertEqual(len(emb.doc_calls), len(c.sentences))
        self.assertEqual(sorted(emb.doc_calls), sorted(c.sentences))              # raw text, no prefix added by the index code
        self.assertEqual(m["counts"], {"sentences": len(c.sentences), "sentence_occurrences": c.occurrences, "meetings": len(MEETINGS)})
        for key in ("model", "vertex", "dimensions", "doc_format", "query_format", "source", "vectors", "index_sqlite", "generated_at_utc", "build", "software"):
            self.assertIn(key, m)
        self.assertEqual((m["model"], m["dimensions"], m["vertex"]), ("gemini-embedding-2", DIM, {"project": "investcorp-assignment", "location": "global"}))
        self.assertEqual(m["source"]["fingerprint_sha256"], ei.source_fingerprint(self.db))
        self.assertEqual(m["vectors"]["sha256"], ei.file_sha256(d / "vectors.f32"))
        self.assertEqual(m["vectors"]["shape"], [len(c.sentences), DIM])
        self.assertEqual(m["build"]["api_requests"], len(c.sentences))
        self.assertFalse((d / "progress").exists())
        raw = np.fromfile(d / "vectors.f32", dtype="<f4").reshape(len(c.sentences), DIM)
        self.assertTrue(np.isfinite(raw).all())
        self.assertTrue(np.allclose(raw[c.sentences.index(CFO_SENT)], emb.embed_document(CFO_SENT)))          # row i = sentence id i

    def test_index_loads_and_manifest_is_validated(self):
        d = self.out("valid")
        ei.build_index(self.db, d, FakeEmbedder(), workers=1)
        idx = SemanticIndex.load(d, expected_fingerprint=ei.source_fingerprint(self.db), expected_config={"model": "gemini-embedding-2", "dimensions": DIM})
        self.assertEqual(idx.dimensions, DIM)
        self.assertEqual(idx.sentence_text(0), ei.collect_corpus(self.db).sentences[0])
        idx.close()
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d, expected_config={"dimensions": 128})           # different configuration is never accepted
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d, expected_config={"model": "another-model"})
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d, expected_fingerprint="0" * 64)                 # built from a different source table
        with self.assertRaises(IndexError_):
            SemanticIndex.load(self.tmp / "nowhere")

    def test_tampered_or_inconsistent_files_are_rejected(self):
        d = self.out("tamper")
        ei.build_index(self.db, d, FakeEmbedder(), workers=1)
        (d / "vectors.f32").write_bytes((d / "vectors.f32").read_bytes()[:-4] + b"\x00\x00\x00\x00")
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d)
        d2 = self.out("tamper2")
        ei.build_index(self.db, d2, FakeEmbedder(), workers=1)
        raw = np.fromfile(d2 / "vectors.f32", dtype="<f4")
        raw[3] = np.nan
        raw.tofile(d2 / "vectors.f32")
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d2, verify_checksums=False)                       # non-finite values are caught even without checksums
        d3 = self.out("tamper3")
        ei.build_index(self.db, d3, FakeEmbedder(), workers=1)
        (d3 / "vectors.f32").write_bytes((d3 / "vectors.f32").read_bytes()[:-8])
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d3, verify_checksums=False)                       # wrong size for the declared shape

    def test_partial_build_cannot_be_used_for_retrieval(self):
        d = self.out("partial")
        m = ei.build_index(self.db, d, FakeEmbedder(), workers=1, limit=4)
        self.assertTrue(m["partial"])
        with self.assertRaises(IndexError_):
            SemanticIndex.load(d)
        SemanticIndex.load(d, allow_partial=True).close()

    def test_wrong_dimension_from_the_embedder_stops_the_build(self):
        class Bad(FakeEmbedder):
            def embed_document(self, text):
                return np.ones(DIM + 3, dtype=np.float32)
        with self.assertRaises(ei.BuildError):
            ei.build_index(self.db, self.out("bad"), Bad(), workers=1, chunk_size=4)

    def test_api_failure_stops_explicitly_keeps_progress_and_resumes_without_rework(self):
        d = self.out("resume")
        c = ei.collect_corpus(self.db)
        failing = FakeEmbedder(fail_docs_after=6)
        with self.assertRaises(ei.BuildError) as cm:
            ei.build_index(self.db, d, failing, workers=1, chunk_size=4)
        self.assertIn("re-run to resume", str(cm.exception))
        self.assertFalse((d / "manifest.json").exists())                          # no index is published from a failed build
        chunks = sorted(p.name for p in (d / "progress").glob("chunk_*.npy"))
        self.assertEqual(chunks, ["chunk_000000.npy"])                           # the finished chunk is kept, the failed one is not
        ok = FakeEmbedder()
        m = ei.build_index(self.db, d, ok, workers=1, chunk_size=4)
        self.assertEqual(m["build"]["resumed_chunks"], 1)
        self.assertEqual(len(ok.doc_calls), len(c.sentences) - 4)                # only the missing chunks were embedded
        clean = self.out("clean")
        ei.build_index(self.db, clean, FakeEmbedder(), workers=1, chunk_size=4)
        self.assertEqual((d / "vectors.f32").read_bytes(), (clean / "vectors.f32").read_bytes())

    def test_embeddings_are_never_mixed_across_configurations(self):
        d = self.out("mix")
        with self.assertRaises(ei.BuildError):
            ei.build_index(self.db, d, FakeEmbedder(fail_docs_after=2), workers=1, chunk_size=4)
        other = FakeEmbedder()
        other.config = vx.VertexConfig(dimensions=DIM, model="gemini-embedding-other")
        with self.assertRaises(ei.BuildError):
            ei.build_index(self.db, d, other, workers=1, chunk_size=4)           # partial progress from another model
        ei.build_index(self.db, d, other, workers=1, chunk_size=4, restart=True)  # only an explicit restart discards it
        with self.assertRaises(ei.BuildError):
            ei.build_index(self.db, d, FakeEmbedder(), workers=1)                # a finished index of another configuration
        again = ei.build_index(self.db, d, other, workers=1, chunk_size=4)       # same configuration: nothing to do
        self.assertEqual(again["model"], "gemini-embedding-other")

    def test_rebuild_is_deterministic(self):
        a, b = self.out("a"), self.out("b")
        ma = ei.build_index(self.db, a, FakeEmbedder(), workers=1)
        mb = ei.build_index(self.db, b, FakeEmbedder(), workers=3, chunk_size=3)
        self.assertEqual(ma["vectors"]["sha256"], mb["vectors"]["sha256"])
        self.assertEqual(ma["index_sqlite"]["sha256"] == mb["index_sqlite"]["sha256"], True)


if __name__ == "__main__":
    unittest.main()
