"""ASGI guard for ``/mcp`` (PLAN §8.8): Host / Origin allowlists and the bearer token.

* ``/livez``, ``/healthz`` and ``/readyz`` are exempt (no token, any Host) — they carry no corpus data.
* Host: exact ``host:port`` entries, ``host:*`` (any port, or none), ``*`` (any; development only) → else 421.
* Origin: absent is fine (server-side clients); present must be in ``LGX_ALLOWED_ORIGINS`` → else 403.
* ``Authorization: Bearer <token>`` compared in constant time → else 401 with ``WWW-Authenticate: Bearer``.

The same allowlists are handed to the SDK's ``TransportSecuritySettings`` (defence in depth, same semantics).
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterable

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

EXEMPT_PATHS = frozenset({"/livez", "/healthz", "/readyz"})


def host_allowed(host: str | None, allowed: Iterable[str]) -> bool:
    if not host:
        return False
    host = host.strip().lower()
    for a in allowed:
        a = a.strip().lower()
        if a == "*" or a == host:
            return True
        if a.endswith(":*"):
            base = a[:-2]
            if host == base or host.startswith(base + ":"):
                return True
    return False


def origin_allowed(origin: str | None, allowed: Iterable[str]) -> bool:
    if not origin:
        return True
    origin = origin.strip().lower().rstrip("/")
    for a in allowed:
        a = a.strip().lower().rstrip("/")
        if a == "*" or a == origin:
            return True
        if a.endswith(":*") and (origin == a[:-2] or origin.startswith(a[:-2] + ":")):
            return True
    return False


def sdk_security(allowed_hosts: Iterable[str], allowed_origins: Iterable[str]) -> TransportSecuritySettings:
    hosts = [h.strip() for h in allowed_hosts if h.strip()]
    origins = [o.strip() for o in allowed_origins if o.strip()]
    if "*" in hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    sdk_hosts: list[str] = []
    for h in hosts:
        sdk_hosts.append(h)
        if h.endswith(":*"):
            sdk_hosts.append(h[:-2])  # our « host:* » also admits a bare Host without port
    sdk_origins = [o for o in origins if o != "*"]
    if "*" in origins:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=sdk_hosts, allowed_origins=sdk_origins
    )


async def _reply(send: Send, status: int, body: dict[str, str], extra: list[tuple[bytes, bytes]] | None = None) -> None:
    data = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra or [])})
    await send({"type": "http.response.body", "body": data})


class GuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        mcp_path: str,
        token: str | None,
        allowed_hosts: Iterable[str],
        allowed_origins: Iterable[str],
    ) -> None:
        self.app = app
        self.mcp_path = mcp_path.rstrip("/") or "/mcp"
        self.token = token.encode() if token else None
        self.allowed_hosts = tuple(allowed_hosts)
        self.allowed_origins = tuple(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if path == self.mcp_path or path.startswith(self.mcp_path + "/"):
            headers = Headers(scope=scope)
            if not host_allowed(headers.get("host"), self.allowed_hosts):
                await _reply(send, 421, {"error": "invalid Host header"})
                return
            if not origin_allowed(headers.get("origin"), self.allowed_origins):
                await _reply(send, 403, {"error": "origin not allowed"})
                return
            if self.token is not None:
                auth = headers.get("authorization", "")
                scheme, _, value = auth.partition(" ")
                ok = scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), self.token)
                if not ok:
                    await _reply(
                        send,
                        401,
                        {"error": "missing or invalid bearer token"},
                        [(b"www-authenticate", b'Bearer realm="lgx-mcp"')],
                    )
                    return
        await self.app(scope, receive, send)
