"""``about_corpus`` (Appendix D.2.5): coverage, gaps, embedding state, licences of the local copy."""

from __future__ import annotations

from typing import Any

from lgx_mcp import __version__
from lgx_mcp.context import CallContext, iso
from lgx_mcp.render import render_about
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.base import ToolOutput

OUT_OF_SCOPE = [
    "droit de l'Union européenne (règlements, directives)",
    "jurisprudence",
    "règlements communaux (RC)",
    "Mémorial B",
    "projets et propositions de loi",
    "branche administrative etat/adm (robots.txt)",
    "lois de naturalisation individuelles (minimisation RGPD)",
]


async def about_corpus(rt: Runtime, ctx: CallContext, args: dict[str, Any]) -> ToolOutput:
    rel = ctx.release
    db = rt.db
    counts = rel.counts or {}
    tables = counts.get("tables") or {}
    live = await db.fetchone(
        "SELECT (SELECT count(*) FROM lgx.act) AS acts, (SELECT count(*) FROM lgx.act_version) AS versions,"
        " (SELECT count(*) FROM lgx.act_version WHERE has_text) AS stored_texts,"
        " (SELECT count(*) FROM lgx.chunk WHERE searchable) AS chunks,"
        " (SELECT count(*) FROM lgx.unit) AS units,"
        " (SELECT count(*) FROM lgx.act_version WHERE role = 'future') AS future_versions,"
        " (SELECT count(*) FROM lgx.chunk WHERE embedding IS NOT NULL) AS vectors,"
        " (SELECT count(*) FROM lgx.act WHERE metadata_reason = 'licence') AS licence_restricted,"
        " (SELECT count(*) FROM lgx.act WHERE metadata_reason = 'not_loaded') AS not_loaded"
    )
    assert live is not None
    by_basis = {r["text_basis"]: int(r["n"]) for r in await db.fetch(
        "SELECT text_basis::text AS text_basis, count(*) AS n FROM lgx.act GROUP BY 1 ORDER BY 1"
    )}  # fmt: skip
    by_flag = {r["flag"]: int(r["n"]) for r in await db.fetch(
        "SELECT f AS flag, count(*) AS n FROM lgx.act, unnest(flags) f GROUP BY 1 ORDER BY 1"
    )}  # fmt: skip
    stubs = await db.fetch(
        "SELECT a.eli_work, w.date_applicability AS announced, t.eli_version AS fallback"
        " FROM lgx.act a, LATERAL lgx.serving_at(a.act_id, %s) s"
        " LEFT JOIN lgx.act_version w ON w.version_id = s.window_version_id"
        " LEFT JOIN lgx.act_version t ON t.version_id = s.text_version_id"
        " WHERE a.text_basis = 'stub_fallback' OR 'stub_no_text' = ANY (a.flags) ORDER BY a.eli_work LIMIT 60",
        [ctx.as_of],
    )
    pdf_codes = await db.fetch(
        "SELECT a.eli_work, a.title FROM lgx.act a WHERE 'pdf_extracted' = ANY (a.flags) ORDER BY a.eli_work LIMIT 60"
    )
    emb = rt.embedder.info
    vector_status = rt.vector_status(rel)
    model = rel.embedding_models[0] if rel.embedding_models else None
    scope = (rel.config or {}).get("scope") or {}
    rr = rt.reranker
    structured: dict[str, Any] = {
        "release": {
            "release_id": rel.release_id,
            "kind": (rel.config or {}).get("release_kind"),
            "built_at": rel.built_at.isoformat() if rel.built_at else None,
            "pipeline_git_sha": rel.pipeline_git_sha,
            "gates": (rel.gates or {}).get("summary"),
        },
        "counts": {
            "acts": int(live["acts"]),
            "versions": int(live["versions"]),
            "stored_texts": int(live["stored_texts"]),
            "units": int(live["units"]),
            "chunks": int(live["chunks"]),
            "future_versions": int(live["future_versions"]),
            "relations": tables.get("lgx.relation"),
            "by_text_basis": by_basis,
            "by_flag": by_flag,
            "by_role": counts.get("by_role"),
            "selection": counts.get("selection"),
        },
        "scope": {
            "branches": scope.get("branches", ["etat/leg"]),
            "text_types": scope.get("text_types", []),
            "metadata_scope": scope.get("metadata_scope"),
            "licence_policy": scope.get("licence_policy"),
            "exclusions": [
                *(f"branche {b}" for b in (scope.get("exclusions") or {}).get("branches", [])),
                *(f"type {t}" for t in (scope.get("exclusions") or {}).get("doc_types", [])),
                "lois de naturalisation individuelles",
            ],
        },
        "known_gaps": {
            "stub_consolidations": [
                {"work_eli": r["eli_work"], "announced_date": iso(r["announced"]), "fallback": r["fallback"]}
                for r in stubs
            ],
            "pdf_codes": [{"eli": r["eli_work"], "title": r["title"]} for r in pdf_codes],
            "licence_restricted": int(live["licence_restricted"]),
            "scan_only": 0,
            "not_loaded": int(live["not_loaded"]),
            "not_loaded_by_planned_text_basis": counts.get("not_loaded_by_planned_text_basis"),
            "texts_missing": counts.get("texts_missing"),
            "out_of_scope": OUT_OF_SCOPE,
        },
        "embedding": {
            "provider": emb.provider.value,
            "model_id": (model or {}).get("model_id") or emb.model_id,
            "model_version": (model or {}).get("model_version") or emb.model_version,
            "dim": (model or {}).get("dim") or emb.dim,
            "vector_status": vector_status,
            "vectors": int(live["vectors"]),
            "parity": None
            if model is None
            else {"checked_at": None, "min_cosine": None, "threshold": model.get("parity_threshold")},
        },
        "retrieval_defaults": {
            "mode": rt.settings.retrieval_mode_default,
            "mode_used": "hybrid"
            if vector_status == "ok" and rt.settings.retrieval_mode_default != "lexical"
            else "lexical",
            "rerank": rt.settings.rerank,
            "rerank_status": rr.state.status if rr.enabled else "off",
            "rerank_reason": rr.state.reason,
            "rerank_k": rt.settings.rerank_k,
            "top_k": 8,
            "search_api": ctx.release.search_api,
            "lexical_ranker": rt.settings.lexical_ranker if ctx.release.search_api >= 2 else "rank_cd",
            "query_expansion": rt.settings.query_expansion and ctx.release.search_api >= 2,
        },
        "licences": {
            "data": "CC BY 4.0",
            "texts_path": "legilux://legal/cc-by-4.0",
            "attribution_path": "legilux://legal/attribution",
            "disclaimer_path": "legilux://legal/disclaimer",
            "notice": rel.legal("notice"),
        },
        "versions": {
            "schema": rel.schema_version,
            "rules": rel.rules_version,
            "parser": rel.parser_version,
            "platform": f"lgx-mcp {__version__}",
        },
    }
    structured.update(ctx.envelope())
    return ToolOutput(structured, render_about(structured, rt.settings.max_result_tokens))
