"""``resolve_citation`` (Appendix D.2.4): text reference → act / article ELIs, ambiguity instead of guessing."""

from __future__ import annotations

from typing import Any

from lgx_core.models import ToolErrorCode, WarningCode
from lgx_mcp.context import CallContext, ToolFailure
from lgx_mcp.render import render_cite
from lgx_mcp.resolve import Candidate
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.base import ToolOutput


async def resolve_citation(rt: Runtime, ctx: CallContext, args: dict[str, Any]) -> ToolOutput:
    text = args["text"].strip()
    facts = rt.facts()
    resolver = rt.resolver(ctx.release, facts)
    res = await resolver.resolve_text(text, ctx.as_of)
    if res.status == "not_found" and res.citation is None:
        # « règlement (UE) 2016/679 », « directive 2019/1152/UE », jurisprudence, Mémorial B: not a national act
        # the parser could cite, but recognisably outside the corpus — say so instead of « not found » (G1, M2)
        oos = resolver.parser.out_of_scope(text)
        if oos:
            from lgx_mcp.tools.search import OOS_FR

            res.status = "out_of_scope"
            res.out_of_scope = ", ".join(dict.fromkeys(OOS_FR.get(o.kind, o.kind) for o in oos))
    if res.status == "out_of_scope":
        ctx.bag.add(WarningCode.W_OUT_OF_SCOPE, what=res.out_of_scope or "référence externe")
        raise ToolFailure(
            ToolErrorCode.OUT_OF_SCOPE,
            f"hors du périmètre de cette copie locale : {res.out_of_scope}",
            hint="Seule la législation nationale (Mémorial A) est couverte ; voir les textes nationaux liés.",
            candidates=[Candidate(a, 0.5, "alias", note="texte national lié").as_dict() for a in res.related],
            warnings=ctx.bag.as_list(),
        )
    # an article that does not exist in the applicable text is not a resolved citation
    status = res.status
    if status == "resolved" and res.article_status in ("not_found",):
        status = "not_found"
    # Status warnings name the candidate they describe (refs = its act ELI). A resolved act only warns for itself, even
    # when its article is missing: an unrelated low-confidence candidate's W_REPEALED was read as the resolved act's
    # (e2e 2026-10-17). The resolver's own status decides, not the article-adjusted one.
    for c in res.candidates[:1] if res.status == "resolved" else res.candidates[:3]:
        await _warn_status(rt, ctx, facts, c)
    structured: dict[str, Any] = {
        "status": status,
        "text": text,
        "parsed": res.citation.parsed() if res.citation else None,
        "candidates": [c.as_dict() for c in res.candidates[:20]],
        "article_status": res.article_status,
        "message": res.message or (res.candidates[0].note if res.candidates else None),
    }
    structured.update(ctx.envelope())
    return ToolOutput(structured, render_cite(structured, rt.settings.max_result_tokens), hits=len(res.candidates))


async def _warn_status(rt: Runtime, ctx: CallContext, facts: Any, cand: Candidate) -> None:
    """Candidates carry their status warnings (repealed / not yet in force) so the model does not cite blindly."""
    from lgx_mcp.annotate import Annotator

    serving = await facts.serving_at(cand.act.act_id, ctx.as_of)
    if serving is not None:
        await Annotator(ctx, facts).status_warnings(cand.act, serving, ref=cand.act.eli_work)
