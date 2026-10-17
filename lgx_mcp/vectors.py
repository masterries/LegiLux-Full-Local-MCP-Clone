"""Query embeddings and the startup probe of ``lgx-mcp`` (PLAN §6.5, §8.1 check 2).

Provider ``none`` (the default): ``vector_status = disabled:provider_none``, nothing is ever sent. Provider
``mistral``: the airgap's on-prem endpoint (``LGX_EMBED_BASE_URL``, same API as Mistral SaaS) embeds queries.

**One probe instead of golden vectors.** At start (and every :data:`REPROBE_OK_S` while ok, every
:data:`REPROBE_FAIL_S` while not, and when a new release is swapped in) the release's ``probe_text`` is embedded
once. Vectors are used only if

1. the endpoint answers (key, network, HTTP status, a vector per input),
2. the id it reports equals the release's ``lgx.embedding_model.model_id`` and the vector has the release's ``dim``,
3. the cosine to the release's ``probe_vector`` is ≥ ``probe_min_cosine``.

Anything else gives ``vector_status = disabled:<reason>`` and lexical retrieval with that reason — never an error.
Every query response is checked again (model id, dim); a mismatch or an endpoint failure switches to lexical
until the next probe. Probe and queries share one :class:`~lgx_core.embed.limiter.SlotLimiter` (the configured
``max_concurrency`` / ``cooldown_s``); a query that finds no free slot within ``timeout_s`` runs lexical
(``throttled``) without disabling vectors.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any

import httpx

from lgx_core.config import EmbeddingConfig
from lgx_core.embed import EmbeddingError, MistralEmbedder, SlotLimiter, cosine
from lgx_core.embed.limiter import Clock, Sleep
from lgx_core.urls import is_internal_url
from lgx_mcp.release import ReleaseInfo

REPROBE_OK_S = 3600.0
REPROBE_FAIL_S = 60.0
TRANSIENT = {"throttled"}  # this request runs lexical, vectors stay enabled


@dataclass
class ProbeState:
    status: str = "disabled:not_probed"
    reason: str | None = None
    release_id: str | None = None
    checked_at: float = 0.0  # monotonic
    checked_iso: str | None = None
    cosine: float | None = None
    model_reported: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "checked_at": self.checked_iso,
            "cosine": round(self.cosine, 6) if self.cosine is not None else None,
            "model_reported": self.model_reported,
        }


class QueryVectors:
    """The runtime side of the embedding stage: status, probe, query vectors."""

    def __init__(
        self,
        cfg: EmbeddingConfig,
        api_key: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self.cfg = cfg
        self.enabled = cfg.enabled
        self._api_key = api_key
        self._transport = transport
        self._sleep = sleep
        self._clock: Clock = clock or time.monotonic
        self.limiter = SlotLimiter(cfg.max_concurrency, cfg.cooldown_s, clock=clock, sleep=sleep)
        self._clients: dict[int, MistralEmbedder] = {}
        self.state = ProbeState("disabled:provider_none") if not self.enabled else ProbeState()

    # ------------------------------------------------------------------------------------------ status
    def config_problem(self) -> tuple[str, str] | None:
        if not self._api_key:
            return "disabled:no_api_key", "LGX_EMBED_API_KEY_FILE (or LGX_EMBED_API_KEY) is not set"
        if self.cfg.base_url.startswith("http://") and not is_internal_url(self.cfg.base_url):
            return "disabled:insecure_url", f"{self.cfg.base_url} is plain http: the API key would travel in clear"
        return None

    def status(self, release: ReleaseInfo | None) -> str:
        if not self.enabled:
            return "disabled:provider_none"
        if release is not None and release.embedding_model is None:
            return "disabled:no_vectors"
        if release is not None and self.state.release_id not in (None, release.release_id):
            return "disabled:not_probed"
        return self.state.status

    def reason(self, release: ReleaseInfo | None) -> str | None:
        status = self.status(release)
        if status == "disabled:no_vectors":
            return "the release carries no vectors (lexical release)"
        return None if status == "ok" else self.state.reason

    # ------------------------------------------------------------------------------------------- probe
    def _set(self, release: ReleaseInfo, status: str, reason: str | None, **extra: Any) -> ProbeState:
        self.state = ProbeState(
            status=status,
            reason=reason,
            release_id=release.release_id,
            checked_at=self._clock(),
            checked_iso=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            **extra,
        )
        return self.state

    def _client(self, dim: int) -> MistralEmbedder:
        if dim not in self._clients:
            cfg = self.cfg.model_copy(update={"dim": dim})  # the release decides the dimension
            self._clients[dim] = MistralEmbedder(
                cfg,
                api_key=self._api_key,
                limiter=self.limiter,
                transport=self._transport,
                trust_env=False,  # internal / enclave endpoint: never through a proxy
                sleep=self._sleep,
            )
        return self._clients[dim]

    async def probe(self, release: ReleaseInfo | None, *, force: bool = False) -> ProbeState:
        """Startup check 2 (cached; see the module docstring for the re-probe intervals)."""
        if not self.enabled or release is None:
            return self.state
        model = release.embedding_model
        if model is None:
            return self._set(release, "disabled:no_vectors", "the release carries no vectors (lexical release)")
        s = self.state
        if not force and s.release_id == release.release_id and s.checked_at > 0:
            age = self._clock() - s.checked_at
            if age < (REPROBE_OK_S if s.status == "ok" else REPROBE_FAIL_S):
                return s
        problem = self.config_problem()
        if problem is not None:
            return self._set(release, *problem)
        text, expected = model.get("probe_text"), model.get("probe_vector")
        if not text or not expected:
            return self._set(release, "disabled:no_probe", "the release's embedding_model row has no probe")
        try:
            res = await self._client(int(model["dim"])).embed([str(text)], slot_timeout=self.cfg.timeout_s, retries=0)
        except EmbeddingError as exc:
            return self._set(release, f"disabled:{exc.reason}", str(exc))
        if res.model != model.get("model_id"):
            return self._set(
                release,
                "disabled:model_mismatch",
                f"the endpoint reports model {res.model!r}, the release was embedded with {model.get('model_id')!r}",
                model_reported=res.model,
            )
        cos = cosine(res.vectors[0], [float(x) for x in expected])
        threshold = float(model.get("probe_min_cosine") or self.cfg.probe_min_cosine)
        if cos < threshold:
            return self._set(
                release,
                "disabled:probe_mismatch",
                f"probe cosine {cos:.4f} < {threshold:g}: the endpoint does not serve the release's model",
                cosine=cos,
                model_reported=res.model,
            )
        return self._set(release, "ok", None, cosine=cos, model_reported=res.model)

    # ------------------------------------------------------------------------------------------- query
    async def embed_query(self, text: str, release: ReleaseInfo) -> list[float]:
        """The query vector; raises :class:`EmbeddingError` (the caller runs lexical with the reason)."""
        model = release.embedding_model
        if model is None or self.status(release) != "ok":
            raise EmbeddingError(f"vectors unavailable ({self.status(release)})", reason="unavailable")
        try:
            res = await self._client(int(model["dim"])).embed([text], slot_timeout=self.cfg.timeout_s, retries=0)
        except EmbeddingError as exc:
            if exc.reason not in TRANSIENT:  # endpoint trouble: lexical until the next probe (≤ REPROBE_FAIL_S)
                self._set(release, f"disabled:{exc.reason}", str(exc))
            raise
        if res.model != model.get("model_id"):
            msg = f"the endpoint now reports model {res.model!r}, the release has {model.get('model_id')!r}"
            self._set(release, "disabled:model_mismatch", msg, model_reported=res.model)
            raise EmbeddingError(msg, reason="model_mismatch")
        return res.vectors[0]

    def describe(self, release: ReleaseInfo | None) -> dict[str, Any]:
        model = release.embedding_model if release is not None else None
        return {
            "provider": self.cfg.provider.value,
            "model_id": (model or {}).get("model_id"),
            "dim": (model or {}).get("dim"),
            "vector_status": self.status(release),
            "reason": self.reason(release),
            "probe": None
            if not self.enabled or model is None
            else {**self.state.as_dict(), "min_cosine": model.get("probe_min_cosine")},
            "throttle": {"max_concurrency": self.cfg.max_concurrency, "cooldown_s": self.cfg.cooldown_s}
            if self.enabled
            else None,
        }

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()


__all__ = ["REPROBE_FAIL_S", "REPROBE_OK_S", "ProbeState", "QueryVectors"]
