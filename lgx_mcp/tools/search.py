"""``search_legislation`` (Appendix D.2.1): citation-aware lexical retrieval with optional reranking."""

from __future__ import annotations

import time
from typing import Any

from lgx_core.embed import EmbeddingDisabledError
from lgx_core.models import ToolErrorCode, WarningCode
from lgx_mcp.annotate import Annotator, clip
from lgx_mcp.context import CallContext, ToolFailure
from lgx_mcp.render import render_search
from lgx_mcp.retrieval import (
    ChunkHit,
    RerankUnavailableError,
    best_per_unit,
    coverage_order,
    decide_mode,
    rerank_text,
    search_candidates,
)
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.base import ToolOutput

MAX_CITED = 5
OOS_FR = {
    "eu_law": "droit de l'UE",
    "case_law": "jurisprudence",
    "communal": "règlements communaux",
    "memorial_b": "Mémorial B",
    "bill": "projets de loi",
    "adm_branch": "branche administrative (etat/adm)",
}


def snippet_of(body: str, limit: int = 600) -> str:
    text = " ".join(body.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit * 0.6 else cut).rstrip(" ,;:") + "…"


async def search_legislation(rt: Runtime, ctx: CallContext, args: dict[str, Any]) -> ToolOutput:
    t0 = time.perf_counter()
    query: str = args["query"].strip()
    if len(query) < 2:
        raise ToolFailure(ToolErrorCode.BAD_INPUT, "requête trop courte")
    filters = args.get("filters") or {}
    top_k = int(args.get("top_k") or 8)
    requested = args.get("retrieval_mode") or rt.settings.retrieval_mode_default
    in_force_only = filters.get("in_force_only", True)
    doc_types = filters.get("doc_types") or None
    facts = rt.facts()
    resolver = rt.resolver(ctx.release, facts)
    analysis = resolver.parser.analyse(query)

    # --- out-of-scope vocabulary: informational warning, the search still runs (national law may apply)
    oos_out = []
    for o in analysis.out_of_scope:
        oos_out.append({"kind": o.kind, "text": o.surface})
    if analysis.out_of_scope:
        what = ", ".join(dict.fromkeys(OOS_FR.get(o.kind, o.kind) for o in analysis.out_of_scope))
        ctx.bag.add(WarningCode.W_OUT_OF_SCOPE, what=what)

    # --- explicit citations in the query become cited units (weight 10 in the RRF)
    cited: list[int] = []
    detected = []
    for cit in analysis.citations[:MAX_CITED]:
        entry: dict[str, Any] = {"text": cit.text, "status": "not_found", "act_eli": None, "unit_eli": None}
        if cit.act is not None:
            res = await resolver.resolve(cit, ctx.as_of)
            entry["status"] = res.status
            if res.status == "resolved" and res.candidates:
                best = res.candidates[0]
                entry["act_eli"] = best.act.eli_work
                if best.unit is not None:
                    entry["unit_eli"] = best.unit.eli_unit
                    cited.append(best.unit.unit_id)
            elif res.status == "ambiguous":
                entry["candidates"] = [c.act.eli_work for c in res.candidates[:5]]
        detected.append(entry)

    # --- filters
    act_ids = None
    if filters.get("act_eli"):
        fres = await resolver.resolve_eli(filters["act_eli"])
        if fres.status == "ambiguous":  # a truncated dated ELI: never narrow to one act of that date silently
            raise ToolFailure(
                ToolErrorCode.AMBIGUOUS,
                f"filters.act_eli ne désigne pas un seul acte : {filters['act_eli']}",
                hint="Choisissez l'ELI complet d'un des candidats (ou utilisez resolve_citation).",
                candidates=[c.as_dict() for c in fres.candidates[:20]],
            )
        if fres.status != "resolved" or not fres.candidates:
            raise ToolFailure(
                ToolErrorCode.NOT_FOUND,
                f"filters.act_eli inconnu dans cette base : {filters['act_eli']}",
                hint="Utilisez resolve_citation pour trouver l'ELI de l'acte.",
            )
        act_ids = [fres.candidates[0].act.act_id]

    # --- mode (vectors are behind the adapter; provider none -> lexical with the reason)
    vector_status = rt.vector_status(ctx.release)
    mode = decide_mode(requested, vector_status)
    qvec = None
    if mode.mode_used == "hybrid":
        try:
            qvec = rt.embedder.embed([query], "query")[0]
        except EmbeddingDisabledError as exc:
            mode = decide_mode("lexical", vector_status)
            mode = type(mode)(requested, "lexical", vector_status, f"query embedding failed: {exc}")

    reranker = rt.reranker
    rerank_k = rt.settings.rerank_k
    limit = max(top_k * 3, rerank_k if reranker.enabled else 0, 24)
    t_sql = time.perf_counter()
    hits = await search_candidates(
        rt.db,
        query,
        as_of=ctx.as_of,
        cited_units=cited,
        exact_tokens=analysis.exact_tokens,
        doc_types=doc_types,
        act_ids=act_ids,
        in_force_only=in_force_only,
        limit=min(limit, 100),
        qvec=qvec,
    )
    sql_ms = (time.perf_counter() - t_sql) * 1000
    if mode.mode_used == "lexical":
        hits = coverage_order(hits)
    units = best_per_unit(hits)

    # --- rerank
    reranked = False
    rerank_status = reranker.state.status if reranker.enabled else "off"
    rerank_reason = reranker.state.reason
    rr_ms = 0.0
    scores: dict[int, float] = {}
    if reranker.enabled and units:
        pool = units[:rerank_k]
        t_rr = time.perf_counter()
        try:
            values = await reranker.rerank(query, [rerank_text(h.header, h.body) for h in pool])
            scores = {h.chunk_id: s for h, s in zip(pool, values, strict=True)}
            order = sorted(pool, key=lambda h: (-scores[h.chunk_id], h.chunk_id))
            units = order + units[rerank_k:]
            reranked = True
            rerank_status, rerank_reason = "ok", None
        except RerankUnavailableError as exc:
            if reranker.mode == "on":
                raise ToolFailure(
                    ToolErrorCode.BACKEND_UNAVAILABLE,
                    f"reranker indisponible ({exc}) et LGX_RERANK=on",
                    hint="Réessayez plus tard ou passez LGX_RERANK=auto.",
                ) from exc
            rerank_status, rerank_reason = "unavailable", f"{exc}; résultats non reclassés"
        rr_ms = (time.perf_counter() - t_rr) * 1000
    elif not reranker.enabled:
        rerank_reason = reranker.state.reason

    selected: list[ChunkHit] = units[:top_k]
    # --- assembly
    acts = await facts.acts({h.act_id for h in selected})
    versions = await facts.versions({h.version_id for h in selected})
    unit_rows = await facts.units([h.unit_id for h in selected])
    annot = Annotator(ctx, facts)
    await annot.prepare([(acts[h.act_id], versions[h.version_id], unit_rows.get(h.unit_id)) for h in selected])
    out_hits = []
    for i, h in enumerate(selected, 1):
        a = await annot.citation(f"C{i}", acts[h.act_id], versions[h.version_id], unit_rows.get(h.unit_id))
        unit = unit_rows.get(h.unit_id)
        out_hits.append(
            {
                "rank": i,
                "score": round(scores.get(h.chunk_id, h.score), 6),
                "citation": a.citation,
                "snippet": snippet_of(h.body),
                "breadcrumb": unit.breadcrumb if unit else None,
                "match": {
                    "lex_rank": h.lex_rank,
                    "exact_hit": h.exact_hit,
                    "cited_hit": h.cited_hit,
                    "coverage": h.coverage,
                    "part": f"{h.part_no}/{h.part_count}",
                },
            }
        )
    total_ms = (time.perf_counter() - t0) * 1000
    emb = rt.embedder.info
    structured: dict[str, Any] = {
        "query": query,
        "hits": out_hits,
        "retrieval": {
            **mode.as_dict(),
            "reranked": reranked,
            "rerank_status": rerank_status,
            "rerank_reason": rerank_reason,
            "rerank_k": rerank_k,
            "embedding_model": (
                {
                    "provider": emb.provider.value,
                    "model_id": emb.model_id,
                    "model_version": emb.model_version,
                    "dim": emb.dim,
                }
                if mode.mode_used == "hybrid"
                else None
            ),
            "candidates": len(units),
            "lexical_order": "coverage" if mode.mode_used == "lexical" else None,
            "latency_ms": {"sql": round(sql_ms, 1), "rerank": round(rr_ms, 1), "total": round(total_ms, 1)},
        },
        "query_interpretation": {
            "citations_detected": detected,
            "exact_tokens": analysis.exact_tokens,
            "out_of_scope": oos_out,
            "filters": {
                "doc_types": doc_types,
                "act_eli": filters.get("act_eli"),
                "in_force_only": in_force_only,
                "top_k": top_k,
            },
        },
    }
    structured.update(ctx.envelope())
    return ToolOutput(structured, render_search(structured, rt.settings.max_result_tokens), hits=len(out_hits))


__all__ = ["clip", "search_legislation", "snippet_of"]
