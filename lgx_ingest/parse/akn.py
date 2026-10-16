"""Akoma Ntoso CSD13 + ``scl:`` parser (PLAN §6.3; lxml port of the research prototype ``akn_parse.py``).

One :class:`~lgx_core.models.Unit` per ``<article>`` reached by walking ``book|part|title|chapter|
section|subsection`` (hierarchy nodes become units too, so ``get_act`` can show a table of contents),
plus ``preamble``, loose body text (``paragraph`` units, e.g. JO acts without article markup) and annexes
(``attachments/attachment/doc/mainBody``; an annex without articles is split on bold « ANNEXE … »).

Hard rules:

* never descend into ``embeddedStructure`` for units — quoted amending text stays inside its host
  article (``contains_quoted_amendment``; the article is ``is_modifying``);
* ``<article style="del">``, all-``<del>`` bodies and « Abrogé(s) (…) » bodies are repeal stubs;
  deleted fragments never reach ``text_plain``;
* ``noteRef`` / ``mod-start|mod-end`` markers and ``scl:comment`` editor notes are dropped; a ``<mod>``
  *with content* (amending acts) is text;
* article ELIs come from ``scl:JOLUXWork//scl:jolux[@scl:name='uriThis']`` ([1] work, [2] version) and
  the article's ``dateApplicability``; JO originals get a derived ``<work>/art_N``
  (``eli_unit_derived``); generated recueil ids (``art_N10064``) are never anchors;
* identity comes from the manifest entry (SPARQL), never from ``<meta>``/``FRBRthis`` (stale).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from lxml import etree

from lgx_core.models import (
    ManifestEntry,
    ParseResult,
    RawFile,
    Relation,
    RelationSource,
    RelType,
    UnitType,
)
from lgx_core.normalise import is_repeal_stub_text, normalise_whitespace
from lgx_ingest.parse.common import (
    Draft,
    LineWriter,
    ParseError,
    UnitBuilder,
    annex_key,
    annex_title,
    derived_article_eli,
    is_annex_heading,
    list_label,
    normalise_href,
    parse_start,
    split_annex_title,
    split_heading,
    table_block,
)
from lgx_ingest.parse.recueil import is_generated_id, member_work_of_wid, normalise_wid

PARSER_VERSION = "akn-1"

SCL_NS_PREFIX = "http://www.scl.lu"

HIERARCHY: dict[str, UnitType] = {
    "book": UnitType.BOOK,
    "tome": UnitType.BOOK,
    "part": UnitType.PART,
    "subpart": UnitType.PART,
    "title": UnitType.TITLE,
    "subtitle": UnitType.TITLE,
    "chapter": UnitType.CHAPTER,
    "subchapter": UnitType.CHAPTER,
    "section": UnitType.SECTION,
    "subsection": UnitType.SUBSECTION,
    "division": UnitType.SECTION,
    "subdivision": UnitType.SUBSECTION,
    "hcontainer": UnitType.SECTION,
    "level": UnitType.SECTION,
}
# block-level content elements: each starts and ends a line
BLOCKS = frozenset(
    {
        "paragraph",
        "subparagraph",
        "alinea",
        "content",
        "p",
        "blockList",
        "list",
        "item",
        "point",
        "indent",
        "intro",
        "wrapUp",
        "listIntroduction",
        "listWrapUp",
        "container",
        "blockContainer",
        "tblock",
        "formula",
        "clause",
        "longTitle",
        "embeddedStructure",
        "quotedStructure",
        "article",
        "heading",
        "subheading",
        "signature",
        "div",
    }
)
LISTS = frozenset({"ol", "ul", "list", "blockList"})
LIST_ITEMS = frozenset({"li", "item", "point"})
# container-level elements that are never content (handled elsewhere or boilerplate)
NOT_CONTENT = frozenset(
    {"num", "heading", "subheading", "docNumber", "clause", "index", "indexterms", "meta", "conclusions"}
)
SKIP_SCL = frozenset({"JOLUXWork", "indexterms", "index", "comment", "rdf", "jolux", "articles"})
MOD_MARKER_CLASSES = frozenset({"mod-start", "mod-end"})
_VALID_ELI = re.compile(r"^http://data\.legilux\.public\.lu/eli/[A-Za-z0-9_./\-]+$")


def _ln(el: etree._Element) -> str:
    """Local name of an element ('' for comments and processing instructions)."""
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _is_scl(el: etree._Element) -> bool:
    tag = el.tag
    return isinstance(tag, str) and tag.startswith("{" + SCL_NS_PREFIX) and not tag.startswith("{http://www.scl.lu/r")


def _attr(el: etree._Element, local: str) -> str | None:
    """Attribute by local name, whatever its namespace prefix (``scl:symbol`` appears under several)."""
    for key, value in el.attrib.items():
        if key == local or key.endswith("}" + local):
            return value
    return None


def _is_deleted(el: etree._Element) -> bool:
    return _ln(el) == "del" or (el.get("style") or "").strip() == "del"


def _children(el: etree._Element) -> Iterator[etree._Element]:
    return (c for c in el if isinstance(c.tag, str))


def _find(el: etree._Element, *names: str) -> etree._Element | None:
    for c in _children(el):
        if _ln(c) in names:
            return c
    return None


# --------------------------------------------------------------------------------------- rendering
@dataclass
class Collected:
    """What rendering one unit's content collected besides text."""

    refs: list[str] = field(default_factory=list)
    mod_pms: list[str] = field(default_factory=list)
    note_ids: list[str] = field(default_factory=list)
    deleted: int = 0
    embedded: bool = False
    content_mod: bool = False
    images: int = 0
    tables: int = 0
    embedded_articles: int = 0


class Renderer:
    """Turns AKN content into lines (see :class:`~lgx_ingest.parse.common.LineWriter`)."""

    def render(self, el: etree._Element, w: LineWriter, st: Collected, *, skip: frozenset[str] = frozenset()) -> None:
        """Render the children (and text) of ``el``; child elements named in ``skip`` are ignored."""
        if el.text:
            w.text(el.text)
        for c in el:
            if isinstance(c.tag, str) and _ln(c) not in skip:
                self.node(c, w, st)
            if c.tail:
                w.text(c.tail)

    def inline_text(self, el: etree._Element, *, include_deleted: bool = False) -> str:
        """Normalised one-line text of ``el`` (num, heading, title)."""
        w = LineWriter()
        st = Collected()
        if include_deleted:
            self._render_keep_deleted(el, w)
        else:
            self.render(el, w, st)
        plain, _ = w.result()
        return normalise_whitespace((plain or "").replace("\n", " "))

    def _render_keep_deleted(self, el: etree._Element, w: LineWriter) -> None:
        if el.text:
            w.text(el.text)
        for c in el:
            name = _ln(c)
            if name and not (_is_scl(c) or name in ("noteRef", "eop") or self._is_marker(c)):
                if name == "sup":
                    w.sup("".join(c.itertext()))
                else:
                    self._render_keep_deleted(c, w)
            if c.tail:
                w.text(c.tail)

    @staticmethod
    def _is_marker(el: etree._Element) -> bool:
        return _ln(el) == "mod" and (el.get("class") or "") in MOD_MARKER_CLASSES

    def node(self, el: etree._Element, w: LineWriter, st: Collected) -> None:
        name = _ln(el)
        if _is_scl(el):
            if name not in SKIP_SCL:  # unknown scl: inline wrapper -> keep its text
                self.render(el, w, st)
            return
        if name == "noteRef":
            href = (el.get("href") or "").lstrip("#")
            if href:
                st.note_ids.append(href)
            return
        if name == "mod":
            if self._is_marker(el):
                if el.get("class") == "mod-start" and el.get("for"):
                    st.mod_pms.append(el.get("for", "").lstrip("#"))
                return
            st.content_mod = True
            self.render(el, w, st)
            return
        if _is_deleted(el):
            st.deleted += 1
            return
        if name in ("eop", "authorialNote", "marker"):
            return
        if name == "img":
            alt = normalise_whitespace(el.get("alt") or "")
            w.text(f" [image: {alt}] " if alt else " [image] ")
            st.images += 1
            return
        if name == "br":
            w.brk()
            return
        if name == "sup":
            sub = LineWriter()
            self.render(el, sub, st)
            plain, _ = sub.result()
            trailing = " " if "".join(el.itertext())[-1:].isspace() else ""
            w.sup((plain or "") + trailing)
            return
        if name == "ref":
            target = normalise_href(el.get("href"))
            if target:
                st.refs.append(target)
            self.render(el, w, st)
            return
        if name == "table":
            self._table(el, w, st)
            return
        if name in ("embeddedText", "quotedText") and not any(_ln(x) in BLOCKS for x in el.iter() if x is not el):
            # the XSLT (HTML) wraps inline quoted text in « … »; do the same unless it is already quoted
            inner = "".join(el.itertext()).strip()
            start = el.get("startQuote") or ("«" if name == "embeddedText" else "")
            end = el.get("endQuote") or ("»" if name == "embeddedText" else "")
            quoted = bool(start) and not inner.startswith(("«", '"', "“"))
            if quoted:
                w.text(f" {start} ")
            self.render(el, w, st)
            if quoted:
                w.text(f" {end} ")
            return
        if name in LISTS:
            self._list(el, w, st)
            return
        if name in ("embeddedStructure", "quotedStructure"):
            st.embedded = True
            st.embedded_articles += sum(1 for a in el.iter() if _ln(a) == "article")
            w.brk()
            self.render(el, w, st)
            w.brk()
            return
        if name in BLOCKS:
            w.brk()
            num = _find(el, "num")
            if num is not None and name != "content":
                w.prefix(self.inline_text(num))
                self.render(el, w, st, skip=frozenset({"num"}))
            else:
                self.render(el, w, st)
            w.brk()
            return
        self.render(el, w, st)  # inline (b, i, u, span, quotedText, embeddedText, term, date …)

    def _list(self, el: etree._Element, w: LineWriter, st: Collected) -> None:
        symbol = _attr(el, "symbol")
        start = parse_start(_attr(el, "start"))
        ordered = _ln(el) != "ul"
        w.brk()
        position = 0
        for c in _children(el):
            name = _ln(c)
            if name not in LIST_ITEMS:
                if not _is_deleted(c) and not _is_scl(c) and not self._is_marker(c):
                    self.node(c, w, st)
                continue
            if _is_deleted(c):
                st.deleted += 1
                position += 1
                continue
            label = _attr(c, "symbol") or list_label(symbol, position, start, ordered=ordered)
            num = _find(c, "num")
            if num is not None:
                label = self.inline_text(num)
            w.prefix(label)
            self.render(c, w, st, skip=frozenset({"num"}))
            w.brk()
            position += 1
        w.brk()

    def _table(self, el: etree._Element, w: LineWriter, st: Collected) -> None:
        rows: list[list[str]] = []
        for tr in el.iter():
            if _ln(tr) != "tr" or _is_deleted(tr):
                continue
            # only rows of this table, not of nested tables
            parent_table = next((a for a in tr.iterancestors() if _ln(a) == "table"), None)
            if parent_table is not el:
                continue
            cells: list[str] = []
            for td in _children(tr):
                if _ln(td) in ("td", "th"):
                    sub = LineWriter()
                    self.render(td, sub, st)
                    plain, _ = sub.result()
                    cells.append((plain or "").replace("\n", " "))
            rows.append(cells)
        plain, md = table_block(rows)
        st.tables += 1
        w.block(plain, md)


# ------------------------------------------------------------------------------------------ parser
@dataclass
class _Apparatus:
    """Modification apparatus of the document (``analysis`` + ``notes``)."""

    textual_mods: dict[str, tuple[str, str | None, str | None]] = field(default_factory=dict)  # id->(type,src,dst)
    notes: dict[str, list[str]] = field(default_factory=dict)  # note id -> ELI refs
    note_refers_to: dict[str, str] = field(default_factory=dict)  # note id -> textualMod id


class AknParser:
    """Stateful single-use parser of one AKN document."""

    def __init__(self, entry: ManifestEntry, raw: RawFile) -> None:
        self.entry = entry
        self.builder = UnitBuilder(entry, raw)
        self.renderer = Renderer()
        self.apparatus = _Apparatus()
        self.relations: list[Relation] = []
        self.collected: dict[int, Collected] = {}
        self.stats: dict[str, int] = {}
        self._loose_seq: dict[str, int] = {}

    # ---------------------------------------------------------------------------------- entry
    def parse(self, data: bytes) -> ParseResult:
        root = self._load(data)
        doc = next((c for c in _children(root) if _ln(c) not in ("meta", "components")), None)
        if doc is None:
            raise ParseError("AKN document without act/doc element")
        for meta in (m for m in doc.iter() if _ln(m) == "meta"):  # the act's and each attachment's
            self._apparatus(meta)
        preamble = _find(doc, "preamble")
        if preamble is not None:
            self._preamble(preamble)
        body = _find(doc, "body", "mainBody")
        if body is not None:
            self._walk(body, None)
        attachments = _find(doc, "attachments")
        if attachments is not None:
            for k, att in enumerate((a for a in _children(attachments) if _ln(a) == "attachment"), 1):
                self._attachment(att, k)
        if not self.builder.drafts:
            raise ParseError("AKN document without any text unit")
        self._finish_relations()
        units = self.builder.units()
        self._count(units)
        return ParseResult(
            eli_work=self.entry.eli_work,
            eli_version=self.builder.eli_version,
            parser="akn",
            parser_version=PARSER_VERSION,
            source_sha256=self.builder.raw.sha256,
            source_format=self.builder.raw.source_format or self.entry.file.source_format,  # type: ignore[union-attr]
            units=units,
            relations=self.builder.ref_relations() + self.relations,
            issues=self.builder.issues,
            stats=self.stats,
        )

    def _load(self, data: bytes) -> etree._Element:
        parser = etree.XMLParser(
            recover=True, huge_tree=True, resolve_entities=False, no_network=True, load_dtd=False, remove_pis=True
        )
        try:
            root = etree.fromstring(data, parser)
        except etree.XMLSyntaxError as exc:  # pragma: no cover - recover=True rarely raises
            raise ParseError(f"unreadable XML: {exc}") from exc
        if root is None or _ln(root) != "akomaNtoso":
            raise ParseError("not an Akoma Ntoso document")
        errors = [e for e in parser.error_log if e.level_name in ("ERROR", "FATAL")]
        if errors:
            self.stats["xml_recovered_errors"] = len(errors)
            self.builder.issue("xml_recovered", f"{len(errors)} XML errors recovered; first: {errors[0].message}")
        return root

    # ------------------------------------------------------------------------------- apparatus
    def _apparatus(self, meta: etree._Element) -> None:
        for tm in meta.iter():
            if _ln(tm) != "textualMod" or not tm.get("id"):
                continue
            src = _find(tm, "source")
            dst = _find(tm, "destination")
            src_eli = normalise_href(src.get("href")) if src is not None else None
            dst_href = ((dst.get("href") or "").lstrip("#") or None) if dst is not None else None
            self.apparatus.textual_mods[tm.get("id", "")] = (tm.get("type") or "", src_eli, dst_href)
        for note in meta.iter():
            if _ln(note) != "note" or not note.get("id"):
                continue
            refs = [r for r in (normalise_href(x.get("href")) for x in note.iter() if _ln(x) == "ref") if r]
            nid = note.get("id", "")
            self.apparatus.notes[nid] = refs
            if note.get("refersTo"):
                self.apparatus.note_refers_to[nid] = note.get("refersTo", "").lstrip("#")

    def _amended_by(self, st: Collected, extra: list[str]) -> list[str]:
        out: list[str] = []
        for pm in st.mod_pms:
            src = self.apparatus.textual_mods.get(pm, ("", None, None))[1]
            if src:
                out.append(src)
        for nid in st.note_ids:
            out.extend(r for r in self.apparatus.notes.get(nid, []) if "/eli/etat/" in r)
        out.extend(extra)
        return list(dict.fromkeys(out))

    def _finish_relations(self) -> None:
        """``akn-textualmod`` edges (one per textualMod) and ``akn-note`` edges for notes without one."""
        anchor_of_pm: dict[str, str] = {}
        anchor_of_note: dict[str, str] = {}
        for d in self.builder.drafts:
            st = self.collected.get(d.ordinal)
            if st is None:
                continue
            for pm in st.mod_pms:
                anchor_of_pm.setdefault(pm, d.anchor)
            for nid in st.note_ids:
                anchor_of_note.setdefault(nid, d.anchor)
        work = self.entry.eli_work
        for pm, (typ, src, dst) in self.apparatus.textual_mods.items():
            if not src:
                continue
            rel = RelType.REPEALS if typ in ("repeal", "deletion") else RelType.MODIFIES
            self.relations.append(
                Relation(
                    src_eli=src,
                    rel_type=rel,
                    dst_eli=work,
                    source=RelationSource.AKN_TEXTUALMOD,
                    dst_unit_hint=dst or anchor_of_pm.get(pm),
                )
            )
        for nid, refs in self.apparatus.notes.items():
            if self.apparatus.note_refers_to.get(nid) in self.apparatus.textual_mods or nid not in anchor_of_note:
                continue
            for ref in dict.fromkeys(r for r in refs if "/eli/etat/" in r):
                self.relations.append(
                    Relation(
                        src_eli=ref,
                        rel_type=RelType.MODIFIES,
                        dst_eli=work,
                        source=RelationSource.AKN_NOTE,
                        dst_unit_hint=anchor_of_note[nid],
                    )
                )
        self.stats["textual_mods"] = len(self.apparatus.textual_mods)
        self.stats["notes"] = len(self.apparatus.notes)

    # ------------------------------------------------------------------------------- structure
    def _preamble(self, el: etree._Element) -> None:
        w, st = LineWriter(), Collected()
        self.renderer.render(el, w, st)
        plain, md = w.result()
        if plain:
            d = self.builder.add(Draft(UnitType.PREAMBLE, "preamble", text_plain=plain, text_md=md, refs=st.refs))
            self.collected[d.ordinal] = st

    def _hier_draft(self, el: etree._Element, parent: Draft | None) -> Draft | None:
        num = _find(el, "num")
        head = _find(el, "heading")
        num_txt = self.renderer.inline_text(num) if num is not None else ""
        head_txt, provenance = "", None
        if head is not None:
            docnum = [self.renderer.inline_text(x) for x in head.iter() if _ln(x) == "docNumber"]
            w = LineWriter()
            self.renderer.render(head, w, Collected(), skip=frozenset({"docNumber"}))
            plain, _ = w.result()
            head_txt = normalise_whitespace((plain or "").replace("\n", " "))
            provenance = " ".join(docnum) or None
        akn_id = el.get("id")
        if not (num_txt or head_txt or akn_id):
            return None
        if akn_id and is_generated_id(akn_id):
            akn_id = None
        anchor = akn_id or f"_{_ln(el)}.{len(self.builder.drafts) + 1}"
        heading, prov2 = split_heading(head_txt)
        return Draft(
            HIERARCHY[_ln(el)],
            anchor,
            parent=parent,
            akn_id=akn_id,
            num_raw=num_txt or None,
            heading=heading,
            provenance=provenance or prov2,
        )

    def _walk(self, container: etree._Element, parent: Draft | None, absorb: LineWriter | None = None) -> None:
        """Walk a body/hierarchy/mainBody container: hierarchy → units, articles → units, rest → loose text."""
        sticky: Draft | None = None  # heading-only hierarchy node (civil 'subchapter') parenting later siblings
        loose: list[etree._Element] = []
        absorb_st = Collected()

        def flush() -> None:
            if not loose:
                return
            if absorb is not None:
                for el in loose:
                    self.renderer.node(el, absorb, absorb_st)
            else:
                self._loose_unit(loose, sticky or parent)
            loose.clear()

        for c in _children(container):
            name = _ln(c)
            if name in HIERARCHY:
                flush()
                d = self._hier_draft(c, parent)
                if d is None:
                    self._walk(c, parent)
                    continue
                self.builder.add(d)
                has_content = any(_ln(x) not in NOT_CONTENT for x in _children(c))
                if has_content:
                    self._walk(c, d)
                    sticky = None
                else:
                    sticky = d
            elif name == "article":
                flush()
                self._article(c, sticky or parent)
            elif name in NOT_CONTENT or _is_scl(c) or name == "" or _is_deleted(c):
                continue
            else:
                loose.append(c)
        flush()

    def _loose_unit(self, elements: list[etree._Element], parent: Draft | None) -> None:
        w, st = LineWriter(), Collected()
        for el in elements:
            self.renderer.node(el, w, st)
        plain, md = w.result()
        if not plain:
            return
        base = parent.anchor if parent else "body"
        first_id = next((el.get("id") for el in elements if el.get("id")), None)
        self._loose_seq[base] = self._loose_seq.get(base, 0) + 1
        anchor = first_id or f"{base}.p{self._loose_seq[base]}"
        stub = is_repeal_stub_text(plain)
        d = self.builder.add(
            Draft(
                UnitType.PARAGRAPH,
                anchor,
                parent=parent,
                text_plain=plain,
                text_md=md,
                is_repealed=stub,
                repeal_note=plain if stub else None,
                refs=st.refs,
                is_modifying=st.embedded or st.content_mod,
                contains_quoted_amendment=st.embedded,
                amended_by=self._amended_by(st, []),
            )
        )
        self.collected[d.ordinal] = st

    def _article_elis(self, art: etree._Element) -> tuple[str | None, str | None, dt.date | None, bool]:
        jw = next((c for c in _children(art) if _is_scl(c) and _ln(c) == "JOLUXWork"), None)
        work = version = None
        date: dt.date | None = None
        if jw is not None:
            uris: list[str] = []
            for j in jw.iter():
                if _ln(j) != "jolux":
                    continue
                key = _attr(j, "name")
                value = (j.text or "").strip()
                if key == "uriThis" and value:
                    uris.append(value)
                elif key == "dateApplicability" and value and date is None:
                    try:
                        date = dt.date.fromisoformat(value[:10])
                    except ValueError:
                        date = None
            work = uris[0] if uris else None
            version = uris[1] if len(uris) > 1 else None
        if work is None:
            scl_uri = _attr(art, "uri")
            work = normalise_href(scl_uri) if scl_uri else None
        malformed = [v for v in (work, version) if v is not None and not _VALID_ELI.match(v)]
        for value in malformed:
            self.builder.issue("malformed_article_eli", value, f"{self.builder.eli_version}#{art.get('id')}")
        if work is not None and not _VALID_ELI.match(work):
            # « …/code/travail/art. l.143-1. »: mint <prefix>/<akn id>, flagged as derived (non-official)
            akn_id = art.get("id") or ""
            base = _valid_prefix(work)
            work = f"{base}/{akn_id}" if base and akn_id.startswith("art_") and not is_generated_id(akn_id) else None
            return work, None, date, work is not None
        if version is not None and not _VALID_ELI.match(version):
            version = None
        return work, version, date, False

    @staticmethod
    def _index_terms(art: etree._Element) -> list[str]:
        terms: list[str] = []
        holder = next((c for c in _children(art) if _is_scl(c) and _ln(c) == "indexterms"), None)
        if holder is None:
            return terms

        def walk(el: etree._Element, prefix: str | None) -> None:
            for term in _children(el):
                if _ln(term) != "indexterm":
                    continue
                label_el = next((x for x in _children(term) if _ln(x) == "label"), None)
                label = normalise_whitespace("".join(label_el.itertext())) if label_el is not None else ""
                label = label.lstrip("-–— ").strip()
                full = f"{prefix} – {label}" if prefix and label else (label or prefix or "")
                if full:
                    terms.append(full)
                for sub in _children(term):
                    if _ln(sub) == "indexterms":
                        walk(sub, full or prefix)

        walk(holder, None)
        return list(dict.fromkeys(terms))

    def _article(self, art: etree._Element, parent: Draft | None) -> None:
        r = self.renderer
        akn_id = art.get("id")
        wid = art.get("wId")
        num = _find(art, "num")
        head = _find(art, "heading")
        num_raw = r.inline_text(num, include_deleted=True) if num is not None else None
        head_st = Collected()
        head_txt = ""
        if head is not None:
            w = LineWriter()
            r.render(head, w, head_st)
            plain, _ = w.result()
            head_txt = normalise_whitespace((plain or "").replace("\n", " "))
        heading, provenance = split_heading(head_txt)

        w, st = LineWriter(), Collected()
        if num is not None:  # markers and note refs on the number belong to the article
            r.render(num, LineWriter(), st)
        r.render(art, w, st, skip=frozenset({"num", "heading"}))
        plain, md = w.result()

        eli_work, eli_version, version_date, derived = self._article_elis(art)
        generated = bool(akn_id and is_generated_id(akn_id))
        if generated:
            akn_id = None
        member = from_wid = None
        if wid:
            from_wid = normalise_wid(wid)
            member = member_work_of_wid(from_wid)
        if eli_work is None and not generated and not wid:
            eli_work = derived_article_eli(self.entry, akn_id)
            derived = eli_work is not None
        anchor = akn_id or (from_wid.split("/eli/", 1)[-1] if from_wid else None)
        if anchor is None:
            anchor = f"_art.{len(self.builder.drafts) + 1}"
            self.builder.issue("article_without_id", num_raw or anchor)

        deleted_article = _is_deleted(art) or (not plain and st.deleted > 0)
        stub_text = bool(plain) and is_repeal_stub_text(plain or "")
        if not plain and not deleted_article and is_repeal_stub_text(head_txt):
            # « Art. L. 232-14. (abrogé par la loi du 8 février 2024 ) » — the note sits in <heading>
            stub_text, plain = True, head_txt
        amended_by = self._amended_by(st, head_st.refs if provenance else [])  # « ( L. 13 mai 2008 ) »
        repeal_note = None
        if deleted_article:
            repeal_note = "Abrogé" + (" par " + ", ".join(amended_by) if amended_by else "")
            plain = md = None
        elif stub_text:
            repeal_note = plain
        d = self.builder.add(
            Draft(
                UnitType.ARTICLE,
                anchor,
                parent=parent,
                akn_id=akn_id,
                eli_unit=eli_work,
                eli_unit_version=eli_version,
                eli_unit_derived=derived,
                unit_version_date=version_date,
                num_raw=num_raw or None,
                heading=heading,
                provenance=provenance,
                text_plain=plain,
                text_md=md,
                is_repealed=deleted_article or stub_text,
                repeal_note=repeal_note,
                is_modifying=st.embedded or st.content_mod,
                contains_quoted_amendment=st.embedded,
                amended_by=amended_by,
                index_terms=self._index_terms(art),
                member_act_eli=member,
                from_recueil_wid=from_wid,
                refs=head_st.refs + st.refs,
            )
        )
        self.collected[d.ordinal] = st
        if eli_work is None and not wid and not derived:
            self.builder.issue("article_without_eli", d.anchor, f"{self.builder.eli_version}#{d.anchor}")
        self.stats["embedded_articles"] = self.stats.get("embedded_articles", 0) + st.embedded_articles

    # --------------------------------------------------------------------------------- annexes
    def _attachment(self, att: etree._Element, k: int) -> None:
        doc = _find(att, "doc")
        if doc is None:
            doc = att
        att_anchor = att.get("id") or f"attachment_{k}"
        preface = _find(doc, "preface")
        title_lines: list[str] = []
        if preface is not None:
            lt = _find(preface, "longTitle")
            if lt is not None:
                tw = LineWriter()
                self.renderer.render(lt, tw, Collected())
                title_lines = (tw.result()[0] or "").split("\n")
        main = _find(doc, "mainBody", "body")
        if main is None:
            return
        structured = any(_ln(c) == "article" or _ln(c) in HIERARCHY for c in _children(main))
        num_raw, heading = annex_title(title_lines)
        if structured:
            w = LineWriter()
            annex = self.builder.add(Draft(UnitType.ANNEX, att_anchor, num_raw=num_raw, heading=heading))
            self._walk(main, annex, absorb=w)
            annex.text_plain, annex.text_md = w.result()
            return
        segments = self._split_annexes(main)
        if len(segments) <= 1:
            self._annex_unit(att_anchor, num_raw, heading, list(_children(main)))
            return
        for label, elements in segments:
            if label is None:  # text before the first « ANNEXE … » heading
                self._annex_unit(att_anchor, num_raw, heading, elements)
            else:
                seg_num, seg_head = split_annex_title(label)
                self._annex_unit(f"{att_anchor}.{annex_key(label)}", seg_num, seg_head, elements)

    def _split_annexes(self, main: etree._Element) -> list[tuple[str | None, list[etree._Element]]]:
        segments: list[tuple[str | None, list[etree._Element]]] = []
        current: list[etree._Element] = []
        label: str | None = None
        for c in _children(main):
            txt = self.renderer.inline_text(c)
            if txt and is_annex_heading(txt) and _is_emphasised(c):
                if current or label is not None:
                    segments.append((label, current))
                label, current = txt, []
                continue
            current.append(c)
        if current or label is not None:
            segments.append((label, current))
        return [s for s in segments if s[0] is not None or any(self.renderer.inline_text(e) for e in s[1])]

    def _annex_unit(self, anchor: str, num_raw: str | None, heading: str | None, elements: list) -> None:
        w, st = LineWriter(), Collected()
        for el in elements:
            if not _is_deleted(el):
                self.renderer.node(el, w, st)
        plain, md = w.result()
        if not (plain or num_raw or heading):
            return
        d = self.builder.add(
            Draft(
                UnitType.ANNEX,
                anchor,
                num_raw=num_raw,
                heading=heading,
                text_plain=plain,
                text_md=md,
                refs=st.refs,
                amended_by=self._amended_by(st, []),
            )
        )
        self.collected[d.ordinal] = st

    # ----------------------------------------------------------------------------------- stats
    def _count(self, units: list) -> None:
        arts = [u for u in units if u.unit_type is UnitType.ARTICLE]
        s = self.stats
        s["units"] = len(units)
        s["articles"] = len(arts)
        s["articles_with_eli"] = sum(1 for u in arts if u.eli_unit and not u.eli_unit_derived)
        s["articles_derived_eli"] = sum(1 for u in arts if u.eli_unit_derived)
        s["repealed"] = sum(1 for u in arts if u.is_repealed)
        s["modifying"] = sum(1 for u in arts if u.is_modifying)
        s["with_quoted_amendment"] = sum(1 for u in arts if u.contains_quoted_amendment)
        s["annexes"] = sum(1 for u in units if u.unit_type is UnitType.ANNEX)
        s["paragraph_units"] = sum(1 for u in units if u.unit_type is UnitType.PARAGRAPH)
        s["hierarchy"] = sum(1 for u in units if u.unit_type.value in {t.value for t in HIERARCHY.values()})
        s["chars"] = sum(len(u.text_plain or "") for u in units)
        s["tables"] = sum(c.tables for c in self.collected.values())
        s["images"] = sum(c.images for c in self.collected.values())
        s["deleted_fragments"] = sum(c.deleted for c in self.collected.values())
        s.setdefault("embedded_articles", 0)


def _valid_prefix(uri: str) -> str | None:
    """Longest prefix of ``uri`` made of well-formed path segments (drops the malformed tail)."""
    head, _, tail = uri.partition("/eli/")
    good: list[str] = []
    for seg in tail.split("/"):
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", seg):
            break
        good.append(seg)
    return f"{head}/eli/" + "/".join(good) if good else None


def _is_emphasised(el: etree._Element) -> bool:
    """Bold or centred paragraph (how annex headings are marked in JO XML)."""
    for x in el.iter():
        if _ln(x) == "b":
            return True
        if _ln(x) == "p" and (_attr(x, "align") or "") == "center":
            return True
    return False


def parse_akn(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Parse one AKN XML file for one manifest entry."""
    return AknParser(entry, raw).parse(data)


def iter_article_ids(data: bytes) -> list[str | None]:
    """``@id`` of every ``<article>`` outside ``embeddedStructure`` (invariant helper for tests/gates)."""
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser)
    out: list[str | None] = []
    for a in root.iter():
        if _ln(a) == "article" and not any(
            _ln(x) in ("embeddedStructure", "quotedStructure") for x in a.iterancestors()
        ):
            out.append(a.get("id"))
    return out
