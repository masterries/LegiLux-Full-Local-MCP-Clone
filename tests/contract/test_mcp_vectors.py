"""Hybrid retrieval through the real server with a FAKE embeddings endpoint (never a real API call; PLAN §6.5, §8.3).

The dev release carries no vectors, so the release the server sees is given a fake ``lgx.embedding_model`` row: the
startup probe then runs against the fake endpoint, ``search_legislation`` embeds the query and calls
``lgx.search_candidates`` with it (the dense branch finds no vectors in this database, the RRF keeps the lexical
candidates — the SQL fusion itself is tested with fake vectors in ``tests/db/test_schema.py``). Every failure mode
must give ``mode_used = lexical`` with a reason, never an error, and ``/readyz`` stays 200.
"""

from __future__ import annotations

import dataclasses
import json
import math
import time
from typing import Any

import httpx
import pytest

from contract.conftest import LiveServer, make_settings, mcp_client, start_server
from lgx_core.config import EmbeddingConfig
from lgx_core.embed import PROBE_TEXT
from lgx_mcp.runtime import create_runtime
from lgx_mcp.vectors import QueryVectors

DIM = 1024


def fake_vector(text: str) -> list[float]:
    raw = [math.sin(len(text) * 0.1 + k) for k in range(DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


class FakeMistral:
    def __init__(self, model: str = "mistral-embed", fail: bool = False) -> None:
        self.model = model
        self.fail = fail
        self.inputs: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("enclave endpoint down")
        body = json.loads(request.content)
        self.inputs.append(body["input"])
        data = [{"index": i, "embedding": fake_vector(t)} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"model": self.model, "data": data})


ROW = {
    "embedding_set_id": 1,
    "provider": "mistral",
    "model_id": "mistral-embed",
    "model_requested": "mistral-embed",
    "dim": DIM,
    "probe_text": PROBE_TEXT,
    "probe_vector": fake_vector(PROBE_TEXT),
    "probe_min_cosine": 0.99,
}


@pytest.fixture
def release_with_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every release the server loads during the test gets the fake lgx.embedding_model row."""
    import lgx_mcp.release as release_mod

    original = release_mod.load_release

    async def load_with_vectors(db: Any) -> Any:
        return dataclasses.replace(await original(db), embedding_models=[ROW])

    monkeypatch.setattr(release_mod, "load_release", load_with_vectors)


def start_with_vectors(mcp_dsn: str, fake: FakeMistral) -> LiveServer:
    emb = EmbeddingConfig(provider="mistral", base_url="https://mistral.enclave.test", cooldown_s=0, timeout_s=5)
    settings = make_settings(mcp_dsn, embedding=emb, embed_api_key="fake-key")
    runtime = create_runtime(settings)
    runtime.vectors = QueryVectors(emb, "fake-key", transport=httpx.MockTransport(fake))
    return start_server(settings, runtime)


def readyz(srv: LiveServer, want: str | None = None, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = httpx.get(srv.base + "/readyz", timeout=3, trust_env=False)
        assert r.status_code == 200  # degraded vector states never make /readyz fail
        body = r.json()
        if want is None or body["vector_status"] == want:
            return body
        time.sleep(0.1)
    return body


async def _search(srv: LiveServer, mode: str = "auto") -> dict[str, Any]:
    async with mcp_client(srv) as c:
        r = await c.call_tool(
            "search_legislation", {"query": "délai de préavis du salarié", "retrieval_mode": mode, "top_k": 3}
        )
    assert not r.is_error, r.content[0].text[:400]
    return dict(r.structured_content or {})


async def test_probe_ok_gives_hybrid_with_the_query_vector(mcp_dsn: str, release_with_vectors: None) -> None:
    fake = FakeMistral()
    srv = start_with_vectors(mcp_dsn, fake)
    try:
        assert readyz(srv, "ok")["vector_status"] == "ok"
        sc = await _search(srv)
        ret = sc["retrieval"]
        assert (ret["mode_used"], ret["vector_status"], ret["reason"]) == ("hybrid", "ok", None)
        assert ret["embedding_model"] == {"provider": "mistral", "model_id": "mistral-embed", "dim": DIM}
        assert "query_embedding" in ret["latency_ms"] and sc["hits"]
        assert fake.inputs[0] == [PROBE_TEXT] and ["délai de préavis du salarié"] in fake.inputs
        lexical = (await _search(srv, "lexical"))["retrieval"]
        assert lexical["mode_used"] == "lexical" and lexical["embedding_model"] is None
        async with mcp_client(srv) as c:
            about = (await c.call_tool("about_corpus", {})).structured_content
        emb = about["embedding"]
        assert emb["vector_status"] == "ok" and emb["probe"]["status"] == "ok" and emb["probe"]["cosine"] > 0.99
    finally:
        srv.stop()


@pytest.mark.parametrize(
    ("fake", "status"),
    [(FakeMistral(model="mistral-embed-next"), "disabled:model_mismatch"),
     (FakeMistral(fail=True), "disabled:endpoint_unreachable")],
)  # fmt: skip
async def test_failed_probe_means_lexical_with_a_reason(
    mcp_dsn: str, release_with_vectors: None, fake: FakeMistral, status: str
) -> None:
    srv = start_with_vectors(mcp_dsn, fake)
    try:
        body = readyz(srv, status)
        assert body["vector_status"] == status and body["vector_reason"]
        for mode in ("auto", "hybrid"):
            ret = (await _search(srv, mode))["retrieval"]
            assert ret["mode_used"] == "lexical" and ret["vector_status"] == status
            assert ret["reason"] == f"vectors disabled: {status.removeprefix('disabled:')}"
    finally:
        srv.stop()
