"""The Mistral embeddings API client (PLAN §6.5): ``POST {base_url}/v1/embeddings`` with a Bearer key.

The same client embeds documents on the build side (``lgx embed``, Mistral SaaS) and queries in the airgap
(``lgx-mcp``, the user's on-prem domain); only ``base_url`` differs. Every request — retries included — takes
one slot of the shared :class:`~lgx_core.embed.limiter.SlotLimiter` (``max_concurrency``, ``cooldown_s``).
Retries cover 429 / 5xx (``retry_on_status``) and network errors, with ``backoff_s`` and the server's
``Retry-After`` (the longer wins). Every response is checked: one vector per input, each of length ``dim``.
The model id the API reports is returned and recorded, never assumed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from lgx_core import __version__
from lgx_core.config import EmbeddingConfig
from lgx_core.embed.limiter import Clock, Sleep, SlotLimiter, ThrottledError
from lgx_core.textsize import estimate_tokens

# The startup probe (lgx-mcp): embedded once on the build side (stored in lgx.embedding_model) and again by the
# runtime endpoint; the two vectors must agree (cosine >= probe_min_cosine).
PROBE_TEXT = "Le contrat de travail à durée indéterminée peut être résilié par l'employeur moyennant un préavis."


class EmbeddingError(RuntimeError):
    """A request failed for good. ``reason`` becomes ``vector_status = disabled:<reason>`` at runtime."""

    def __init__(self, message: str, *, reason: str = "endpoint_error", status: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    model: str  # the id the API reports (falls back to the requested id when the response has none)
    tokens: int | None = None
    attempts: int = 1


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def plan_batches(texts: Sequence[str], batch_size: int, max_tokens: int) -> list[list[int]]:
    """Indices of ``texts`` grouped into requests: ≤ ``batch_size`` texts and ≤ ``max_tokens`` estimated tokens
    each (a single longer text goes alone; the API truncates or rejects it, which is reported)."""
    batches: list[list[int]] = []
    current: list[int] = []
    tokens = 0
    for i, text in enumerate(texts):
        t = estimate_tokens(text)
        if current and (len(current) >= batch_size or tokens + t > max_tokens):
            batches.append(current)
            current, tokens = [], 0
        current.append(i)
        tokens += t
    if current:
        batches.append(current)
    return batches


def retry_after_s(value: str | None, now: dt.datetime | None = None) -> float | None:
    """``Retry-After`` as seconds (delta-seconds or an HTTP date); ``None`` when absent or unparsable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return max(0.0, (when - (now or dt.datetime.now(dt.UTC))).total_seconds())


class MistralEmbedder:
    """Async client; use as ``async with MistralEmbedder(cfg, api_key=…) as emb: await emb.embed([...])``."""

    def __init__(
        self,
        cfg: EmbeddingConfig,
        *,
        api_key: str | None,
        limiter: SlotLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = False,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.cfg = cfg
        self._sleep: Sleep = sleep or asyncio.sleep
        self.limiter = limiter or SlotLimiter(cfg.max_concurrency, cfg.cooldown_s, clock=clock, sleep=sleep)
        headers = {"Accept": "application/json", "User-Agent": user_agent or f"lgx/{__version__}"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(cfg.timeout_s),
            trust_env=trust_env,  # the build host may need its HTTPS proxy; lgx-mcp never uses one
            headers=headers,
            follow_redirects=False,
        )
        self.tokens = 0
        self.retries = 0

    async def __aenter__(self) -> MistralEmbedder:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(
        self, texts: Sequence[str], *, slot_timeout: float | None = None, retries: int | None = None
    ) -> EmbedResult:
        """One request (``len(texts) ≤ batch_size``) with retries; raises :class:`EmbeddingError`."""
        if not texts:
            return EmbedResult([], self.cfg.model, 0, 0)
        max_retries = self.cfg.retries if retries is None else retries
        payload = {"model": self.cfg.model, "input": list(texts)}
        attempt = 0
        while True:
            attempt += 1
            delay = 0.0
            try:
                async with self.limiter.slot(slot_timeout):
                    resp = await self._client.post(self.cfg.endpoint, json=payload)
            except ThrottledError as exc:
                raise EmbeddingError(str(exc), reason="throttled") from exc
            except httpx.TimeoutException as exc:
                error = EmbeddingError(f"timeout after {self.cfg.timeout_s:g} s: {exc}", reason="endpoint_timeout")
            except httpx.TransportError as exc:
                error = EmbeddingError(f"{type(exc).__name__}: {exc}", reason="endpoint_unreachable")
            else:
                if resp.status_code == 200:
                    result = self._parse(resp, len(texts), attempt)
                    self.tokens += result.tokens or 0
                    return result
                text = resp.text[:300].replace("\n", " ")
                if resp.status_code in (401, 403):
                    raise EmbeddingError(
                        f"HTTP {resp.status_code}: {text}", reason="unauthorized", status=resp.status_code
                    )
                if resp.status_code not in self.cfg.retry_on_status:
                    raise EmbeddingError(
                        f"HTTP {resp.status_code}: {text}", reason="endpoint_error", status=resp.status_code
                    )
                reason = "rate_limited" if resp.status_code == 429 else "endpoint_error"
                error = EmbeddingError(f"HTTP {resp.status_code}: {text}", reason=reason, status=resp.status_code)
                delay = retry_after_s(resp.headers.get("Retry-After")) or 0.0
            if attempt > max_retries:
                raise error
            backoff = self.cfg.backoff_s[min(attempt - 1, len(self.cfg.backoff_s) - 1)] if self.cfg.backoff_s else 0
            self.retries += 1
            await self._sleep(max(float(backoff), delay))

    def _parse(self, resp: httpx.Response, n: int, attempts: int) -> EmbedResult:
        try:
            data: dict[str, Any] = resp.json()
            items = sorted(data["data"], key=lambda d: int(d.get("index", 0)))
            vectors = [[float(x) for x in item["embedding"]] for item in items]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError(f"unexpected response: {exc}", reason="bad_response") from exc
        if len(vectors) != n:
            raise EmbeddingError(f"{len(vectors)} vectors for {n} inputs", reason="bad_response")
        bad = {len(v) for v in vectors if len(v) != self.cfg.dim}
        if bad:
            raise EmbeddingError(f"vector length {sorted(bad)} != dim {self.cfg.dim}", reason="dim_mismatch")
        usage = data.get("usage") or {}
        tokens = usage.get("prompt_tokens") or usage.get("total_tokens")
        model = str(data.get("model") or self.cfg.model)
        return EmbedResult(vectors, model, int(tokens) if tokens is not None else None, attempts)


__all__ = [
    "PROBE_TEXT",
    "EmbedResult",
    "EmbeddingError",
    "MistralEmbedder",
    "cosine",
    "plan_batches",
    "retry_after_s",
]
