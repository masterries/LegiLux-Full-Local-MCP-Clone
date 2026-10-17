#!/usr/bin/env python3
"""A deterministic OpenAI-compatible Chat Completions stub (stdlib only) for keyless tests of the chat path.

No model, no network: it lets the tester UI's real tool loop run end to end (UI → stub → MCP tools → stub →
answer) on a host without an LLM key, inside the internal network of the egress check, or as "any
OpenAI-compatible URL" in the sidebar. Behaviour:

* ``GET /v1/models`` lists one model (``lgx-stub``);
* ``POST /v1/chat/completions`` with ``tools`` and no ``role: tool`` message yet → one ``search_legislation`` tool
  call whose query is the last user message;
* with tool results in the conversation → a short French answer that cites the first ``citation_string`` found
  in the tool results (or says nothing was found), then the disclaimer;
* without ``tools`` → a plain answer (the retrieve-first path).

Usage: ``python3 scripts/openai_stub.py [--host 127.0.0.1] [--port 8089]`` (inside a container:
``docker compose -f deploy/compose.dev.yml exec -T lgx-streamlit python - --port 8089 < scripts/openai_stub.py``).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "lgx-stub"
CITATION = re.compile(r'"citation_string"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def reply(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") or []
    tools = body.get("tools") or []
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    message: dict[str, Any]
    if tools and not tool_msgs:
        names = [t.get("function", {}).get("name") for t in tools]
        name = "search_legislation" if "search_legislation" in names else names[0]
        args = {"query": _last_user(messages)[:900] or "contrat de travail", "top_k": 3}
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_stub_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            ],
        }
        finish = "tool_calls"
    else:
        found = None
        for m in tool_msgs + [m for m in messages if m.get("role") == "user"]:
            hit = CITATION.search(str(m.get("content") or ""))
            if hit:
                found = json.loads(f'"{hit.group(1)}"')
                break
        text = (
            f"Selon les textes disponibles localement, voir {found} [C1]."
            if found
            else "Les textes disponibles localement ne permettent pas de répondre."
        )
        message = {"role": "assistant", "content": text + " Information non officielle, pas un avis juridique."}
        finish = "stop"
    prompt = sum(len(str(m.get("content") or "")) for m in messages) // 4
    return {
        "id": f"chatcmpl-stub-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": 20, "total_tokens": prompt + 20},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "lgx-openai-stub/1"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "lgx"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self._send(200, reply(body))

    def log_message(self, fmt: str, *args: Any) -> None:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8089)
    a = ap.parse_args()
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"openai stub on http://{a.host}:{a.port}/v1 (model {MODEL})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
