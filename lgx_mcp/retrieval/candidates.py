"""Candidate generation in one SQL statement (``lgx.search_candidates``, Appendix B.14) and retrieval modes.

Lexical (OR-query over ``fr_legal`` lexemes), exact identifiers (``tsv_exact``), cited units (weight 10) and —
only when ``vector_status = ok`` and the mode allows it — dense candidates, fused with RRF (k = 60) and the
chunk's ``rank_prior``. Every filter (``doc_types``, ``act_eli`` → act ids, ``in_force_only``, ``as_of``)
applies to every branch inside the SQL function.

Embeddings are OFF by default (provider ``none``): ``auto`` and ``hybrid`` then run lexical and say why.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lgx_mcp.db import Database

VECTORS_DISABLED_REASON = "vectors disabled: {status}"


@dataclass(frozen=True)
class ModeDecision:
    mode_requested: str
    mode_used: str
    vector_status: str
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode_requested": self.mode_requested,
            "mode_used": self.mode_used,
            "vector_status": self.vector_status,
            "reason": self.reason,
        }


def decide_mode(requested: str, vector_status: str) -> ModeDecision:
    """auto → hybrid iff vectors are ok; lexical → lexical; hybrid → lexical with a reason when unavailable."""
    if requested == "lexical":
        return ModeDecision("lexical", "lexical", vector_status, None)
    if vector_status == "ok":
        return ModeDecision(requested, "hybrid", vector_status, None)
    status = vector_status.removeprefix("disabled:")
    return ModeDecision(requested, "lexical", vector_status, VECTORS_DISABLED_REASON.format(status=status))


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: int
    unit_id: int
    act_id: int
    version_id: int
    part_no: int
    part_count: int
    header: str
    body: str
    doc_type: str
    valid_from: dt.date
    valid_to: dt.date | None
    score: float
    lex_rank: int | None
    dense_rank: int | None
    exact_hit: bool
    cited_hit: bool
    coverage: int = 0  # distinct query lexemes present in the chunk


async def search_candidates(
    db: Database,
    query: str,
    *,
    as_of: dt.date,
    cited_units: Sequence[int] = (),
    exact_tokens: Sequence[str] = (),
    doc_types: Sequence[str] | None = None,
    act_ids: Sequence[int] | None = None,
    in_force_only: bool = True,
    limit: int = 40,
    qvec: Sequence[float] | None = None,
) -> list[ChunkHit]:
    vec = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]" if qvec else None
    rows = await db.fetch(
        """
        WITH ex AS (
          SELECT (SELECT string_agg('(' || q::text || ')', ' | ')
                    FROM (SELECT phraseto_tsquery('lgx.simple_ua', t) AS q FROM unnest(%(toks)s::text[]) AS t) z
                   WHERE q::text <> '')::tsquery AS q
        )
        SELECT s.chunk_id, s.score, s.lex_rank, s.dense_rank, s.exact_hit, s.cited_hit,
               c.unit_id, c.act_id, c.version_id, c.part_no, c.part_count, c.header, c.body, c.doc_type,
               c.valid_from, c.valid_to,
               (SELECT count(*) FROM unnest(tsvector_to_array(to_tsvector('lgx.fr_legal'::regconfig, %(q)s))) AS l(lex)
                 WHERE c.tsv @@ quote_literal(l.lex)::tsquery) AS coverage
          FROM ex,
               lgx.search_candidates(%(q)s, %(qvec)s::halfvec, %(as_of)s, %(cited)s::bigint[], ex.q,
                                     %(doc_types)s::text[], %(limit)s, %(act_ids)s::bigint[], %(ifo)s) s
          JOIN lgx.chunk c ON c.chunk_id = s.chunk_id
         ORDER BY s.score DESC, s.chunk_id
        """,
        {
            "toks": list(exact_tokens),
            "q": query,
            "qvec": vec,
            "as_of": as_of,
            "cited": list(cited_units),
            "doc_types": list(doc_types) if doc_types else None,
            "limit": int(limit),
            "act_ids": list(act_ids) if act_ids else None,
            "ifo": in_force_only,
        },
    )
    return [
        ChunkHit(
            chunk_id=int(r["chunk_id"]),
            unit_id=int(r["unit_id"]),
            act_id=int(r["act_id"]),
            version_id=int(r["version_id"]),
            part_no=int(r["part_no"]),
            part_count=int(r["part_count"]),
            header=r["header"],
            body=r["body"],
            doc_type=r["doc_type"],
            valid_from=r["valid_from"],
            valid_to=r["valid_to"],
            score=float(r["score"]),
            lex_rank=r["lex_rank"],
            dense_rank=r["dense_rank"],
            exact_hit=bool(r["exact_hit"]),
            cited_hit=bool(r["cited_hit"]),
            coverage=int(r["coverage"] or 0),
        )
        for r in rows
    ]


def coverage_order(hits: Sequence[ChunkHit]) -> list[ChunkHit]:
    """Lexical-only re-ordering of the fused candidates: cited units, then the number of distinct query terms a
    chunk contains, then the RRF score. Native FTS has no IDF, so ``ts_rank_cd`` lets one frequent term outrank
    a chunk that matches every term (« transport des cadavres » → the AGD of 1913, not the Code du travail). The
    cross-encoder reranker (PLAN §8.3 step 3), when configured, decides after this."""
    return sorted(hits, key=lambda h: (not h.cited_hit, -h.coverage, -h.score, h.chunk_id))


def best_per_unit(hits: Sequence[ChunkHit]) -> list[ChunkHit]:
    """One hit per article (parts of a split article collapse to their best-scored part), order kept."""
    seen: set[int] = set()
    out: list[ChunkHit] = []
    for h in hits:
        if h.unit_id in seen:
            continue
        seen.add(h.unit_id)
        out.append(h)
    return out
