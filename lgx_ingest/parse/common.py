"""Building blocks shared by the AKN and HTML parsers: line writer, list labels, tables, unit builder.

Text rules (PLAN §6.3): text nodes are concatenated *without* inserted spaces and each block (paragraph,
alinea, list item, table row …) is whitespace-normalised on its own; blocks are joined with ``\\n`` so
paragraph boundaries survive for quoting and for the chunker. ``text_md`` is only set when it differs
from ``text_plain`` (tables rendered as Markdown).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from lgx_core.eli import ELI_BASE
from lgx_core.models import (
    BREADCRUMB_SEP,
    DataQualityIssue,
    ManifestEntry,
    RawFile,
    Relation,
    RelationSource,
    RelType,
    SourceFormat,
    Unit,
    UnitType,
)
from lgx_core.normalise import LATIN_SUFFIXES, normalise_whitespace, num_norm, unaccent

DATA_HOST = "http://data.legilux.public.lu"
_ELI_HOSTS = (
    "http://data.legilux.public.lu/eli/",
    "https://data.legilux.public.lu/eli/",
    "http://legilux.public.lu/eli/",
    "https://legilux.public.lu/eli/",
)
_EU_ELI = ("http://data.europa.eu/eli/", "https://data.europa.eu/eli/")
_PROVENANCE = re.compile(r"^\(.*\)$", re.DOTALL)
_LATIN_SUFFIX_START = re.compile(r"^(?:" + "|".join(sorted(LATIN_SUFFIXES, key=len, reverse=True)) + r")(?![a-zà-ÿ])")
_ORDINAL_SUP = re.compile(r"^\s*(?:er|re|ère|e|ème|eme|o|bis|ter|quater|nd|nde|d|me)\b", re.IGNORECASE)


class ParseError(ValueError):
    """The file cannot be turned into units (not AKN/HTML, empty, unreadable)."""


# --------------------------------------------------------------------------------------- hrefs
def normalise_href(href: str | None) -> str | None:
    """Absolute ELI IRI for a reference target, or None when it is not an ELI (internal anchors, web links).

    ``/eli/etat/leg/…`` gets the ``http://data.legilux.public.lu`` prefix; host variants are unified;
    EU ELIs (``http://data.europa.eu/eli/…``) are kept as they are.
    """
    if not href:
        return None
    h = href.strip().split("#", 1)[0].rstrip("/")
    if not h:
        return None
    if h.startswith("/eli/"):
        return DATA_HOST + h
    for host in _ELI_HOSTS:
        if h.startswith(host):
            return ELI_BASE + h[len(host) :]
    if h.startswith(_EU_ELI):
        return "http://data.europa.eu/eli/" + h.split("/eli/", 1)[1]
    return None


def split_heading(text: str | None) -> tuple[str | None, str | None]:
    """(heading, provenance): a heading entirely in parentheses ('( L. 13 mai 2008 )') is provenance."""
    if not text:
        return None, None
    t = normalise_whitespace(text)
    if not t:
        return None, None
    if _PROVENANCE.match(t) and len(t) <= 300:
        return None, t
    return t, None


# ---------------------------------------------------------------------------------------- lists
_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl")]
_ROMAN += [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
_TEMPLATE = re.compile(r"^(?P<pre>\W*)(?P<tok>1|a|i|A|I)(?P<post>\W*)$")
_BULLETS = {"dash": "-", "dot": "•", "bullet": "•", "square": "▪", "circle": "◦"}


def _roman(n: int) -> str:
    out = []
    for value, sym in _ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _alpha(n: int) -> str:
    """a … z, then aa, bb, cc … (the doubling convention Legilux uses for long lists)."""
    letter = chr(ord("a") + (n - 1) % 26)
    return letter * ((n - 1) // 26 + 1)


def list_label(symbol: str | None, position: int, start: int = 1, *, ordered: bool = True) -> str:
    """Rendered label of the ``position``-th (0-based) item of a list with ``scl:symbol``/``scl:start``.

    ``1°`` → ``1°, 2°…``; ``a)`` → ``a), b)…``; ``i)`` → roman; ``I.`` → upper roman; ``(1)``; ``dash`` →
    ``-``; ``none``/``custom`` (without a per-item symbol) → ``''``.
    """
    if symbol is None:
        return "" if ordered else "•"  # the XSLT renders a <ul> without scl:symbol with a bullet
    s = symbol.strip()
    if s in ("none", "custom", ""):
        return ""
    if s in _BULLETS:
        return _BULLETS[s]
    m = _TEMPLATE.match(s)
    if not m:
        return s  # a literal symbol such as '–' or '(2-1)'
    n = start + position
    tok = m["tok"]
    if tok == "1":
        val = str(n)
    elif tok == "a":
        val = _alpha(n)
    elif tok == "A":
        val = _alpha(n).upper()
    elif tok == "i":
        val = _roman(n)
    else:
        val = _roman(n).upper()
    return f"{m['pre']}{val}{m['post']}"


def parse_start(value: str | None) -> int:
    try:
        return int(value) if value else 1
    except ValueError:
        return 1


# ----------------------------------------------------------------------------------------- writer
@dataclass
class LineWriter:
    """Accumulates inline text into the current block and flushes blocks as normalised lines.

    Each emitted item is ``(plain, md)``; they differ only for tables. A *prefix* (paragraph number,
    list label) waits for the next non-empty text of its block, so ``(1)`` followed by an alinea renders
    as one line « (1) Le TVP UE … »; a prefix whose content never comes (deleted text) is dropped.
    """

    items: list[tuple[str, str]] = field(default_factory=list)
    _buf: list[str] = field(default_factory=list)
    _pending: str = ""

    def _has_text(self) -> bool:
        return any(part.strip() for part in self._buf)

    def text(self, s: str | None) -> None:
        if not s:
            return
        if self._buf and re.match(r"^\s*\n\s*[.,]", s):  # pretty-printed '</ref>\n.' -> '….'
            s = s.lstrip()
        if self._buf and _LATIN_SUFFIX_START.match(s.lstrip()):  # pretty-printed 'Art. 383\n<i>bis</i>'
            joined = "".join(self._buf)
            stripped = joined.rstrip()
            if stripped[-1:].isdigit() and "\n" in joined[len(stripped) :]:
                self._buf = [stripped]
                s = s.lstrip()
        if self._pending and s.strip():
            self._buf.insert(0, self._pending + " ")
            self._pending = ""
        self._buf.append(s)

    def sup(self, s: str) -> None:
        """Superscript: glue ordinal suffixes to the preceding number ('1\\n<sup>er</sup>' -> '1er')."""
        if _ORDINAL_SUP.match(s) and self._buf:
            joined = "".join(self._buf)
            stripped = joined.rstrip()
            if stripped and stripped[-1].isdigit():
                self._buf = [stripped]
            s = s.lstrip()
        self.text(s)

    def brk(self) -> None:
        if not self._buf:
            return
        line = normalise_whitespace("".join(self._buf))
        self._buf = []
        if line:
            self.items.append((line, line))

    def block(self, plain: str, md: str) -> None:
        self.brk()
        if self._pending:
            plain, md = f"{self._pending} {plain}", f"{self._pending} {md}"
            self._pending = ""
        if plain or md:
            self.items.append((plain, md))

    def prefix(self, label: str) -> None:
        """Start a new block whose first non-empty text is preceded by ``label``."""
        self.brk()
        self._pending = normalise_whitespace(label)

    def result(self) -> tuple[str | None, str | None]:
        """(text_plain, text_md or None when identical)."""
        self.brk()
        self._pending = ""
        plain = "\n".join(p for p, _ in self.items if p)
        md = "\n".join(m for _, m in self.items if m)
        return (plain or None), (md if md and md != plain else None)

    @property
    def has_table(self) -> bool:
        return any(p != m for p, m in self.items)


def table_block(rows: list[list[str]]) -> tuple[str, str]:
    """(plain, markdown) for a table given as rows of cell texts."""
    rows = [[normalise_whitespace(c) for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return "", ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    plain = "\n".join(" | ".join(c for c in r if c) for r in rows)

    def esc(c: str) -> str:
        return c.replace("|", "\\|")

    md_lines = ["| " + " | ".join(esc(c) for c in rows[0]) + " |", "|" + "---|" * width]
    md_lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:]]
    return plain, "\n".join(md_lines)


# ----------------------------------------------------------------------------------------- annexes
_ANNEX_HEAD = re.compile(r"^annexes?\b", re.IGNORECASE)


def is_annex_heading(text: str) -> bool:
    """« ANNEXE I », « Annexe 2 – … » (one line, at most ~200 chars)."""
    t = normalise_whitespace(text)
    return bool(_ANNEX_HEAD.match(unaccent(t))) and len(t) <= 200


def annex_key(label: str) -> str:
    """Anchor-safe key of an annex heading: 'ANNEXE II' -> 'annexe_ii', 'Annexe 5 – Agents …' -> 'annexe_5'."""
    t = unaccent(normalise_whitespace(label)).lower()
    m = re.match(r"^(annexes?)\s*([0-9]+|[ivxlc]+\b)?", t)
    if m and m.group(2):
        return f"annexe_{m.group(2)}"
    return re.sub(r"[^a-z0-9]+", "_", t)[:40].strip("_") or "annexe"


_ANNEX_TITLE = re.compile(r"^(annexes?\s*(?:[0-9]+[a-z]?|[IVXLC]+\b)?)\s*[-–—:.]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
_ANNEX_DESIGNATOR = re.compile(r"^annexe\s+(?:[0-9]+[a-z]?|[IVXLC]+)\b", re.IGNORECASE)


def split_annex_title(title: str) -> tuple[str | None, str | None]:
    """'Annexe 5 – Agents …' -> ('Annexe 5', 'Agents …'); 'Annexes' -> ('Annexes', None); '' -> (None, None)."""
    t = normalise_whitespace(title)
    if not t:
        return None, None
    m = _ANNEX_TITLE.match(t)
    if m and is_annex_heading(m.group(1)):
        return m.group(1).strip() or None, (m.group(2).strip() or None)
    return None, t


def annex_title(lines: list[str]) -> tuple[str | None, str | None]:
    """(num_raw, heading) of an annex from its title lines.

    A line « ANNEXE I » (designator) wins over the generic « Annexes »; other lines form the heading.
    """
    lines = [normalise_whitespace(x) for x in lines if normalise_whitespace(x)]
    designated = next((x for x in lines if _ANNEX_DESIGNATOR.match(unaccent(x))), None)
    if designated is None:
        return split_annex_title(" ".join(lines))
    num, rest = split_annex_title(designated)
    others = [x for x in lines if x is not designated and not re.fullmatch(r"(?i)annexes?\.?", unaccent(x))]
    heading = " – ".join([*([rest] if rest else []), *others]) or None
    return num, heading


# ------------------------------------------------------------------------------------ unit builder
@dataclass
class Draft:
    """A unit before ordinals/provenance are applied."""

    unit_type: UnitType
    anchor: str
    parent: Draft | None = None
    akn_id: str | None = None
    eli_unit: str | None = None
    eli_unit_version: str | None = None
    eli_unit_derived: bool = False
    unit_version_date: dt.date | None = None
    num_raw: str | None = None
    heading: str | None = None
    provenance: str | None = None
    text_plain: str | None = None
    text_md: str | None = None
    is_repealed: bool = False
    repeal_note: str | None = None
    is_modifying: bool = False
    contains_quoted_amendment: bool = False
    amended_by: list[str] = field(default_factory=list)
    index_terms: list[str] = field(default_factory=list)
    member_act_eli: str | None = None
    from_recueil_wid: str | None = None
    refs: list[str] = field(default_factory=list)
    page_from: int | None = None  # PDF-derived units: 1-based page range
    page_to: int | None = None
    ordinal: int = 0

    @property
    def label(self) -> str:
        """Breadcrumb label: 'Chapitre 3 – Commissariat …' / 'Art. 5.' / heading only."""
        parts = [p for p in (self.num_raw, self.heading) if p]
        return " – ".join(parts) if parts else (self.provenance or self.anchor)

    @property
    def depth(self) -> int:
        d, p = 0, self.parent
        while p is not None:
            d, p = d + 1, p.parent
        return d

    def breadcrumb(self) -> str | None:
        labels: list[str] = []
        p = self.parent
        while p is not None:
            labels.append(p.label)
            p = p.parent
        return BREADCRUMB_SEP.join(reversed(labels)) or None


@dataclass
class UnitBuilder:
    """Collects drafts in document order, keeps anchors unique and turns drafts into :class:`Unit`."""

    entry: ManifestEntry
    raw: RawFile
    drafts: list[Draft] = field(default_factory=list)
    issues: list[DataQualityIssue] = field(default_factory=list)
    _anchors: set[str] = field(default_factory=set)

    @property
    def eli_version(self) -> str:
        if not self.entry.eli_version:
            raise ParseError(f"{self.entry.eli_work}: manifest entry without eli_version")
        return self.entry.eli_version

    def issue(self, code: str, detail: str | None = None, entity: str | None = None) -> None:
        self.issues.append(
            DataQualityIssue(
                entity_eli=entity or self.eli_version,
                code=code,
                detail=detail,
                first_seen=self.raw.fetched_at.date(),
            )
        )

    def unique_anchor(self, anchor: str) -> str:
        if anchor not in self._anchors:
            self._anchors.add(anchor)
            return anchor
        k = 2
        while f"{anchor}~{k}" in self._anchors:
            k += 1
        self.issue("duplicate_anchor", f"{anchor} -> {anchor}~{k}", f"{self.eli_version}#{anchor}")
        self._anchors.add(f"{anchor}~{k}")
        return f"{anchor}~{k}"

    def add(self, draft: Draft) -> Draft:
        draft.anchor = self.unique_anchor(draft.anchor)
        self.drafts.append(draft)
        draft.ordinal = len(self.drafts)
        return draft

    def units(self) -> list[Unit]:
        e, r = self.entry, self.raw
        if e.file is None:
            raise ParseError(f"{e.eli_version}: manifest entry without file")
        out: list[Unit] = []
        for d in self.drafts:
            out.append(
                Unit(
                    eli_work=e.eli_work,
                    eli_version=self.eli_version,
                    ordinal=d.ordinal,
                    parent_ordinal=d.parent.ordinal if d.parent else None,
                    depth=d.depth,
                    unit_type=d.unit_type,
                    member_act_eli=d.member_act_eli,
                    akn_id=d.akn_id,
                    anchor=d.anchor,
                    eli_unit=d.eli_unit,
                    eli_unit_version=d.eli_unit_version,
                    eli_unit_derived=d.eli_unit_derived,
                    unit_version_date=d.unit_version_date,
                    num_raw=d.num_raw,
                    num_norm=(num_norm(d.num_raw) or None) if d.num_raw else None,
                    heading=d.heading,
                    provenance=d.provenance,
                    breadcrumb=d.breadcrumb(),
                    text_plain=d.text_plain,
                    text_md=d.text_md,
                    is_repealed=d.is_repealed,
                    repeal_note=d.repeal_note,
                    is_modifying=d.is_modifying,
                    contains_quoted_amendment=d.contains_quoted_amendment,
                    amended_by=d.amended_by,
                    index_terms=d.index_terms,
                    page_from=d.page_from,
                    page_to=d.page_to,
                    from_recueil_wid=d.from_recueil_wid,
                    doc_type=e.doc_type,
                    source_format=e.file.source_format,
                    source_url=r.url,
                    source_sha256=r.sha256,
                    licence=e.file.licence,
                    text_basis=e.text_basis,
                    flags=list(e.flags),
                )
            )
        return out

    def ref_relations(self) -> list[Relation]:
        """``refers_to`` edges (source ``akn-ref``) from every unit's references, one per (unit, target)."""
        rels: list[Relation] = []
        for d in self.drafts:
            for dst in dict.fromkeys(d.refs):
                rels.append(
                    Relation(
                        src_eli=self.entry.eli_work,
                        rel_type=RelType.REFERS_TO,
                        dst_eli=dst,
                        source=RelationSource.AKN_REF,
                        src_version_eli=self.eli_version,
                        src_unit_ordinal=d.ordinal,
                    )
                )
        return rels


def is_jo_original(entry: ManifestEntry) -> bool:
    return bool(entry.eli_version and entry.eli_version.rstrip("/").endswith("/jo"))


def derived_article_eli(entry: ManifestEntry, akn_id: str | None) -> str | None:
    """Locally minted ``<work>/art_N`` for JO originals (non-official; ``eli_unit_derived = true``)."""
    if not akn_id or not akn_id.startswith("art") or not is_jo_original(entry):
        return None
    return f"{entry.eli_work.rstrip('/')}/{akn_id}"


def source_format_of(entry: ManifestEntry) -> SourceFormat:
    if entry.file is None:
        raise ParseError(f"{entry.eli_version}: manifest entry without file")
    return entry.file.source_format
