"""How the harness reaches the tools: in process (the server's own ``run_tool`` on a ``Runtime``) or over MCP HTTP.

* :class:`InProcessBackend` builds the real runtime against a database DSN (default: the dev runtime database as
  ``lgx_ro``) and calls :func:`lgx_mcp.server.run_tool` — validation, envelope, error mapping and every retrieval
  step exactly as served, without the transport. The clock is pinned (default: noon of the release's T) so
  ``as_of`` defaults are reproducible; a gold item may pin its own clock (``W_CLOCK_SUSPECT`` cases).
* :class:`HttpBackend` calls a running server (``--url``) with the official mcp 2.3.0 client: the configuration
  is the server's (mode, reranker); items with their own clock are skipped.

Modes (``--mode``): ``lexical`` = reranker off; ``rerank`` = ``LGX_RERANK=on`` against ``--rerank-url`` (a reranker
failure is a ``BACKEND_UNAVAILABLE`` error, never a silent lexical result).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

DEV_DSN = "postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux"
DEV_RERANK_URL = "http://127.0.0.1:8090"


@dataclass
class Call:
    is_error: bool
    structured: dict[str, Any]
    latency_ms: float
    skipped: str | None = None


class Backend(Protocol):
    description: dict[str, Any]

    async def call(self, tool: str, arguments: Mapping[str, Any], *, clock: str | None = None) -> Call: ...


@dataclass(frozen=True)
class Options:
    mode: str = "lexical"  # lexical | rerank
    dsn: str = DEV_DSN
    rerank_url: str = DEV_RERANK_URL
    rerank_k: int = 16
    rerank_body_chars: int = 600
    rerank_timeout_s: float = 180.0
    ranker: str = "bm25"  # bm25 | rank_cd
    expansion: bool = True
    lexical_opts: str = ""  # JSON object of ranker knobs (LGX_LEXICAL_OPTS)
    clock: str | None = None  # ISO datetime; default noon of T


class InProcessBackend:
    def __init__(self, opts: Options) -> None:
        from lgx_mcp.audit import AuditLog
        from lgx_mcp.runtime import create_runtime
        from lgx_mcp.settings import McpSettings

        env = {
            "LGX_MCP_PG_DSN": opts.dsn,
            "LGX_RERANK": "on" if opts.mode == "rerank" else "off",
            "LGX_RERANK_URL": opts.rerank_url if opts.mode == "rerank" else "",
            "LGX_RERANK_K": str(opts.rerank_k),
            "LGX_RERANK_BATCH": str(min(32, opts.rerank_k)),
            "LGX_RERANK_BODY_CHARS": str(opts.rerank_body_chars),
            "LGX_RERANK_TIMEOUT_S": str(opts.rerank_timeout_s),
            "LGX_LEXICAL_RANKER": opts.ranker,
            "LGX_QUERY_EXPANSION": "1" if opts.expansion else "0",
            "LGX_LEXICAL_OPTS": opts.lexical_opts,
            "LGX_ALLOW_EXTERNAL_URLS": "0",
        }
        settings = McpSettings.from_env(env=env, transport=None)
        self.settings = replace(settings, rate_per_client_min=0, rate_global_min=0, clock_override=opts.clock)
        self.settings.validate(transport="stdio")
        self.rt = create_runtime(self.settings)
        self.audit = AuditLog(str(Path("/dev/null")), b"eval-harness-key", query_text=False)
        self.opts = opts
        self._default_clock = opts.clock
        self.description = {
            "backend": "in-process",
            "mode": opts.mode,
            "ranker": opts.ranker,
            "expansion": opts.expansion,
            "lexical_opts": self.settings.lexical_opts,
            "rerank_k": opts.rerank_k if opts.mode == "rerank" else None,
            "rerank_body_chars": opts.rerank_body_chars if opts.mode == "rerank" else None,
            "rerank_url": opts.rerank_url if opts.mode == "rerank" else None,
        }

    async def start(self) -> dict[str, Any]:
        from lgx_mcp.context import Clock

        await self.rt.db.open()
        rel = await self.rt.release()
        if self._default_clock is None:
            self._default_clock = f"{rel.sync_date.isoformat()}T12:00:00+02:00"
        self.rt.clock = Clock(self.settings.tz, self._default_clock)
        info = {
            "release_id": rel.release_id,
            "sync_date": rel.sync_date.isoformat(),
            "search_api": rel.search_api,
            "clock": self._default_clock,
        }
        if self.opts.mode == "rerank":
            state = await self.rt.reranker.probe(force=True)
            info["reranker"] = {"status": state.status, "reason": state.reason}
            if state.status != "ok":
                raise RuntimeError(f"--mode rerank but the reranker at {self.opts.rerank_url} is {state.reason}")
        self.description.update(info)
        return info

    async def stop(self) -> None:
        await self.rt.db.close()

    async def call(self, tool: str, arguments: Mapping[str, Any], *, clock: str | None = None) -> Call:
        from lgx_mcp.context import Clock
        from lgx_mcp.server import run_tool

        if clock:
            self.rt.clock = Clock(self.settings.tz, clock)
        t0 = time.perf_counter()
        try:
            res = await run_tool(self.rt, self.audit, tool, dict(arguments))
        finally:
            if clock:
                self.rt.clock = Clock(self.settings.tz, self._default_clock)
        ms = (time.perf_counter() - t0) * 1000
        return Call(bool(res.is_error), dict(res.structured_content or {}), ms)


class HttpBackend:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.description = {"backend": "http", "url": url}
        self._stack: contextlib.AsyncExitStack | None = None
        self._client: Any = None

    async def start(self) -> dict[str, Any]:
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        self._stack = contextlib.AsyncExitStack()
        http = await self._stack.enter_async_context(
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}, timeout=300, trust_env=False)
        )
        self._client = await self._stack.enter_async_context(Client(streamable_http_client(self.url, http_client=http)))
        about = await self.call("about_corpus", {})
        snap = about.structured.get("snapshot") or {}
        info = {"release_id": snap.get("release_id"), "sync_date": snap.get("sync_date")}
        self.description.update(info)
        return info

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def call(self, tool: str, arguments: Mapping[str, Any], *, clock: str | None = None) -> Call:
        if clock:
            return Call(False, {}, 0.0, skipped="the item pins a server clock (in-process backend only)")
        t0 = time.perf_counter()
        res = await self._client.call_tool(tool, dict(arguments))
        ms = (time.perf_counter() - t0) * 1000
        return Call(bool(res.is_error), dict(res.structured_content or {}), ms)


@contextlib.asynccontextmanager
async def open_backend(
    opts: Options, *, url: str | None = None, token: str | None = None
) -> AsyncIterator[InProcessBackend | HttpBackend]:
    backend: InProcessBackend | HttpBackend = HttpBackend(url, token or "") if url else InProcessBackend(opts)
    await backend.start()
    try:
        yield backend
    finally:
        await backend.stop()
