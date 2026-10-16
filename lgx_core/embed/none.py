"""The default provider: no vectors, never any network call."""

from __future__ import annotations

from collections.abc import Sequence

from lgx_core.embed.base import EmbeddingDisabledError, EmbeddingInfo, EmbedKind
from lgx_core.models import EmbeddingProvider


class NoneEmbedder:
    """``vector_status = disabled:provider_none``; retrieval runs lexical."""

    @property
    def info(self) -> EmbeddingInfo:
        return EmbeddingInfo(provider=EmbeddingProvider.NONE)

    def vector_status(self) -> str:
        return "disabled:provider_none"

    def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
        raise EmbeddingDisabledError("embedding provider is 'none' (default): no vectors are computed")
