"""``get_article`` (Appendix D.2.2): verbatim text of one article at ``as_of``, paginated by ``cursor``."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from lgx_core.citeparse import ActMention, ArticleMention, Citation
from lgx_core.models import ToolErrorCode
from lgx_core.textsize import max_chars
from lgx_mcp.annotate import METADATA_REASON_FR, Annotator, clip
from lgx_mcp.context import CallContext, ToolFailure, iso
from lgx_mcp.facts import Facts, Unit, normalise_eli
from lgx_mcp.render import render_article
from lgx_mcp.resolve import Candidate, Resolution
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.base import ToolOutput

PAGE_CHARS = 8000  # Appendix D.2.2: ≤ 8k characters of verbatim text per page


def page_size(max_result_tokens: int) -> int:
    """8,000 characters, smaller when the result budget is (cpu profile: 2.5k tokens → ≈ 5.7k characters)."""
    return min(PAGE_CHARS, max(2000, max_chars(max_result_tokens) - 3000))


def encode_cursor(unit_id: int, offset: int, as_of: str) -> str:
    raw = json.dumps({"u": unit_id, "o": offset, "d": as_of}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, int, str]:
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad))
        return int(data["u"]), int(data["o"]), str(data["d"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ToolFailure(
            ToolErrorCode.BAD_INPUT, "cursor invalide", hint="Réutilisez la valeur next_cursor telle quelle."
        ) from exc


def page_of(text: str, offset: int, size: int = PAGE_CHARS) -> tuple[str, int | None]:
    """One page from ``offset``; cut at a line break (or space) near the end; returns (page, next_offset)."""
    if offset >= len(text):
        return "", None
    end = offset + size
    if end >= len(text):
        return text[offset:], None
    cut = text.rfind("\n", offset + size // 2, end)
    if cut == -1:
        cut = text.rfind(" ", offset + size // 2, end)
    if cut == -1:
        cut = end
    return text[offset:cut], cut + (1 if text[cut : cut + 1] in ("\n", " ") else 0)


def page_starts(text: str, size: int = PAGE_CHARS) -> list[int]:
    """Offsets at which the pages of ``text`` start (the same cuts as :func:`page_of`)."""
    starts = [0]
    off: int | None = 0
    while off is not None:
        _, off = page_of(text, off, size)
        if off is not None:
            starts.append(off)
    return starts


def _candidates_payload(res: Resolution) -> list[dict[str, Any]]:
    return [c.as_dict() for c in res.candidates[:20]]


async def _resolve_target(rt: Runtime, ctx: CallContext, facts: Facts, args: dict[str, Any]) -> tuple[Resolution, Unit]:
    citation = (args.get("citation") or "").strip()
    act_eli = (args.get("act_eli") or "").strip()
    article = (args.get("article") or "").strip()
    if citation and (act_eli or article):
        raise ToolFailure(ToolErrorCode.BAD_INPUT, "donnez soit `citation`, soit `act_eli` + `article`, pas les deux")
    if not citation and not (act_eli and article):
        raise ToolFailure(
            ToolErrorCode.BAD_INPUT,
            "`citation` ou (`act_eli` + `article`) est requis",
            hint='Exemple : {"citation": "art. L. 121-1 C. trav."} ou {"act_eli": "http://data.legilux.public.lu/eli/'
            'etat/leg/loi/2018/08/01/a686", "article": "5"}',
        )
    resolver = rt.resolver(ctx.release, facts)
    no_article = ToolFailure(
        ToolErrorCode.BAD_INPUT,
        "la référence ne contient pas de numéro d'article",
        hint="Utilisez get_act pour la fiche d'un acte, ou précisez l'article (« art. 5 … »).",
    )
    if citation:
        cit = resolver.parser.parse_one(citation)
        # an article ELI (citation_string / article_eli of an earlier result) names its article by itself
        eli_only = cit is not None and cit.article is None and cit.act is not None and cit.act.kind == "eli"
        if cit is not None and cit.article is None and not eli_only:
            raise no_article
        res = await resolver.resolve_text(citation, ctx.as_of)
        if eli_only and res.status == "resolved" and res.article_status == "not_requested":
            assert cit is not None and cit.act is not None
            if "/art_" in (cit.act.eli or ""):
                raise ToolFailure(
                    ToolErrorCode.NOT_FOUND,
                    f"article introuvable dans cette base : {cit.act.eli}",
                    hint='Vérifiez le numéro ou consultez la table des matières via get_act include=["toc"].',
                )
            raise no_article
    else:
        eli = normalise_eli(act_eli)
        mention = ActMention(kind="eli", span=(0, len(eli)), surface=eli, eli=eli)
        art = ArticleMention(span=(0, len(article)), raw=article, key=resolver.parser._key(article))
        cit = Citation(span=(0, len(eli)), text=f"{eli} art. {article}", article=art, act=mention)
        res = await resolver.resolve(cit, ctx.as_of)
    _raise_for(res, ctx)
    best = res.candidates[0]
    assert best.unit is not None
    return res, best.unit


def _raise_for(res: Resolution, ctx: CallContext) -> None:
    if res.status == "out_of_scope":
        raise ToolFailure(
            ToolErrorCode.OUT_OF_SCOPE,
            f"hors du périmètre de cette copie locale : {res.out_of_scope}",
            hint="Droit de l'UE, jurisprudence, règlements communaux, Mémorial B et projets de loi ne sont pas"
            " couverts.",
            candidates=[Candidate(a, 0.5, "alias", note="texte national lié").as_dict() for a in res.related],
        )
    if res.status == "ambiguous":
        raise ToolFailure(
            ToolErrorCode.AMBIGUOUS,
            f"référence ambiguë : {res.message or 'plusieurs candidats'}",
            hint="Précisez l'intitulé, l'ELI ou le numéro du Mémorial ; ne choisissez pas à la place de l'utilisateur.",
            candidates=_candidates_payload(res),
        )
    if res.status == "not_found" or not res.candidates:
        raise ToolFailure(
            ToolErrorCode.NOT_FOUND,
            res.message or "référence introuvable dans cette base",
            candidates=_candidates_payload(res),
        )
    best = res.candidates[0]
    if res.article_status == "no_text":
        reason = METADATA_REASON_FR.get(best.act.metadata_reason or "", "")
        raise ToolFailure(
            ToolErrorCode.NOT_FOUND,
            f"aucun texte stocké de {best.act.eli_work} n'est applicable au {iso(ctx.as_of)}"
            + (f" ({reason})" if reason else ""),
            hint="get_act donne la fiche (statut, versions, actes modificatifs) même sans texte.",
            candidates=_candidates_payload(res),
        )
    if res.article_status != "resolved" or best.unit is None:
        raise ToolFailure(
            ToolErrorCode.NOT_FOUND,
            best.note or "article introuvable",
            hint="Vérifiez le numéro (« L. 121-1 », « 12bis », « 1er ») ou consultez la table des matières via"
            ' get_act include=["toc"].',
            candidates=_candidates_payload(res),
        )


async def get_article(rt: Runtime, ctx: CallContext, args: dict[str, Any]) -> ToolOutput:
    facts = rt.facts()
    offset = 0
    res: Resolution | None = None
    if args.get("cursor"):
        unit_id, offset, cursor_as_of = decode_cursor(args["cursor"])
        if args.get("as_of") and args["as_of"] != cursor_as_of:
            raise ToolFailure(ToolErrorCode.BAD_INPUT, "cursor émis pour une autre date as_of")
        unit_opt = await facts.unit(unit_id)
        if unit_opt is None:
            raise ToolFailure(ToolErrorCode.NOT_FOUND, "cursor expiré (base de données remplacée)")
        unit = unit_opt
    else:
        res, unit = await _resolve_target(rt, ctx, facts, args)
    full = await facts.unit(unit.unit_id, with_text=True)
    assert full is not None
    act = await facts.act(full.act_id)
    version = await facts.version(full.version_id)
    assert act is not None and version is not None
    if not version.window_contains(ctx.as_of):
        raise ToolFailure(
            ToolErrorCode.NOT_FOUND,
            f"la version {version.eli_version} n'est pas applicable au {iso(ctx.as_of)}",
            hint="Rappelez get_article sans cursor pour obtenir la version applicable.",
        )
    annot = Annotator(ctx, facts)
    await annot.prepare([(act, version, full)])
    a = await annot.citation("C1", act, version, full)
    text = full.text_plain or ""
    size = page_size(rt.settings.max_result_tokens)
    page, next_offset = page_of(text, offset, size)
    starts = page_starts(text, size) if text else [0]
    page_no = next((i for i, s in enumerate(starts, 1) if s == offset), 1)
    # amended_by: textualMod / note sources of this unit (ELIs stored on the unit + resolved relations)
    srcs = list(full.amended_by)
    rel_rows = await facts.db.fetch(
        "SELECT DISTINCT r.src_eli FROM lgx.relation r WHERE r.dst_unit_id = %s"
        " AND r.rel_type IN ('modifies', 'repeals')",
        [full.unit_id],
    )
    srcs += [r["src_eli"] for r in rel_rows if r["src_eli"] not in srcs]
    by_eli = await facts.acts_by_eli(srcs)
    amended_by = [
        {
            "eli": e,
            "title": clip(by_eli[e].title, 200) if e in by_eli else None,
            "date": iso(by_eli[e].publication_date) if e in by_eli else None,
            "date_entry_into_force": iso(by_eli[e].dei_min) if e in by_eli else None,
        }
        for e in srcs[:50]
    ]
    repealed_by = None
    if full.is_repealed:
        rep = await facts.unit_repealer(full)
        if rep is not None:
            repealed_by = {"eli": rep.eli, "title": clip(rep.title, 200), "date": iso(rep.date_entry_into_force)}
    future = None
    if a.serving.next_version_date:
        nv = await facts.next_version(act.act_id, a.serving.next_version_date)
        future = {
            "applicable_from": iso(a.serving.next_version_date),
            "version_eli": nv.eli_version if nv else None,
            "has_text": nv.has_text if nv else bool(a.serving.next_version_has_text),
            "has_files": nv.has_files if nv else False,
        }
    member = None
    if full.member_act_id:
        m = await facts.act(full.member_act_id)
        if m is not None:
            member = {"eli": m.eli_work, "title": clip(m.title, 200)}
    structured: dict[str, Any] = {
        "citation": a.citation,
        "breadcrumb": full.breadcrumb,
        "heading": full.heading,
        "provenance": full.provenance,
        "text": page,
        "text_md": full.text_md if (full.text_md and offset == 0 and len(full.text_md) <= size) else None,
        "next_cursor": encode_cursor(full.unit_id, next_offset, iso(ctx.as_of) or "") if next_offset else None,
        "page_info": {
            "offset": offset,
            "length": len(page),
            "total_chars": len(text),
            "page": page_no,
            "pages": len(starts),
        },
        "is_repealed": full.is_repealed,
        "repeal_note": full.repeal_note,
        "repealed_by": repealed_by,
        "amended_by": amended_by,
        "future_version": future,
        "member_act": member,
    }
    if res is not None:
        structured["resolution"] = {
            "status": res.status,
            "reason": res.candidates[0].reason if res.candidates else None,
            "parsed": res.citation.parsed() if res.citation else None,
        }
    structured.update(ctx.envelope())
    return ToolOutput(structured, render_article(structured, rt.settings.max_result_tokens), hits=1)
