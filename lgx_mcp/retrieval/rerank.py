"""Optional cross-encoder reranking over TEI ``/rerank`` (PLAN §8.3 step 3).

* Disabled cleanly when ``LGX_RERANK_URL`` is unset or ``LGX_RERANK=off``: ``rerank_status = off``.
* ``LGX_RERANK=auto`` (default): an unreachable reranker gives unreranked results with a reason.
  ``LGX_RERANK=on``: the tool fails with ``BACKEND_UNAVAILABLE`` instead.
* TEI 1.9.4 rejects more than ``--max-client-batch-size`` texts (default 32) per call: requests are split into
  batches of at most ``LGX_RERANK_BATCH`` (≤ 32) and the scores are merged.
* httpx with ``trust_env=False`` (no proxy, no netrc); only the configured internal URL is ever called.
* After a failure the reranker is not called again until the next probe (``REPROBE_FAIL_S``): searches degrade at
  once with the recorded reason instead of each waiting for the timeout.
* Text per candidate (:func:`rerank_text`): the chunk header without its version label (« — Version consolidée
  applicable au … » carries no relevance signal) plus the body cut to ``LGX_RERANK_BODY_CHARS`` (default 1,800 ≈
  512 tokens, PLAN §8.3). On 4 idle vCPU (fp32): 16 candidates with 600 body characters ≈ 19 s, with 1,800
  ≈ 60 s; one 1,800-character pair ≈ 3.8 s (eval/reports/m2-baseline.md).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("lgx_mcp.rerank")

BODY_CHARS = 1800  # ≈ 512 tokens by ceil(chars / 3.5); TEI --auto-truncate cuts anything longer
HEADER_CHARS = 400  # compacted header (title + innermost breadcrumb levels + article + index terms)
PROBE_QUERY = "durée du préavis de licenciement du salarié"
PROBE_TEXTS = (
    "La pêche à la ligne est autorisée dans les cours d'eau du domaine public.",
    "En cas de licenciement, le salarié a droit à un délai de préavis dont la durée dépend de son ancienneté.",
)
PROBE_EXPECTED_BEST = 1
REPROBE_OK_S = 300.0
REPROBE_FAIL_S = 60.0


class RerankUnavailableError(RuntimeError):
    """The reranker did not answer (or answered nonsense)."""


@dataclass
class RerankState:
    status: str  # ok | unavailable | off
    reason: str | None
    checked_at: float = 0.0


@dataclass
class RerankStats:
    """Timing of the last :meth:`Reranker.rerank` call (per HTTP batch), for ``retrieval.latency_ms``."""

    texts: int = 0
    chars: int = 0
    batch_ms: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"texts": self.texts, "chars": self.chars, "batches": len(self.batch_ms), "batch_ms": self.batch_ms}


class Reranker:
    def __init__(
        self,
        url: str | None,
        mode: str = "auto",
        *,
        batch: int = 32,
        timeout_s: float = 10.0,
        body_chars: int = BODY_CHARS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/") if url else None
        self.mode = mode
        self.batch = max(1, min(32, batch))
        self.timeout_s = timeout_s
        self.body_chars = max(100, body_chars)
        self.last = RerankStats()
        self._transport = transport
        if not self.url:
            self.state = RerankState("off", "LGX_RERANK_URL not set")
        elif mode == "off":
            self.state = RerankState("off", "LGX_RERANK=off")
        else:
            self.state = RerankState("unavailable", "not probed yet")

    @property
    def enabled(self) -> bool:
        return self.url is not None and self.mode != "off"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_s, trust_env=False, transport=self._transport)

    async def _call(self, client: httpx.AsyncClient, query: str, texts: Sequence[str]) -> list[float]:
        resp = await client.post(f"{self.url}/rerank", json={"query": query, "texts": list(texts), "truncate": True})
        resp.raise_for_status()
        data = resp.json()
        scores = [0.0] * len(texts)
        if not isinstance(data, list):
            raise RerankUnavailableError("unexpected /rerank response")
        for item in data:
            idx = int(item["index"])
            if not 0 <= idx < len(texts):
                raise RerankUnavailableError("index out of range in /rerank response")
            scores[idx] = float(item["score"])
        return scores

    def recently_failed(self) -> bool:
        """True while a recorded failure is younger than the re-probe interval (no call, degrade at once)."""
        s = self.state
        return s.status == "unavailable" and s.checked_at > 0 and time.monotonic() - s.checked_at < REPROBE_FAIL_S

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """Scores aligned with ``texts`` (higher is better); batches of at most ``self.batch``."""
        if not self.enabled:
            raise RerankUnavailableError(self.state.reason or "reranker disabled")
        if self.recently_failed():
            raise RerankUnavailableError(self.state.reason or "reranker unavailable")
        scores: list[float] = []
        stats = RerankStats(texts=len(texts), chars=sum(len(t) for t in texts))
        try:
            async with self._client() as client:
                for i in range(0, len(texts), self.batch):
                    t0 = time.perf_counter()
                    scores.extend(await self._call(client, query, texts[i : i + self.batch]))
                    stats.batch_ms.append(round((time.perf_counter() - t0) * 1000, 1))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self.state = RerankState("unavailable", f"reranker error: {type(exc).__name__}", time.monotonic())
            raise RerankUnavailableError(self.state.reason or "reranker error") from exc
        self.state = RerankState("ok", None, time.monotonic())
        self.last = stats
        return scores

    def text(self, header: str, body: str) -> str:
        return rerank_text(header, body, self.body_chars)

    async def probe(self, *, force: bool = False) -> RerankState:
        """Startup check 3 (PLAN §8.1): a probe pair must come back in the expected order. Cached."""
        if not self.enabled:
            return self.state
        age = time.monotonic() - self.state.checked_at
        if (
            not force
            and self.state.checked_at
            and age < (REPROBE_OK_S if self.state.status == "ok" else REPROBE_FAIL_S)
        ):
            return self.state
        try:
            async with self._client() as client:
                scores = await self._call(client, PROBE_QUERY, PROBE_TEXTS)
            best = max(range(len(scores)), key=scores.__getitem__)
            if best == PROBE_EXPECTED_BEST:
                self.state = RerankState("ok", None, time.monotonic())
            else:
                self.state = RerankState("unavailable", "probe pair ranked in the wrong order", time.monotonic())
        except (httpx.HTTPError, RerankUnavailableError, ValueError, KeyError, TypeError) as exc:
            self.state = RerankState("unavailable", f"reranker unreachable: {type(exc).__name__}", time.monotonic())
        if self.state.status != "ok":
            log.warning("reranker %s: %s", self.url, self.state.reason)
        return self.state


_VERSION_LABEL = re.compile(
    r"\s*[—–-]\s*(?:version consolidée applicable au \d{2}/\d{2}/\d{4}|texte original[^>|]*?)(?=\s*(?:>|\||$))",
    re.IGNORECASE,
)
_INNER_LABEL = re.compile(r"version consolidée applicable au \d{2}/\d{2}/\d{4}\s*:\s*", re.IGNORECASE)


def compact_header(header: str, limit: int = HEADER_CHARS) -> str:
    """The chunk header without version labels; when still too long, the title and the innermost levels are kept
    (« Code du travail > … > Section 1. – Résiliation avec préavis > Art. L. 124-3. | Index : … »)."""
    h = " ".join(_INNER_LABEL.sub("", _VERSION_LABEL.sub("", header, count=1)).split())
    if len(h) <= limit:
        return h
    main, sep, index = h.partition(" | ")
    parts = main.split(" > ")
    head, tail = parts[0], parts[1:]
    kept: list[str] = []
    budget = limit - len(head) - 5 - (len(sep) + min(len(index), limit // 4) if index else 0)
    for part in reversed(tail):
        if len(part) + 3 > budget and kept:
            break
        kept.insert(0, part)
        budget -= len(part) + 3
    out = " > ".join([head, "…", *kept] if len(kept) < len(tail) else [head, *kept])
    if index:
        out += sep + index[: limit // 4]
    return out[:limit]


def rerank_text(header: str, body: str, body_chars: int = BODY_CHARS) -> str:
    return f"{compact_header(header)}\n{body[:body_chars]}"
