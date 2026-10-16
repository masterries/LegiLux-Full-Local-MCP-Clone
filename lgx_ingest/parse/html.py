"""CC-BY HTML parser (PLAN §6.3; the text path for ≈ 15.5k acts whose XML is licenceSCL).

Legilux HTML is XSLT output of the same AKN source with the same anchors, so this parser produces the
same :class:`~lgx_core.models.Unit` shape as :mod:`lgx_ingest.parse.akn`:

* articles: ``div.richtext_article`` — anchor from ``@id`` or, for repealed articles (``id="mod_del_…"``,
  ``style="display: none;del"``), from the inner ``<a name="art_N">``; number from
  ``p.richtext_num_article``, heading from ``span.richtext_heading_article``;
* hierarchy: ``div.richtext_{book,part,title,chapter,section,subsection,subchapter}`` with their
  ``richtext_heading_*`` (num and heading are one string in HTML → ``heading``);
* lists: ``table.richtext_ol|richtext_ul`` rows (``td.richtext_numLI`` label + ``td.richtext_contentLI``);
* removed: ``<script>``/``<style>``, everything ``display:none`` (modification widgets, hidden « (L du …) »
  references, deleted ``mod_del_*`` fragments), ``line-through`` text, the table of contents, the
  JSON-LD block (its ``legislationLegalForce`` contradicts SPARQL) and the signatures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import lxml.html
from lxml import etree

from lgx_core.models import ManifestEntry, ParseResult, RawFile, UnitType
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
    is_jo_original,
    normalise_href,
    split_annex_title,
    split_heading,
    table_block,
)

PARSER_VERSION = "html-1"

HIERARCHY: dict[str, UnitType] = {
    "richtext_book": UnitType.BOOK,
    "richtext_part": UnitType.PART,
    "richtext_title": UnitType.TITLE,
    "richtext_chapter": UnitType.CHAPTER,
    "richtext_subchapter": UnitType.CHAPTER,
    "richtext_section": UnitType.SECTION,
    "richtext_subsection": UnitType.SUBSECTION,
}
BLOCK_TAGS = frozenset({"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "ul", "ol", "blockquote"})
SKIP_TAGS = frozenset({"script", "style", "noscript", "head", "title", "meta", "link", "hr", "button", "svg"})
SKIP_CLASSES = frozenset(
    {"richtext_toc", "richtext_conclusions", "richtext_interstitial", "richtext_notes", "richtext_clause"}
)
SKIP_IDS = frozenset({"toc", "index"})  # table of contents, back-of-book index (AKN scl:index)


def _classes(el: etree._Element) -> set[str]:
    return set((el.get("class") or "").split())


def _style(el: etree._Element) -> str:
    return (el.get("style") or "").replace(" ", "").lower()


def _is_hidden(el: etree._Element) -> bool:
    return "display:none" in _style(el) or el.get("aria-hidden") == "true"


def _is_deleted(el: etree._Element) -> bool:
    st = _style(el)
    return "line-through" in st or (el.get("id") or "").startswith("mod_del") or st.endswith(";del")


def _tag(el: etree._Element) -> str:
    return el.tag.lower() if isinstance(el.tag, str) else ""


@dataclass
class _Collected:
    refs: list[str] = field(default_factory=list)
    deleted: int = 0
    embedded: bool = False
    images: int = 0
    tables: int = 0


class _Renderer:
    def render(self, el: etree._Element, w: LineWriter, st: _Collected, *, keep_deleted: bool = False) -> None:
        if el.text:
            w.text(el.text)
        for c in el:
            if isinstance(c.tag, str):
                self.node(c, w, st, keep_deleted=keep_deleted)
            if c.tail:
                w.text(c.tail)

    def inline_text(self, el: etree._Element, *, keep_deleted: bool = False) -> str:
        w = LineWriter()
        self.render(el, w, _Collected(), keep_deleted=keep_deleted)
        plain, _ = w.result()
        return normalise_whitespace((plain or "").replace("\n", " "))

    def node(self, el: etree._Element, w: LineWriter, st: _Collected, *, keep_deleted: bool = False) -> None:
        tag = _tag(el)
        cls = _classes(el)
        if tag in SKIP_TAGS or cls & SKIP_CLASSES or el.get("id") in SKIP_IDS:
            return
        if _is_deleted(el):
            if keep_deleted:
                self.render(el, w, st, keep_deleted=True)
            else:
                st.deleted += 1
            return
        if _is_hidden(el):
            return
        if tag == "a" and el.get("name") and not el.get("href"):
            return  # anchor target with a zero-width space
        if tag == "a" and ("noteCall" in cls or (el.get("href") or "").startswith("#note")):
            return  # footnote call (AKN noteRef)
        if tag == "img":
            alt = normalise_whitespace(el.get("alt") or "")
            w.text(f" [image: {alt}] " if alt else " [image] ")
            st.images += 1
            return
        if tag == "br":
            w.brk()
            return
        if tag == "sup":
            sub = LineWriter()
            self.render(el, sub, st)
            plain, _ = sub.result()
            w.sup((plain or "") + (" " if "".join(el.itertext())[-1:].isspace() else ""))
            return
        if tag == "a":
            target = normalise_href(el.get("href"))
            if target:
                st.refs.append(target)
            self.render(el, w, st)
            return
        if "richtext_num_paragraph" in cls:
            w.prefix(self.inline_text(el))
            return
        if tag == "table":
            self._table(el, w, st)
            return
        if tag in BLOCK_TAGS:
            w.brk()
            self.render(el, w, st, keep_deleted=keep_deleted)
            w.brk()
            return
        self.render(el, w, st, keep_deleted=keep_deleted)

    def _table(self, el: etree._Element, w: LineWriter, st: _Collected) -> None:
        cls = _classes(el)
        if cls & SKIP_CLASSES:
            return
        rows = [tr for tr in el.iter("tr") if next(tr.iterancestors("table"), None) is el]
        if cls & {"richtext_ol", "richtext_ul"}:
            w.brk()
            for tr in rows:
                if _is_deleted(tr):
                    st.deleted += 1
                    continue
                if _is_hidden(tr):
                    continue
                num = next((td for td in tr if "richtext_numLI" in _classes(td)), None)
                content = next((td for td in tr if "richtext_contentLI" in _classes(td)), None)
                w.prefix(self.inline_text(num) if num is not None else "")
                if content is not None:
                    self.render(content, w, st)
                w.brk()
            w.brk()
            return
        if "richtext_embeddedStructurequote" in cls:
            st.embedded = True
            w.brk()
            for tr in rows:
                for td in tr:
                    if _tag(td) in ("td", "th"):
                        self.render(td, w, st)
                        w.brk()
            return
        data: list[list[str]] = []
        for tr in rows:
            if _is_hidden(tr) or _is_deleted(tr):
                continue
            cells = []
            for td in tr:
                if _tag(td) in ("td", "th"):
                    sub = LineWriter()
                    self.render(td, sub, st)
                    cells.append((sub.result()[0] or "").replace("\n", " "))
            data.append(cells)
        plain, md = table_block(data)
        st.tables += 1
        w.block(plain, md)


class HtmlParser:
    """Single-use parser of one Legilux HTML manifestation."""

    def __init__(self, entry: ManifestEntry, raw: RawFile) -> None:
        self.entry = entry
        self.builder = UnitBuilder(entry, raw)
        self.r = _Renderer()
        self.collected: dict[int, _Collected] = {}
        self.stats: dict[str, int] = {}
        self._loose_seq: dict[str, int] = {}

    def parse(self, data: bytes) -> ParseResult:
        try:
            doc = lxml.html.document_fromstring(data)
        except (etree.ParserError, ValueError) as exc:
            raise ParseError(f"unreadable HTML: {exc}") from exc
        _strip_namespaces(doc)
        act = next(
            iter(doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " richtext_act ")]')), None
        )
        body = next(
            iter(doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " richtext_body ")]')), None
        )
        if act is None and body is None:
            raise ParseError("not a Legilux richtext HTML document")
        if act is not None and body is not None and is_jo_original(self.entry):
            self._preamble(act, body)  # consolidations have no visas; their title spills into <p>s
        if body is not None:
            self._walk(body, None)
        for k, att in enumerate(
            doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " richtext_attachment ")]'), 1
        ):
            self._attachment(att, k)
        if not self.builder.drafts:
            raise ParseError("HTML document without any text unit")
        units = self.builder.units()
        self._count(units)
        return ParseResult(
            eli_work=self.entry.eli_work,
            eli_version=self.builder.eli_version,
            parser="html",
            parser_version=PARSER_VERSION,
            source_sha256=self.builder.raw.sha256,
            source_format=self.builder.raw.source_format or self.entry.file.source_format,  # type: ignore[union-attr]
            units=units,
            relations=self.builder.ref_relations(),
            issues=self.builder.issues,
            stats=self.stats,
        )

    # ------------------------------------------------------------------------------- preamble
    def _preamble(self, act: etree._Element, body: etree._Element) -> None:
        """Visas and formula: the content between the long title and the body."""
        holder = body.getparent()
        if holder is None:
            return
        w, st = LineWriter(), _Collected()
        skip_title_p = False
        for c in holder:
            if c is body:
                break
            if not isinstance(c.tag, str):
                continue
            if c.get("id") == "intituleAct" or "richtext_longTitle" in _classes(c):
                # libxml2 moves <p> out of <h1>: an empty title heading means the next <p> is the title
                skip_title_p = not self.r.inline_text(c)
                continue
            if skip_title_p and _tag(c) == "p":
                skip_title_p = False
                continue
            skip_title_p = False
            self.r.node(c, w, st)
        plain, md = w.result()
        if plain:
            d = self.builder.add(Draft(UnitType.PREAMBLE, "preamble", text_plain=plain, text_md=md, refs=st.refs))
            self.collected[d.ordinal] = st

    # ------------------------------------------------------------------------------- structure
    def _walk(self, container: etree._Element, parent: Draft | None, absorb: LineWriter | None = None) -> None:
        loose: list[etree._Element] = []
        absorb_st = _Collected()

        def flush() -> None:
            if not loose:
                return
            if absorb is not None:
                for el in loose:
                    self.r.node(el, absorb, absorb_st)
            else:
                self._loose_unit(loose, parent)
            loose.clear()

        for c in container:
            if not isinstance(c.tag, str):
                continue
            cls = _classes(c)
            hier = next((HIERARCHY[k] for k in cls if k in HIERARCHY), None)
            if "richtext_article" in cls and not _is_loose_article(c):
                flush()
                self._article(c, parent)
            elif hier is not None:
                flush()
                if _is_hidden(c) or _is_deleted(c):
                    continue
                d = self._hier_draft(c, hier, parent)
                self.builder.add(d)
                self._walk(c, d)
            elif _is_container_noise(c, cls):
                continue
            else:
                loose.append(c)
        flush()

    def _hier_draft(self, el: etree._Element, unit_type: UnitType, parent: Draft | None) -> Draft:
        head = next(
            (c for c in el if isinstance(c.tag, str) and any(k.startswith("richtext_heading_") for k in _classes(c))),
            None,
        )
        text = provenance = None
        if head is not None:
            docnums = [self.r.inline_text(x) for x in head.iter() if "richtext_docNumber" in _classes(x)]
            for x in list(head.iter()):
                if "richtext_docNumber" in _classes(x) and x.getparent() is not None:
                    x.drop_tree()
            text = self.r.inline_text(head) or None
            provenance = " ".join(docnums) or None
        anchor = el.get("id") or _anchor_name(el) or f"_{unit_type.value}.{len(self.builder.drafts) + 1}"
        num_raw, text = _split_hier_heading(text)
        heading, prov2 = split_heading(text)
        return Draft(
            unit_type,
            anchor,
            parent=parent,
            akn_id=el.get("id"),
            num_raw=num_raw,
            heading=heading,
            provenance=provenance or prov2,
        )

    def _loose_unit(self, elements: list[etree._Element], parent: Draft | None) -> None:
        w, st = LineWriter(), _Collected()
        for el in elements:
            self.r.node(el, w, st)
        plain, md = w.result()
        if not plain:
            return
        base = parent.anchor if parent else "body"
        self._loose_seq[base] = self._loose_seq.get(base, 0) + 1
        first_id = next((el.get("id") for el in elements if el.get("id")), None)
        stub = is_repeal_stub_text(plain)
        d = self.builder.add(
            Draft(
                UnitType.PARAGRAPH,
                first_id or f"{base}.p{self._loose_seq[base]}",
                parent=parent,
                text_plain=plain,
                text_md=md,
                is_repealed=stub,
                repeal_note=plain if stub else None,
                refs=st.refs,
                is_modifying=st.embedded,
                contains_quoted_amendment=st.embedded,
            )
        )
        self.collected[d.ordinal] = st

    def _article(self, art: etree._Element, parent: Draft | None) -> None:
        deleted_article = _is_deleted(art) or _is_hidden(art)
        art_id = art.get("id") or ""
        anchor = art_id if art_id.startswith("art") else (_anchor_name(art) or art_id or None)
        num_p = next((c for c in art if "richtext_num_article" in _classes(c)), None)
        num_raw = heading = provenance = None
        head_st = _Collected()
        if num_p is not None:
            head_el = next((x for x in num_p.iter() if "richtext_heading_article" in _classes(x)), None)
            if head_el is not None:
                hw = LineWriter()
                self.r.render(head_el, hw, head_st)
                heading, provenance = split_heading((hw.result()[0] or "").replace("\n", " "))
                head_el.drop_tree()
            num_raw = self.r.inline_text(num_p, keep_deleted=True) or None
        w, st = LineWriter(), _Collected()
        for c in art:
            if not isinstance(c.tag, str) or c is num_p or (_tag(c) == "a" and c.get("name")):
                if c.tail and c is not num_p:
                    w.text(c.tail)
                continue
            self.r.node(c, w, st)
            if c.tail:
                w.text(c.tail)
        plain, md = w.result()
        if anchor is None:
            anchor = f"_art.{len(self.builder.drafts) + 1}"
            self.builder.issue("article_without_id", num_raw or anchor)
        stub_text = bool(plain) and not deleted_article and is_repeal_stub_text(plain or "")
        head_txt = " ".join(x for x in (heading, provenance) if x)
        if not plain and not deleted_article and head_txt and is_repeal_stub_text(head_txt):
            stub_text, plain = True, head_txt  # the repeal note sits in the article heading
        if deleted_article:
            plain = md = None
        eli = derived_article_eli(self.entry, anchor)
        d = self.builder.add(
            Draft(
                UnitType.ARTICLE,
                anchor,
                parent=parent,
                akn_id=anchor if not anchor.startswith("_") else None,
                eli_unit=eli,
                eli_unit_derived=eli is not None,
                num_raw=num_raw,
                heading=heading,
                provenance=provenance,
                text_plain=plain,
                text_md=md,
                is_repealed=deleted_article or stub_text,
                repeal_note="Abrogé" if deleted_article else (plain if stub_text else None),
                is_modifying=st.embedded,
                contains_quoted_amendment=st.embedded,
                amended_by=[r for r in dict.fromkeys(head_st.refs) if "/eli/etat/" in r] if provenance else [],
                refs=head_st.refs + st.refs,
            )
        )
        self.collected[d.ordinal] = st
        if eli is None and not deleted_article:
            # HTML carries no article ELIs; only JO originals get a derived one
            self.builder.issue("article_without_eli", d.anchor, f"{self.builder.eli_version}#{d.anchor}")

    # --------------------------------------------------------------------------------- annexes
    def _attachment(self, att: etree._Element, k: int) -> None:
        anchor = _anchor_name(att) or att.get("id") or f"attachment_{k}"
        doc = next((c for c in att.iter() if "richtext_mainBody" in _classes(c)), None)
        title_lines: list[str] = []
        for c in att:
            if not isinstance(c.tag, str) or c is doc or "richtext_doc" in _classes(c):
                break
            if _tag(c) in ("p", "h1", "h2", "div") and not _is_hidden(c):
                t = self.r.inline_text(c)
                if t:
                    title_lines.append(t)
        main = doc if doc is not None else att
        num_raw, heading = annex_title(title_lines)
        structured = any("richtext_article" in _classes(x) or (_classes(x) & set(HIERARCHY)) for x in main.iter())
        if structured:
            w = LineWriter()
            annex = self.builder.add(Draft(UnitType.ANNEX, anchor, num_raw=num_raw, heading=heading))
            self._walk(main, annex, absorb=w)
            annex.text_plain, annex.text_md = w.result()
            return
        segments: list[tuple[str | None, list[etree._Element]]] = [(None, [])]
        for c in main:
            if not isinstance(c.tag, str):
                continue
            t = self.r.inline_text(c)
            if t and is_annex_heading(t) and c.find(".//b") is not None:
                segments.append((t, []))
            else:
                segments[-1][1].append(c)
        segments = [s for s in segments if s[0] is not None or any(self.r.inline_text(e) for e in s[1])]
        if len(segments) <= 1:
            self._annex_unit(anchor, num_raw, heading, segments[0][1] if segments else [])
            return
        for label, elements in segments:
            if label is None:
                self._annex_unit(anchor, num_raw, heading, elements)
            else:
                n, h = split_annex_title(label)
                self._annex_unit(f"{anchor}.{annex_key(label)}", n, h, elements)

    def _annex_unit(self, anchor: str, num_raw: str | None, heading: str | None, elements: list) -> None:
        w, st = LineWriter(), _Collected()
        for el in elements:
            self.r.node(el, w, st)
        plain, md = w.result()
        if not (plain or num_raw or heading):
            return
        d = self.builder.add(
            Draft(UnitType.ANNEX, anchor, num_raw=num_raw, heading=heading, text_plain=plain, text_md=md, refs=st.refs)
        )
        self.collected[d.ordinal] = st

    def _count(self, units: list) -> None:
        arts = [u for u in units if u.unit_type is UnitType.ARTICLE]
        s = self.stats
        s["units"] = len(units)
        s["articles"] = len(arts)
        s["articles_derived_eli"] = sum(1 for u in arts if u.eli_unit_derived)
        s["repealed"] = sum(1 for u in arts if u.is_repealed)
        s["modifying"] = sum(1 for u in arts if u.is_modifying)
        s["annexes"] = sum(1 for u in units if u.unit_type is UnitType.ANNEX)
        s["paragraph_units"] = sum(1 for u in units if u.unit_type is UnitType.PARAGRAPH)
        s["chars"] = sum(len(u.text_plain or "") for u in units)
        s["tables"] = sum(c.tables for c in self.collected.values())
        s["images"] = sum(c.images for c in self.collected.values())
        s["deleted_fragments"] = sum(c.deleted for c in self.collected.values())


_HIER_START = re.compile(
    r"^(?:livre|titre|chapitre|section|sous-section|partie|sous-titre|tome|book|title|chapter|part)\b", re.IGNORECASE
)
_HIER_SEPARATORS = (" – ", " — ", ".–", ".—", ".-", ". -", " - ", "–", "—")


def _split_hier_heading(text: str | None) -> tuple[str | None, str | None]:
    """HTML joins number and heading: 'Chapitre II.-Des fraudes' -> ('Chapitre II.', 'Des fraudes')."""
    if not text or not _HIER_START.match(text):
        return None, text
    cut = min(((text.find(sep), sep) for sep in _HIER_SEPARATORS if 0 < text.find(sep) <= 40), default=None)
    if cut is None:
        return None, text
    pos, sep = cut
    num = text[:pos].rstrip() + ("." if sep.startswith(".") else "")
    head = text[pos + len(sep) :].strip()
    return num, head or None


def _is_container_noise(el: etree._Element, cls: set[str]) -> bool:
    """Anchor targets, hierarchy headings (read by the parent), TOC/index/signatures/clause."""
    if _tag(el) == "a" and el.get("name") and not el.get("href"):
        return True
    return any(k.startswith("richtext_heading_") for k in cls) or bool(cls & SKIP_CLASSES) or el.get("id") in SKIP_IDS


def _is_loose_article(el: etree._Element) -> bool:
    """``div.richtext_article`` without id, anchor or number (a stray alinea such as « (Section 3 … abrogée) »)."""
    if el.get("id") or _anchor_name(el):
        return False
    return not any("richtext_num_article" in _classes(c) for c in el if isinstance(c.tag, str))


def _anchor_name(el: etree._Element) -> str | None:
    """``<a name="art_48">`` directly inside ``el`` (the anchor of repealed articles and attachments)."""
    for c in el:
        if isinstance(c.tag, str) and _tag(c) == "a" and c.get("name"):
            return c.get("name")
    return None


def _strip_namespaces(doc: etree._Element) -> None:
    """Legilux XHTML declares the XHTML namespace; work on local names only."""
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{"):
            el.tag = el.tag.split("}", 1)[1]
    etree.cleanup_namespaces(doc)


def parse_html(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Parse one Legilux HTML manifestation for one manifest entry."""
    return HtmlParser(entry, raw).parse(data)
