"""Offline unit tests of the MCP server's ops pieces: settings, auth guard, audit, reranker, modes, rate limit."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from lgx_core.models import EmbeddingProvider
from lgx_mcp.audit import AuditLog, canonical_sha256
from lgx_mcp.auth import GuardMiddleware, host_allowed, origin_allowed, sdk_security
from lgx_mcp.retrieval import decide_mode
from lgx_mcp.retrieval.rerank import PROBE_QUERY, Reranker, RerankUnavailableError, rerank_text
from lgx_mcp.settings import McpSettings, SettingsError, is_internal_url

TOKEN = "unit-test-token-0123456789"


# ------------------------------------------------------------------------------------------ settings
def test_settings_from_compose_style_env(tmp_path: Path) -> None:
    pw = tmp_path / "pg_ro_pw"
    pw.write_text("s3cret\n")
    tok = tmp_path / "mcp_token"
    tok.write_text(TOKEN + "\n")
    s = McpSettings.from_env(
        {
            "LGX_DB_HOST": "db",
            "LGX_DB_NAME": "legilux",
            "LGX_DB_USER": "lgx_ro",
            "LGX_DB_PASSWORD_FILE": str(pw),
            "LGX_MCP_TOKEN_FILE": str(tok),
            "LGX_ALLOWED_HOSTS": "mcp:8000,localhost:8000",
            "LGX_ALLOWED_ORIGINS": "https://legilux.intra",
            "LGX_RERANK_URL": "http://rerank:8080",
            "LGX_RERANK_K": "16",
        }
    )
    assert s.token == TOKEN and "password=s3cret" in s.pg_conninfo and "host=db" in s.pg_conninfo
    assert s.allowed_hosts == ("mcp:8000", "localhost:8000") and s.rerank_k == 16
    assert "s3cret" not in json.dumps(s.describe()) and TOKEN not in repr(s)
    assert s.embed_provider is EmbeddingProvider.NONE and s.audit_query_text is False


def test_settings_dsn_and_validation() -> None:
    s = McpSettings.from_env(
        {"LGX_MCP_PG_DSN": "postgresql://lgx_ro:pw@127.0.0.1:55432/legilux", "LGX_MCP_AUTH": "off"}
    )
    assert "port=55432" in s.pg_conninfo and s.auth is False
    with pytest.raises(SettingsError, match="bearer token"):
        McpSettings.from_env({})
    with pytest.raises(SettingsError, match="LGX_RERANK_BATCH"):
        McpSettings.from_env({"LGX_MCP_TOKEN": TOKEN, "LGX_RERANK_BATCH": "64"})
    with pytest.raises(SettingsError, match="internal"):
        McpSettings.from_env({"LGX_MCP_TOKEN": TOKEN, "LGX_RERANK_URL": "https://api.example.com"})
    with pytest.raises(SettingsError, match="16 characters"):
        McpSettings.from_env({"LGX_MCP_TOKEN": "short"})
    with pytest.raises(SettingsError, match="LGX_RERANK must"):
        McpSettings.from_env({"LGX_MCP_TOKEN": TOKEN, "LGX_RERANK": "maybe"})
    # stdio needs no token
    assert McpSettings.from_env({}, transport="stdio").token is None


@pytest.mark.parametrize(
    ("url", "ok"),
    [("http://rerank:8080", True), ("http://localhost:1", True), ("http://10.1.2.3/x", True),
     ("http://tei.lgx.internal", True), ("https://api.mistral.ai", False), ("http://8.8.8.8", False)],
)  # fmt: skip
def test_internal_url(url: str, ok: bool) -> None:
    assert is_internal_url(url) is ok


# ---------------------------------------------------------------------------------------------- auth
def test_host_and_origin_matching() -> None:
    allowed = ("mcp:8000", "localhost:*")
    assert (
        host_allowed("mcp:8000", allowed)
        and host_allowed("localhost:1234", allowed)
        and host_allowed("localhost", allowed)
    )
    assert (
        not host_allowed("mcp:9000", allowed)
        and not host_allowed("evil:8000", allowed)
        and not host_allowed(None, allowed)
    )
    assert host_allowed("anything", ("*",))
    assert origin_allowed(None, ()) and origin_allowed("https://legilux.intra/", ("https://legilux.intra",))
    assert not origin_allowed("https://evil.example", ("https://legilux.intra",))
    sec = sdk_security(allowed, ("https://legilux.intra",))
    assert sec.enable_dns_rebinding_protection and "localhost" in sec.allowed_hosts
    assert sdk_security(("*",), ()).enable_dns_rebinding_protection is False


def _guarded(token: str | None = TOKEN) -> TestClient:
    async def ok(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"]), Route("/readyz", ok), Route("/livez", ok)])
    guarded = GuardMiddleware(
        app,
        mcp_path="/mcp",
        token=token,
        allowed_hosts=("testserver", "mcp:8000"),
        allowed_origins=("https://ui.intra",),
    )
    return TestClient(guarded)


def test_guard_middleware() -> None:
    c = _guarded()
    assert c.post("/mcp").status_code == 401
    assert c.post("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.post("/mcp", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200
    assert c.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil:1"}).status_code == 421
    assert c.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://evil"}).status_code == 403
    assert c.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://ui.intra"}).status_code == 200
    # health routes: no token, any Host
    assert c.get("/readyz", headers={"Host": "evil:1"}).status_code == 200
    assert c.get("/livez").status_code == 200
    assert _guarded(None).post("/mcp").status_code == 200  # LGX_MCP_AUTH=off (dev)


# --------------------------------------------------------------------------------------------- audit
def test_audit_record_hashes_and_hmac(tmp_path: Path) -> None:
    path = tmp_path / "a" / "audit.jsonl"
    log = AuditLog(str(path), b"k" * 32)
    e = log.record(
        tool="search_legislation",
        arguments={"query": "ma question", "top_k": 3},
        latency_ms=12.345,
        hits=3,
        error_code=None,
        headers={"x-openwebui-user-id": "alice", "x-openwebui-chat-id": "c1"},
        peer="10.0.0.5",
        protocol_version="2025-11-25",
    )
    line = json.loads(path.read_text())
    assert line == e and "query" not in line and "alice" not in path.read_text()
    assert line["args_sha256"] == canonical_sha256({"top_k": 3, "query": "ma question"})
    assert line["client"].startswith("u:") and line["chat"] and line["latency_ms"] == 12.3
    assert log.client_id(None, "10.0.0.5") == log.client_id({}, "10.0.0.5") != log.client_id(None, "10.0.0.6")
    with_text = AuditLog(None, b"k", query_text=True).record(
        tool="get_article", arguments={"citation": "art. 5"}, latency_ms=1, hits=1, error_code="NOT_FOUND",
        headers=None, peer=None, protocol_version=None,
    )  # fmt: skip
    assert with_text["query"] == "art. 5" and with_text["is_error"] is True


# ------------------------------------------------------------------------------------------ reranker
async def test_reranker_batches_and_merges_scores() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/rerank" and body["truncate"] is True
        seen.append(len(body["texts"]))
        # TEI returns the list sorted by score; the client must map by index
        items = [{"index": i, "score": float(len(t))} for i, t in enumerate(body["texts"])]
        return httpx.Response(200, json=sorted(items, key=lambda x: -x["score"]))

    rr = Reranker("http://rerank:8080", "auto", batch=32, transport=httpx.MockTransport(handler))
    texts = ["x" * (i + 1) for i in range(40)]
    scores = await rr.rerank("q", texts)
    assert seen == [32, 8] and scores == [float(i + 1) for i in range(40)]
    assert rr.state.status == "ok"


async def test_reranker_probe_and_failures() -> None:
    def good(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == PROBE_QUERY
        return httpx.Response(200, json=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}])

    rr = Reranker("http://rerank:8080", "auto", transport=httpx.MockTransport(good))
    assert (await rr.probe()).status == "ok"

    def wrong(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"index": 0, "score": 0.9}, {"index": 1, "score": 0.1}])

    assert (await Reranker("http://r:1", "auto", transport=httpx.MockTransport(wrong)).probe()).status == "unavailable"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    bad = Reranker("http://r:1", "auto", transport=httpx.MockTransport(down))
    with pytest.raises(RerankUnavailableError):
        await bad.rerank("q", ["a"])
    assert bad.state.status == "unavailable" and "ConnectError" in (bad.state.reason or "")


def test_reranker_disabled_states() -> None:
    assert Reranker(None).state.status == "off" and not Reranker(None).enabled
    off = Reranker("http://rerank:8080", "off")
    assert off.state.status == "off" and off.state.reason == "LGX_RERANK=off"
    assert len(rerank_text("H", "b" * 5000)) == 2 + 1800


@pytest.mark.parametrize(
    ("requested", "status", "used", "reason"),
    [
        ("auto", "disabled:provider_none", "lexical", "vectors disabled: provider_none"),
        ("hybrid", "disabled:provider_none", "lexical", "vectors disabled: provider_none"),
        ("hybrid", "disabled:parity_failed", "lexical", "vectors disabled: parity_failed"),
        ("lexical", "ok", "lexical", None),
        ("auto", "ok", "hybrid", None),
        ("hybrid", "ok", "hybrid", None),
    ],
)
def test_decide_mode(requested: str, status: str, used: str, reason: str | None) -> None:
    m = decide_mode(requested, status)
    assert (m.mode_requested, m.mode_used, m.vector_status, m.reason) == (requested, used, status, reason)


def test_unimplemented_embedding_provider_degrades_to_lexical() -> None:
    from lgx_mcp.runtime import create_runtime

    s = McpSettings.from_env({"LGX_MCP_AUTH": "off", "LGX_EMBED_PROVIDER": "mistral-local"})
    rt = create_runtime(s)
    assert rt.vector_status(None) == "disabled:endpoint_unreachable"
    assert decide_mode("hybrid", rt.vector_status(None)).mode_used == "lexical"


def test_rate_limiter() -> None:
    from lgx_mcp.runtime import create_runtime

    s = McpSettings.from_env({"LGX_MCP_AUTH": "off", "LGX_RATE_PER_CLIENT_MIN": "2", "LGX_RATE_GLOBAL_MIN": "3"})
    rt = create_runtime(s)
    assert rt.rate_limited("a") is None and rt.rate_limited("a") is None
    assert rt.rate_limited("a") is not None  # third call of client a within a minute
    assert rt.rate_limited("b") is None
    assert rt.rate_limited("c") is not None  # global budget (3) exhausted
