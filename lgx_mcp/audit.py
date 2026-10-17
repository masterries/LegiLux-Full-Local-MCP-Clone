"""Audit log (PLAN §8.8): one JSON line per tool call.

Fields: ``ts``, ``tool``, ``latency_ms``, ``hits``, ``is_error``/``error_code``, ``args_sha256`` (canonical JSON of
the arguments), ``client`` (HMAC of the Open WebUI user/chat headers or of the peer address), ``protocol_version``.
The question text is **off** by default (OD14); ``LGX_AUDIT_QUERY_TEXT=true`` adds ``query``. Lines go to
``LGX_AUDIT_FILE`` when set, else to the ``lgx_mcp.audit`` logger (stderr — never stdout, which is the stdio
transport).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger("lgx_mcp.audit")

# Header names Open WebUI forwards when ENABLE_FORWARD_USER_INFO_HEADERS is on (exact names [U] per PLAN §8.8).
USER_HEADERS = ("x-openwebui-user-id", "x-openwebui-user-email", "x-lgx-user")
CHAT_HEADERS = ("x-openwebui-chat-id", "x-lgx-chat")
QUERY_ARGS = ("query", "citation", "text")


def canonical_sha256(arguments: Mapping[str, Any]) -> str:
    blob = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str | None, hmac_key: bytes, *, query_text: bool = False) -> None:
        self.path = Path(path) if path else None
        self.key = hmac_key
        self.query_text = query_text
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _mac(self, value: str) -> str:
        return hmac.new(self.key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    def client_id(self, headers: Mapping[str, str] | None, peer: str | None) -> str:
        """Stable pseudonymous client key (rate limits, audit); never the raw header value."""
        if headers:
            for name in USER_HEADERS:
                if headers.get(name):
                    return "u:" + self._mac(headers[name])
        return "p:" + self._mac(peer or "local")

    def record(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any],
        latency_ms: float,
        hits: int,
        error_code: str | None,
        headers: Mapping[str, str] | None,
        peer: str | None,
        protocol_version: str | None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
            "tool": tool,
            "latency_ms": round(latency_ms, 1),
            "hits": hits,
            "is_error": error_code is not None,
            "error_code": error_code,
            "args_sha256": canonical_sha256(arguments),
            "client": self.client_id(headers, peer),
            "protocol_version": protocol_version,
        }
        if headers:
            for name in CHAT_HEADERS:
                if headers.get(name):
                    entry["chat"] = self._mac(headers[name])
                    break
        if self.query_text:
            for k in QUERY_ARGS:
                if isinstance(arguments.get(k), str):
                    entry["query"] = arguments[k]
                    break
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        if self.path is not None:
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        else:
            log.info(line)
        return entry
