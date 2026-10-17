"""Contract-test fixtures: the real server (uvicorn, in a thread) against the persistent dev database.

The database is ``LGX_TEST_MCP_PG_DSN`` / ``LGX_MCP_PG_DSN`` or the stage-1 dev DSN
(``lgx_ro`` on ``127.0.0.1:55432/legilux``, docs/dev/INTERFACES.md §6.1). Every test here is skipped when it is
not reachable or does not hold the 2026-10-16 sample release the expectations were written for. The server's
clock is pinned to T so ``as_of`` defaults are deterministic.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

DEV_DSN = "postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux"
TOKEN = "contract-test-token-0123456789abcdef"
CLOCK = "2026-10-16T12:00:00+02:00"
RELEASE_PREFIX = "data-20261016"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "legacy_client: needs the isolated mcp==1.27.2 client (uv run --isolated)")
    config.addinivalue_line(
        "markers",
        "any_release: needs no sample content (auth, health, protocol eras, degraded states); CI runs these"
        " against the offline fixture release (scripts/ci_fixture_release.py)",
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class LiveServer:
    base: str
    url: str
    token: str
    app: Any
    server: Any
    thread: threading.Thread

    @property
    def runtime(self) -> Any:
        return self.app.runtime

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def start_server(settings: Any, runtime: Any = None) -> LiveServer:
    import uvicorn

    from lgx_mcp.server import create_app

    app = create_app(settings, runtime)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning", lifespan="on", access_log=False)
    )
    thread = threading.Thread(target=server.run, name=f"lgx-mcp-{settings.port}", daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{settings.port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(base + "/livez", timeout=1, trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        server.should_exit = True
        raise AssertionError("lgx-mcp did not start")
    return LiveServer(
        base=base, url=base + settings.path, token=settings.token or "", app=app, server=server, thread=thread
    )


def make_settings(dsn: str, **overrides: Any) -> Any:
    from lgx_mcp.settings import McpSettings

    base = McpSettings.from_env(env={"LGX_MCP_PG_DSN": dsn}, transport=None)
    values: dict[str, Any] = {
        "token": TOKEN,
        "host": "127.0.0.1",
        "port": free_port(),
        "clock_override": CLOCK,
        "rate_per_client_min": 0,
        "rate_global_min": 0,
    }
    values.update(overrides)
    return replace(base, **values)


@pytest.fixture(scope="session")
def mcp_dsn() -> str:
    dsn = os.environ.get("LGX_TEST_MCP_PG_DSN") or os.environ.get("LGX_MCP_PG_DSN") or DEV_DSN
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            row = conn.execute("SELECT release_id FROM lgx_meta.release").fetchone()
    except psycopg.Error as exc:
        msg = f"MCP contract tests need the dev database ({exc.__class__.__name__}); see docs/dev/INTERFACES.md §6.1"
        if os.environ.get("LGX_TEST_REQUIRE_DB") == "1":  # CI: a skipped contract suite must not pass as green
            pytest.fail(msg)
        pytest.skip(msg)
    if not row or not str(row[0]).startswith(RELEASE_PREFIX):
        pytest.skip(f"contract expectations are written for the {RELEASE_PREFIX} sample release, found {row}")
    return dsn


@pytest.fixture(scope="session")
def audit_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("audit") / "audit.jsonl"


@pytest.fixture(scope="session")
def live(mcp_dsn: str, audit_path: Path) -> Iterator[LiveServer]:
    srv = start_server(make_settings(mcp_dsn, audit_file=str(audit_path)))
    yield srv
    srv.stop()


@asynccontextmanager
async def mcp_client(server: LiveServer, mode: str = "auto", token: str | None = None) -> AsyncIterator[Any]:
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token or server.token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=60, trust_env=False) as http,
        Client(streamable_http_client(server.url, http_client=http), mode=mode) as client,
    ):
        yield client
