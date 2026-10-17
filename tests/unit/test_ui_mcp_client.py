"""lgx_ui.mcp_client against a real ``mcp==2.3.0`` server: in-process, and over Streamable HTTP (ASGI, no socket)
with the bearer token checked; plus the /readyz probe (respx) and the environment settings."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import httpx2
import pytest
import respx

from lgx_ui.mcp_client import (
    LgxMcpClient,
    McpUnavailableError,
    ToolSession,
    describe_exception,
    fetch_readyz,
    health_url,
    run_sync,
)
from lgx_ui.settings import NO_KEY, UiSettingsError, load_settings
from unit.test_ui_fakes import FAKE_PROMPT, SCHEMAS, bearer_guard, build_fake_server

URL = "http://localhost:8000/mcp"


async def test_in_process_server_lists_calls_and_prompts() -> None:
    async with LgxMcpClient("in-process", server=build_fake_server(), timeout_s=5) as client:
        assert isinstance(client, ToolSession)
        assert client.display_url == "in-process"
        tools = await client.list_tools()
        assert [t.name for t in tools] == list(SCHEMAS)
        assert tools[0].input_schema == SCHEMAS["search_legislation"]
        assert all(t.read_only is True for t in tools)

        ok = await client.call_tool("search_legislation", {"query": "licenciement"})
        assert not ok.is_error and ok.error is None
        assert ok.structured["retrieval"]["mode_used"] == "lexical"
        assert ok.text.startswith("Résultats") and ok.latency_s >= 0

        err = await client.call_tool("get_article", {"article": "5"})
        assert err.is_error and err.error_code == "BAD_INPUT" and err.error["code"] == "BAD_INPUT"

        assert await client.get_prompt("legal_answer", {"lang": "de"}) == f"{FAKE_PROMPT} (de)"
        assert await client.get_prompt("no_such_prompt") is None


async def test_streamable_http_sends_the_bearer_token() -> None:
    server = build_fake_server()
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    seen: list[str | None] = []
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=bearer_guard(app, "s3cret", seen))
        http = httpx2.AsyncClient(transport=transport, headers={"Authorization": "Bearer s3cret"})
        async with LgxMcpClient(URL, http_client=http, timeout_s=5) as client:
            assert client.protocol_version
            out = await client.call_tool("about_corpus", {})
            assert not out.is_error and out.structured["embedding"]["vector_status"] == "disabled:provider_none"
        assert seen and set(seen) == {"Bearer s3cret"}

        wrong = httpx2.AsyncClient(transport=transport, headers={"Authorization": "Bearer wrong"})
        with pytest.raises(McpUnavailableError) as exc:
            async with LgxMcpClient(URL, http_client=wrong, timeout_s=5):
                pass
        assert "s3cret" not in str(exc.value)


async def test_default_http_client_carries_the_token_and_ignores_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    real = httpx2.AsyncClient

    def spy(*args: object, **kwargs: object) -> httpx2.AsyncClient:
        captured.update(kwargs)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("lgx_ui.mcp_client.httpx2.AsyncClient", spy)
    with socket.socket() as s:  # a closed loopback port: connection refused, no network involved
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(McpUnavailableError) as exc:
        async with LgxMcpClient(f"http://127.0.0.1:{port}/mcp?x=1", token="tok", timeout_s=2):
            pass
    assert captured["trust_env"] is False
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    msg = str(exc.value)
    assert "connexion impossible" in msg and "tok" not in msg and "x=1" not in msg


def test_describe_exception_flattens_groups() -> None:
    group = ExceptionGroup("outer", [ExceptionGroup("inner", [httpx2.ConnectError("refused")]), ValueError("bad")])
    assert describe_exception(group) == "connexion impossible (ConnectError); ValueError: bad"
    assert describe_exception(TimeoutError()) == "délai dépassé"


def test_health_url() -> None:
    assert health_url("http://mcp:8000/mcp") == "http://mcp:8000/readyz"
    assert health_url("https://h:1/x/mcp?q=1", "/livez") == "https://h:1/livez"


@respx.mock
def test_fetch_readyz_states() -> None:
    respx.get("http://mcp:8000/readyz").mock(
        return_value=httpx.Response(200, json={"schema_ok": True, "vector_status": "disabled:provider_none"})
    )
    ok = fetch_readyz("http://mcp:8000/mcp")
    assert ok.reachable and ok.ready and ok.vector_status == "disabled:provider_none"

    respx.get("http://down:8000/readyz").mock(return_value=httpx.Response(503, json={"schema_ok": False}))
    degraded = fetch_readyz("http://down:8000/mcp")
    assert degraded.reachable and not degraded.ready and degraded.error == "HTTP 503"

    respx.get("http://gone:8000/readyz").mock(side_effect=httpx.ConnectError("refused"))
    gone = fetch_readyz("http://gone:8000/mcp")
    assert not gone.reachable and "connexion impossible" in (gone.error or "")


async def test_run_sync_inside_a_running_loop() -> None:
    async def answer() -> int:
        await asyncio.sleep(0)
        return 42

    assert run_sync(answer()) == 42  # falls back to a worker thread when a loop is running


def test_run_sync_without_loop() -> None:
    async def answer() -> str:
        return "ok"

    assert run_sync(answer()) == "ok"


def test_settings_from_environment(tmp_path: Path) -> None:
    token = tmp_path / "mcp_token"
    token.write_text("file-token\n")
    key = tmp_path / "llm_key"
    key.write_text("")  # an empty secret file falls back to the plain variable
    s = load_settings(
        {
            "LGX_MCP_URL": "http://mcp:8000/mcp",
            "LGX_MCP_TOKEN_FILE": str(token),
            "LGX_MCP_TOKEN": "plain-token",
            "OPENAI_API_KEY_FILE": str(key),
            "OPENAI_API_KEY": "sk-plain",
            "OPENAI_BASE_URL": "https://mistral.example.intra/v1",
            "LGX_LLM_MODEL": "mistral-medium",
            "LGX_MAX_TOOL_ITERATIONS": "7",
            "LGX_MCP_TIMEOUT_S": "bogus",
        }
    )
    assert s.mcp_token == "file-token" and s.llm_api_key == "sk-plain"
    assert s.max_tool_iterations == 7 and s.mcp_timeout_s == 30.0
    assert (s.llm_base_url, s.llm_model) == ("https://mistral.example.intra/v1", "mistral-medium")
    assert s.llm_trust_env is False and not s.llm_external  # *.intra counts as inside the organisation
    assert load_settings({"LGX_LLM_TRUST_ENV": "1"}).llm_trust_env is True

    empty = load_settings({})
    assert empty.mcp_token is None and empty.llm_api_key == NO_KEY and empty.eval_dir is None
    # default: the user's Mistral endpoint (same API as Mistral SaaS; the airgap sets its on-prem domain)
    assert empty.llm_base_url == "https://api.mistral.ai/v1" and empty.llm_external


def test_unreadable_secret_file_fails_loudly(tmp_path: Path) -> None:
    """Review 2026-10-17: a root-owned 0600 secret made the UI fall back to no token (401 on every call)."""
    missing = tmp_path / "secrets" / "mcp_token"
    with pytest.raises(UiSettingsError, match="LGX_MCP_TOKEN_FILE"):
        load_settings({"LGX_MCP_TOKEN_FILE": str(missing), "LGX_MCP_TOKEN": "plain-token"})
    with pytest.raises(UiSettingsError, match="OPENAI_API_KEY_FILE"):
        load_settings({"OPENAI_API_KEY_FILE": str(tmp_path)})  # a directory: read fails


def test_any_openai_compatible_url_but_the_key_only_for_the_configured_one() -> None:
    cfg = load_settings({"OPENAI_BASE_URL": "https://mistral.example.intra/v1/", "LGX_LLM_MODEL": "m"})
    assert cfg.key_allowed("https://mistral.example.intra/v1")  # configured (trailing slash ignored)
    assert not cfg.key_allowed("http://10.1.2.3:8000/v1") and not cfg.key_allowed("https://api.example.com/v1")
