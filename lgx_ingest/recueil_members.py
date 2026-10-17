"""Derive ``config/recueil_members.yaml`` v1 (PLAN §2.6 item 1, §2.2 step 3 routing).

The RDF has **no** links from recueils or code-recueils to their members (research r1/r2: outgoing
predicates are only isMemberOf, isRealizedBy, typeDocument …; re-checked on the 2026-10-16 snapshot: no
``rel_other``/``rel_modifies_repeals`` row and no ``conso_base_acts`` row for any recueil). Membership is
therefore derived from the **cached texts** and resolved against the **metadata snapshot**:

* XML recueils: article ``wId`` s name the member work directly (method ``wid``);
* PDF code-recueils: member headings (« Arrêté grand-ducal du 23 novembre 1955 portant … ») give
  (type, date, title); the act is the unique act of that type and date in ``acts.jsonl`` whose title best
  matches (method ``heading``; ties within 0.05, or a best score < 0.35, go to ``unresolved``).

Routing (``route``): a member is routed to the code text — JO original flagged ``superseded_by_code``, not
searchable — only when (a) the recueil itself is served with text, (b) the member has no consolidation of
its own (its JO original is the outdated text) and (c) the recueil part is not an excerpt (« …, art. 8 et 12 »,
« (extraits) » in the heading, or « Extrait: Art. 9 » at the start of the part's preamble, after the Mémorial
reference); XML ``/jo`` members are not routed by default (``coverage: unknown`` — the recueil may
reproduce only some articles). Every entry is ``reviewed: false`` until a lawyer signs it off; recueils
whose text is not cached in this build are listed under ``pending``.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lgx_core.config import RecueilMemberEntry, RecueilMembersConfig, RecueilPending, RecueilUnresolved
from lgx_core.eli import ELI_BASE
from lgx_core.io import atomic_write_text, read_jsonl
from lgx_core.models import Act, ManifestEntry, MetadataReason, TextBasis, Unit, UnitType, VersionRole
from lgx_core.normalise import unaccent
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.parse.pdf import EU_PART_PREFIX, match_member
from lgx_ingest.parse.recueil import parse_wid

RECUEIL_TYPES = frozenset({"CODE_RECUEIL", "RECUEIL"})
_CODE_TYPES = frozenset({"CODE", "CODE_RECUEIL", "Constitution"})
_TYPE_CANDIDATES = {
    "LOI": {"LOI"},
    "RGD": {"RGD", "RMIN", "RGC"},
    "AGD": {"AGD", "ARGD"},
    "RMIN": {"RMIN"},
    "AMIN": {"AMIN", "AGD", "AGC", "A", "AR", "ARGD"},
    "RI": {"RI"},
    "CONV": {"CONV", "ACC", "PROT"},
    "ACC": {"ACC", "CONV"},
    "PROT": {"PROT"},
    "ST": {"ST"},
    "DEC": {"DEC", "D"},
    "ORD": {"ORD", "ORDR"},
}
_NOT_ROUTED = frozenset(
    {MetadataReason.REPEALED, MetadataReason.REPEALED_BASE, MetadataReason.TEMPORARY, MetadataReason.EXCLUDED_BY_CONFIG}
)
_STOP = frozenset(
    [
        "le",
        "la",
        "les",
        "l",
        "du",
        "de",
        "des",
        "d",
        "un",
        "une",
        "et",
        "a",
        "au",
        "aux",
        "en",
        "sur",
        "pour",
        "par",
        "portant",
        "concernant",
        "relatif",
        "relative",
        "relatifs",
        "relatives",
        "ayant",
        "objet",
        "modifie",
        "modifiee",
        "modifies",
        "telle",
        "tel",
        "qu",
        "il",
        "elle",
        "a",
        "ete",
        "loi",
        "reglement",
        "arrete",
        "grand",
        "ducal",
        "ministeriel",
    ]
)
_EXCERPT = re.compile(r",\s*(?:art\.|articles?)\s*\d|\(extraits?\)|\bextraits?\b", re.IGNORECASE)
# « (Mém. A - 18 du 27 mars 1987, p. 230) … Extrait: Art. 9 » — the excerpt note printed under the Mémorial reference
_EXCERPT_NOTE = re.compile(r"(?im)^\s*\(?\s*extraits?\s*(?:[:)]|$)|\(extraits?\)|\bextraits?\s*:\s*(?:art|chap|titre)")
_ELI_DATE = re.compile(r"/eli/etat/leg/(?P<type>[a-z_]+)/(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/")


def _words(text: str) -> set[str]:
    t = unaccent(text or "").lower().replace("\\n", " ")
    t = re.sub(r"^.*?\bdu\s+\d{1,2}(?:er)?\s+\w+\s+\d{4}\b", " ", t, count=1)  # drop « Loi du 5 juin 2009 »
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP and len(w) > 1}


def title_score(heading: str, title: str) -> float:
    """Word-overlap score: mean of the shares of each title's content words found in the other."""
    a, b = _words(heading), _words(title)
    if not a or not b:
        return 0.0
    common = len(a & b)
    return 0.5 * (common / len(a) + common / len(b))


@dataclass(slots=True)
class ActIndex:
    by_type_date: dict[tuple[str, dt.date], list[Act]]
    by_work: dict[str, Act]

    @classmethod
    def of(cls, acts: Iterable[Act]) -> ActIndex:
        idx: dict[tuple[str, dt.date], list[Act]] = collections.defaultdict(list)
        works: dict[str, Act] = {}
        for a in acts:
            works[a.eli_work] = a
            day = a.date_document
            if day is None:  # matching key only (never used for status): the signature date of the ELI
                m = _ELI_DATE.search(a.eli_work + "/")
                if m:
                    try:
                        day = dt.date(int(m["y"]), int(m["m"]), int(m["d"]))
                    except ValueError:
                        day = None
            if day is not None:
                idx[(a.doc_type, day)].append(a)
        return cls(idx, works)

    def resolve(self, act_type: str, day: dt.date, heading: str) -> tuple[Act | None, float, list[tuple[Act, float]]]:
        cands: list[Act] = []
        for t in sorted(_TYPE_CANDIDATES.get(act_type, {act_type}) | _CODE_TYPES):  # codes keep their base act date
            cands.extend(self.by_type_date.get((t, day), []))
        scored = sorted(((a, title_score(heading, a.title or "")) for a in cands), key=lambda x: (-x[1], x[0].eli_work))
        if not scored:
            return None, 0.0, []
        best, score = scored[0]
        if len(scored) == 1 and score >= 0.2:
            return best, score, scored
        second = scored[1][1] if len(scored) > 1 else 0.0
        if score >= 0.35 and score - second >= 0.05:
            return best, score, scored
        return None, score, scored


def _rel(eli: str) -> str:
    return eli.split("/eli/", 1)[-1] if eli.startswith(ELI_BASE) else eli


def _served_text(entries: Sequence[ManifestEntry]) -> dict[str, str]:
    """work → served version with text (role current/fallback, a file)."""
    out: dict[str, str] = {}
    for e in entries:
        if e.eli_version and e.file is not None and e.role in (VersionRole.CURRENT, VersionRole.FALLBACK):
            out.setdefault(e.eli_work, e.eli_version)
    return out


def derive(
    *,
    acts: Sequence[Act],
    entries: Sequence[ManifestEntry],
    units_of: dict[str, list[Unit]],
    generated: str,
    dropped_of: dict[str, list[str]] | None = None,
) -> RecueilMembersConfig:
    """Membership entries for every recueil version whose units are available (pure). ``dropped_of``: version →
    anchors of the PDF member-article copies the structure pass dropped (they still count as the part's articles:
    the code reproduces that member, whose own consolidation is canonical)."""
    index = ActIndex.of(acts)
    acts_by_work = index.by_work
    served = _served_text(entries)
    version_work = {e.eli_version: e.eli_work for e in entries if e.eli_version}
    recueil_versions = sorted(
        {(e.eli_work, e.eli_version) for e in entries if e.eli_version and e.doc_type in RECUEIL_TYPES and e.file},
    )
    recueil_works = sorted({a.eli_work for a in acts if a.doc_type in RECUEIL_TYPES})
    members: list[RecueilMemberEntry] = []
    unresolved: list[RecueilUnresolved] = []
    pending: list[RecueilPending] = []
    done_works: set[str] = set()
    for work, version in recueil_versions:
        units = units_of.get(version)
        rec_act = acts_by_work.get(work)
        served_with_text = rec_act is not None and rec_act.text_basis is not TextBasis.METADATA_ONLY
        if units is None:
            pending.append(
                RecueilPending(recueil=_rel(work), version=_rel(version), reason="text not cached in this build")
            )
            continue
        done_works.add(work)
        if any(u.source_format.value == "pdf" for u in units[:1]):
            dropped = (dropped_of or {}).get(version, [])
            m, un = _from_pdf(work, version, units, index, acts_by_work, served, served_with_text, dropped)
        else:
            m, un = _from_xml(work, version, units, acts_by_work, served, version_work, served_with_text)
        members.extend(m)
        unresolved.extend(un)
    for work in recueil_works:
        if work in done_works or any(p.recueil == _rel(work) for p in pending):
            continue
        a = acts_by_work[work]
        reason = (
            "no text served at T (" + (a.metadata_reason.value if a.metadata_reason else a.text_basis.value) + ")"
            if a.text_basis is TextBasis.METADATA_ONLY
            else "text not in the dev sample (derive after `lgx ingest --full`)"
        )
        pending.append(
            RecueilPending(
                recueil=_rel(work), version=_rel(a.basis_version_eli) if a.basis_version_eli else None, reason=reason
            )
        )
    members.sort(key=lambda m: (m.recueil, m.member, m.member_key or "", m.part_heading or ""))
    unresolved.sort(key=lambda u: (u.recueil, u.member_key or "", u.part_heading))
    pending.sort(key=lambda p: p.recueil)
    return RecueilMembersConfig(version=1, generated=generated, members=members, unresolved=unresolved, pending=pending)


def _is_member_part(u: Unit) -> bool:
    """PDF member parts are top-level parts without a number (hierarchy « Partie I » has one)."""
    return u.unit_type is UnitType.PART and u.depth == 0 and u.num_raw is None


def _own_consolidation(member: Act | None, served: dict[str, str]) -> str | None:
    if member is None or member.text_basis not in (TextBasis.CONSOLIDATED, TextBasis.STUB_FALLBACK):
        return None
    return member.basis_version_eli or served.get(member.eli_work)


def _route(member: Act | None, own: str | None, *, served_with_text: bool, partial: bool) -> tuple[bool, str | None]:
    if not served_with_text:
        return False, "recueil not served with text at T"
    if member is None:
        return False, "member not in the metadata scope"
    if own:
        return False, "member has its own consolidation (canonical)"
    if member.text_basis is TextBasis.METADATA_ONLY and member.metadata_reason in _NOT_ROUTED:
        return False, f"member not in force or excluded at T ({member.metadata_reason.value})"
    if partial:
        return False, "excerpt only (routing would hide the other articles)"
    return True, None


def _from_pdf(
    work: str,
    version: str,
    units: Sequence[Unit],
    index: ActIndex,
    acts_by_work: dict[str, Act],
    served: dict[str, str],
    served_with_text: bool,
    dropped: Sequence[str] = (),
) -> tuple[list[RecueilMemberEntry], list[RecueilUnresolved]]:
    articles: collections.Counter[int] = collections.Counter()
    part_of_anchor = {u.anchor: u.ordinal for u in units if _is_member_part(u)}
    for anchor in dropped:  # copies dropped by the structure pass still are articles of their part
        part = part_of_anchor.get(anchor.split(".", 1)[0])
        if part is not None:
            articles[part] += 1
    top: dict[int, int] = {}
    preamble: dict[int, str] = {}  # part ordinal -> the first lines of its preamble (Mémorial reference, notes)
    for u in units:
        part = u.ordinal if _is_member_part(u) else top.get(u.parent_ordinal or 0)
        if part is not None:
            top[u.ordinal] = part
            if u.unit_type is UnitType.ARTICLE:
                articles[part] += 1
            elif u.unit_type is UnitType.PREAMBLE and part not in preamble:
                preamble[part] = (u.text_plain or "")[:600]
            if u.unit_type is UnitType.ARTICLE and articles[part] == 1 and u.heading:  # its first article's title
                preamble[part] = f"{preamble.get(part, '')}\n{u.heading}"
    out: list[RecueilMemberEntry] = []
    unresolved: list[RecueilUnresolved] = []
    for u in units:
        if not _is_member_part(u) or not u.heading or u.anchor.startswith(EU_PART_PREFIX):
            continue  # EU / international « Complément » parts are out of scope, never members
        if not articles.get(u.ordinal):
            continue  # an empty title part (section title page, TOC heading): nothing to map
        mh = match_member(u.heading)
        if mh is None:
            unresolved.append(
                RecueilUnresolved(
                    recueil=_rel(work), part_heading=u.heading[:300], member_key=u.anchor, reason="undated title"
                )
            )
            continue
        act, score, scored = index.resolve(mh.act_type, mh.date, u.heading)
        if act is None:
            unresolved.append(
                RecueilUnresolved(
                    recueil=_rel(work),
                    part_heading=u.heading[:300],
                    member_key=u.anchor,
                    candidates=[f"{_rel(a.eli_work)} ({s:.2f})" for a, s in scored[:4]],
                    reason="no act of that type and date" if not scored else "ambiguous or weak title match",
                )
            )
            continue
        partial = bool(_EXCERPT.search(u.heading) or _EXCERPT_NOTE.search(preamble.get(u.ordinal, "")))
        own = _own_consolidation(act, served)
        route, why = _route(act, own, served_with_text=served_with_text, partial=partial)
        out.append(
            RecueilMemberEntry(
                recueil=_rel(work),
                member=_rel(act.eli_work),
                part_heading=u.heading[:300],
                method="heading",
                reviewed=False,
                route=route,
                member_key=u.anchor,  # with its occurrence suffix (« rgd_2016-01-26~2 »): two members may share a key
                recueil_version=_rel(version),
                member_title=(act.title or "")[:300] or None,
                own_consolidation=_rel(own) if own else None,
                coverage="partial" if partial else "unknown",
                articles=articles.get(u.ordinal, 0),
                confidence=round(score, 2),
                note=why,
            )
        )
    return out, unresolved


def _from_xml(
    work: str,
    version: str,
    units: Sequence[Unit],
    acts_by_work: dict[str, Act],
    served: dict[str, str],
    version_work: dict[str, str],
    served_with_text: bool,
) -> tuple[list[RecueilMemberEntry], list[RecueilUnresolved]]:
    per: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for u in units:
        if u.unit_type is not UnitType.ARTICLE or not u.from_recueil_wid:
            continue
        ref = parse_wid(u.from_recueil_wid)
        if ref.kind == "malformed" or ref.member_work is None:
            continue
        member = version_work.get(ref.member_version or "") or ref.member_work
        if member not in acts_by_work and ref.kind == "code_version":
            member = version_work.get(ref.member_version or "", member)
        per[member][ref.kind] += 1
    out: list[RecueilMemberEntry] = []
    unresolved: list[RecueilUnresolved] = []
    for member, kinds in sorted(per.items()):
        act = acts_by_work.get(member)
        if act is None or member == work:
            if member != work:
                unresolved.append(
                    RecueilUnresolved(
                        recueil=_rel(work),
                        part_heading=f"wId member {_rel(member)}",
                        reason="member not in the metadata scope",
                    )
                )
            continue
        own = _own_consolidation(act, served)
        only_jo = set(kinds) <= {"jo", "plain"}
        route, why = _route(act, own, served_with_text=served_with_text, partial=False)
        if route and only_jo:
            route, why = False, "JO articles kept from the recueil; confirm full coverage before routing"
        out.append(
            RecueilMemberEntry(
                recueil=_rel(work),
                member=_rel(member),
                part_heading=None,
                method="wid",
                reviewed=False,
                route=route,
                recueil_version=_rel(version),
                member_title=(act.title or "")[:300] or None,
                own_consolidation=_rel(own) if own else None,
                coverage="unknown",
                articles=sum(kinds.values()),
                confidence=1.0,
                note=why or ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
            )
        )
    return out, unresolved


HEADER = """\
# Code-recueil membership and routing (PLAN §2.6) — v1, DERIVED, NOT YET REVIEWED.
#
# Generated by `lgx content recueil-members --write` from the cached recueil texts of this build
# (XML article wIds, PDF member headings) resolved against the metadata snapshot (acts: type, date,
# title). The RDF itself has no recueil -> member links. Every entry is `reviewed: false` until a lawyer
# confirms it (set `reviewed: true` and `reviewed_by`). `route: true` flags the member's JO original
# `superseded_by_code` (kept for display, not searchable) and routes it to the code text — only for
# members without their own consolidation, outside excerpts, in recueils served with text.
# `unresolved`: headings that need a human decision; `pending`: recueils whose text this build does not
# have (the dev sample) — re-run the derivation after `lgx ingest --full`.
"""


_DROP_IN_YAML = ("member_title", "recueil_version")  # derivable from acts.jsonl / the selection


def to_yaml(cfg: RecueilMembersConfig) -> str:
    data = cfg.model_dump(mode="json", exclude_defaults=False)
    for m in data["members"]:
        for k in [k for k, v in m.items() if v is None or k in _DROP_IN_YAML]:
            del m[k]
        if m.get("coverage") == "unknown":
            del m["coverage"]
        if m.get("method") == "wid":
            m.pop("confidence", None)
    for u in data["unresolved"]:
        if not u.get("candidates"):
            u.pop("candidates", None)
        if u.get("member_key") is None:
            u.pop("member_key", None)
    return HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)


def run_recueil_members(ctx: StageContext, *, write: Path | None = None) -> StageResult:
    """Derive the membership from ``units/<T>/`` + ``manifest/<T>/`` (write YAML with ``write``)."""
    result = StageResult(stage="recueil-members")
    layout = ctx.layout
    if not layout.acts_path.exists() or not layout.manifest_path.exists():
        result.ok = False
        result.messages.append("run `lgx select` first")
        return result
    acts = list(read_jsonl(layout.acts_path, Act))
    entries = list(read_jsonl(layout.manifest_path, ManifestEntry))
    units_of: dict[str, list[Unit]] = {}
    dropped_of: dict[str, list[str]] = {}
    for e in entries:
        if e.eli_version and e.file and e.doc_type in RECUEIL_TYPES:
            path = layout.units_path(e.eli_version)
            if path.exists():
                units_of[e.eli_version] = list(read_jsonl(path, Unit))
            structure = path.with_suffix(".structure.json")
            if structure.exists():
                dedup = (json.loads(structure.read_text(encoding="utf-8")) or {}).get("dedup") or {}
                dropped_of[e.eli_version] = [
                    a["anchor"] for a in dedup.get("articles", []) if a.get("outcome") == "dropped_duplicate"
                ]
    cfg = derive(
        acts=acts, entries=entries, units_of=units_of, generated=ctx.sync_date.isoformat(), dropped_of=dropped_of
    )
    by_recueil = collections.Counter(m.recueil for m in cfg.members)
    result.counts = {
        "members": len(cfg.members),
        "routed": sum(m.route for m in cfg.members),
        "unresolved": len(cfg.unresolved),
        "pending": len(cfg.pending),
        "recueils_derived": len(by_recueil),
    }
    result.details = {"by_recueil": dict(sorted(by_recueil.items())), "pending": [p.recueil for p in cfg.pending]}
    if write is not None:
        atomic_write_text(write, to_yaml(cfg))
        result.outputs.append(str(write))
        report = layout.reports_dir / "recueil_members.json"
        atomic_write_text(report, json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    return result


def summary(cfg: RecueilMembersConfig) -> dict[str, Any]:
    return {"members": len(cfg.members), "unresolved": len(cfg.unresolved), "pending": len(cfg.pending)}
