"""Retrieval: SQL candidates (lexical / exact / cited / optional dense) and the optional TEI reranker."""

from lgx_mcp.retrieval.candidates import (
    ChunkHit,
    ModeDecision,
    best_per_unit,
    coverage_order,
    decide_mode,
    search_candidates,
)
from lgx_mcp.retrieval.rerank import Reranker, RerankUnavailableError, rerank_text

__all__ = [
    "ChunkHit",
    "ModeDecision",
    "RerankUnavailableError",
    "Reranker",
    "best_per_unit",
    "coverage_order",
    "decide_mode",
    "rerank_text",
    "search_candidates",
]
