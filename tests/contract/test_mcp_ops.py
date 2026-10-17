"""Operations contract (PLAN §8.1, §8.8, Appendix D.0/D.3): health routes, auth, degraded states, stdio."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from contract.conftest import CLOCK, TOKEN, LiveServer, free_port, make_settings, mcp_client, start_server

REPO = Path(__file__).resolve().parents[2]
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _post(server: LiveServer, body: dict[str, Any], **headers: str) -> httpx.Response:
    h = {**MCP_HEADERS, "Authorization": f"Bearer {server.token}", **headers}
    return httpx.post(server.url, json=body, headers=h, timeout=30, trust_env=False)


def _init(version: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "old-client", "version": "0"},
        },
    }


# ------------------------------------------------------------------------------------ health + auth
def test_health_routes_need_no_token_and_accept_any_host(live: LiveServer) -> None:
    for path in ("/livez", "/healthz", "/readyz"):
        r = httpx.get(live.base + path, headers={"Host": "evil.example"}, timeout=10, trust_env=False)
        assert r.status_code == 200, (path, r.text)
    body = httpx.get(live.base + "/readyz", timeout=10, trust_env=False).json()
    assert body["schema_ok"] is True and body["db_ok"] is True
    assert body["vector_status"] == "disabled:provider_none"
    assert body["release_id"].startswith("data-20261016")
    assert body["clock_ok"] is True and body["canned_query_ok"] is True
    assert body["reranker_ok"] is None and body["rerank_status"] == "off"


def test_mcp_requires_bearer_token(live: LiveServer) -> None:
    body = _init("2025-03-26")
    r = httpx.post(live.url, json=body, headers=MCP_HEADERS, timeout=10, trust_env=False)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    r = httpx.post(
        live.url, json=body, headers={**MCP_HEADERS, "Authorization": "Bearer wrong-token"}, timeout=10, trust_env=False
    )
    assert r.status_code == 401


def test_host_and_origin_allowlists(live: LiveServer) -> None:
    assert _post(live, _init("2025-03-26"), Host="evil.example:8000").status_code == 421
    assert _post(live, _init("2025-03-26"), Origin="https://evil.example").status_code == 403
    assert _post(live, _init("2025-03-26"), Host=f"localhost:{live.base.rsplit(':', 1)[1]}").status_code == 200


async def test_wrong_token_fails_the_mcp_client(live: LiveServer) -> None:
    with pytest.raises(Exception):  # noqa: B017 - transport error type differs per era
        async with mcp_client(live, token="not-the-token-0123456789") as c:
            await c.list_tools()


# ----------------------------------------------------------------- handshake era without a 1.x client
@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"])
def test_old_protocol_versions_initialise_and_call(live: LiveServer, version: str) -> None:
    r = _post(live, _init(version))
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["protocolVersion"] == version and result["serverInfo"]["name"] == "lgx-mcp"
    assert set(result["capabilities"]) >= {"tools", "resources", "prompts"}
    hdr = {"mcp-protocol-version": version}
    assert _post(live, {"jsonrpc": "2.0", "method": "notifications/initialized"}, **hdr).status_code == 202
    tools = _post(live, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, **hdr).json()["result"]["tools"]
    assert [t["name"] for t in tools][:2] == ["search_legislation", "get_article"]
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_article", "arguments": {"citation": "art. L. 121-1 C. trav."}},
    }
    res = _post(live, call, **hdr).json()["result"]
    assert res["isError"] is False
    assert res["structuredContent"]["citation"]["article_eli"].endswith("/code/travail/art_l_121-1")
    assert res["content"][0]["type"] == "text"


# ------------------------------------------------------------------------------- degraded states
def test_database_down_readyz_503_livez_200_and_backend_unavailable(mcp_dsn: str) -> None:
    dead = f"postgresql://lgx_ro:x@127.0.0.1:{free_port()}/legilux"
    srv = start_server(make_settings(mcp_dsn, pg_conninfo=dead, db_timeout_s=1.0))
    try:
        assert httpx.get(srv.base + "/livez", timeout=10, trust_env=False).status_code == 200
        r = httpx.get(srv.base + "/readyz", timeout=10, trust_env=False)
        assert r.status_code == 503 and r.json()["schema_ok"] is False
        _post(srv, _init("2025-11-25"))
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "about_corpus", "arguments": {}}}
        res = _post(srv, call, **{"mcp-protocol-version": "2025-11-25"}).json()["result"]
        assert res["isError"] is True and res["structuredContent"]["error"]["code"] == "BACKEND_UNAVAILABLE"
    finally:
        srv.stop()


async def test_reranker_down_auto_degrades_on_fails(mcp_dsn: str) -> None:
    url = f"http://127.0.0.1:{free_port()}"  # nothing listens there
    srv = start_server(make_settings(mcp_dsn, rerank_url=url, rerank="auto", rerank_timeout_s=1.0))
    try:
        body = httpx.get(srv.base + "/readyz", timeout=10, trust_env=False)
        assert body.status_code == 200 and body.json()["reranker_ok"] is False
        async with mcp_client(srv) as c:
            r = await c.call_tool("search_legislation", {"query": "contrat de travail", "top_k": 3})
        ret = r.structured_content["retrieval"]
        assert not r.is_error and r.structured_content["hits"]
        assert ret["reranked"] is False and ret["rerank_status"] == "unavailable" and ret["rerank_reason"]
    finally:
        srv.stop()
    srv = start_server(make_settings(mcp_dsn, rerank_url=url, rerank="on", rerank_timeout_s=1.0))
    try:
        async with mcp_client(srv) as c:
            r = await c.call_tool("search_legislation", {"query": "contrat de travail", "top_k": 3})
        assert r.is_error and r.structured_content["error"]["code"] == "BACKEND_UNAVAILABLE"
    finally:
        srv.stop()


async def test_reranker_batches_of_at_most_32(mcp_dsn: str) -> None:
    from lgx_mcp.retrieval.rerank import PROBE_QUERY, Reranker
    from lgx_mcp.runtime import create_runtime

    batches: list[int] = []

    def tei(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        n = len(payload["texts"])
        if payload["query"] != PROBE_QUERY:
            batches.append(n)
        # reverse the lexical order: the last text of every batch scores highest
        return httpx.Response(200, json=[{"index": i, "score": float(i)} for i in range(n)])

    settings = make_settings(mcp_dsn, rerank_url="http://rerank:8080", rerank="auto", rerank_k=40)
    rt = create_runtime(settings)
    rt.reranker = Reranker(settings.rerank_url, "auto", batch=32, transport=httpx.MockTransport(tei))
    srv = start_server(settings, rt)
    try:
        async with mcp_client(srv) as c:
            r = await c.call_tool("search_legislation", {"query": "travail salarié contrat", "top_k": 5})
        ret = r.structured_content["retrieval"]
        assert ret["reranked"] is True and ret["rerank_status"] == "ok" and ret["rerank_k"] == 40
        assert batches and max(batches) <= 32 and sum(batches) == min(40, ret["candidates"])
        assert len(batches) == -(-sum(batches) // 32)
    finally:
        srv.stop()


async def test_clock_suspect_keeps_ready_and_warns(mcp_dsn: str) -> None:
    srv = start_server(make_settings(mcp_dsn, clock_override="2028-06-01T12:00:00+02:00"))
    try:
        body = httpx.get(srv.base + "/readyz", timeout=10, trust_env=False)
        assert body.status_code == 200 and body.json()["clock_ok"] is False
        async with mcp_client(srv) as c:
            r = await c.call_tool("about_corpus", {})
        sc = r.structured_content
        assert "W_CLOCK_SUSPECT" in [w["code"] for w in sc["warnings"]]
        assert sc["as_of"] == "2026-10-16" and sc["snapshot"]["clock_ok"] is False
    finally:
        srv.stop()


async def test_rate_limit_per_client(mcp_dsn: str) -> None:
    srv = start_server(make_settings(mcp_dsn, rate_per_client_min=2, rate_global_min=0))
    try:
        async with mcp_client(srv) as c:
            r1 = await c.call_tool("about_corpus", {})
            r2 = await c.call_tool("about_corpus", {})
            r3 = await c.call_tool("about_corpus", {})
        assert not r1.is_error and not r2.is_error
        assert r3.is_error and r3.structured_content["error"]["code"] == "BACKEND_UNAVAILABLE"
    finally:
        srv.stop()


# ------------------------------------------------------------------------------------------ stdio
async def test_stdio_entry_point(mcp_dsn: str) -> None:
    from mcp import Client, StdioServerParameters

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LGX_MCP_PG_DSN": mcp_dsn,
        "LGX_CLOCK_OVERRIDE": CLOCK,
        "LGX_LOG_LEVEL": "WARNING",
        "LGX_RATE_PER_CLIENT_MIN": "0",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "lgx_mcp", "--stdio"], env=env, cwd=str(REPO))
    async with Client(params, mode="legacy") as c:
        tools = await c.list_tools()
        r = await c.call_tool("get_article", {"citation": "art. L. 121-1 C. trav."})
    assert tools.tools[0].name == "search_legislation"
    assert not r.is_error and r.structured_content["citation"]["article_eli"].endswith("/art_l_121-1")


def test_cli_check_prints_readyz(mcp_dsn: str) -> None:
    import subprocess

    env = {**os.environ, "LGX_MCP_PG_DSN": mcp_dsn, "LGX_MCP_TOKEN": TOKEN}
    out = subprocess.run(
        [sys.executable, "-m", "lgx_mcp", "--check"], capture_output=True, text=True, env=env, cwd=REPO, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["schema_ok"] is True
