"""OpenAI-compatible Chat Completions client (official ``openai==3.24.0``; PLAN §9.3 "LLM access").

Works against Ollama ``/v1``, vLLM, the user's Mistral on-prem endpoint or, on the connected dev host only,
a cloud endpoint (opt-in ``cloudllm`` profile). The transport is an ``httpx2.AsyncClient`` with
``trust_env=False`` by default, so internal endpoints are never reached through a proxy.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2
import openai

from lgx_ui.settings import NO_KEY


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str = NO_KEY
    temperature: float = 0.2
    timeout_s: float = 120.0
    trust_env: bool = False
    max_tokens: int | None = None


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments_json: str


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str | None = None
    latency_s: float = 0.0
    model: str | None = None
    usage: dict[str, Any] | None = None

    def assistant_message(self) -> dict[str, Any]:
        """The message to append to the transcript (OpenAI shape, tool calls included)."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments_json}}
                for tc in self.tool_calls
            ]
        return msg


class LlmError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ToolsUnsupportedError(LlmError):
    """The endpoint rejected the ``tools`` parameter (e.g. Ollama: "model does not support tools")."""


class ChatModel(Protocol):
    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> ChatResponse: ...


def _error_text(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
    return str(exc.message)


def _arguments_json(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if raw is None:
        return "{}"
    return json.dumps(raw, ensure_ascii=False)


class OpenAICompatChat:
    """Async context manager; one instance per user turn (Streamlit runs each turn in ``asyncio.run``)."""

    def __init__(self, config: LlmConfig, *, http_client: httpx2.AsyncClient | None = None) -> None:
        self.config = config
        self._http = http_client or httpx2.AsyncClient(
            timeout=httpx2.Timeout(config.timeout_s, connect=min(10.0, config.timeout_s)),
            trust_env=config.trust_env,
        )
        self._client = openai.AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or NO_KEY,
            timeout=config.timeout_s,
            max_retries=0,  # the tool loop owns retries and the time budget
            http_client=self._http,
        )

    async def __aenter__(self) -> OpenAICompatChat:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": self.config.temperature,
        }
        if tools:
            kwargs["tools"] = list(tools)
            kwargs["tool_choice"] = tool_choice or "auto"
        if self.config.max_tokens:
            kwargs["max_tokens"] = self.config.max_tokens
        start = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            text = _error_text(exc)
            if tools and exc.status_code in (400, 404, 422, 500, 501) and "tool" in text.lower():
                raise ToolsUnsupportedError(text, exc.status_code) from exc
            raise LlmError(f"LLM HTTP {exc.status_code}: {text}"[:600], exc.status_code) from exc
        except openai.APITimeoutError as exc:
            raise LlmError(f"LLM : délai dépassé ({self.config.timeout_s:g} s)") from exc
        except openai.APIConnectionError as exc:
            raise LlmError(f"LLM injoignable ({self.config.base_url}) : {type(exc.__cause__ or exc).__name__}") from exc
        latency = time.perf_counter() - start
        if not resp.choices:
            raise LlmError("LLM : réponse sans 'choices'")
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCallRequest] = []
        for i, tc in enumerate(msg.tool_calls or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            calls.append(ToolCallRequest(tc.id or f"call_{i + 1}", fn.name, _arguments_json(fn.arguments)))
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) is not None else None
        return ChatResponse(msg.content, calls, choice.finish_reason, latency, resp.model, usage)
