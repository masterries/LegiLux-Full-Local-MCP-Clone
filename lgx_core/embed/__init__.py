"""Embeddings (PLAN §6.5): OFF by default (provider ``none``: nothing is computed, nothing is sent).

One provider, ``mistral``: the Mistral embeddings API, identical on the build side (documents, ``lgx embed``) and
in the airgap (queries, ``lgx-mcp``); only the base URL differs. Every request goes through the shared throughput
limiter (:mod:`lgx_core.embed.limiter`: ``max_concurrency`` in flight, ``cooldown_s`` per slot after each request).
"""

from lgx_core.embed.limiter import SlotLimiter, ThrottledError, estimate_duration_s
from lgx_core.embed.mistral import (
    PROBE_TEXT,
    EmbeddingError,
    EmbedResult,
    MistralEmbedder,
    cosine,
    plan_batches,
    retry_after_s,
)

__all__ = [
    "PROBE_TEXT",
    "EmbedResult",
    "EmbeddingError",
    "MistralEmbedder",
    "SlotLimiter",
    "ThrottledError",
    "cosine",
    "estimate_duration_s",
    "plan_batches",
    "retry_after_s",
]
