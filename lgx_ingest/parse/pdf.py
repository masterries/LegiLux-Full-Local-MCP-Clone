"""Born-digital PDF codes and consolidations (PLAN §6.3, §2.6) → units.

The PDF-only code-recueils (Code de la route, Code de la sécurité sociale, Code communal, Code de
l'éducation nationale …) and old PDF consolidations are born-digital: no OCR. Pipeline:

1. :mod:`.pdf_layout` extracts clean body lines per page with ``pdfplumber`` (headers/footers, footnotes,
   margin notes, superscripts, tables, TOC pages are handled there);
2. lines are joined into paragraphs (layout-aware: vertical gaps, indents, list labels, short lines;
   end-of-line hyphenation repaired with a document vocabulary, « Grand-␤Duché » keeps its hyphen);
3. paragraphs are segmented: **member-act headings** (« Arrêté grand-ducal du 23 novembre 1955 portant … »
   followed by its Mémorial reference, or set in a larger font) open a member part — the unit key of a
   code-recueil is ``(code version, member act, num_norm)`` because article numbers repeat across
   members, so article anchors are ``<member key>.art_<num_norm>``; hierarchy headings (Partie … Sous-section),
   ``Art. …`` headings (L./R. numbering, bis/ter, ranges, « Article unique »), annexes, and « Jurisprudence »
   sections (case law: skipped, out of scope);
4. low confidence → **page units** (``anchor = p.N``, ``num_raw = 'p. N'``): every page of a member section
   (or document) of ≥ 3 pages without a single article heading, and every page in ``mode="pages"``
   (``scope.pdf_codes: pages``, the first cut of PLAN §13 M2).

Every unit carries ``page_from``/``page_to`` (1-based PDF page index: what a viewer shows, ``#page=N``) and
the ``pdf_extracted`` flag (``W_PDF_EXTRACTED``). Right-margin notes (« L. 27.7.92 ») and parenthesised
provenance lines (« (Loi du 1er août 1971) ») go to ``provenance``; footnotes are dropped (counted).
Member ELIs are never guessed here: :func:`lgx_ingest.parse.recueil.apply_members` maps member headings
to acts from ``config/recueil_members.yaml`` after parsing.

Gate (PLAN §11.4): ``stats['articles']`` must be within ±1 % of ``stats['article_candidates']``, the number
of body lines that start like an article heading; otherwise the result carries a ``pdf_article_gate``
issue and :mod:`lgx_ingest.gates` fails the build for that file.
"""

from __future__ import annotations

import collections
import datetime as dt
import itertools
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from lgx_core.models import ActFlag, ManifestEntry, ParseResult, RawFile, SourceFormat, UnitType, VersionKind
from lgx_core.normalise import LATIN_SUFFIXES, is_repeal_stub_text, normalise_whitespace, num_norm, unaccent
from lgx_ingest.parse.common import Draft, ParseError, UnitBuilder
from lgx_ingest.parse.pdf_layout import LAYOUT_VERSION, CleanStats, Line, Page, clean_document, extract_pages

PARSER_VERSION = "pdf-1"
ARTICLE_GATE_TOLERANCE = 0.01
__all__ = [
    "ARTICLE_GATE_TOLERANCE",
    "PARSER_VERSION",
    "ArticleHead",
    "MemberHead",
    "article_candidates",
    "build_paragraphs",
    "is_recueil",
    "match_article",
    "match_member",
    "parse_pages",
    "parse_pdf",
]

_SUFFIX = "|".join(sorted(LATIN_SUFFIXES, key=len, reverse=True))
_NUM_ONE = (
    r"(?:[LRAD]\s?\.\s?(?=\d))?"  # « L. 121-1 », « R. 3 »
    r"(?:\d+(?:\s?(?:er|re|ère))?|[IVXLC]+(?:er)?(?![a-zA-Z])|unique|premier)"
    r"(?:[-–./]\d+[a-z]?)*"  # « 24-1 », « 121-1-2 », « 3.1 »
    rf"(?:\s?(?:{_SUFFIX})(?![a-z])|[A-Z](?![a-zA-Z.]))*"  # « 73bis », « 12 ter », « 5A »
)
_ART_HEAD = re.compile(
    r"^(?P<lead>[«\"“]\s*)?(?P<kw>Art\.|Art(?=\s)|Article)\s*"
    rf"(?P<num>{_NUM_ONE}(?:\.?\s*(?:à|au|et|,|-|–)\s*(?:Art\.\s*)?(?:{_NUM_ONE}|dernier))*)"
    r"\s*[»”\"]?\s*(?P<end>\.(?!\d)|\s[–—-]\s|:|$|(?=\s\())"
)
_ART_LOOSE = re.compile(r"^[«\"“]?\s*Art(?:icle)?\.?\s*(?:[LRAD]\.?\s*)?(?:\d|[IVXLC]+(?:er)?\b|unique|premier\b)")
_MONTHS = "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
_ACT_TYPES = (
    r"Loi|Règlement grand-ducal|Règlement grand ducal|Règlement ministériel|Règlement interne|Règlement"
    r"|Arrêté grand-ducal|Arrêté grand ducal|Arrêté royal grand-ducal|Arrêté royal|Arrêté ministériel|Arrêté"
    r"|Décret|Convention|Accord|Traité|Protocole|Ordonnance|Statuts?|Code"
)
_MEMBER_HEAD = re.compile(
    r"^(?:[«\"“]\s*)?(?:Texte coordonné (?:de la |du |de l['’])\s*)?"
    rf"(?P<type>{_ACT_TYPES})\b(?P<mid>[^.;:()]{{0,60}}?)\s+du\s+"
    rf"(?P<day>\d{{1,2}})(?:er)?\s+(?P<month>{_MONTHS})\s+(?P<year>\d{{4}})\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_MEM_REF = re.compile(r"^\(?\s*(?:Mém(?:orial)?\.?\s*A|J\.?O\.?|Journal officiel)\b", re.IGNORECASE)
_HIER = re.compile(
    r"^(?:[«\"“]\s*)?(?P<kind>Partie|Livre|Titre|Chapitre|Section|Sous-section|Sous-titre)s?\s+"
    r"(?P<num>[IVXLC]+(?:er|re)?|\d+(?:er|re|ère)?(?:[-.]\d+)*(?:\s?(?:bis|ter|quater))?|premi(?:er|ère)|unique"
    r"|[A-Z](?![a-zA-Z]))\b\.?"
    r"(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_HIER_RANK = {"partie": 1, "livre": 2, "titre": 3, "sous-titre": 4, "chapitre": 5, "section": 6, "sous-section": 7}
_HIER_TYPE = {
    "partie": UnitType.PART,
    "livre": UnitType.BOOK,
    "titre": UnitType.TITLE,
    "sous-titre": UnitType.TITLE,
    "chapitre": UnitType.CHAPTER,
    "section": UnitType.SECTION,
    "sous-section": UnitType.SUBSECTION,
}
_ANNEX = re.compile(r"^(?:[«\"“]\s*)?(?:ANNEXES?\b|Annexes?\s+(?:[IVXLC]+|\d+[a-z]?)\b)")
_JURIS = re.compile(r"^jurisprudences?$", re.IGNORECASE)
_LABEL = re.compile(
    r"^(?:[«\"“]\s*)?(?:\(\d+[a-z]*(?:[- ]\d+)?\)|\d+[°º]|\d+\)|\d+\.(?!\d)|[a-z]{1,2}\)|[a-z]\.(?=\s)|[ivx]+\)"
    r"|[-–•·▪◦]|\(\w{1,4}\))\s"
)
_TERMINAL = re.compile(r"[.;:!?»\")]$")
_PROVENANCE_LINE = re.compile(r"^\(\s*(?:Loi|L\.|Règlement|R\.|Arrêté|A\.|Rectificatif|Acte|Décret|Ordonnance)\b.*\)$")
_VERSION_LINE = re.compile(r"^Version consolidée applicable au\b", re.IGNORECASE)
_KEEP_HYPHEN = frozenset(
    [
        "vingt",
        "trente",
        "quarante",
        "cinquante",
        "soixante",
        "quatre",
        "dix",
        "peut",
        "non",
        "demi",
        "semi",
        "ex",
        "anti",
        "post",
        "avant",
        "après",
        "arrière",
        "grand",
        "petit",
        "franco",
        "belgo",
        "germano",
        "socio",
        "psycho",
        "médico",
        "technico",
        "politico",
        "extra",
        "inter",
        "intra",
        "multi",
        "pluri",
        "bi",
        "tri",
        "auto",
        "contre",
        "entre",
        "sous",
        "sur",
        "nord",
        "sud",
        "ouest",
    ]
)
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")


# ------------------------------------------------------------------------------------------ matching
@dataclass(frozen=True, slots=True)
class ArticleHead:
    num_raw: str  # « Art. 1er. »
    rest: str  # text after the number on the heading line
    inserted: bool  # « « Art. 1bis. » (inserted by an amendment)


def match_article(text: str) -> ArticleHead | None:
    """``Art. …`` heading at the start of a paragraph (strict: the number must be followed by a period,
    a dash, a colon, the end of the line or an opening parenthesis — « Art. 5 de la loi » is not one)."""
    m = _ART_HEAD.match(text)
    if not m:
        return None
    num = normalise_whitespace(m["num"]).rstrip(",")
    end = m["end"].strip()
    kw = "Article" if m["kw"] == "Article" else "Art."
    num_raw = f"{kw} {num}{'.' if end == '.' else ''}"
    rest = text[m.end() :].strip()
    if end in ("–", "—", "-", ":"):
        rest = rest.lstrip("–—-: ")
    return ArticleHead(num_raw, rest, bool(m["lead"]))


@dataclass(frozen=True, slots=True)
class MemberHead:
    act_type: str  # LOI | RGD | AGD | RMIN | AMIN | …
    date: dt.date
    title: str  # full heading, normalised
    key: str  # anchor key, e.g. 'agd_1955-11-23'


_TYPE_CODE = {
    "loi": "LOI",
    "reglement grand-ducal": "RGD",
    "reglement grand ducal": "RGD",
    "reglement ministeriel": "RMIN",
    "reglement interne": "RI",
    "reglement": "RGD",
    "arrete grand-ducal": "AGD",
    "arrete grand ducal": "AGD",
    "arrete royal grand-ducal": "AGD",
    "arrete royal": "AMIN",
    "arrete ministeriel": "AMIN",
    "arrete": "AMIN",
    "decret": "DEC",
    "convention": "CONV",
    "accord": "ACC",
    "traite": "CONV",
    "protocole": "PROT",
    "ordonnance": "ORD",
    "statut": "ST",
    "statuts": "ST",
    "code": "CODE",
}
_MONTH_NO = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def match_member(text: str) -> MemberHead | None:
    """« Loi (modifiée) du 14 février 1955 concernant … » → (LOI, 1955-02-14, title, 'loi_1955-02-14')."""
    m = _MEMBER_HEAD.match(text)
    if not m:
        return None
    mid = unaccent(m["mid"]).lower()
    if re.search(r"\d", mid) or len(mid.split()) > 6:
        return None
    code = _TYPE_CODE.get(unaccent(m["type"]).lower(), "ACT")
    try:
        day = dt.date(int(m["year"]), _MONTH_NO[unaccent(m["month"]).lower()], int(m["day"]))
    except (KeyError, ValueError):
        return None
    title = normalise_whitespace(text).lstrip('«"“ ')
    title = re.sub(r"(?<=[^\W\d_]),\d{1,2}$", "", title)  # « … voies publiques,1 » (footnote marker)
    return MemberHead(code, day, title.rstrip(",;: "), f"{code.lower()}_{day.isoformat()}")


def article_candidates(lines: Sequence[str]) -> int:
    """Lines that start like an article heading (« Art. 5 », « Article 1er », « « Art. 1bis ») — the
    reference count of the article gate (PLAN §11.4: articles mapped within ±1 %)."""
    return sum(1 for t in lines if _ART_LOOSE.match(t))


def is_recueil(entry: ManifestEntry) -> bool:
    return entry.kind in (VersionKind.CODE_RECUEIL, VersionKind.RECUEIL) or entry.doc_type in (
        "CODE_RECUEIL",
        "RECUEIL",
    )


# ---------------------------------------------------------------------------------------- paragraphs
@dataclass(slots=True)
class Para:
    lines: list[Line]
    text: str
    md: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def first(self) -> Line:
        return self.lines[0]

    @property
    def page_from(self) -> int:
        return self.lines[0].page

    @property
    def page_to(self) -> int:
        return self.lines[-1].page

    @property
    def bold(self) -> bool:
        return all(ln.bold for ln in self.lines)

    @property
    def italic(self) -> bool:
        return all(ln.italic for ln in self.lines)

    @property
    def size(self) -> float:
        return max(ln.size for ln in self.lines)

    @property
    def is_table(self) -> bool:
        return self.lines[0].kind == "table"


def _vocab(pages: Sequence[Page]) -> collections.Counter[str]:
    c: collections.Counter[str] = collections.Counter()
    for p in pages:
        for ln in p.lines:
            if ln.kind == "body":
                words = _WORD.findall(ln.text)
                if ln.text.endswith("-") and words:
                    words = words[:-1]  # the broken word at the line end does not count
                c.update(w.lower() for w in words)
    return c


def _join(prev: str, nxt: str, vocab: collections.Counter[str]) -> str:
    """Join two lines of a paragraph, repairing end-of-line hyphenation."""
    if len(prev) >= 2 and prev.endswith("-") and prev[-2].isalnum():
        m1 = re.search(r"([^\W\d_]+)-$", prev)
        m2 = re.match(r"([^\W\d_]+)", nxt)
        if m1 and m2 and nxt[0].islower():
            w1, w2 = m1.group(1).lower(), m2.group(1).lower()
            hyph, solid = vocab[f"{w1}-{w2}"], vocab[w1 + w2]
            if hyph > solid or (hyph == solid == 0 and w1 in _KEEP_HYPHEN):
                return prev + nxt
            return prev[:-1] + nxt
        return prev + nxt  # « Grand-␤Duché », « L. 121-␤1 »
    return f"{prev} {nxt}"


def _is_heading_line(ln: Line) -> bool:
    t = ln.text
    return bool(_ART_HEAD.match(t) or _HIER.match(t) or _JURIS.match(t) or (_ANNEX.match(t) and len(t) < 120)) or bool(
        _VERSION_LINE.match(t)
    )


def _page_metrics(lines: Sequence[Line]) -> tuple[float, float, float]:
    """(typical gap between consecutive lines, left text edge, right text edge) of one page."""
    gaps = [b.top - a.bottom for a, b in itertools.pairwise(lines) if abs(a.size - b.size) < 0.3]
    gaps = [g for g in gaps if -2 < g < 40]
    typical = statistics.median(gaps) if gaps else 2.0
    xs0 = sorted(ln.x0 for ln in lines)
    xs1 = sorted(ln.x1 for ln in lines)
    left = xs0[len(xs0) // 10] if xs0 else 0.0
    right = xs1[int(len(xs1) * 0.9)] if xs1 else 0.0
    return typical, left, right


def _starts_new(a: Line, b: Line, metrics: dict[int, tuple[float, float, float]], body: float) -> bool:
    """Does line ``b`` start a new paragraph after line ``a``?"""
    if a.kind == "table" or b.kind == "table" or b.alinea is not None:
        return True
    if (b.bold or b.size >= body + 0.5) and _MEMBER_HEAD.match(b.text):
        return True  # a member-act title never continues the previous paragraph
    if a.page != b.page and a.size >= body + 0.5:
        return True  # headings do not run across pages
    if _is_heading_line(b) or _PROVENANCE_LINE.match(b.text):
        return True
    if _LABEL.match(b.text) and not (a.bold and b.bold):  # a numbered list inside a bold title continues it
        return True
    if _PROVENANCE_LINE.match(a.text) or _VERSION_LINE.match(a.text):
        return True
    if a.bold != b.bold or abs(a.size - b.size) >= 0.5:
        return True
    terminal = bool(_TERMINAL.search(a.text))
    if a.page != b.page:
        return terminal and (b.text[:1].isupper() or b.text[:1] in "«(")
    typical, left, right = metrics[a.page]
    if b.top - a.bottom > typical + max(1.5, 0.25 * b.size):
        return True
    short = a.x1 < right - max(15.0, 0.08 * (right - left))
    if short and terminal:
        return True
    return b.x0 > a.x0 + 6 and terminal


def build_paragraphs(pages: Sequence[Page], body: float) -> list[Para]:
    """Paragraphs of the body text (non-TOC pages), in reading order."""
    vocab = _vocab(pages)
    metrics = {p.number: _page_metrics([ln for ln in p.lines if ln.kind == "body"]) for p in pages}
    paras: list[Para] = []
    for p in pages:
        if p.is_toc:
            continue
        for ln in p.lines:
            if ln.kind == "table":
                paras.append(Para([ln], ln.text, ln.md))
                continue
            if ln.kind != "body":
                continue
            if paras and not paras[-1].is_table and not _starts_new(paras[-1].lines[-1], ln, metrics, body):
                last = paras[-1]
                last.lines.append(ln)
                last.text = _join(last.text, ln.text, vocab)
                last.notes.extend(ln.notes)
                continue
            paras.append(Para([ln], ln.text, None, list(ln.notes)))
    return paras


# ------------------------------------------------------------------------------------- segmentation
@dataclass(slots=True)
class _Item:
    plain: str
    md: str
    page_from: int
    page_to: int


@dataclass
class _Node:
    """A draft plus the text items it receives (document order is the order of ``_Segmenter.nodes``)."""

    draft: Draft
    page_from: int | None = None
    page_to: int | None = None
    items: list[_Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    head_item: _Item | None = None  # the heading line itself (page units in mode="pages")
    member: _Node | None = None

    def touch(self, a: int, b: int) -> None:
        self.page_from = a if self.page_from is None else min(self.page_from, a)
        self.page_to = b if self.page_to is None else max(self.page_to, b)

    def add(self, para: Para, plain: str | None = None, md: str | None = None) -> None:
        plain = para.text if plain is None else plain
        md = (para.md or plain) if md is None else md
        self.notes.extend(para.notes)
        self.touch(para.page_from, para.page_to)
        if plain:
            self.items.append(_Item(plain, md, para.page_from, para.page_to))


class _Segmenter:
    def __init__(self, body: float, *, recueil: bool) -> None:
        self.body = body
        self.recueil = recueil
        self.nodes: list[_Node] = []
        self.cur: _Node | None = None  # article / annex / preamble / paragraph receiving text
        self.member: _Node | None = None
        self.stack: list[tuple[int, _Node]] = []  # hierarchy (rank, node)
        self.in_juris = False
        self.stats: collections.Counter[str] = collections.Counter()
        self.members: list[tuple[_Node, MemberHead]] = []
        self.member_keys: collections.Counter[str] = collections.Counter()
        self.seen_nums: set[str] = set()  # article keys of the current member (numbering restarts)
        self.last_title: Para | None = None  # last title-like paragraph since the previous article
        self.anonymous: list[_Node] = []

    # --- helpers ---------------------------------------------------------------------------
    def _parent(self) -> Draft | None:
        if self.stack:
            return self.stack[-1][1].draft
        return self.member.draft if self.member else None

    def _new(
        self, utype: UnitType, anchor: str, para: Para | None, parent: Draft | None = None, **kw: str | None
    ) -> _Node:
        node = _Node(Draft(utype, anchor, parent=parent, **kw), member=self.member)  # type: ignore[arg-type]
        if para is not None:
            node.touch(para.page_from, para.page_to)
            node.head_item = _Item(para.first.text, para.first.text, para.page_from, para.page_from)
        self.nodes.append(node)
        return node

    def _prefix(self) -> str:
        return f"{self.member.draft.anchor}." if self.member else ""

    # --- paragraphs --------------------------------------------------------------------------
    def feed(self, paras: Sequence[Para]) -> None:
        for i, para in enumerate(paras):
            self._feed_one(i, para, paras)
            if not self.in_juris and not para.is_table:
                self.stats["article_candidates"] += sum(1 for ln in para.lines if _ART_LOOSE.match(ln.text))

    def _feed_one(self, i: int, para: Para, paras: Sequence[Para]) -> None:
        text = para.text
        if not para.is_table and _JURIS.match(text.strip()):
            self.in_juris, self.cur = True, None
            self.stats["jurisprudence_sections"] += 1
            return
        mh = self._member_heading(para, paras[i + 1 : i + 5]) if self.recueil else None
        if mh is not None:
            self._open_member(para, mh)
            return
        if self.in_juris:
            self.stats["jurisprudence_paragraphs"] += 1
            return
        head = match_article(text) if not para.is_table else None
        if head is not None and self._plausible_article(para, head):
            self._open_article(para, head, paras[i + 1] if i + 1 < len(paras) else None)
            return
        if not para.is_table and self._hierarchy(para):
            return
        if not para.is_table and self._annex(para):
            return
        self._text(para)

    def _member_heading(self, para: Para, following: Sequence[Para]) -> MemberHead | None:
        if para.is_table or len(para.text) > 1500:
            return None
        mh = match_member(para.text)
        if mh is None:
            return None
        larger = para.size >= self.body + 0.5
        large = para.size >= 1.15 * self.body
        mem_ref = False
        for f in following:  # the Mémorial reference follows the (possibly multi-paragraph) bold title
            if _MEM_REF.match(f.text):
                mem_ref = True
                break
            if not f.bold:
                break
        if (para.bold and (larger or mem_ref)) or (large and (mem_ref or len(para.lines) <= 3)):
            return mh
        return None

    def _open_member(self, para: Para, mh: MemberHead) -> None:
        self.member, self.stack, self.in_juris = None, [], False
        self.seen_nums, self.last_title = set(), None
        self.member_keys[mh.key] += 1
        key = mh.key if self.member_keys[mh.key] == 1 else f"{mh.key}~{self.member_keys[mh.key]}"
        node = self._new(UnitType.PART, key, para, parent=None, heading=mh.title)
        self.member = node
        node.member = node
        self.members.append((node, mh))
        self.cur = self._new(UnitType.PREAMBLE, f"{key}.preamble", None, parent=node.draft)
        self.cur.touch(para.page_from, para.page_to)
        self.stats["members"] += 1

    def _plausible_article(self, para: Para, head: ArticleHead) -> bool:
        if len(para.lines) == 1 and len(para.text) > 600:
            return False
        # « Art. 5 de la loi … » never matches; « Art. 12. les dispositions … » (lower-case body right
        # after the number) is a citation that wrapped to a line start unless the number is bold
        return not (head.rest and head.rest[:1].islower() and not para.first.bold)

    def _open_article(self, para: Para, head: ArticleHead, nxt: Para | None) -> None:
        heading = rest = None
        if head.rest:
            if len(para.lines) == 1 and para.first.bold and (nxt is None or not nxt.bold) and len(head.rest) <= 200:
                heading = head.rest  # « Art. 5. L'administration centrale et l'infrastructure. »
            else:
                rest = head.rest
        key = num_norm(head.num_raw) or "x"
        if self.recueil and key in ("1", "premier", "unique") and key in self.seen_nums:
            self._restart_member(para)  # numbering restarts: a member whose title was not recognised
        self.seen_nums.add(key)
        anchor = f"{self._prefix()}art_{key}"
        node = self._new(UnitType.ARTICLE, anchor, para, parent=self._parent(), num_raw=head.num_raw, heading=heading)
        if rest:
            node.add(para, rest, para.md if para.md else rest)
        else:
            node.notes.extend(para.notes)
        self.cur = node
        self.last_title = None
        self.stats["articles"] += 1
        self.stats["inserted_articles"] += head.inserted

    def _restart_member(self, para: Para) -> None:
        """Open an anonymous member part (``membre_N``) when article numbering restarts at 1 inside a
        member: the title of the new text was not recognised (no date, e.g. « Statuts de … »)."""
        self.stats["anonymous_members"] += 1
        title = self.last_title.text if self.last_title is not None else None
        key = f"membre_{self.stats['anonymous_members']}"
        self.member, self.stack = None, []
        node = self._new(UnitType.PART, key, para, parent=None, heading=title)
        self.member = node
        node.member = node
        self.anonymous.append(node)
        self.seen_nums = set()
        self.last_title = None

    def _hierarchy(self, para: Para) -> bool:
        m = _HIER.match(para.text)
        if not m or len(para.text) > 220 or len(para.lines) > 3 or para.text.rstrip().endswith((";", ",")):
            return False
        if not (para.bold or para.italic or para.size > self.body + 0.2 or para.first.x0 > 120):
            return False
        kind = m["kind"].lower()
        rank = _HIER_RANK[kind]
        while self.stack and self.stack[-1][0] >= rank:
            self.stack.pop()
        rest = m["rest"].strip().lstrip("-–—.: ").strip().rstrip(".»").strip() or None
        num_raw = normalise_whitespace(para.text[: m.start("rest")]).lstrip('«"“ ').rstrip(".")
        key = re.sub(r"[^a-z0-9]+", "_", unaccent(num_raw).lower()).strip("_")
        base = self.stack[-1][1].draft.anchor if self.stack else (self.member.draft.anchor if self.member else "")
        node = self._new(
            _HIER_TYPE[kind],
            f"{base}.{key}" if base else key,
            para,
            parent=self._parent(),
            num_raw=num_raw,
            heading=rest,
        )
        self.stack.append((rank, node))
        self.cur = None
        self.stats["hierarchy"] += 1
        return True

    def _annex(self, para: Para) -> bool:
        if not _ANNEX.match(para.text) or len(para.text) > 160 or not (para.bold or para.size > self.body + 0.2):
            return False
        self.stats["annexes"] += 1
        num, _, heading = para.text.partition(" – ")
        self.cur = self._new(
            UnitType.ANNEX,
            f"{self._prefix()}annexe_{self.stats['annexes']}",
            para,
            parent=self.member.draft if self.member else None,
            num_raw=num.strip('«"“ ')[:80],
            heading=heading.strip() or None,
        )
        return True

    def _text(self, para: Para) -> None:
        if self.cur is None:
            if self.stack:
                top = self.stack[-1][1]
                self.cur = self._new(UnitType.PARAGRAPH, f"{top.draft.anchor}.p1", None, parent=top.draft)
            elif self.member is not None:
                self.cur = self._new(
                    UnitType.PREAMBLE, f"{self.member.draft.anchor}.preamble", None, parent=self.member.draft
                )
            else:
                self.cur = self._new(UnitType.PREAMBLE, "preamble", None, parent=None)
        if para.is_table:
            self.stats["tables"] += 1
        elif para.bold and para.size >= self.body + 0.5 and len(para.text) <= 400:
            self.last_title = para
        self.cur.add(para)

    # --- output ------------------------------------------------------------------------------
    def finish(self, *, mode: str) -> list[_Node]:
        nodes = self._pages_mode() if mode == "pages" else self._page_fallback()
        for node in nodes:
            d = node.draft
            d.page_from, d.page_to = node.page_from, node.page_to
            if node.items:
                plain = "\n".join(i.plain for i in node.items if i.plain)
                md = "\n".join(i.md for i in node.items if i.md)
                d.text_plain = plain or None
                d.text_md = md if md and md != plain else None
            if d.unit_type is not UnitType.ARTICLE:
                continue
            prov = [i.plain for i in node.items if _PROVENANCE_LINE.match(i.plain)]
            notes = list(dict.fromkeys([*(n for n in node.notes if n), *prov]))
            if notes:
                d.provenance = "; ".join(notes)[:500]
            if d.text_plain and is_repeal_stub_text(d.text_plain):
                d.is_repealed, d.repeal_note = True, d.text_plain
            elif not d.text_plain and d.heading and is_repeal_stub_text(d.heading):
                d.is_repealed, d.repeal_note = True, d.heading
        return nodes

    def _page_fallback(self) -> list[_Node]:
        """Member sections (or the whole document) of ≥ 3 pages without any article heading → page units."""
        by_member: dict[int, list[_Node]] = collections.defaultdict(list)
        for n in self.nodes:
            by_member[id(n.member) if n.member else 0].append(n)
        replace: dict[int, list[_Node]] = {}
        for nodes in by_member.values():
            if any(n.draft.unit_type is UnitType.ARTICLE for n in nodes):
                continue
            texts = [n for n in nodes if n.draft.unit_type in (UnitType.PREAMBLE, UnitType.PARAGRAPH) and n.items]
            for n in texts:
                pages = {p for i in n.items for p in range(i.page_from, i.page_to + 1)}
                if len(pages) >= 3:
                    replace[id(n)] = self._split_pages(n)
                    self.stats["page_fallback_sections"] += 1
        out: list[_Node] = []
        for n in self.nodes:
            out.extend(replace.get(id(n), [n]))
        return out

    def _split_pages(self, node: _Node) -> list[_Node]:
        prefix = f"{node.member.draft.anchor}." if node.member else ""
        by_page: dict[int, list[_Item]] = collections.defaultdict(list)
        for it in node.items:
            by_page[it.page_from].append(it)
        out = []
        for page in sorted(by_page):
            d = Draft(UnitType.PAGE, f"{prefix}p.{page}", parent=node.draft.parent, num_raw=f"p. {page}")
            items = by_page[page]
            out.append(_Node(d, page, max(i.page_to for i in items), items, member=node.member))
        self.stats["page_units"] += len(out)
        return out

    def _pages_mode(self) -> list[_Node]:
        """``mode='pages'``: member parts plus one page unit per (member, page) with all its text."""
        out: list[_Node] = []
        pages: dict[tuple[int, int], _Node] = {}
        for n in self.nodes:
            if n.draft.unit_type is UnitType.PART and n.member is n:
                out.append(n)
                continue
            items = ([n.head_item] if n.head_item and n.draft.unit_type is not UnitType.PART else []) + n.items
            for it in items:
                key = (id(n.member) if n.member else 0, it.page_from)
                page = pages.get(key)
                if page is None:
                    prefix = f"{n.member.draft.anchor}." if n.member else ""
                    d = Draft(
                        UnitType.PAGE,
                        f"{prefix}p.{it.page_from}",
                        parent=n.member.draft if n.member else None,
                        num_raw=f"p. {it.page_from}",
                    )
                    page = pages[key] = _Node(d, it.page_from, it.page_to, member=n.member)
                    out.append(page)
                page.items.append(it)
                page.page_to = max(page.page_to or it.page_to, it.page_to)
                page.notes.extend(n.notes)
        self.stats["page_units"] = len(pages)
        return out


# ------------------------------------------------------------------------------------------- entry
def parse_pages(
    pages: list[Page],
    entry: ManifestEntry,
    raw: RawFile,
    *,
    mode: str = "articles",
) -> ParseResult:
    """Segment already-extracted pages (the pure part of :func:`parse_pdf`; tests use it directly)."""
    if entry.eli_version is None or entry.file is None:
        raise ParseError(f"{entry.eli_work}: manifest entry without version/file")
    if mode not in ("articles", "pages"):
        raise ValueError(f"unknown PDF mode {mode!r} (articles | pages)")
    pages, clean, body = clean_document(pages)
    if not any(p.has_text for p in pages):
        raise ParseError(f"{entry.eli_version}: no page has a text layer (scanned PDF: OCR is out of scope)")
    paras = build_paragraphs(pages, body)
    seg = _Segmenter(body, recueil=is_recueil(entry))
    seg.feed(paras)
    nodes = seg.finish(mode=mode)
    builder = UnitBuilder(entry, raw)
    for p in pages:
        if not p.has_text:
            builder.issue("pdf_page_without_text", f"page {p.number}: no text layer (skipped)")
    for n in nodes:
        builder.add(n.draft)
    units = builder.units()
    for u in units:
        if ActFlag.PDF_EXTRACTED not in u.flags:
            u.flags = [*u.flags, ActFlag.PDF_EXTRACTED]
    stats = _stats(seg, clean, pages, units, body)
    gate = _article_gate(stats)
    if gate is not None and mode == "articles":
        builder.issue("pdf_article_gate", gate)
    for node, _ in seg.members:
        if not any(n.member is node and n.draft.unit_type is UnitType.ARTICLE for n in nodes):
            builder.issue("pdf_member_without_articles", f"{node.draft.anchor}: {node.draft.heading}"[:300])
    return ParseResult(
        eli_work=entry.eli_work,
        eli_version=entry.eli_version,
        parser="pdf",
        parser_version=PARSER_VERSION if mode == "articles" else f"{PARSER_VERSION}+pages",
        source_sha256=raw.sha256,
        source_format=SourceFormat.PDF,
        units=units,
        relations=[],
        issues=builder.issues,
        stats=stats,
    )


def _stats(seg: _Segmenter, clean: CleanStats, pages: Sequence[Page], units: list, body: float) -> dict[str, int]:
    candidates = seg.stats["article_candidates"]
    arts = [u for u in units if u.unit_type is UnitType.ARTICLE]
    s = {
        "pages": len(pages),
        "pages_without_text": sum(1 for p in pages if not p.has_text),
        "toc_pages": clean.toc_pages,
        "toc_box_lines": clean.toc_lines,
        "header_lines": clean.header_lines,
        "footer_lines": clean.footer_lines,
        "footnote_lines": clean.footnote_lines,
        "margin_notes": clean.margin_notes,
        "alinea_numbers": clean.alinea_numbers,
        "footnote_markers": clean.markers,
        "rotated_chars": clean.rotated_chars,
        "tables": clean.tables,
        "jurisprudence_paragraphs": seg.stats["jurisprudence_paragraphs"],
        "anonymous_members": seg.stats["anonymous_members"],
        "units": len(units),
        "articles": len(arts),
        "article_candidates": candidates,
        "repealed": sum(1 for u in arts if u.is_repealed),
        "members": seg.stats["members"],
        "hierarchy": seg.stats["hierarchy"],
        "annexes": seg.stats["annexes"],
        "page_units": sum(1 for u in units if u.unit_type is UnitType.PAGE),
        "page_fallback_sections": seg.stats["page_fallback_sections"],
        "inserted_articles": seg.stats["inserted_articles"],
        "chars": sum(len(u.text_plain or "") for u in units),
        "body_size_x10": round(body * 10),
    }
    return s


def _article_gate(stats: dict[str, int]) -> str | None:
    cand, arts = stats["article_candidates"], stats["articles"]
    if cand == 0:
        return None if arts == 0 else f"{arts} articles but no candidate line"
    diff = abs(arts - cand) / cand
    if diff > ARTICLE_GATE_TOLERANCE:
        return f"articles {arts} vs candidates {cand} ({diff:.1%} > {ARTICLE_GATE_TOLERANCE:.0%})"
    return None


def parse_pdf(
    data: bytes, entry: ManifestEntry, raw: RawFile, *, mode: str = "articles", workers: int = 1
) -> ParseResult:
    """Parse a PDF-only code or consolidation into article (or page) units."""
    if not data.startswith(b"%PDF"):
        raise ParseError(f"{entry.eli_version}: not a PDF")
    try:
        pages = extract_pages(data, workers=workers)
    except Exception as exc:  # pdfminer raises many exception types on broken files
        if isinstance(exc, ParseError):
            raise
        raise ParseError(f"{entry.eli_version}: unreadable PDF ({type(exc).__name__}: {exc})") from exc
    return parse_pages(pages, entry, raw, mode=mode)


LAYOUT = LAYOUT_VERSION  # re-exported for the parse cache key documentation
