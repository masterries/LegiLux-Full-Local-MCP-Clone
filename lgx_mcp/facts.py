"""Typed lookups over the release database (acts, versions, units, serving decision, relations).

All status and label logic is the database's (``lgx.serving_at``, ``lgx.amending_acts_since``, twins of
``lgx_core.rules``); this module only fetches rows in batches and shapes them.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.range import Range

from lgx_core.eli import ELI_BASE, LEG_PREFIX
from lgx_mcp.db import Database

_SUBRESOURCE = re.compile(r"^(?:art_.+|jo|consolide|\d{8}|fr|de|en|html|xml|pdf|.+\.(?:html|xml|pdf))$")
_DATED_PATH = re.compile(r"/\d{4}/\d{2}/\d{2}/")

ACT_COLS = (
    "a.act_id, a.eli_work, a.eli_jo, a.doc_type, a.date_document, a.publication_date, a.dei_min, a.dnl,"
    " a.rdf_status, a.memorial_ref, a.title, a.title_short, a.citation_title, a.text_basis::text AS text_basis,"
    " a.metadata_reason, a.flags, a.basis_version_id, a.superseded_by_act_id"
)
VERSION_COLS = (
    "v.version_id, v.act_id, v.eli_version, v.kind::text AS kind, v.role::text AS role, v.date_applicability,"
    " v.date_end_applicability, v.is_stub, v.has_files, v.has_text, v.text_valid, v.label, v.source_format,"
    " v.source_url, v.licence, encode(v.sha256, 'hex') AS sha256"
)
UNIT_COLS = (
    "u.unit_id, u.version_id, u.act_id, u.member_act_id, u.parent_id, u.unit_type, u.akn_id, u.anchor, u.eli_unit,"
    " u.eli_unit_version, u.eli_unit_derived, u.unit_version_date, u.num_raw, u.num_norm, u.heading, u.provenance,"
    " u.breadcrumb, u.ordinal, u.depth, u.is_repealed, u.repeal_note, u.is_modifying, u.amended_by, u.page_from,"
    " u.page_to"
)


@dataclass(frozen=True)
class Act:
    act_id: int
    eli_work: str
    eli_jo: str | None
    doc_type: str
    date_document: dt.date | None
    publication_date: dt.date | None
    dei_min: dt.date | None
    dnl: dt.date | None
    rdf_status: str | None
    memorial_ref: str | None
    title: str | None
    title_short: str | None
    citation_title: str | None
    text_basis: str  # label at T
    metadata_reason: str | None
    flags: tuple[str, ...]
    basis_version_id: int | None
    superseded_by_act_id: int | None

    @property
    def memorial(self) -> str | None:
        """« Mémorial A n° 686 de 2018 »: stored reference, else derived from the post-2016 ``aNNN`` work id."""
        if self.memorial_ref:
            return self.memorial_ref
        m = re.search(r"/a(\d+)$", self.eli_work)
        if m and self.publication_date and self.eli_work.startswith(LEG_PREFIX):
            return f"Mémorial A n° {int(m.group(1))} de {self.publication_date.year}"
        return None

    @property
    def memorial_short(self) -> str | None:
        """« Mém. A n° 686/2018 »."""
        mem = self.memorial
        if not mem:
            return None
        m = re.match(r"Mémorial (\w) n° (\d+) de (\d{4})", mem)
        return f"Mém. {m.group(1)} n° {m.group(2)}/{m.group(3)}" if m else mem


@dataclass(frozen=True)
class Version:
    version_id: int
    act_id: int
    eli_version: str
    kind: str
    role: str
    date_applicability: dt.date | None
    date_end_applicability: dt.date | None
    is_stub: bool
    has_files: bool
    has_text: bool
    valid_from: dt.date | None  # text_valid lower bound
    valid_to: dt.date | None  # text_valid upper bound (exclusive), None = open
    label: str | None
    source_format: str | None
    source_url: str | None
    licence: str | None
    sha256: str | None

    @property
    def is_original(self) -> bool:
        return self.kind == "original"

    @property
    def display_label(self) -> str:
        """'version consolidée applicable au 26/07/2026' | 'texte original'."""
        if self.is_original:
            return "texte original"
        label = self.label or (
            f"Version consolidée applicable au {self.date_applicability:%d/%m/%Y}" if self.date_applicability else ""
        )
        return label[:1].lower() + label[1:] if label else "version consolidée"

    def window_contains(self, d: dt.date) -> bool:
        return (self.valid_from is None or self.valid_from <= d) and (self.valid_to is None or d < self.valid_to)


@dataclass(frozen=True)
class Serving:
    act_id: int
    as_of: dt.date
    status: str
    window_version_id: int | None
    text_version_id: int | None
    text_basis: str
    flags: tuple[str, ...]
    next_version_date: dt.date | None
    next_version_has_text: bool | None


@dataclass(frozen=True)
class Unit:
    unit_id: int
    version_id: int
    act_id: int
    member_act_id: int | None
    parent_id: int | None
    unit_type: str
    akn_id: str | None
    anchor: str
    eli_unit: str | None
    eli_unit_version: str | None
    eli_unit_derived: bool
    unit_version_date: dt.date | None
    num_raw: str | None
    num_norm: str | None
    heading: str | None
    provenance: str | None
    breadcrumb: str | None
    ordinal: int
    depth: int
    is_repealed: bool
    repeal_note: str | None
    is_modifying: bool
    amended_by: tuple[str, ...]
    page_from: int | None
    page_to: int | None
    text_plain: str | None = None
    text_md: str | None = None

    @property
    def article_label(self) -> str | None:
        """« Art. L. 121-1. » as written (num_raw)."""
        return self.num_raw

    @property
    def in_annex(self) -> bool:
        return bool(self.akn_id and self.akn_id.startswith("annex")) or "annexe" in (self.breadcrumb or "").lower()


@dataclass(frozen=True)
class AmendingAct:
    eli: str
    rel_type: str
    date_entry_into_force: dt.date | None
    title: str | None
    has_text: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "eli": self.eli,
            "title": self.title,
            "rel_type": self.rel_type,
            "date_entry_into_force": self.date_entry_into_force.isoformat() if self.date_entry_into_force else None,
            "has_text": self.has_text,
        }


@dataclass
class FactCache:
    """Per-call memo so a search with 20 hits does each lookup once."""

    acts: dict[int, Act] = field(default_factory=dict)
    versions: dict[int, Version] = field(default_factory=dict)
    serving: dict[tuple[int, dt.date], Serving] = field(default_factory=dict)
    short_titles: dict[int, str] = field(default_factory=dict)


def _range_bounds(r: Any) -> tuple[dt.date | None, dt.date | None]:
    if r is None:
        return None, None
    if isinstance(r, Range):
        if r.isempty:
            return None, None
        return r.lower, r.upper
    return None, None


def _act(row: dict[str, Any]) -> Act:
    return Act(
        act_id=int(row["act_id"]),
        eli_work=row["eli_work"],
        eli_jo=row["eli_jo"],
        doc_type=row["doc_type"],
        date_document=row["date_document"],
        publication_date=row["publication_date"],
        dei_min=row["dei_min"],
        dnl=row["dnl"],
        rdf_status=row["rdf_status"],
        memorial_ref=row["memorial_ref"],
        title=row["title"],
        title_short=row["title_short"],
        citation_title=row["citation_title"],
        text_basis=row["text_basis"],
        metadata_reason=row["metadata_reason"],
        flags=tuple(row["flags"] or ()),
        basis_version_id=row["basis_version_id"],
        superseded_by_act_id=row["superseded_by_act_id"],
    )


def _version(row: dict[str, Any]) -> Version:
    lo, hi = _range_bounds(row["text_valid"])
    return Version(
        version_id=int(row["version_id"]),
        act_id=int(row["act_id"]),
        eli_version=row["eli_version"],
        kind=row["kind"],
        role=row["role"],
        date_applicability=row["date_applicability"],
        date_end_applicability=row["date_end_applicability"],
        is_stub=bool(row["is_stub"]),
        has_files=bool(row["has_files"]),
        has_text=bool(row["has_text"]),
        valid_from=lo,
        valid_to=hi,
        label=row["label"],
        source_format=row["source_format"],
        source_url=row["source_url"],
        licence=row["licence"],
        sha256=row["sha256"],
    )


def _unit(row: dict[str, Any]) -> Unit:
    return Unit(
        unit_id=int(row["unit_id"]),
        version_id=int(row["version_id"]),
        act_id=int(row["act_id"]),
        member_act_id=row["member_act_id"],
        parent_id=row["parent_id"],
        unit_type=row["unit_type"],
        akn_id=row["akn_id"],
        anchor=row["anchor"],
        eli_unit=row["eli_unit"],
        eli_unit_version=row["eli_unit_version"],
        eli_unit_derived=bool(row["eli_unit_derived"]),
        unit_version_date=row["unit_version_date"],
        num_raw=row["num_raw"],
        num_norm=row["num_norm"],
        heading=row["heading"],
        provenance=row["provenance"],
        breadcrumb=row["breadcrumb"],
        ordinal=int(row["ordinal"]),
        depth=int(row["depth"]),
        is_repealed=bool(row["is_repealed"]),
        repeal_note=row["repeal_note"],
        is_modifying=bool(row["is_modifying"]),
        amended_by=tuple(row["amended_by"] or ()),
        page_from=row["page_from"],
        page_to=row["page_to"],
        text_plain=row.get("text_plain"),
        text_md=row.get("text_md"),
    )


def normalise_eli(text: str) -> str:
    """Accept data ELIs, display URLs, ``legilux://eli/…`` and relative ``etat/leg/…`` forms."""
    t = text.strip().rstrip("/")
    for prefix in ("legilux://eli/", "https://legilux.public.lu/eli/", "http://legilux.public.lu/eli/",
                   "https://data.legilux.public.lu/eli/"):  # fmt: skip
        if t.startswith(prefix):
            return ELI_BASE + t[len(prefix) :]
    if t.startswith(("etat/", "eli/etat/")):
        return ELI_BASE + t.removeprefix("eli/")
    return t


class Facts:
    def __init__(self, db: Database, cache: FactCache | None = None) -> None:
        self.db = db
        self.cache = cache or FactCache()

    # ------------------------------------------------------------------------------------------ acts
    async def acts(self, ids: Iterable[int]) -> dict[int, Act]:
        want = [i for i in dict.fromkeys(int(x) for x in ids) if i not in self.cache.acts]
        if want:
            for r in await self.db.fetch(f"SELECT {ACT_COLS} FROM lgx.act a WHERE a.act_id = ANY(%s)", [want]):
                a = _act(r)
                self.cache.acts[a.act_id] = a
        return {i: self.cache.acts[i] for i in (int(x) for x in ids) if i in self.cache.acts}

    async def act(self, act_id: int) -> Act | None:
        return (await self.acts([act_id])).get(act_id)

    async def acts_by_eli(self, elis: Sequence[str]) -> dict[str, Act]:
        """Acts whose work or /jo ELI is in ``elis`` (keyed by the given ELI)."""
        if not elis:
            return {}
        rows = await self.db.fetch(
            f"SELECT {ACT_COLS} FROM lgx.act a WHERE a.eli_work = ANY(%s) OR a.eli_jo = ANY(%s)",
            [list(elis), list(elis)],
        )
        out: dict[str, Act] = {}
        for r in rows:
            a = _act(r)
            self.cache.acts[a.act_id] = a
            for e in (a.eli_work, a.eli_jo):
                if e and e in elis:
                    out[e] = a
        return out

    async def find_act_by_eli(self, eli: str) -> tuple[Act | None, Unit | None, Version | None]:
        """Exact work / jo / version / article ELI, then the path walked upwards (…/art_5, code/travail)."""
        eli = normalise_eli(eli)
        row = await self.db.fetchone(
            f"SELECT {ACT_COLS} FROM lgx.act a WHERE a.eli_work = %s OR a.eli_jo = %s LIMIT 1", [eli, eli]
        )
        if row:
            a = _act(row)
            self.cache.acts[a.act_id] = a
            return a, None, None
        vrow = await self.db.fetchone(f"SELECT {VERSION_COLS} FROM lgx.act_version v WHERE v.eli_version = %s", [eli])
        if vrow:
            v = _version(vrow)
            self.cache.versions[v.version_id] = v
            return await self.act(v.act_id), None, v
        urow = await self.db.fetchone(
            f"SELECT {UNIT_COLS} FROM lgx.unit u WHERE u.eli_unit = %s OR u.eli_unit_version = %s"
            " ORDER BY u.version_id DESC LIMIT 1",
            [eli, eli],
        )
        if urow:
            u = _unit(urow)
            return await self.act(u.act_id), u, None
        # walk up sub-resource segments only (…/a686/art_49, …/a686/consolide/20251226, …/jo/fr/html/…):
        # never from one work to a sibling (…/a686999 is unknown, not …/2018/08/01/*)
        parts = eli.rstrip("/").split("/")
        while len(parts) > 7 and _SUBRESOURCE.match(parts[-1]):
            parts = parts[:-1]
            prefix = "/".join(parts)
            row = await self.db.fetchone(
                f"SELECT {ACT_COLS} FROM lgx.act a WHERE a.eli_work = %s OR a.eli_jo = %s LIMIT 1", [prefix, prefix]
            )
            if row:
                a = _act(row)
                self.cache.acts[a.act_id] = a
                return a, None, None
        # code / recueil families without a dated work id: …/code/travail -> its versions …/code/travail/<date>
        prefix = "/".join(parts)
        if not _DATED_PATH.search(prefix):
            vrow = await self.db.fetchone(
                f"SELECT {VERSION_COLS} FROM lgx.act_version v WHERE v.eli_version LIKE %s"
                " ORDER BY v.date_applicability DESC NULLS LAST LIMIT 1",
                [prefix.replace("%", r"\%").replace("_", r"\_") + "/%"],
            )
            if vrow:
                v = _version(vrow)
                return await self.act(v.act_id), None, None
        return None, None, None

    # -------------------------------------------------------------------------------------- versions
    async def versions(self, ids: Iterable[int]) -> dict[int, Version]:
        want = [i for i in dict.fromkeys(int(x) for x in ids if x is not None) if i not in self.cache.versions]
        if want:
            for r in await self.db.fetch(
                f"SELECT {VERSION_COLS} FROM lgx.act_version v WHERE v.version_id = ANY(%s)", [want]
            ):
                v = _version(r)
                self.cache.versions[v.version_id] = v
        return {i: self.cache.versions[i] for i in (int(x) for x in ids if x is not None) if i in self.cache.versions}

    async def version(self, version_id: int | None) -> Version | None:
        if version_id is None:
            return None
        return (await self.versions([version_id])).get(version_id)

    async def versions_of_act(self, act_id: int, limit: int = 200) -> list[Version]:
        rows = await self.db.fetch(
            f"SELECT {VERSION_COLS} FROM lgx.act_version v WHERE v.act_id = %s"
            " ORDER BY v.date_applicability DESC NULLS LAST, v.version_id DESC LIMIT %s",
            [act_id, limit],
        )
        out = [_version(r) for r in rows]
        for v in out:
            self.cache.versions[v.version_id] = v
        return out

    # --------------------------------------------------------------------------------------- serving
    async def serving(self, pairs: Iterable[tuple[int, dt.date]]) -> dict[tuple[int, dt.date], Serving]:
        want = [p for p in dict.fromkeys(pairs) if p not in self.cache.serving]
        if want:
            rows = await self.db.fetch(
                "SELECT s.act_id, s.as_of, s.status, s.window_version_id, s.text_version_id, s.text_basis::text AS"
                " text_basis, s.flags, s.next_version_date, s.next_version_has_text"
                " FROM unnest(%s::bigint[], %s::date[]) AS p(a, d), LATERAL lgx.serving_at(p.a, p.d) s",
                [[a for a, _ in want], [d for _, d in want]],
            )
            for r in rows:
                s = Serving(
                    act_id=int(r["act_id"]),
                    as_of=r["as_of"],
                    status=r["status"],
                    window_version_id=r["window_version_id"],
                    text_version_id=r["text_version_id"],
                    text_basis=r["text_basis"],
                    flags=tuple(r["flags"] or ()),
                    next_version_date=r["next_version_date"],
                    next_version_has_text=r["next_version_has_text"],
                )
                self.cache.serving[(s.act_id, s.as_of)] = s
        return {p: self.cache.serving[p] for p in pairs if p in self.cache.serving}

    async def serving_at(self, act_id: int, as_of: dt.date) -> Serving | None:
        return (await self.serving([(act_id, as_of)])).get((act_id, as_of))

    # ----------------------------------------------------------------------------------------- units
    async def units(self, ids: Iterable[int], *, with_text: bool = False) -> dict[int, Unit]:
        want = list(dict.fromkeys(int(x) for x in ids))
        if not want:
            return {}
        cols = UNIT_COLS + (", u.text_plain, u.text_md" if with_text else "")
        rows = await self.db.fetch(f"SELECT {cols} FROM lgx.unit u WHERE u.unit_id = ANY(%s)", [want])
        return {int(r["unit_id"]): _unit(r) for r in rows}

    async def unit(self, unit_id: int, *, with_text: bool = False) -> Unit | None:
        return (await self.units([unit_id], with_text=with_text)).get(unit_id)

    async def article_units(self, version_id: int, key: str) -> list[Unit]:
        """Article units of one stored version whose number key (or extra alias key) is ``key``."""
        rows = await self.db.fetch(
            f"SELECT {UNIT_COLS} FROM lgx.unit u WHERE u.version_id = %s AND u.unit_type = 'article'"
            " AND (u.num_norm = %s OR u.unit_id IN (SELECT aa.unit_id FROM lgx.article_alias aa"
            " JOIN lgx.unit u2 ON u2.unit_id = aa.unit_id WHERE u2.version_id = %s AND aa.num_norm = %s))"
            " ORDER BY (u.num_norm = %s) DESC, u.ordinal",
            [version_id, key, version_id, key, key],
        )
        return [_unit(r) for r in rows]

    async def toc(self, version_id: int, limit: int = 300) -> tuple[list[Unit], int]:
        rows = await self.db.fetch(
            f"SELECT {UNIT_COLS}, count(*) OVER () AS total FROM lgx.unit u WHERE u.version_id = %s"
            " AND u.unit_type IN ('book', 'part', 'title', 'chapter', 'section', 'subsection', 'article', 'annex')"
            " ORDER BY u.ordinal LIMIT %s",
            [version_id, limit],
        )
        total = int(rows[0]["total"]) if rows else 0
        return [_unit(r) for r in rows], total

    async def short_titles(self, versions: Iterable[Version], acts: dict[int, Act]) -> dict[int, str]:
        """Titre court per act: title_short → the chunker's header prefix (curated alias) → title → slug."""
        vs = [v for v in versions if v.act_id not in self.cache.short_titles]
        if vs:
            rows = await self.db.fetch(
                "SELECT DISTINCT ON (c.version_id) c.version_id, split_part(c.header, ' — ', 1) AS ts"
                " FROM lgx.chunk c WHERE c.version_id = ANY(%s) ORDER BY c.version_id, c.chunk_id",
                [[v.version_id for v in vs]],
            )
            by_version = {int(r["version_id"]): r["ts"] for r in rows}
            for v in vs:
                ts = by_version.get(v.version_id)
                act = acts.get(v.act_id)
                if act is not None and act.title_short:
                    ts = act.title_short
                if not ts or ts.startswith("etat/"):
                    ts = None
                self.cache.short_titles[v.act_id] = ts or (short_title_of(act) if act else "")
        return {v.act_id: self.cache.short_titles.get(v.act_id, "") for v in versions}

    # ------------------------------------------------------------------------------------- relations
    async def amending_since(self, act_id: int, since: dt.date, as_of: dt.date, limit: int = 50) -> list[AmendingAct]:
        rows = await self.db.fetch(
            "SELECT m.src_eli, m.rel_type, m.src_dei, m.title, EXISTS (SELECT 1 FROM lgx.act s JOIN lgx.act_version sv"
            " ON sv.act_id = s.act_id AND sv.has_text WHERE s.eli_jo = m.src_eli OR s.eli_work = m.src_eli) AS has_text"
            " FROM lgx.amending_acts_since(%s, %s, %s) m LIMIT %s",
            [act_id, since, as_of, limit],
        )
        return [
            AmendingAct(
                eli=r["src_eli"],
                rel_type=r["rel_type"],
                date_entry_into_force=r["src_dei"],
                title=r["title"],
                has_text=bool(r["has_text"]),
            )
            for r in rows
        ]

    async def repealer(self, act: Act) -> AmendingAct | None:
        row = await self.db.fetchone(
            "SELECT r.src_eli, r.src_dei, s.title FROM lgx.relation r LEFT JOIN lgx.act s ON s.act_id = r.src_act_id"
            " WHERE r.dst_act_id = %s AND r.rel_type = 'repeals' AND r.source = 'sparql'"
            " ORDER BY (r.src_dei IS NOT DISTINCT FROM %s) DESC, r.src_dei DESC NULLS LAST LIMIT 1",
            [act.act_id, act.dnl],
        )
        if row is None:
            return None
        return AmendingAct(
            eli=row["src_eli"], rel_type="repeals", date_entry_into_force=row["src_dei"], title=row["title"]
        )

    async def unit_repealer(self, unit: Unit) -> AmendingAct | None:
        row = await self.db.fetchone(
            "SELECT r.src_eli, r.src_dei, s.title, r.rel_type FROM lgx.relation r LEFT JOIN lgx.act s"
            " ON s.act_id = r.src_act_id WHERE r.dst_unit_id = %s AND r.rel_type IN ('repeals', 'modifies')"
            " ORDER BY (r.rel_type = 'repeals') DESC, r.src_dei DESC NULLS LAST LIMIT 1",
            [unit.unit_id],
        )
        if row is None:
            return None
        return AmendingAct(
            eli=row["src_eli"], rel_type=row["rel_type"], date_entry_into_force=row["src_dei"], title=row["title"]
        )

    async def related(
        self, act_id: int, *, outgoing: bool, rel_types: Sequence[str], limit: int = 50
    ) -> list[dict[str, Any]]:
        if outgoing:
            sql = (
                "SELECT DISTINCT ON (r.dst_eli) r.dst_eli AS eli, r.rel_type, d.act_id, d.title, d.dei_min, d.dnl,"
                " d.rdf_status FROM lgx.relation r LEFT JOIN lgx.act d ON d.act_id = r.dst_act_id"
                " WHERE r.src_act_id = %s AND r.rel_type = ANY(%s) AND r.source = 'sparql'"
                " ORDER BY r.dst_eli LIMIT %s"
            )
        else:
            sql = (
                "SELECT DISTINCT ON (r.src_eli) r.src_eli AS eli, r.rel_type, s.act_id, s.title, s.dei_min, s.dnl,"
                " s.rdf_status FROM lgx.relation r LEFT JOIN lgx.act s ON s.act_id = r.src_act_id"
                " WHERE r.dst_act_id = %s AND r.rel_type = ANY(%s) AND r.source = 'sparql'"
                " ORDER BY r.src_eli LIMIT %s"
            )
        return await self.db.fetch(sql, [act_id, list(rel_types), limit])

    async def data_quality(self, elis: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        elis = [e for e in dict.fromkeys(elis) if e]
        if not elis:
            return {}
        rows = await self.db.fetch(
            "SELECT entity_eli, code, detail FROM lgx.data_quality_issue WHERE entity_eli = ANY(%s)"
            " ORDER BY entity_eli, code",
            [elis],
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["entity_eli"], []).append({"code": r["code"], "detail": r["detail"]})
        return out


_TYPE_LABELS = {"RECUEIL": "Recueil", "CODE_RECUEIL": "Code", "CODE": "Code"}


def short_title_of(act: Act) -> str:
    for t in (act.title_short, act.citation_title):
        if t:
            return t
    if act.title:
        t = act.title.strip().rstrip(".")
        return t if len(t) <= 120 else t[:119].rstrip() + " …"
    slug = act.eli_work.rstrip("/").rsplit("/", 1)[-1].replace("_", " ")
    return f"{_TYPE_LABELS.get(act.doc_type, act.doc_type)} {slug}".strip()
