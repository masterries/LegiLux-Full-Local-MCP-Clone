"""Citation objects (PLAN §8.4, Appendix D.1) and the mandatory warnings computed by code (PLAN §8.5).

Warnings are derived from ``lgx.serving_at`` (status and label recomputed at ``as_of``), the act flags, the
unit (repealed stub, derived ELI, PDF pages) and ``lgx.data_quality_issue`` — never chosen by a model.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from lgx_core.eli import display_url, resource_uri
from lgx_core.models import Severity, WarningCode
from lgx_mcp.context import CallContext, fr_date, iso
from lgx_mcp.facts import Act, AmendingAct, Facts, Serving, Unit, Version, short_title_of

EU_PART_PREFIX = "ue_"  # lgx_ingest.parse.pdf.EU_PART_PREFIX (the runtime image does not ship lgx_ingest)

TEXT_BASIS_FR = {
    "consolidated": "version consolidée",
    "stub_fallback": "consolidation annoncée sans texte — version antérieure affichée",
    "original_never_amended": "texte original (non modifié depuis)",
    "original_possibly_outdated": "texte original non consolidé (peut être dépassé)",
    "metadata_only": "métadonnées seulement (pas de texte)",
}
STATUS_FR = {
    "in-force": "en vigueur",
    "no-longer-in-force": "plus en vigueur (abrogé)",
    "not-yet-in-force": "pas encore en vigueur",
}
METADATA_REASON_FR = {
    "not_loaded": "texte non chargé dans cette base (échantillon)",
    "repealed": "acte abrogé",
    "repealed_base": "base légale abrogée",
    "type": "type d'acte sans texte dans le périmètre",
    "licence": "texte sous licence restrictive (non CC BY)",
    "pdf_only": "texte disponible seulement en PDF",
    "temporary": "acte temporaire",
    "robots": "exclu (robots.txt)",
    "excluded_by_config": "exclu par configuration",
    "not_yet_text": "aucun texte applicable à ce jour (seulement une version future ou annoncée)",
}
CODE_KINDS = ("code", "code_recueil", "recueil")
MAX_ACTS_IN_MESSAGE = 5


def status_basis(act: Act) -> str:
    """'rdf_flag' when the status rests on the raw flag (implicit / undated repeal), else 'dates'."""
    if act.rdf_status == "no-longer-in-force-implicit":
        return "rdf_flag"
    if act.dnl is None and act.rdf_status == "no-longer-in-force":
        return "rdf_flag"
    return "dates"


def clip(text: str | None, n: int) -> str:
    if not text:
        return ""
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def acts_phrase(acts: list[AmendingAct], limit: int = MAX_ACTS_IN_MESSAGE) -> str:
    parts = []
    for a in acts[:limit]:
        label = clip(a.title, 90) or a.eli
        when = f" (en vigueur le {fr_date(a.date_entry_into_force)})" if a.date_entry_into_force else ""
        parts.append(label + when)
    if len(acts) > limit:
        parts.append(f"et {len(acts) - limit} autre(s) acte(s)")
    return " ; ".join(parts) if parts else "actes modificatifs postérieurs (voir get_act)"


def article_short(num_raw: str | None) -> str | None:
    """'Art. L. 121-1.' -> 'Art. L. 121-1'."""
    if not num_raw:
        return None
    return num_raw.strip().rstrip(".").strip()


@dataclass
class Annotated:
    citation: dict[str, Any]
    act: Act
    version: Version
    unit: Unit | None
    serving: Serving  # at as_of
    effective: Serving  # at the date the cited text serves (as_of, or the start of a future window)


_ACT_TITLE = re.compile(r"(?i)^\s*(loi|règlement|reglement|arrêté|arrete|décret|decret|convention)\b")


def of_member(title: str | None, limit: int = 160) -> str | None:
    """« de la loi du … », « de l'arrêté grand-ducal du … », « du règlement grand-ducal du … » from a member title."""
    t = (title or "").strip().rstrip(".").strip()
    if not t:
        return None
    t = clip(t[0].lower() + t[1:], limit)
    if t.startswith(("règlement", "reglement", "décret", "decret")):
        return "du " + t
    if t.startswith(("loi", "convention")):
        return "de la " + t
    if t[0] in "aeiouéèêh":
        return "de l'" + t
    return "de " + t


class Annotator:
    def __init__(self, ctx: CallContext, facts: Facts) -> None:
        self.ctx = ctx
        self.facts = facts
        self._amending: dict[tuple[int, dt.date], list[AmendingAct]] = {}

    async def amending(self, act: Act, since: dt.date | None) -> list[AmendingAct]:
        since = since or act.dei_min or dt.date(1800, 1, 1)
        key = (act.act_id, since)
        if key not in self._amending:
            self._amending[key] = await self.facts.amending_since(act.act_id, since, self.ctx.as_of)
        return self._amending[key]

    async def superseded_by_code(self, act: Act, *, ref: str | None, variant: str | None = None) -> None:
        """W_SUPERSEDED_BY_CODE naming the code; ``variant`` redirected / no_text when the release has it (older
        releases carry only the base text: their ``lgx.warning_def`` is the catalogue)."""
        code = await self.facts.act(act.superseded_by_act_id) if act.superseded_by_act_id else None
        name = "code"
        if code is not None:
            name = short_title_of(code)
            serving = await self.facts.serving_at(code.act_id, self.ctx.as_of)
            version = await self.facts.version(serving.text_version_id) if serving and serving.text_version_id else None
            if version is not None:
                name = (await self.facts.short_titles([version], {code.act_id: code})).get(code.act_id) or name
        known = self.ctx.bag.catalogue[WarningCode.W_SUPERSEDED_BY_CODE].variants
        self.ctx.bag.add(
            WarningCode.W_SUPERSEDED_BY_CODE,
            ref=ref,
            variant=variant if variant in known else None,
            data={"act_eli": act.eli_work, "code_eli": code.eli_work if code else None},
            code=name,
        )

    async def prepare(self, items: list[tuple[Act, Version, Unit | None]]) -> None:
        """Batch the serving lookups of a result page."""
        pairs: list[tuple[int, dt.date]] = []
        for act, version, _ in items:
            pairs.append((act.act_id, self.ctx.as_of))
            pairs.append((act.act_id, self.effective_date(version)))
        await self.facts.serving(pairs)
        await self.facts.short_titles([v for _, v, _ in items], {a.act_id: a for a, _, _ in items})

    def effective_date(self, version: Version) -> dt.date:
        as_of = self.ctx.as_of
        if version.valid_from and version.valid_from > as_of:
            return version.valid_from
        return as_of

    # --------------------------------------------------------------------------------------- citation
    async def citation(self, key: str, act: Act, version: Version, unit: Unit | None) -> Annotated:
        ctx = self.ctx
        serving = await self.facts.serving_at(act.act_id, ctx.as_of)
        eff_date = self.effective_date(version)
        effective = await self.facts.serving_at(act.act_id, eff_date) if eff_date != ctx.as_of else serving
        assert serving is not None and effective is not None
        short = (await self.facts.short_titles([version], {act.act_id: act})).get(act.act_id) or short_title_of(act)
        text_basis = effective.text_basis if effective.text_version_id == version.version_id else act.text_basis
        if text_basis == "metadata_only":
            text_basis = act.text_basis if act.text_basis != "metadata_only" else "original_never_amended"
        member_eli = None
        member_of = None
        out_of_scope = False
        if unit is not None and unit.member_act_id:
            member = await self.facts.act(unit.member_act_id)
            member_eli = member.eli_work if member else None
            member_of = of_member(member.title) if member else None
        elif unit is not None and version.source_format == "pdf" and version.kind in ("code_recueil", "recueil"):
            in_part, heading, part_anchor = await self.facts.member_part(unit.unit_id)
            if in_part:  # never cite a member's article as the code's own
                member_of = of_member(heading) if _ACT_TITLE.match(heading or "") else "d'un acte membre non identifié"
                out_of_scope = (part_anchor or "").startswith(EU_PART_PREFIX)
        article = article_short(unit.num_raw) if unit else None
        article_eli = unit.eli_unit if unit else None
        if unit is not None and unit.num_norm:
            res = resource_uri(act.eli_work) + "/art/" + unit.num_norm
        else:
            res = resource_uri(version.eli_version)
        title = short if version.kind in CODE_KINDS else clip(act.title, 200) or short
        cit: dict[str, Any] = {
            "key": key,
            "act_eli": act.eli_work,
            "act_jo_eli": act.eli_jo,
            "act_title": title,
            "short_title": short,
            "doc_type": act.doc_type,
            "memorial": act.memorial,
            "article": unit.num_raw if unit else None,
            "article_eli": article_eli,
            "article_eli_derived": bool(unit.eli_unit_derived) if unit else False,
            "member_act": member_eli,
            "version_eli": version.eli_version,
            "version_label": version.display_label,
            "valid_from": iso(version.valid_from or version.date_applicability),
            "valid_to": iso(version.valid_to),
            "article_wording_since": iso(unit.unit_version_date) if unit else None,
            "article_wording_since_inferred": True,
            "text_basis": text_basis,
            "flags": list(serving.flags),
            "status_as_of": {"as_of": iso(ctx.as_of), "status": serving.status, "basis": status_basis(act)},
            "page": unit.page_from if unit else None,
            "display_url": display_url(version.eli_version),
            "resource_uri": res,
            "licence": version.licence,
            "source_sha256": version.sha256,
            "warnings": [],
        }
        cit["citation_string"] = self.citation_string(
            article, short, version, act, text_basis, article_eli, member=member_of
        )
        await self.add_warnings(key, act, version, unit, serving, effective, text_basis)
        if out_of_scope:  # an EU text the code prints as a « Complément »: shown, never the code's own law
            ctx.bag.add(WarningCode.W_OUT_OF_SCOPE, ref=key, what="droit de l'UE reproduit en complément du code")
        cit["warnings"] = ctx.bag.codes_for(key)
        return Annotated(citation=cit, act=act, version=version, unit=unit, serving=serving, effective=effective)

    @staticmethod
    def citation_string(
        article: str | None,
        short: str,
        version: Version,
        act: Act,
        text_basis: str,
        article_eli: str | None,
        *,
        member: str | None = None,
    ) -> str:
        """« Art. L. 121-1, Code du travail (version consolidée applicable au 26/07/2026) — <ELI> » (PLAN §8.6); an
        article of a member act in a code-recueil names that act (PLAN §2.6): « Art. 2 de l'arrêté grand-ducal du 23
        novembre 1955 … (Code de la route, version consolidée applicable au 12/06/2026) — <ELI> »."""
        if version.is_original:
            mem = act.memorial_short or "Mém. A"
            label = f"texte original, {mem} — non consolidé" if text_basis != "consolidated" else "texte original"
        else:
            label = version.display_label
        if article and member:
            return f"{article} {member} ({short}, {label}) — {article_eli or version.eli_version}"
        head = f"{article}, {short}" if article else short
        return f"{head} ({label}) — {article_eli or version.eli_version}"

    # --------------------------------------------------------------------------------------- warnings
    async def add_warnings(
        self,
        key: str,
        act: Act,
        version: Version,
        unit: Unit | None,
        serving: Serving,
        effective: Serving,
        text_basis: str,
    ) -> None:
        ctx = self.ctx
        bag = ctx.bag
        as_of = ctx.as_of
        flags = set(serving.flags)
        since = version.valid_from or version.date_applicability or act.dei_min
        # --- text basis
        if text_basis == "original_possibly_outdated":
            acts = await self.amending(act, since)
            bag.add(
                WarningCode.W_NOT_CONSOLIDATED,
                ref=key,
                variant=None if acts else "no_known_acts",  # never claim amendments that get_act cannot list
                memorial=act.memorial or "Mémorial A",
                acts=acts_phrase(acts),
                data={"amending_acts": [a.as_dict() for a in acts]},
            )
        if text_basis == "stub_fallback":
            window = await self.facts.version(effective.window_version_id)
            d = window.date_applicability if window else None
            old = bool(
                version.date_applicability
                and (as_of - version.date_applicability).days > 365.25 * self.max_stub_age_years()
            )
            acts = await self.amending(act, since)
            bag.add(
                WarningCode.W_STUB,
                ref=key,
                variant="old_fallback" if old else None,
                d=fr_date(d),
                shown=version.display_label,
                data={
                    "announced_version": window.eli_version if window else None,
                    "amending_acts": [a.as_dict() for a in acts],
                },
            )
        elif "stub_no_text" in flags:
            stub = await self.latest_stub(act)
            bag.add(
                WarningCode.W_STUB,
                ref=key,
                d=fr_date(stub.date_applicability if stub else None),
                shown=version.display_label,
                data={"announced_version": stub.eli_version if stub else None},
            )
        if "consolidation_lags" in flags and not version.is_original:
            acts = await self.amending(act, since)
            bag.add(
                WarningCode.W_CONSO_LAGGING,
                ref=key,
                d=fr_date(version.date_applicability),
                acts=acts_phrase(acts),
                dates=", ".join(fr_date(a.date_entry_into_force) for a in acts[:MAX_ACTS_IN_MESSAGE]) or "?",
                data={"pending_acts": [a.as_dict() for a in acts]},
            )
        if "consolidation_discontinued" in flags:
            last = await self.last_consolidation(act)
            bag.add(
                WarningCode.W_CONSO_DISCONTINUED,
                ref=key,
                a=fr_date(last.date_applicability if last else None),
                b=fr_date(last.date_end_applicability if last else None),
            )
        # --- status at as_of
        await self.status_warnings(act, serving, ref=key)
        # --- versions
        if version.valid_from and version.valid_from > as_of:
            # the displayed text itself is the future wording: its own message (not merged with the current
            # hit's « nouvelle rédaction … » by the bag) and high severity (PLAN §8.7 V6)
            bag.add(
                WarningCode.W_FUTURE_VERSION,
                ref=key,
                variant="shown",
                d=fr_date(version.valid_from),
                severity=Severity.HIGH,
            )
        elif serving.next_version_date:
            bag.add(
                WarningCode.W_FUTURE_VERSION,
                ref=key,
                variant=await self.next_version_variant(act, serving),
                d=fr_date(serving.next_version_date),
            )
        # --- unit
        if unit is not None and unit.is_repealed:
            rep = await self.facts.unit_repealer(unit)
            if rep is not None:  # textualMod / note source of a repealed stub = the repealing act
                bag.add(
                    WarningCode.W_ARTICLE_REPEALED,
                    ref=key,
                    variant="by_act",
                    act=clip(rep.title, 120) or rep.eli,
                    data={"repeal_note": unit.repeal_note, "repealed_by": rep.as_dict()},
                )
            else:
                bag.add(WarningCode.W_ARTICLE_REPEALED, ref=key, data={"repeal_note": unit.repeal_note})
        # --- flags
        if "base_legale_abrogee" in flags:
            bag.add(WarningCode.W_BASE_LEGALE_ABROGEE, ref=key)
        if "acte_temporaire" in flags:
            bag.add(WarningCode.W_TEMPORARY_ACT, ref=key)
        if "pdf_extracted" in flags or version.source_format == "pdf":
            pages = "?"
            if unit is not None and unit.page_from:
                pages = f"{unit.page_from}" + (
                    f"–{unit.page_to}" if unit.page_to and unit.page_to != unit.page_from else ""
                )
            bag.add(WarningCode.W_PDF_EXTRACTED, ref=key, pages=pages)
        if "superseded_by_code" in flags:
            await self.superseded_by_code(act, ref=key)
        if version.is_original:
            bag.add(WarningCode.W_PARTIAL_REPEAL_UNKNOWN, ref=key)
        if unit is not None and unit.eli_unit_derived:
            bag.add(WarningCode.W_DERIVED_ELI, ref=key)
        await self.data_quality_warnings(
            [act.eli_work, act.eli_jo, version.eli_version, unit.eli_unit if unit else None], ref=key
        )

    async def next_version_variant(self, act: Act, serving: Serving) -> str | None:
        """None = stored; not_stored = published on Legilux (has_files) but not in this base; announced = no files."""
        if serving.next_version_has_text:
            return None
        nv = await self.facts.next_version(act.act_id, serving.next_version_date)
        return "not_stored" if nv is not None and nv.has_files else "announced"

    async def status_warnings(self, act: Act, serving: Serving, *, ref: str | None) -> None:
        bag = self.ctx.bag
        as_of = self.ctx.as_of
        if serving.status == "no-longer-in-force":
            if status_basis(act) == "rdf_flag":
                bag.add(WarningCode.W_REPEALED, ref=ref, variant="implicit", as_of=fr_date(as_of))
            else:
                rep = await self.facts.repealer(act)
                if rep is not None:
                    bag.add(
                        WarningCode.W_REPEALED,
                        ref=ref,
                        dnl=fr_date(act.dnl),
                        act=clip(rep.title, 120) or rep.eli,
                        as_of=fr_date(as_of),
                        data={"repealed_by": rep.as_dict()},
                    )
                else:
                    bag.add(
                        WarningCode.W_REPEALED, ref=ref, variant="date_only", dnl=fr_date(act.dnl), as_of=fr_date(as_of)
                    )
        elif serving.status == "not-yet-in-force":
            bag.add(WarningCode.W_NOT_YET_IN_FORCE, ref=ref, dei=fr_date(act.dei_min))
        if act.dnl and as_of < act.dnl <= as_of + dt.timedelta(days=90):
            bag.add(WarningCode.W_SUNSET, ref=ref, dnl=fr_date(act.dnl))

    async def data_quality_warnings(self, elis: list[str | None], *, ref: str | None) -> None:
        issues = await self.facts.data_quality([e for e in elis if e])
        n = 0
        for eli in elis:
            for issue in issues.get(eli or "", []):
                if n >= 3:
                    return
                detail = f"{issue['code']} ({clip(issue['detail'], 160)})" if issue.get("detail") else issue["code"]
                self.ctx.bag.add(WarningCode.W_DATA_QUALITY, ref=ref, detail=detail, data={"entity": eli, **issue})
                n += 1

    def max_stub_age_years(self) -> int:
        scope = self.ctx.release.config.get("scope") or {}
        try:
            return int(scope.get("stub_fallback_max_age_years", 5))
        except (TypeError, ValueError):
            return 5

    async def latest_stub(self, act: Act) -> Version | None:
        for v in await self.facts.versions_of_act(act.act_id):
            if v.is_stub and (v.date_applicability is None or v.date_applicability <= self.ctx.as_of):
                return v
        return None

    async def last_consolidation(self, act: Act) -> Version | None:
        for v in await self.facts.versions_of_act(act.act_id):
            if not v.is_original:
                return v
        return None
