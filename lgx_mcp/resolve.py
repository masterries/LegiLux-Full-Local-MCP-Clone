"""Citation resolver over the release database (PLAN §8.3 step 1, Appendix D.2.4).

Order: ELI → Mémorial reference → ``act_alias`` (+ the out-of-scope entries of ``config/aliases.yaml``) →
(type, date) candidates ranked by title trigram similarity → typed title (fuzzy). **Never guess**: when the
two best candidates are within 0.05 the status is ``ambiguous`` and every candidate is listed.

The (type, date) match uses the act's work ELI (``…/loi/2018/08/01/…``): it *is* the signature date by which
acts are cited. ``lgx.act.date_document`` is empty in the M1 release (open issue), and dates are never used
for status, only to find candidates.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lgx_core.citeparse import ALIAS_EQUIVALENTS, ActMention, Citation, CitationParser
from lgx_core.config import AliasesConfig, load_yaml
from lgx_core.eli import ELI_BASE, LEG_PREFIX, expand
from lgx_core.normalise import alias_norm
from lgx_mcp.facts import ACT_COLS, Act, Facts, Unit, _act, normalise_eli

log = logging.getLogger("lgx_mcp.resolve")

AMBIGUITY_MARGIN = 0.05
WEAK_SCORE = 0.2
# the only act of a (type, date) is taken only when the cited title fits it: « loi du 30 mai 2018 relative à la
# protection des animaux » scores 0.31 against the MiFID law of that date, « … sur les marchés financiers » 0.48
LONE_TITLE_SCORE = 0.4
# a truncated dated ELI (…/etat/leg/loi/2018/08/01) names a date, not an act: the acts of that date are candidates
_ELI_DATE = re.compile("^" + re.escape(LEG_PREFIX) + r"(?P<seg>[a-z_]+)/(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})$")
Status = Literal["resolved", "ambiguous", "not_found", "out_of_scope"]
ArticleStatus = Literal["resolved", "ambiguous", "not_found", "no_text", "not_requested"]


@dataclass(frozen=True)
class OutOfScopeAlias:
    alias_norm: str
    reason: str
    related: tuple[str, ...]


def load_out_of_scope_aliases(config_dir: Path) -> dict[str, OutOfScopeAlias]:
    """``config/aliases.yaml`` entries without a target (RGPD → EU law): not in the database (stage 1 open issue)."""
    path = config_dir / "aliases.yaml"
    if not path.exists():
        return {}
    try:
        cfg = AliasesConfig.model_validate(load_yaml(path))
    except Exception as exc:  # a broken optional file must not stop the server
        log.warning("cannot read %s: %s", path, exc)
        return {}
    out: dict[str, OutOfScopeAlias] = {}
    for e in cfg.aliases:
        if e.target is None and e.out_of_scope:
            n = alias_norm(e.alias)
            out[n] = OutOfScopeAlias(n, e.out_of_scope, tuple(expand(r) for r in e.related))
    return out


@dataclass
class Candidate:
    act: Act
    confidence: float
    reason: str  # eli | memorial | alias | type_date_title | title_fuzzy
    unit: Unit | None = None
    article: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "act_eli": self.act.eli_work,
            "title": self.act.title,
            "doc_type": self.act.doc_type,
            "date_document": self.act.date_document.isoformat() if self.act.date_document else None,
            "article": self.unit.num_raw if self.unit else self.article,
            "unit_eli": self.unit.eli_unit if self.unit else None,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "text_basis_at_T": self.act.text_basis,
            "metadata_reason": self.act.metadata_reason,
            "note": self.note,
        }


@dataclass
class Resolution:
    status: Status
    citation: Citation | None
    candidates: list[Candidate] = field(default_factory=list)
    article_status: ArticleStatus = "not_requested"
    message: str | None = None
    out_of_scope: str | None = None
    related: list[Act] = field(default_factory=list)
    # the cited act is a member superseded by a code-recueil; its article is served from the code's text
    redirected_from: Act | None = None

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.status == "resolved" and self.candidates else None


class Resolver:
    def __init__(self, facts: Facts, aliases: dict[str, list[int]], oos_aliases: dict[str, OutOfScopeAlias]) -> None:
        self.facts = facts
        self.aliases = aliases
        self.oos_aliases = oos_aliases
        self.parser = CitationParser(list(aliases) + list(oos_aliases))

    # -------------------------------------------------------------------------------------- entry points
    async def resolve_text(self, text: str, as_of: dt.date, *, want_article: bool = True) -> Resolution:
        cit = self.parser.parse_one(text)
        if cit is None:
            return Resolution(status="not_found", citation=None, message="aucune référence reconnue dans le texte")
        return await self.resolve(cit, as_of, want_article=want_article)

    async def resolve(self, cit: Citation, as_of: dt.date, *, want_article: bool = True) -> Resolution:
        if cit.act is None:
            return Resolution(
                status="not_found",
                citation=cit,
                message="article sans acte identifiable : précisez la loi, le règlement ou le code",
            )
        res = await self.resolve_act(cit.act, cit)
        # an article ELI (…/a686/art_5, …/code/travail/art_l_121-1) names its article even without « art. … »
        named_unit = res.status == "resolved" and res.candidates[0].unit is not None
        if res.status == "resolved" and want_article and (cit.article is not None or named_unit):
            await self.resolve_article(res, cit, as_of)
        return res

    async def resolve_eli(self, eli: str) -> Resolution:
        """An ELI given as such (get_act ``eli``, search ``filters.act_eli``): exact act, or that date's acts."""
        eli = normalise_eli(eli)
        m = ActMention(kind="eli", span=(0, len(eli)), surface=eli, eli=eli)
        return await self._by_eli(m, Citation(span=(0, len(eli)), text=eli, act=m))

    # --------------------------------------------------------------------------------------------- act
    async def resolve_act(self, m: ActMention, cit: Citation) -> Resolution:
        if m.out_of_scope:
            return Resolution(status="out_of_scope", citation=cit, out_of_scope=m.out_of_scope, message=m.out_of_scope)
        if m.kind == "eli":
            return await self._by_eli(m, cit)
        if m.kind == "memorial":
            return await self._by_memorial(m, cit)
        if m.kind == "alias":
            return await self._by_alias(m, cit)
        if m.kind == "dated":
            return await self._by_type_date(m, cit)
        if m.kind == "type_title":
            return await self._by_title(m, cit)
        return Resolution(status="not_found", citation=cit)

    async def _by_eli(self, m: ActMention, cit: Citation) -> Resolution:
        assert m.eli is not None
        act, unit, _ = await self.facts.find_act_by_eli(m.eli)
        if act is None:
            dm = _ELI_DATE.match(normalise_eli(m.eli))
            if dm:
                try:
                    d = dt.date(int(dm["y"]), int(dm["m"]), int(dm["d"]))
                except ValueError:
                    d = None
                if d is not None:
                    dated = ActMention(kind="dated", span=m.span, surface=m.surface, eli_segment=dm["seg"], date=d)
                    return await self._by_type_date(dated, cit)
            return Resolution(status="not_found", citation=cit, message=f"ELI inconnu dans cette base : {m.eli}")
        return Resolution(status="resolved", citation=cit, candidates=[Candidate(act, 1.0, "eli", unit=unit)])

    async def _by_memorial(self, m: ActMention, cit: Citation) -> Resolution:
        n, y = m.memorial_number, m.memorial_year
        assert n is not None
        if y is not None:
            rows = await self.facts.db.fetch(
                f"SELECT {ACT_COLS} FROM lgx.act a WHERE a.memorial_ref = %s"
                " OR (a.eli_work ~ %s AND extract(year FROM a.publication_date) = %s) ORDER BY a.eli_work LIMIT 50",
                [f"Mémorial A n° {n} de {y}", f"/a{n}$", y],
            )
        else:
            rows = await self.facts.db.fetch(
                f"SELECT {ACT_COLS} FROM lgx.act a WHERE a.memorial_ref LIKE %s OR a.eli_work ~ %s"
                " ORDER BY a.eli_work LIMIT 50",
                [f"Mémorial A n° {n} de %", f"/a{n}$"],
            )
        acts = [_act(r) for r in rows]
        return self._ranked(cit, [(a, 1.0) for a in acts], "memorial", tie_message="plusieurs actes dans ce Mémorial")

    async def _by_alias(self, m: ActMention, cit: Citation) -> Resolution:
        norm = m.alias_norm or alias_norm(m.surface)
        keys = [norm, ALIAS_EQUIVALENTS.get(norm, norm)]
        if norm in self.oos_aliases:
            oos = self.oos_aliases[norm]
            related = list((await self.facts.acts_by_eli(list(oos.related))).values())
            return Resolution(
                status="out_of_scope", citation=cit, out_of_scope=oos.reason, message=oos.reason, related=related
            )
        ids: list[int] = []
        for k in keys:
            ids.extend(self.aliases.get(k, []))
        if not ids:
            return Resolution(
                status="not_found", citation=cit, message=f"abréviation inconnue dans cette base : {m.surface}"
            )
        acts = list((await self.facts.acts(ids)).values())
        return self._ranked(cit, [(a, 0.98) for a in acts], "alias", tie_message="abréviation ambiguë")

    async def _by_type_date(self, m: ActMention, cit: Citation) -> Resolution:
        assert m.date is not None
        d = m.date
        segment = m.eli_segment or "%"
        pattern = f"{ELI_BASE}etat/leg/{segment}/{d.year:04d}/{d.month:02d}/{d.day:02d}/%"
        hint = m.title_hint or ""
        rows = await self.facts.db.fetch(
            f"SELECT {ACT_COLS},"
            " similarity(lgx.f_unaccent(lower(coalesce(a.title, ''))), lgx.f_unaccent(lower(%s))) AS sim,"
            " word_similarity(lgx.f_unaccent(lower(%s)), lgx.f_unaccent(lower(coalesce(a.title, '')))) AS wsim"
            " FROM lgx.act a WHERE a.eli_work LIKE %s ORDER BY a.eli_work LIMIT 100",
            [hint, hint, pattern],
        )
        if not rows:
            return Resolution(
                status="not_found",
                citation=cit,
                message=f"aucun acte {m.doc_type or ''} du {d:%d/%m/%Y} dans cette base".replace("  ", " "),
            )
        scored: list[tuple[Act, float]] = []
        inner = {i for a in m.inner_aliases for i in self.aliases.get(a, [])}
        for r in rows:
            act = _act(r)
            if act.act_id in inner:
                score = 0.99
            elif hint:
                score = 0.85 * float(r["wsim"] or 0) + 0.15 * float(r["sim"] or 0)
            else:
                score = 1.0 / len(rows)
            scored.append((act, score))
        if len(scored) == 1:
            act, score = scored[0]
            if not hint or act.act_id in inner or score >= LONE_TITLE_SCORE:
                scored = [(act, max(score, 0.95))]
            else:  # never guess: the cited title does not fit the only act of that date
                return Resolution(
                    status="ambiguous",
                    citation=cit,
                    candidates=[Candidate(act, score, "type_date_title")],
                    message="l'intitulé cité ne correspond pas au seul acte de cette date dans cette base",
                )
        return self._ranked(
            cit,
            scored,
            "type_date_title",
            tie_message=f"{len(scored)} actes portent cette date",
            weak_is_ambiguous=bool(hint),
        )

    async def _by_title(self, m: ActMention, cit: Citation) -> Resolution:
        hint = m.title_hint or ""
        segment = m.eli_segment or "loi"
        inner = {i for a in m.inner_aliases for i in self.aliases.get(a, [])}
        if inner:
            acts = list((await self.facts.acts(inner)).values())
            return self._ranked(cit, [(a, 0.97) for a in acts], "alias")
        rows = await self.facts.db.fetch(
            f"SELECT {ACT_COLS},"
            " similarity(lgx.f_unaccent(lower(coalesce(a.title, ''))), lgx.f_unaccent(lower(%s))) AS sim,"
            " word_similarity(lgx.f_unaccent(lower(%s)), lgx.f_unaccent(lower(coalesce(a.title, '')))) AS wsim"
            " FROM lgx.act a WHERE a.eli_work LIKE %s AND lgx.f_unaccent(lower(%s)) <%% lgx.f_unaccent(lower(a.title))"
            " ORDER BY wsim DESC, sim DESC, a.eli_work LIMIT 8",
            [hint, hint, f"{ELI_BASE}etat/leg/{segment}/%", hint],
        )
        scored = [(_act(r), 0.6 * float(r["wsim"] or 0) + 0.4 * float(r["sim"] or 0)) for r in rows]
        if not scored:
            return Resolution(status="not_found", citation=cit, message="aucun intitulé correspondant")
        return self._ranked(
            cit, scored, "title_fuzzy", tie_message="plusieurs intitulés proches", weak_is_ambiguous=True
        )

    @staticmethod
    def _ranked(
        cit: Citation,
        scored: list[tuple[Act, float]],
        reason: str,
        *,
        tie_message: str = "plusieurs candidats",
        weak_is_ambiguous: bool = False,
    ) -> Resolution:
        if not scored:
            return Resolution(status="not_found", citation=cit)
        scored.sort(key=lambda x: (-x[1], x[0].eli_work))
        cands = [Candidate(a, s, reason) for a, s in scored[:20]]
        top = cands[0].confidence
        if weak_is_ambiguous and top < WEAK_SCORE:
            return Resolution(status="ambiguous", citation=cit, candidates=cands, message=tie_message)
        if len(cands) == 1:
            return Resolution(status="resolved", citation=cit, candidates=cands)
        second = cands[1].confidence
        if top - second < AMBIGUITY_MARGIN:
            return Resolution(status="ambiguous", citation=cit, candidates=cands, message=tie_message)
        return Resolution(status="resolved", citation=cit, candidates=cands)

    # ----------------------------------------------------------------------------------------- article
    async def resolve_article(self, res: Resolution, cit: Citation, as_of: dt.date) -> None:
        best = res.candidates[0]
        art = cit.article
        if art is None:  # the article an article ELI names
            assert best.unit is not None
            key, raw = best.unit.num_norm or "", best.unit.num_raw or best.unit.eli_unit or ""
        else:
            key, raw = art.key, art.raw
            if best.unit is not None and not await self.facts.unit_has_key(best.unit, key):
                # an article ELI used as act_eli with another `article`: the act is the ELI's, the article the
                # one asked for (never the ELI's article under the requested number)
                best.unit = None
        best.article = raw
        serving = await self.facts.serving_at(best.act.act_id, as_of)
        version_id = serving.text_version_id if serving else None
        routable = version_id is None and key and best.act.superseded_by_act_id
        if routable and await self._route_to_code(res, best, key, raw, as_of):
            return
        if version_id is None:
            res.article_status = "no_text"
            reason = best.act.metadata_reason
            best.note = "aucun texte stocké applicable à cette date" + (f" ({reason})" if reason else "")
            return
        if best.unit is not None and best.unit.version_id == version_id:
            res.article_status = "resolved"
            return
        if best.unit is not None and best.unit.eli_unit:
            rows = await self.facts.db.fetch(
                "SELECT u.unit_id FROM lgx.unit u WHERE u.version_id = %s AND u.eli_unit = %s",
                [version_id, best.unit.eli_unit],
            )
            if rows:
                best.unit = await self.facts.unit(int(rows[0]["unit_id"]))
                res.article_status = "resolved"
                return
        units = await self.facts.article_units(version_id, key) if key else []
        if not units:
            best.unit = None
            res.article_status = "not_found"
            best.note = f"article {raw} introuvable dans la version applicable"
            return
        main = [u for u in units if not u.in_annex] or units
        exact = [u for u in main if u.num_norm == key] or main
        if len(exact) > 1:
            exact = await self._own_articles(best.act, exact)
        if len(exact) > 1:
            res.article_status = "ambiguous"
            res.status = "ambiguous"
            res.message = f"plusieurs articles {raw} dans ce texte (parties ou actes membres différents)"
            res.candidates = [
                Candidate(best.act, 0.5, best.reason, unit=u, article=u.num_raw, note=u.breadcrumb) for u in exact[:20]
            ]
            return
        best.unit = exact[0]
        res.article_status = "resolved"

    async def _own_articles(self, act: Act, units: list[Unit]) -> list[Unit]:
        """A base act whose consolidation is a code-recueil (``loi/1925/12/17/n1`` = Code de la sécurité sociale):
        its own articles come before the members' (« art. 24 du Code de la sécurité sociale » is the code's art. 24,
        not that of a law printed after it): own = no member act and no member part above it (unnamed members are
        ``part`` units too). Unchanged for pure recueils (``…/code/route``, ``…/code/fonction_publique``: every
        article belongs to a member, ambiguous stays ambiguous) and outside code-recueils."""
        if "/code/" in act.eli_work or "/recueil/" in act.eli_work or not any(u.member_act_id for u in units):
            return units
        in_part = await self.facts.under_part(u.unit_id for u in units if u.member_act_id is None)
        own = [u for u in units if u.member_act_id is None and u.unit_id not in in_part]
        return own or units

    async def code_articles(self, member: Act, key: str, as_of: dt.date) -> list[Unit]:
        """The article units numbered ``key`` of ``member`` in the text of the code-recueil that supersedes it."""
        if member.superseded_by_act_id is None or not key:
            return []
        serving = await self.facts.serving_at(member.superseded_by_act_id, as_of)
        version_id = serving.text_version_id if serving else None
        if version_id is None:
            return []
        units = [u for u in await self.facts.article_units(version_id, key) if u.member_act_id == member.act_id]
        main = [u for u in units if not u.in_annex] or units
        return [u for u in main if u.num_norm == key] or main

    async def _route_to_code(self, res: Resolution, best: Candidate, key: str, raw: str, as_of: dt.date) -> bool:
        """PLAN §2.6 (4), Appendix D.2.2: a member act superseded by a code-recueil and without a text of its own at
        ``as_of`` is served from the code's text (« redirect to the code article »). An act with its own (original)
        text keeps it, with W_SUPERSEDED_BY_CODE and a pointer to the code article (``get_article``). ``False`` when
        the code has no text at ``as_of`` or no such article of that member."""
        exact = await self.code_articles(best.act, key, as_of)
        if not exact:
            return False
        res.redirected_from = best.act
        if len(exact) > 1:
            res.article_status = "ambiguous"
            res.status = "ambiguous"
            res.message = f"plusieurs articles {raw} de cet acte dans le code qui l'intègre"
            res.candidates = [
                Candidate(best.act, 0.5, best.reason, unit=u, article=u.num_raw, note=u.breadcrumb) for u in exact[:20]
            ]
            return True
        best.unit = exact[0]
        best.note = "acte intégré à un code : article servi depuis le texte du code (superseded_by_code)"
        res.article_status = "resolved"
        return True
