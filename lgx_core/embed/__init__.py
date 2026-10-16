"""Pluggable embedding adapters (PLAN §6.5). OFF by default: provider ``none`` computes nothing.

Only the ``none`` provider exists before M3. ``mistral-api`` (build side), ``mistral-local`` (airgap),
``openai-compatible`` and ``tei`` are added in M3 behind the same :class:`Embedder` protocol.
"""

from lgx_core.embed.base import Embedder, EmbeddingDisabledError, EmbeddingInfo, get_embedder
from lgx_core.embed.none import NoneEmbedder

__all__ = ["Embedder", "EmbeddingDisabledError", "EmbeddingInfo", "NoneEmbedder", "get_embedder"]
