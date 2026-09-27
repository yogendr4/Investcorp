"""Minimal Vertex AI client for gemini-embedding-2 (standard library only).

One REST call per text: POST .../publishers/google/models/gemini-embedding-2:embedContent with an OAuth token from
`gcloud auth print-access-token`. No SDK, no retries (no transient-failure policy is documented in the sources this
project could verify), every failure is an explicit VertexError. Tokens are never logged or stored on disk.

Gemini Embedding 2 takes no taskType: the task is part of the text.
  document: "title: none | text: {text}"      query: "task: search result | query: {text}"
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

MODEL = "gemini-embedding-2"
DOC_FORMAT = "title: none | text: {text}"
QUERY_FORMAT = "task: search result | query: {text}"
TOKEN_TTL_S = 40 * 60          # gcloud user tokens last about an hour; refreshed well before that
_WINDOWS_GCLOUD = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd"


class VertexError(Exception):
    """kind: auth / http / network / malformed."""

    def __init__(self, kind: str, message: str, status: Optional[int] = None):
        super().__init__(f"{kind}: {message}")
        self.kind, self.status, self.message = kind, status, message


@dataclass(frozen=True)
class VertexConfig:
    project: str = "investcorp-assignment"
    location: str = "global"
    model: str = MODEL
    dimensions: int = 128
    timeout_s: float = 60.0

    @property
    def url(self) -> str:
        host = "aiplatform.googleapis.com" if self.location == "global" else f"{self.location}-aiplatform.googleapis.com"
        return f"https://{host}/v1/projects/{self.project}/locations/{self.location}/publishers/google/models/{self.model}:embedContent"

    def as_dict(self) -> dict:
        return {"project": self.project, "location": self.location, "model": self.model, "dimensions": self.dimensions, "doc_format": DOC_FORMAT, "query_format": QUERY_FORMAT}


def find_gcloud() -> str:
    found = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if found:
        return found
    if _WINDOWS_GCLOUD.is_file():
        return str(_WINDOWS_GCLOUD)
    raise VertexError("auth", "gcloud was not found on PATH or in the default Windows install location")


class TokenProvider:
    """Access token from the authenticated gcloud user (cached in memory only, never printed or written)."""

    def __init__(self, runner: Optional[Callable[[], str]] = None, ttl_s: float = TOKEN_TTL_S) -> None:
        self._runner, self._ttl = runner or self._gcloud, ttl_s
        self._token: Optional[str] = None
        self._at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _gcloud() -> str:
        try:
            done = subprocess.run([find_gcloud(), "auth", "print-access-token"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VertexError("auth", f"could not run gcloud: {type(exc).__name__}") from None
        token = (done.stdout or "").strip()
        if done.returncode != 0 or not token:
            raise VertexError("auth", "gcloud did not return an access token (run: gcloud auth login)")
        return token

    def get(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() - self._at > self._ttl:
                self._token, self._at = self._runner(), time.monotonic()
            return self._token


def _default_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VertexError("network", f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}") from None


def parse_embedding(payload: bytes, dimensions: int) -> np.ndarray:
    """Strict validation of an embedContent response: embedding.values, exactly `dimensions` finite numbers."""
    try:
        obj = json.loads(payload)
    except ValueError:
        raise VertexError("malformed", "the response is not valid JSON") from None
    values = (obj.get("embedding") or {}).get("values") if isinstance(obj, dict) else None
    if not isinstance(values, list):
        raise VertexError("malformed", "the response has no embedding.values list")
    if len(values) != dimensions:
        raise VertexError("malformed", f"expected {dimensions} values, got {len(values)}")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        raise VertexError("malformed", "the embedding contains non-numeric or non-finite values")
    return np.asarray(values, dtype=np.float32)


class VertexEmbedder:
    def __init__(self, config: VertexConfig = VertexConfig(), token_provider: Optional[TokenProvider] = None, post: Optional[Callable] = None) -> None:
        self.config = config
        self.tokens = token_provider or TokenProvider()
        self._post = post or _default_post
        self.requests = 0                       # API requests made by this object (for build reporting)
        self._count_lock = threading.Lock()

    def _embed(self, formatted_text: str) -> np.ndarray:
        body = json.dumps({"content": {"parts": [{"text": formatted_text}]}, "outputDimensionality": self.config.dimensions}, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.tokens.get()}", "Content-Type": "application/json; charset=utf-8"}
        with self._count_lock:
            self.requests += 1
        status, payload = self._post(self.config.url, headers, body, self.config.timeout_s)
        if status != 200:
            detail = ""
            try:
                err = json.loads(payload).get("error", {})
                detail = f"{err.get('status', '')}: {str(err.get('message', ''))[:300]}"
            except (ValueError, AttributeError):
                detail = payload[:200].decode("utf-8", "replace")
            raise VertexError("http", detail.strip() or "no error body", status)
        return parse_embedding(payload, self.config.dimensions)

    def embed_document(self, text: str) -> np.ndarray:
        return self._embed(DOC_FORMAT.format(text=text))

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(QUERY_FORMAT.format(text=text))

    def embed_documents(self, texts: Sequence[str], workers: int = 1) -> list[np.ndarray]:
        """One request per text (no server-side batching is used), in order. Any failure raises; nothing is retried."""
        if workers <= 1:
            return [self.embed_document(t) for t in texts]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.embed_document, texts))
