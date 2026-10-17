"""Code-recueils and recueils (``code/fonction_publique``, ``code/sante``, ``recueil/*``, PDF code-recueils;
PLAN §2.6 and research « gaps-and-reconciliation » §2.3).

* Parsing helpers (used by the AKN parser): generated ids (``art_N10064``) are never anchors, ``wId``
  article ELIs are normalised (``…/n2//consolide/…``) and give the member act.
* :func:`parse_wid` classifies a ``wId``: ``<work>/art_X/consolide/YYYYMMDD`` (a member consolidation),
  ``<work>/art_X/jo`` (the member's JO text), ``<code>/YYYYMMDD/art_X`` (a code version); malformed
  variants are normalised (``…/n2//consolide/…`` without article segment → matched by article number;
  accents/commas in the article segment) and counted, anything else is ``malformed``.
* :func:`deduplicate` (XML recueils, structure stage): a recueil article whose ``wId`` points to the
  member's consolidation **that is stored in this build** and that has the same article is dropped — the
  member consolidation is canonical; ``/jo``-sourced articles are kept from the recueil; every other
  article (member consolidation not in this build, another version than the member's served one,
  unmatched article, malformed ``wId``) is **kept and logged**, never silently dropped. The full list of
  outcomes is returned for ``units/<T>/<slug>.dedup.json``.
* :func:`apply_members` (PDF code-recueils): member parts (``agd_1955-11-23``) get the member act ELI from
  ``config/recueil_members.yaml`` (lawyer-reviewed), inherited by every unit inside the part.
"""

from __future__ import annotations

import collections
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lgx_core.eli import ELI_BASE
from lgx_core.models import DataQualityIssue, Relation, Unit, UnitType
from lgx_core.normalise import key_from_eid, num_norm, unaccent

_GENERATED_ID = re.compile(r"^[a-z]+_N[0-9A-F]{4,}$")
_DATA_HOST = "http://data.legilux.public.lu"
DEDUP_VERSION = "dedup-1"


def is_generated_id(akn_id: str) -> bool:
    """True for editor-generated, unstable ids such as ``art_N10064`` / ``part_N10026``."""
    return bool(_GENERATED_ID.match(akn_id))


def normalise_wid(wid: str) -> str:
    """Absolute, slash-normalised ``wId`` (``/eli/etat/leg/…/n2//consolide/…`` → ``…/n2/consolide/…``)."""
    w = wid.strip()
    if w.startswith("/eli/"):
        w = _DATA_HOST + w
    head, sep, tail = w.partition("://")
    if sep:
        tail = re.sub(r"/{2,}", "/", tail)
        w = f"{head}{sep}{tail}"
    return w.rstrip("/")


_ARTICLE_SEGMENT = re.compile(r"^(?P<work>.+?)/(?:art_|art\d|article_)")


def member_work_of_wid(wid: str) -> str | None:
    """Work ELI of the member act a recueil article comes from (the part before ``/art_…``,
    ``/art8``, ``/article_unique``)."""
    m = _ARTICLE_SEGMENT.match(wid)
    return m["work"] if m and wid.startswith(ELI_BASE) else None


# ------------------------------------------------------------------------------------------- wIds
_WID_CONSO = re.compile(r"^(?P<work>.+?)/(?P<art>art[_\d][^/]*|article_[^/]+)/consolide/(?P<date>\d{8})$")
_WID_JO = re.compile(r"^(?P<work>.+?)/(?P<art>art[_\d][^/]*|article_[^/]+)/jo$")
_WID_CODE = re.compile(r"^(?P<version>.+?/code/[a-z_]+/\d{8})/(?P<art>art[_\d][^/]*|article_[^/]+)$")
_WID_PLAIN = re.compile(r"^(?P<work>.+?)/(?P<art>art[_\d][^/]*|article_[^/]+)$")
_WID_VERSION = re.compile(r"^(?P<work>.+?)/consolide/(?P<date>\d{8})$")  # « …/n2//consolide/… » (no article)
_VALID = re.compile(r"^http://data\.legilux\.public\.lu/eli/[A-Za-z0-9_.\-]+(?:/[^/\s]+)+$")
_CLEAN = re.compile(r"^http://data\.legilux\.public\.lu/eli/[A-Za-z0-9_.\-/]+$")


@dataclass(frozen=True, slots=True)
class WidRef:
    """A classified recueil ``wId``."""

    wid: str  # normalised
    kind: str  # consolide | jo | code_version | plain | malformed
    member_work: str | None = None  # work ELI as written in the wId (for codes: the code family)
    member_version: str | None = None  # version the article was copied from
    article: str | None = None  # 'art_11', 'art_l_411_1', 'article_unique'
    normalised: bool = False  # the raw wId was malformed ('…//consolide/…', accents, commas, spaces)

    @property
    def article_key(self) -> str | None:
        return fold_article(self.article) if self.article else None


def fold_article(eid: str) -> str:
    """Comparable article key: ``art_l_411_1`` ~ ``…/art_l_411-1`` ~ ``art_l.411-1`` → ``l4111``."""
    return re.sub(r"[-_.\s]", "", key_from_eid(eid))


def parse_wid(raw: str) -> WidRef:
    """Classify a recueil article ``wId`` (absolute or relative, malformed variants normalised)."""
    wid = normalise_wid(raw)
    if not wid.startswith(ELI_BASE) or not _VALID.match(wid):
        return WidRef(wid, "malformed", normalised=True)
    fixed = wid != (_DATA_HOST + raw.strip() if raw.strip().startswith("/eli/") else raw.strip()).rstrip("/")
    fixed = fixed or not _CLEAN.match(wid)
    if m := _WID_CONSO.match(wid):
        return WidRef(wid, "consolide", m["work"], f"{m['work']}/consolide/{m['date']}", m["art"], fixed)
    if m := _WID_JO.match(wid):
        return WidRef(wid, "jo", m["work"], f"{m['work']}/jo", m["art"], fixed)
    if m := _WID_CODE.match(wid):
        return WidRef(wid, "code_version", m["version"].rsplit("/", 1)[0], m["version"], m["art"], fixed)
    if m := _WID_PLAIN.match(wid):
        return WidRef(wid, "plain", m["work"], None, m["art"], fixed)
    if m := _WID_VERSION.match(wid):  # the article segment is missing: match by article number
        return WidRef(wid, "consolide", m["work"], f"{m['work']}/consolide/{m['date']}", None, True)
    return WidRef(wid, "malformed", normalised=True)


# ---------------------------------------------------------------------------------------- dedup
@dataclass(slots=True)
class Canonical:
    """Article index of a version stored in this build (the member consolidation)."""

    eli_version: str
    eli_work: str
    by_key: dict[str, Unit] = field(default_factory=dict)
    by_num: dict[str, list[Unit]] = field(default_factory=dict)

    @classmethod
    def of(cls, eli_version: str, eli_work: str, units: Iterable[Unit]) -> Canonical:
        c = cls(eli_version, eli_work)
        for u in units:
            if u.unit_type is not UnitType.ARTICLE:
                continue
            for src in (u.eli_unit, u.akn_id, u.anchor):
                if src and not is_generated_id(src.rsplit("/", 1)[-1]):
                    c.by_key.setdefault(fold_article(src), u)
            if u.num_norm:
                c.by_num.setdefault(u.num_norm, []).append(u)
        return c

    def find(self, ref: WidRef, num: str | None) -> Unit | None:
        if ref.article_key and ref.article_key in self.by_key:
            return self.by_key[ref.article_key]
        if num and len(self.by_num.get(num, [])) == 1:
            return self.by_num[num][0]
        return None


@dataclass(slots=True)
class DedupResult:
    units: list[Unit]
    relations: list[Relation]
    issues: list[DataQualityIssue]
    report: dict[str, Any]


def _outcome(u: Unit, outcome: str, **extra: str | None) -> dict[str, Any]:
    return {"anchor": u.anchor, "num": u.num_raw, "wid": u.from_recueil_wid, "outcome": outcome, **extra}


def deduplicate(
    units: Sequence[Unit],
    relations: Sequence[Relation],
    *,
    canonical: Mapping[str, Canonical],
    served: Mapping[str, str],
    version_work: Mapping[str, str] | None = None,
    first_seen: Any = None,
) -> DedupResult:
    """Drop recueil copies of articles whose canonical member consolidation is stored in this build.

    ``canonical``: version ELI → :class:`Canonical` for every non-recueil version with units in this build;
    ``served``: member work ELI → the version the selection serves (role current/fallback);
    ``version_work``: version ELI → work ELI (codes: ``code/travail/20260726`` → ``loi/2006/07/31/n2``).
    Ordinals are renumbered (document order kept), relations of dropped units are dropped, the others
    re-pointed; the report lists every article with its outcome.
    """
    version_work = version_work or {}
    outcomes: list[dict[str, Any]] = []
    drop: set[int] = set()
    counts: collections.Counter[str] = collections.Counter()
    counts_norm: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for u in units:
        if u.unit_type is not UnitType.ARTICLE:
            continue
        if not u.from_recueil_wid:
            counts["kept_no_wid"] += 1
            continue
        ref = parse_wid(u.from_recueil_wid)
        if ref.normalised and ref.kind != "malformed":
            counts_norm["wid_normalised"] += 1
            if len(examples["wid_normalised"]) < 8:
                examples["wid_normalised"].append(u.anchor)
        if ref.kind == "malformed":
            outcome, extra = "kept_malformed_wid", {}
        elif ref.kind == "jo":
            outcome, extra = "kept_jo", {}
        else:
            work = (
                version_work.get(ref.member_version or "")
                or (ref.member_work if ref.kind != "code_version" else None)
                or ref.member_work
            )
            assert work is not None
            served_v = served.get(work)
            target = ref.member_version or served_v
            canon = canonical.get(target or "")
            if canon is None:
                outcome = (
                    "kept_canonical_not_loaded"
                    if target is not None and target == served_v
                    else ("kept_version_mismatch" if served_v else "kept_member_not_served")
                )
                extra = {"member_version": target, "served": served_v}
            else:
                hit = canon.find(ref, u.num_norm)
                if hit is None:
                    outcome, extra = "kept_unmatched", {"member_version": canon.eli_version}
                elif served_v is not None and served_v != canon.eli_version:
                    outcome, extra = "kept_version_mismatch", {"member_version": canon.eli_version, "served": served_v}
                else:
                    outcome = "dropped_duplicate"
                    extra = {"canonical_version": canon.eli_version, "canonical_anchor": hit.anchor}
                    drop.add(u.ordinal)
        counts[outcome] += 1
        if len(examples[outcome]) < 8:
            examples[outcome].append(u.anchor)
        outcomes.append(_outcome(u, outcome, **extra))
    kept, relations_out = _renumber(units, relations, drop)
    issues: list[DataQualityIssue] = []
    if units:
        version = units[0].eli_version
        for code, key in (
            ("recueil_wid_malformed", "kept_malformed_wid"),
            ("recueil_article_unmatched", "kept_unmatched"),
            ("recueil_version_mismatch", "kept_version_mismatch"),
        ):
            if counts[key]:
                issues.append(
                    DataQualityIssue(
                        entity_eli=version,
                        code=code,
                        detail=f"{counts[key]} article(s) kept from the recueil, e.g. {', '.join(examples[key])}",
                        first_seen=first_seen,
                    )
                )
        if counts_norm["wid_normalised"]:
            issues.append(
                DataQualityIssue(
                    entity_eli=version,
                    code="recueil_wid_normalised",
                    detail=f"{counts_norm['wid_normalised']} malformed wId(s) normalised, e.g. "
                    + ", ".join(examples["wid_normalised"]),
                    first_seen=first_seen,
                )
            )
    report = {
        "version": DEDUP_VERSION,
        "counts": dict(sorted(counts.items())),
        "wid_normalised": counts_norm["wid_normalised"],
        "dropped": counts["dropped_duplicate"],
        "kept": sum(v for k, v in counts.items() if k.startswith("kept")),
        "relations_dropped": len(relations) - len(relations_out),
        "articles": outcomes,
    }
    return DedupResult(kept, relations_out, issues, report)


def _renumber(
    units: Sequence[Unit], relations: Sequence[Relation], drop: set[int]
) -> tuple[list[Unit], list[Relation]]:
    if not drop:
        return list(units), list(relations)
    mapping: dict[int, int] = {}
    kept: list[Unit] = []
    for u in sorted(units, key=lambda u: u.ordinal):
        if u.ordinal not in drop:
            mapping[u.ordinal] = len(mapping) + 1
    for u in sorted(units, key=lambda u: u.ordinal):
        if u.ordinal in drop:
            continue
        parent = u.parent_ordinal
        while parent is not None and parent in drop:  # never happens for leaf articles; be safe
            parent = next((x.parent_ordinal for x in units if x.ordinal == parent), None)
        kept.append(
            u.model_copy(
                update={
                    "ordinal": mapping[u.ordinal],
                    "parent_ordinal": mapping[parent] if parent is not None else None,
                }
            )
        )
    rels: list[Relation] = []
    for r in relations:
        if r.src_unit_ordinal is None:
            rels.append(r)
        elif r.src_unit_ordinal not in drop:
            rels.append(r.model_copy(update={"src_unit_ordinal": mapping[r.src_unit_ordinal]}))
    return kept, rels


# --------------------------------------------------------------------------------- PDF members
def member_key_of(anchor: str) -> str:
    """Member key of a PDF member part anchor (``agd_1955-11-23~2`` → ``agd_1955-11-23``)."""
    return anchor.split("~", 1)[0]


def _norm_heading(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unaccent(text or "").lower()).strip()


@dataclass(frozen=True, slots=True)
class MemberRef:
    member_eli: str
    member_key: str | None
    part_heading: str | None


def apply_members(units: Sequence[Unit], members: Sequence[MemberRef]) -> tuple[list[Unit], dict[str, int]]:
    """Set ``member_act_eli`` on PDF member parts (matched by heading, then by member key when the key is
    unique) and on every unit inside them."""
    by_heading = {_norm_heading(m.part_heading): m.member_eli for m in members if m.part_heading}
    keys = collections.Counter(m.member_key for m in members if m.member_key)
    by_key = {m.member_key: m.member_eli for m in members if m.member_key and keys[m.member_key] == 1}
    resolved: dict[int, str] = {}
    stats: collections.Counter[str] = collections.Counter()
    for u in units:
        if u.unit_type is UnitType.PART and u.parent_ordinal is None and u.num_raw is None:
            eli = (by_heading.get(_norm_heading(u.heading)) if u.heading else None) or by_key.get(
                member_key_of(u.anchor)
            )
            if eli:
                resolved[u.ordinal] = eli
                stats["member_parts_resolved"] += 1
            elif re.match(r"^[a-z]+_\d{4}-\d{2}-\d{2}", u.anchor) or u.anchor.startswith("membre_"):
                stats["member_parts_unresolved"] += 1
    out: list[Unit] = []
    for u in units:
        eli = resolved.get(u.ordinal) or (resolved.get(u.parent_ordinal) if u.parent_ordinal else None)
        if eli is None and u.parent_ordinal is not None:
            eli = _inherited(u, out)
        if eli and u.member_act_eli != eli:
            out.append(u.model_copy(update={"member_act_eli": eli}))
            stats["units_with_member"] += 1
        else:
            out.append(u)
    return out, dict(stats)


def _inherited(u: Unit, done: Sequence[Unit]) -> str | None:
    """Member ELI of the parent (already processed: parents precede children)."""
    parent = done[u.parent_ordinal - 1] if u.parent_ordinal and u.parent_ordinal <= len(done) else None
    return parent.member_act_eli if parent is not None and parent.ordinal == u.parent_ordinal else None


def num_key(num_raw: str | None) -> str | None:
    return num_norm(num_raw) if num_raw else None
