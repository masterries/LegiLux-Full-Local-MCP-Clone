"""MCP client of the tester UI: official ``mcp==2.3.0`` client over Streamable HTTP with a bearer token.

The UI talks to the server only through the MCP protocol (``tools/list`` → dynamic tool set,
``tools/call``, ``prompts/get``), so it keeps working when tool details change. Streamlit re-runs scripts,
so every user action opens a short-lived client (the server is stateless, PLAN §9.3) inside
:func:`run_sync`.

Everything a caller needs is normalised into plain dataclasses (:class:`ToolSpec`, :class:`ToolOutcome`);
transport and protocol failures never raise out of :meth:`LgxMcpClient.call_tool`, they come back as
``ToolOutcome(is_error=True, error_code="CLIENT_ERROR" | "TIMEOUT")`` so the tool loop can feed them to
the model.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

import anyio
import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

CLIENT_INFO_NAME = "lgx-streamlit"


@dataclass(frozen=True)
class ToolSpec:
    """One tool as listed by ``tools/list``."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] | None = None
    title: str | None = None
    read_only: bool | None = None

    @property
    def properties(self) -> dict[str, Any]:
        props = self.input_schema.get("properties")
        return props if isinstance(props, dict) else {}


@dataclass
class ToolOutcome:
    """Result of one ``tools/call`` (or of a call that never reached the server)."""

    name: str
    arguments: dict[str, Any]
    is_error: bool
    structured: Any = None
    text: str = ""
    latency_s: float = 0.0
    error_code: str | None = None
    error_message: str | None = None

    @property
    def error(self) -> dict[str, Any] | None:
        """The Appendix D.0 error object (``{code, message, hint, candidates}``) of an ``isError`` result."""
        if not self.is_error:
            return None
        if isinstance(self.structured, dict) and isinstance(self.structured.get("error"), dict):
            return self.structured["error"]
        return {"code": self.error_code or "ERROR", "message": self.error_message or self.text or "erreur"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "is_error": self.is_error,
            "latency_s": round(self.latency_s, 4),
            "structured": self.structured,
            "text": self.text,
            "error": self.error,
        }


@runtime_checkable
class ToolSession(Protocol):
    """What the tool loop, Direct search and Eval need from an MCP connection."""

    async def list_tools(self) -> list[ToolSpec]: ...

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome: ...

    async def get_prompt(self, name: str, arguments: Mapping[str, str] | None = None) -> str | None: ...


class McpUnavailableError(RuntimeError):
    """The MCP server could not be reached or the handshake failed."""


def leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten (nested) exception groups, which anyio task groups raise for transport errors."""
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for sub in exc.exceptions:
            out.extend(leaf_exceptions(sub))
        return out
    return [exc]


def describe_exception(exc: BaseException) -> str:
    """A short, secret-free description of a client-side failure."""
    parts: list[str] = []
    for leaf in leaf_exceptions(exc):
        if isinstance(leaf, httpx2.ConnectError | httpx.ConnectError | ConnectionError):
            text = f"connexion impossible ({type(leaf).__name__})"
        elif isinstance(leaf, TimeoutError | httpx2.TimeoutException | httpx.TimeoutException):
            text = "délai dépassé"
        else:
            text = f"{type(leaf).__name__}: {leaf}".strip()
        if text not in parts:
            parts.append(text)
    return "; ".join(parts)[:500] or type(exc).__name__


def _text_of(content: Any) -> str:
    texts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def _spec_from_tool(tool: Any) -> ToolSpec:
    annotations = getattr(tool, "annotations", None)
    schema = getattr(tool, "input_schema", None)
    return ToolSpec(
        name=tool.name,
        description=getattr(tool, "description", None) or "",
        input_schema=dict(schema) if isinstance(schema, Mapping) else {"type": "object", "properties": {}},
        output_schema=getattr(tool, "output_schema", None),
        title=getattr(tool, "title", None) or getattr(annotations, "title", None),
        read_only=getattr(annotations, "read_only_hint", None),
    )


class LgxMcpClient:
    """Async context manager around :class:`mcp.Client` (implements :class:`ToolSession`).

    ``server`` replaces the URL with anything :class:`mcp.Client` accepts (an in-process ``Server`` in
    tests); ``http_client`` replaces the default ``httpx2.AsyncClient`` (tests mount an ASGI app).
    The default client sends ``Authorization: Bearer <token>`` and ignores proxy environment variables
    (``trust_env=False``, PLAN §8.1): the MCP server is an internal service name.
    """

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_s: float = 20.0,
        server: Any = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self._token = token
        self._server = server
        self._http_client = http_client
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self._tools: list[ToolSpec] | None = None

    async def __aenter__(self) -> LgxMcpClient:
        stack = AsyncExitStack()
        try:
            if self._server is not None:
                target: Any = self._server
            else:
                http_client = self._http_client
                if http_client is None:
                    headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
                    http_client = httpx2.AsyncClient(
                        headers=headers,
                        timeout=httpx2.Timeout(self.timeout_s, read=self.timeout_s),
                        trust_env=False,
                    )
                await stack.enter_async_context(http_client)
                target = streamable_http_client(self.url, http_client=http_client, terminate_on_close=False)
            client = Client(target, read_timeout_seconds=self.timeout_s)
            # No anyio.fail_after here: the transport opens task groups that outlive this call, and a cancel
            # scope must not be exited before them. The handshake is bounded by read_timeout_seconds and the
            # httpx2 timeouts instead.
            self._client = await stack.enter_async_context(client)
        except BaseException as exc:
            await stack.aclose()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise McpUnavailableError(f"MCP {self.display_url}: {describe_exception(exc)}") from exc
        self._stack = stack
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            with contextlib.suppress(Exception):  # closing a broken transport must not hide the real result
                await stack.aclose()

    @property
    def display_url(self) -> str:
        """The URL without credentials or query string (safe to show)."""
        if self._server is not None:
            return "in-process"
        parts = urlsplit(self.url)
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    @property
    def protocol_version(self) -> str | None:
        return self._client.protocol_version if self._client is not None else None

    def _require(self) -> Client:
        if self._client is None:
            raise RuntimeError("LgxMcpClient must be used as 'async with'")
        return self._client

    async def list_tools(self) -> list[ToolSpec]:
        client = self._require()
        specs: list[ToolSpec] = []
        cursor: str | None = None
        for _ in range(20):  # pagination guard
            page = await client.list_tools(cursor=cursor)
            specs.extend(_spec_from_tool(t) for t in page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        self._tools = specs
        return specs

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        client = self._require()
        args = dict(arguments)
        start = time.perf_counter()
        try:
            with anyio.fail_after(self.timeout_s):
                result = await client.call_tool(name, args, read_timeout_seconds=self.timeout_s)
        except TimeoutError:
            return ToolOutcome(
                name,
                args,
                True,
                latency_s=time.perf_counter() - start,
                error_code="TIMEOUT",
                error_message=f"pas de réponse de l'outil en {self.timeout_s:g} s",
            )
        except Exception as exc:
            return ToolOutcome(
                name,
                args,
                True,
                latency_s=time.perf_counter() - start,
                error_code="CLIENT_ERROR",
                error_message=describe_exception(exc),
            )
        structured = result.structured_content
        text = _text_of(result.content)
        if structured is None and text:
            try:  # some servers return JSON only as text
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                structured = parsed
        outcome = ToolOutcome(
            name, args, bool(result.is_error), structured, text, latency_s=time.perf_counter() - start
        )
        if outcome.is_error:
            err = outcome.error or {}
            outcome.error_code = str(err.get("code") or "ERROR")
            outcome.error_message = str(err.get("message") or text or "erreur")
        return outcome

    async def get_prompt(self, name: str, arguments: Mapping[str, str] | None = None) -> str | None:
        """Concatenated text of a prompt's messages, or ``None`` when the server does not offer it."""
        client = self._require()
        try:
            with anyio.fail_after(self.timeout_s):
                result = await client.get_prompt(name, dict(arguments or {}))
        except Exception:
            return None
        texts = [getattr(m.content, "text", "") for m in result.messages]
        joined = "\n\n".join(t for t in texts if t).strip()
        return joined or None


# --------------------------------------------------------------------------------------------- health


@dataclass(frozen=True)
class HealthStatus:
    """``GET /readyz`` of the MCP server (exempt from auth; degraded states in the body, Appendix D.0)."""

    reachable: bool
    ready: bool
    status_code: int | None = None
    body: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_s: float = 0.0

    @property
    def vector_status(self) -> str | None:
        value = self.body.get("vector_status")
        return str(value) if value is not None else None


def health_url(mcp_url: str, path: str = "/readyz") -> str:
    parts = urlsplit(mcp_url)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def fetch_readyz(mcp_url: str, *, timeout_s: float = 3.0, client: httpx.Client | None = None) -> HealthStatus:
    url = health_url(mcp_url)
    start = time.perf_counter()
    try:
        if client is None:
            with httpx.Client(timeout=timeout_s, trust_env=False) as own:
                resp = own.get(url)
        else:
            resp = client.get(url, timeout=timeout_s)
    except httpx.HTTPError as exc:
        return HealthStatus(False, False, error=describe_exception(exc), latency_s=time.perf_counter() - start)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return HealthStatus(
        True,
        resp.status_code == 200,
        resp.status_code,
        body if isinstance(body, dict) else {},
        None if resp.status_code == 200 else f"HTTP {resp.status_code}",
        time.perf_counter() - start,
    )


# --------------------------------------------------------------------------------------------- sync bridge


def run_sync[R](coro: Coroutine[Any, Any, R]) -> R:
    """Run a coroutine from Streamlit's script thread (no loop running there); falls back to a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


SessionFactory = Callable[[], Any]
"""Returns an async context manager yielding a :class:`ToolSession`."""


async def with_session[R](factory: SessionFactory, fn: Callable[[ToolSession], Awaitable[R]]) -> R:
    """Open a short-lived session, run ``fn`` on it, close it."""
    async with factory() as session:
        return await fn(session)
