"""Embedder protocol shared by the build-side embed stage and the runtime parity check."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel

from lgx_core.config import EmbeddingConfig
from lgx_core.models import EmbeddingProvider

EmbedKind = Literal["doc", "query"]


class EmbeddingDisabledError(RuntimeError):
    """Raised when vectors are requested while the provider is ``none`` or parity failed."""


class EmbeddingInfo(BaseModel):
    provider: EmbeddingProvider
    model_id: str | None = None
    model_version: str | None = None
    dim: int | None = None


class Embedder(Protocol):
    @property
    def info(self) -> EmbeddingInfo: ...

    def vector_status(self) -> str:
        """``ok`` or ``disabled:<reason>`` (Appendix D.1 retrieval block)."""
        ...

    def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]: ...


def get_embedder(config: EmbeddingConfig) -> Embedder:
    """Factory. Every provider except ``none`` arrives in M3."""
    if config.provider is EmbeddingProvider.NONE:
        from lgx_core.embed.none import NoneEmbedder

        return NoneEmbedder()
    raise NotImplementedError(f"embedding provider {config.provider.value!r} is implemented in M3")
