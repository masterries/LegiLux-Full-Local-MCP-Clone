"""``search_legislation`` (Appendix D.2.1): citation-aware lexical retrieval with optional reranking.

Pipeline (PLAN §8.3): citations in the query → cited units (weight 10) and act boosts; the rest of the question,
without interrogative noise and with curated French expansions (:mod:`lgx_mcp.retrieval.expansion`), goes to the
lexical ranker of ``lgx.search_candidates`` (IDF-weighted on M2 releases); the TEI cross-encoder, when configured,
re-orders the first ``LGX_RERANK_K`` articles.
"""

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
    search_candidates,
)
from lgx_mcp.retrieval.expansion import load_expander
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.base import ToolOutput

MAX_CITED = 5
MAX_BOOST_ACTS = 50  # an ambiguous dated mention boosts every candidate act of that date, up to this many
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

    # --- explicit citations in the query become cited units (weight 10 in the RRF); the acts they name are boosted
    #     and their words leave the lexical query (« Code du travail » would otherwise match every code header)
    cited: list[int] = []
    boost_acts: list[int] = []
    strip_spans: list[tuple[int, int]] = []
    detected = []
    for cit in analysis.citations[:MAX_CITED]:
        entry: dict[str, Any] = {"text": cit.text, "status": "not_found", "act_eli": None, "unit_eli": None}
        if cit.act is not None:
            res = await resolver.resolve(cit, ctx.as_of)
            entry["status"] = res.status
            if res.status == "resolved" and res.candidates:
                best = res.candidates[0]
                entry["act_eli"] = best.act.eli_work
                boost_acts.append(best.act.act_id)
                strip_spans.append(cit.span)
                if best.unit is not None:
                    entry["unit_eli"] = best.unit.eli_unit
                    cited.append(best.unit.unit_id)
            elif res.status == "ambiguous":
                entry["candidates"] = [c.act.eli_work for c in res.candidates[:5]]
                boost_acts.extend(c.act.act_id for c in res.candidates[:MAX_BOOST_ACTS])
                strip_spans.append(cit.span)
        detected.append(entry)
    boost_acts = list(dict.fromkeys(boost_acts))[:MAX_BOOST_ACTS]

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

    # --- lexical query (search API 2): citation spans and noise out, curated expansions in
    api = ctx.release.search_api
    ranker = rt.settings.lexical_ranker if api >= 2 else "rank_cd"
    prepared = None
    lexical_query = query
    if api >= 2 and ranker == "bm25":
        prepared = load_expander(rt.settings.config_dir).prepare(
            query, strip_spans=strip_spans, expand=rt.settings.query_expansion
        )
        lexical_query = prepared.lexical
    reranker = rt.reranker
    rerank_k = rt.settings.rerank_k
    limit = max(top_k * 3, rerank_k if reranker.enabled else 0, 24)
    t_sql = time.perf_counter()
    hits = await search_candidates(
        rt.db,
        lexical_query,
        as_of=ctx.as_of,
        cited_units=cited,
        exact_tokens=analysis.exact_tokens,
        doc_types=doc_types,
        act_ids=act_ids,
        in_force_only=in_force_only,
        limit=min(limit, 100),
        qvec=qvec,
        api=api,
        expansion=prepared.expansion if prepared else None,
        boost_act_ids=boost_acts if ranker == "bm25" else None,
        opts={**rt.settings.lexical_opts, "ranker": ranker},
    )
    sql_ms = (time.perf_counter() - t_sql) * 1000
    lexical_order = None
    if mode.mode_used == "lexical":
        if api >= 2 and ranker == "bm25":
            lexical_order = "bm25"  # IDF-weighted in SQL; the fused order already puts cited units first
        else:
            hits = coverage_order(hits)
            lexical_order = "coverage"
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
            values = await reranker.rerank(query, [reranker.text(h.header, h.body) for h in pool])
            scores = {h.chunk_id: s for h, s in zip(pool, values, strict=True)}
            # an article the question cites explicitly stays first (PLAN §8.3: cited units weigh 10)
            order = sorted(pool, key=lambda h: (not h.cited_hit, -scores[h.chunk_id], h.chunk_id))
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
                    "lex_score": round(h.lex_score, 4) if h.lex_score is not None else None,
                    "exact_hit": h.exact_hit,
                    "cited_hit": h.cited_hit,
                    "coverage": h.coverage,
                    "phrase_hits": h.phrase_hits,
                    "act_boosted": h.act_boosted,
                    "rerank_score": round(scores[h.chunk_id], 6) if h.chunk_id in scores else None,
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
            "lexical_order": lexical_order,
            "lexical_ranker": ranker,
            "search_api": api,
            "latency_ms": {
                "sql": round(sql_ms, 1),
                "rerank": round(rr_ms, 1),
                "total": round(total_ms, 1),
                **({"rerank_batches_ms": reranker.last.batch_ms} if reranked else {}),
            },
        },
        "query_interpretation": {
            "citations_detected": detected,
            "exact_tokens": analysis.exact_tokens,
            "out_of_scope": oos_out,
            "lexical": prepared.as_dict() if prepared else None,
            "boosted_acts": len(boost_acts) if ranker == "bm25" and api >= 2 else 0,
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
