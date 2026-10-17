"""Records produced by the citation parser (pure data, JSON-friendly)."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ActMentionKind = Literal["eli", "memorial", "alias", "dated", "type_title"]


@dataclass(frozen=True)
class ArticleMention:
    """« art. L. 121-1 », « articles 17 à 20 », « article 5, paragraphe 2, point 3° »."""

    span: tuple[int, int]
    raw: str  # article number as written: 'L. 121-1', '1er', '12bis'
    key: str  # lgx_core.normalise.num_norm(raw): 'l121-1', '1', '12bis'
    to_raw: str | None = None  # range / list end: 'articles 17 à 20' -> '20'
    to_key: str | None = None
    connector: Literal["range", "list"] | None = None
    paragraph: str | None = None
    alinea: str | None = None
    point: str | None = None


@dataclass(frozen=True)
class ActMention:
    """One reference to an act: ELI, Mémorial, alias (code abbreviation, acronym, short title), dated act."""

    kind: ActMentionKind
    span: tuple[int, int]
    surface: str
    doc_type: str | None = None  # Legilux resource-type code (LOI, RGD …) when the surface names one
    eli_segment: str | None = None  # path segment of that type in work ELIs ('loi', 'rgd' …)
    date: dt.date | None = None  # signature date as cited (« loi du 1er août 2018 »)
    title_hint: str | None = None  # « portant organisation de la Commission nationale … »
    modified: bool = False  # « loi modifiée du … » (informational; ignored for matching)
    alias_norm: str | None = None  # lgx_core.normalise.alias_norm of the alias surface
    eli: str | None = None  # absolute http://data.legilux.public.lu/eli/… identifier
    eli_article: str | None = None  # article key from legilux://eli/…/art/{n}
    memorial_series: str | None = None  # 'A' | 'B'
    memorial_number: int | None = None
    memorial_year: int | None = None
    out_of_scope: str | None = None  # reason when the reference is outside the corpus (adm branch, Mémorial B)
    inner_aliases: tuple[str, ...] = ()  # alias_norms found inside a dated mention's title hint (« … de la CNPD »)


@dataclass(frozen=True)
class OutOfScopeMention:
    """EU law, case law, communal regulations, Mémorial B, bills (W_OUT_OF_SCOPE)."""

    kind: Literal["eu_law", "case_law", "communal", "memorial_b", "bill", "adm_branch"]
    span: tuple[int, int]
    surface: str


@dataclass(frozen=True)
class Citation:
    """An article reference with the act it belongs to (either part may be missing)."""

    span: tuple[int, int]
    text: str
    article: ArticleMention | None = None
    act: ActMention | None = None

    def parsed(self) -> dict[str, Any]:
        """The ``parsed`` block of ``resolve_citation`` (Appendix D.2.4)."""
        act = self.act
        art = self.article
        return {
            "type": act.doc_type if act else None,
            "date": act.date.isoformat() if act and act.date else None,
            "article": art.raw if art else None,
            "article_key": art.key if art else None,
            "article_to": art.to_raw if art else None,
            "paragraph": art.paragraph if art else None,
            "alinea": art.alinea if art else None,
            "point": art.point if art else None,
            "title_hint": act.title_hint if act else None,
            "act_kind": act.kind if act else None,
            "alias": act.surface if act and act.kind == "alias" else None,
            "eli": act.eli if act else None,
            "memorial": (
                {"series": act.memorial_series, "number": act.memorial_number, "year": act.memorial_year}
                if act and act.kind == "memorial"
                else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.act and self.act.date:
            d["act"]["date"] = self.act.date.isoformat()
        return d


@dataclass(frozen=True)
class QueryAnalysis:
    """What ``search_legislation`` learns from its query before searching."""

    citations: list[Citation] = field(default_factory=list)
    exact_tokens: list[str] = field(default_factory=list)
    out_of_scope: list[OutOfScopeMention] = field(default_factory=list)
