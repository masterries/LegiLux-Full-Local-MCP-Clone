"""``get_act`` (Appendix D.2.3): act card with status computed from dates at ``as_of``."""

from __future__ import annotations

import datetime as dt
from typing import Any

from lgx_core.eli import display_url, resource_uri
from lgx_core.models import ToolErrorCode
from lgx_core.rules.status import act_status
from lgx_mcp.annotate import METADATA_REASON_FR, TEXT_BASIS_FR, Annotator, clip, status_basis
from lgx_mcp.context import CallContext, ToolFailure, iso
from lgx_mcp.facts import Act, Version, short_title_of
from lgx_mcp.render import render_act
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.article import _raise_for
from lgx_mcp.tools.base import ToolOutput

MANDATORY_AMENDMENTS = ("original_possibly_outdated", "stub_fallback")


def version_ref(v: Version | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {
        "version_eli": v.eli_version,
        "valid_from": iso(v.date_applicability),
        "valid_to": iso(v.date_end_applicability),
        "text_valid_from": iso(v.valid_from),
        "text_valid_to": iso(v.valid_to),
        "is_stub": v.is_stub,
        "has_text": v.has_text,
        "role": v.role,
        "kind": v.kind,
        "label": v.display_label,
    }


def status_reasons(act: Act, as_of: dt.date) -> list[str]:
    out = []
    if act.rdf_status == "no-longer-in-force-implicit":
        out.append("abrogation implicite (indicateur RDF no-longer-in-force-implicit)")
    if act.dnl:
        out.append(f"date de fin de vigueur {act.dnl.isoformat()} {'≤' if act.dnl <= as_of else '>'} as_of")
    elif act.rdf_status == "no-longer-in-force":
        out.append("abrogation sans date (indicateur RDF no-longer-in-force)")
    if act.dei_min:
        out.append(f"entrée en vigueur {act.dei_min.isoformat()} {'≤' if act.dei_min <= as_of else '>'} as_of")
    else:
        out.append("date d'entrée en vigueur inconnue")
    computed = act_status(act.dei_min, act.dnl, act.rdf_status, as_of).value
    out.append(f"statut calculé : {computed}")
    return out


async def get_act(rt: Runtime, ctx: CallContext, args: dict[str, Any]) -> ToolOutput:
    facts = rt.facts()
    eli = (args.get("eli") or "").strip()
    citation = (args.get("citation") or "").strip()
    include = set(args.get("include") or ["amendments"])
    if bool(eli) == bool(citation):
        raise ToolFailure(ToolErrorCode.BAD_INPUT, "donnez soit `eli`, soit `citation` (exactement un des deux)")
    resolution: dict[str, Any] | None = None
    if eli:
        act, _, _ = await facts.find_act_by_eli(eli)
        if act is None:
            raise ToolFailure(ToolErrorCode.NOT_FOUND, f"ELI inconnu dans cette base : {eli}")
    else:
        resolver = rt.resolver(ctx.release, facts)
        res = await resolver.resolve_text(citation, ctx.as_of, want_article=False)
        res.article_status = "resolved"  # an act card needs no article
        if res.status != "resolved":
            _raise_for(res, ctx)
        act = res.candidates[0].act
        resolution = {
            "status": res.status,
            "reason": res.candidates[0].reason,
            "parsed": res.citation.parsed() if res.citation else None,
        }
    serving = await facts.serving_at(act.act_id, ctx.as_of)
    assert serving is not None
    window = await facts.version(serving.window_version_id)
    text_v = await facts.version(serving.text_version_id)
    all_versions = await facts.versions_of_act(act.act_id) if ("versions" in include or text_v is None) else []
    fallback = (
        text_v if (text_v is not None and window is not None and text_v.version_id != window.version_id) else None
    )
    next_version = None
    if serving.next_version_date:
        nv = await facts.db.fetchone(
            "SELECT eli_version, has_text FROM lgx.act_version WHERE act_id = %s AND kind <> 'original'"
            " AND date_applicability = %s ORDER BY version_id LIMIT 1",
            [act.act_id, serving.next_version_date],
        )
        next_version = {
            "applicable_from": iso(serving.next_version_date),
            "has_text": bool(serving.next_version_has_text),
            "version_eli": nv["eli_version"] if nv else None,
        }
    # --- warnings: the act card is "C1"; with a stored text the same rules as for an article apply
    annot = Annotator(ctx, facts)
    shown = text_v or window or (all_versions[0] if all_versions else None)
    if shown is not None:
        await annot.prepare([(act, shown, None)])
        await annot.add_warnings("C1", act, shown, None, serving, serving, serving.text_basis)
    else:
        await annot.status_warnings(act, serving, ref="C1")
        await annot.data_quality_warnings([act.eli_work, act.eli_jo], ref="C1")
    repealed_by = None
    if serving.status == "no-longer-in-force" or act.dnl:
        rep = await facts.repealer(act)
        if rep is not None:
            repealed_by = {
                "eli": rep.eli,
                "title": clip(rep.title, 200),
                "date": iso(rep.date_entry_into_force or act.dnl),
            }
    amending: list[dict[str, Any]] = []
    if "amendments" in include or serving.text_basis in MANDATORY_AMENDMENTS:
        since = (text_v.valid_from or text_v.date_applicability) if text_v else act.dei_min
        amending = [a.as_dict() for a in await annot.amending(act, since)]
    based_on: list[dict[str, Any]] = []
    implementing: list[dict[str, Any]] = []
    if "relations" in include:
        for r in await facts.related(act.act_id, outgoing=True, rel_types=["based_on"]):
            st = act_status(r["dei_min"], r["dnl"], r["rdf_status"], ctx.as_of).value if r["act_id"] else None
            based_on.append({"eli": r["eli"], "title": clip(r["title"], 200) or None, "status": st})
        for r in await facts.related(act.act_id, outgoing=False, rel_types=["based_on"]):
            st = act_status(r["dei_min"], r["dnl"], r["rdf_status"], ctx.as_of).value if r["act_id"] else None
            implementing.append({"eli": r["eli"], "title": clip(r["title"], 200) or None, "status": st})
    superseded = None
    if act.superseded_by_act_id:
        code = await facts.act(act.superseded_by_act_id)
        if code is not None:
            cs = await facts.serving_at(code.act_id, ctx.as_of)
            cv = await facts.version(cs.text_version_id) if cs else None
            superseded = {
                "code_eli": code.eli_work,
                "version_eli": cv.eli_version if cv else None,
                "member_context": clip(code.title, 200),
            }
    toc: list[dict[str, Any]] = []
    toc_total = None
    if "toc" in include and text_v is not None:
        units, toc_total = await facts.toc(text_v.version_id)
        toc = [
            {
                "level": u.depth,
                "type": u.unit_type,
                "num": u.num_raw,
                "heading": clip(u.heading, 200) or None,
                "article_eli": u.eli_unit,
            }
            for u in units
        ]
    versions_out = [version_ref(v) for v in all_versions] if "versions" in include else []
    short = (await facts.short_titles([shown], {act.act_id: act})).get(act.act_id) if shown else short_title_of(act)
    structured: dict[str, Any] = {
        "key": "C1",
        "act_eli": act.eli_work,
        "act_jo_eli": act.eli_jo,
        "title": act.title,
        "title_short": act.title_short or short,
        "citation_title": act.citation_title,
        "doc_type": act.doc_type,
        "memorial": act.memorial,
        "date_document": iso(act.date_document),
        "publication_date": iso(act.publication_date),
        "date_entry_into_force": iso(act.dei_min),
        "date_no_longer_in_force": iso(act.dnl),
        "status_as_of": {
            "as_of": iso(ctx.as_of),
            "status": serving.status,
            "basis": status_basis(act),
            "reasons": status_reasons(act, ctx.as_of),
        },
        "repealed_by": repealed_by,
        "text_basis": serving.text_basis,
        "text_basis_label": TEXT_BASIS_FR.get(serving.text_basis, serving.text_basis),
        "text_basis_at_T": act.text_basis,
        "flags": list(serving.flags),
        "metadata_reason": act.metadata_reason if serving.text_basis == "metadata_only" else None,
        "metadata_reason_label": (
            METADATA_REASON_FR.get(act.metadata_reason or "") if serving.text_basis == "metadata_only" else None
        ),
        "current_version": version_ref(window),
        "text_version": version_ref(text_v),
        "next_version": next_version,
        "fallback_version": version_ref(fallback),
        "versions": versions_out,
        "versions_total": len(all_versions) if "versions" in include else None,
        "amending_acts_since_text_date": amending,
        "based_on": based_on,
        "implementing_acts": implementing,
        "superseded_by_code": superseded,
        "toc": toc,
        "toc_total": toc_total,
        "resource_uri": resource_uri(act.eli_work),
        "display_url": display_url(act.eli_work),
    }
    if resolution is not None:
        structured["resolution"] = resolution
    structured.update(ctx.envelope())
    return ToolOutput(structured, render_act(structured, rt.settings.max_result_tokens), hits=1)
