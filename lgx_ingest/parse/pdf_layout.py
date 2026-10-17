"""Layout layer of the PDF parser: born-digital pages → clean body lines (PLAN §6.3).

``pdfplumber`` gives positioned characters; this module turns them into reading-order lines and removes
everything that is not legal text of the page body:

* rotated characters (vertical running titles on the page edge) are dropped;
* lines are clustered by vertical overlap; raised small characters are **superscripts**: ordinal suffixes
  are glued to their number (« 1 » + « er » → « 1er »), footnote markers (« 1) », « 2 ») are dropped;
* broken glyph mappings (``' .'`` for a period in the InDesign exports) and ligatures are repaired;
* right-margin notes beyond the text column (« L. 27.7.92 ») are split off as *notes* (→ ``provenance``),
  left-margin alinea numbers (« Les chiffres à gauche … n'ont pas de caractère officiel ») are dropped;
* running headers and footers (top/bottom band, repeated on several pages, page numbers) are removed —
  the header text is kept per page as context (« CODE DE LA ROUTE – Loi du 14 février 1955 »);
* the footnote zone at the bottom of a page (smaller font, starting with a marker) is removed;
* ruled tables (``page.find_tables``) become one block with a Markdown rendering;
* table-of-contents pages (dot leaders, right-aligned page numbers / « Art. 1 - 7 ») are flagged.

The document-level pass (:func:`clean_document`) needs all pages (repetition counts) and is
deterministic; page extraction can run in parallel processes (:func:`extract_pages`).
"""

from __future__ import annotations

import collections
import io
import multiprocessing
import re
import statistics
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from lgx_core.normalise import normalise_whitespace, unaccent

LAYOUT_VERSION = "layout-1"

_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st", "­": ""}
_ORDINAL_SUP = re.compile(r"^(?:er|re|ère|e|ème|eme|o|nd|nde|d|me|bis|ter|quater)$", re.IGNORECASE)
_MARKER_SUP = re.compile(r"^(?:(?:\(?\d{1,3}\)|\d{1,3}|\*{1,3}|[a-z]\))\s*)+$")
_MARK = "\x00"
_PAGE_NO = re.compile(r"^[-–—\s]*(?:page\s*)?\d{1,4}[-–—\s]*$", re.IGNORECASE)
_DOT_LEADER = re.compile(r"(?:\.\s?){4,}|(?:\.\s{1,3}){4,}|…{2,}")
_TRAILING_REF = re.compile(
    r"^(?:(?:p\.|page)\s*)?\d{1,4}$|^Art\.?\s*[LRA]?\.?\s*\d[\w-]*(?:\s*[-–à]\s*(?:Art\.?\s*)?[LRA]?\.?\s*\d[\w-]*)?,?$",
    re.IGNORECASE,
)
_FOOTNOTE_START = re.compile(r"^(?:\(?\d{1,3}\)|\d{1,3}\s|\*{1,3}\s?)")
_BARE_INT = re.compile(r"^\d{1,3}$")
_TOC_TITLE = re.compile(r"^(?:sommaire|table des mati[eè]res|table chronologique|plan g[ée]n[ée]ral)\b", re.I)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,])(?=\s|$)")
_MARK_BEFORE_PUNCT = re.compile(r"\s*\x00[\s\x00]*([.,;:)\]])")
_MARK_ANY = re.compile(r"\s*\x00[\s\x00]*")


# ------------------------------------------------------------------------------------------ records
@dataclass(slots=True)
class Line:
    """One reading-order line of a page (after superscript and margin handling)."""

    page: int
    top: float
    bottom: float
    x0: float
    x1: float
    text: str
    size: float
    bold: bool = False
    italic: bool = False
    notes: list[str] = field(default_factory=list)  # right-margin notes (provenance)
    alinea: str | None = None  # dropped left-margin alinea number
    markers: int = 0  # footnote markers dropped from this line
    gap_split: bool = False  # the line has a wide internal gap (tab stops, TOC leaders)
    last_seg: str = ""  # text after the last wide gap (TOC page numbers, « Art. 1 - 7 »)
    kind: str = "body"  # body | header | footer | footnote | table | margin
    md: str | None = None  # Markdown for table blocks
    words: list[tuple[float, float, str]] = field(default_factory=list, repr=False)  # cleared after margins


@dataclass(slots=True)
class Page:
    number: int  # 1-based PDF page index (what a PDF viewer shows, `#page=N`)
    width: float
    height: float
    lines: list[Line]
    chars: int = 0  # upright characters with a text layer
    rotated: int = 0  # rotated characters dropped
    tables: int = 0
    header: str | None = None  # running header text (context only)
    is_toc: bool = False
    footnote_lines: int = 0

    @property
    def has_text(self) -> bool:
        return self.chars > 0


# ------------------------------------------------------------------------------------- page extraction
def _char_text(c: dict[str, Any]) -> str:
    t = str(c.get("text") or "")
    if len(t) > 1 and t[0] == " ":  # InDesign ToUnicode maps the period glyph to ' .'
        t = t.lstrip()
    for k, v in _LIGATURES.items():
        if k in t:
            t = t.replace(k, v)
    return t


def _is_bold(fontname: str) -> bool:
    f = fontname.lower()
    return "bold" in f or "black" in f or "heavy" in f or "semibold" in f


def _is_italic(fontname: str) -> bool:
    f = fontname.lower()
    return "italic" in f or "oblique" in f


@dataclass(slots=True)
class _Cluster:
    top: float
    bottom: float
    chars: list[dict[str, Any]]


def _cluster_lines(chars: list[dict[str, Any]]) -> list[_Cluster]:
    """Group characters into lines: a character joins the line whose band contains its vertical centre."""
    clusters: list[_Cluster] = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        cy = (c["top"] + c["bottom"]) / 2
        for cl in reversed(clusters[-8:]):
            if cl.top - 0.5 <= cy <= cl.bottom + 0.5:
                cl.chars.append(c)
                cl.top = min(cl.top, c["top"])
                cl.bottom = max(cl.bottom, c["bottom"])
                break
        else:
            clusters.append(_Cluster(c["top"], c["bottom"], [c]))
    clusters.sort(key=lambda cl: cl.top)
    return clusters


def _merge_small_clusters(clusters: list[_Cluster], body: float) -> list[_Cluster]:
    """Attach clusters made only of a few small characters (a superscript above its line) to that line."""
    out: list[_Cluster] = []
    for i, cl in enumerate(clusters):
        small = max(c["size"] for c in cl.chars) < 0.85 * body and len(cl.chars) <= 8
        if small:
            below = clusters[i + 1] if i + 1 < len(clusters) else None
            if below is not None and 0 <= below.top - cl.top <= 0.9 * body and below.bottom > cl.bottom:
                below.chars.extend(cl.chars)
                below.top = min(below.top, cl.top)
                continue
            if out and cl.top - out[-1].bottom <= 1.0:
                out[-1].chars.extend(cl.chars)
                continue
        out.append(cl)
    return out


@dataclass(slots=True)
class _Run:
    text: str
    x0: float
    x1: float
    sup: bool
    space_before: bool
    bold: int = 0
    italic: int = 0
    n: int = 0
    words: list[tuple[float, float, str]] = field(default_factory=list)


def _runs(chars: list[dict[str, Any]]) -> tuple[list[_Run], float]:
    """Character runs of one line in x order (superscript vs normal); spaces from space glyphs or gaps."""
    chars = sorted(chars, key=lambda c: c["x0"])
    visible = [c for c in chars if _char_text(c).strip()]
    big = max(c["size"] for c in visible)
    base_chars = [c for c in visible if c["size"] >= 0.9 * big]
    baseline = statistics.median(c["bottom"] for c in base_chars)
    size = statistics.mode(round(c["size"], 1) for c in visible)  # dominant size, not the largest glyph
    runs: list[_Run] = []
    prev_x1: float | None = None
    prev_size = size
    pending_space = False
    word: list[Any] = []  # [x0, x1, text] of the current normal word

    def close_word() -> None:
        if word and runs:
            runs[-1].words.append((word[0], word[1], word[2]))
        word.clear()

    for c in chars:
        t = _char_text(c)
        if not t.strip():
            pending_space = prev_x1 is not None
            if prev_x1 is not None:
                prev_x1 = max(prev_x1, c["x1"])
            continue
        sup = c["size"] < 0.85 * big and c["bottom"] < baseline - 0.8
        gap = c["x0"] - prev_x1 if prev_x1 is not None else 0.0
        space = pending_space or (prev_x1 is not None and gap > 0.18 * min(c["size"], prev_size))
        if not runs or runs[-1].sup != sup:
            close_word()
            runs.append(_Run("", c["x0"], c["x1"], sup, space))
            space = False
        r = runs[-1]
        if space:
            r.text += " "
            if not sup:
                close_word()
        r.text += t
        r.x1 = max(r.x1, c["x1"])
        r.n += 1
        fontname = str(c.get("fontname") or "")
        r.bold += _is_bold(fontname)
        r.italic += _is_italic(fontname)
        if not sup:
            if word:
                word[1] = c["x1"]
                word[2] += t
            else:
                word.extend([c["x0"], c["x1"], t])
        prev_x1, prev_size, pending_space = c["x1"], c["size"], False
    close_word()
    return runs, size


def _ends_with_number(parts: list[str]) -> bool:
    last = "".join(parts).rstrip()
    return bool(last) and (last[-1].isdigit() or bool(re.search(r"\b[IVXLC]+$", last)))


def _line_from_runs(runs: list[_Run], size: float, page: int, top: float, bottom: float) -> Line | None:
    parts: list[str] = []
    markers = bold = italic = n = 0
    words: list[tuple[float, float, str]] = []
    for r in runs:
        if r.sup:
            core = r.text.strip()
            if not r.space_before and _ORDINAL_SUP.match(core) and _ends_with_number(parts):
                parts = ["".join(parts).rstrip(), core]
                continue
            if _MARKER_SUP.match(core):
                markers += len(re.findall(r"\d+|\*+|[a-z]\)", core)) or 1
                parts.append(_MARK)
                continue
            parts.append((" " if r.space_before else "") + core)
            continue
        parts.append((" " if r.space_before else "") + r.text)
        words.extend(r.words)
        bold += r.bold
        italic += r.italic
        n += r.n
    joined = _MARK_ANY.sub(" ", _MARK_BEFORE_PUNCT.sub(r"\1", "".join(parts)))
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", normalise_whitespace(joined))
    if not text or not words:
        return None
    return Line(
        page,
        top,
        bottom,
        words[0][0],
        words[-1][1],
        text,
        size,
        bold > 0.6 * n,
        italic > 0.6 * n,
        markers=markers,
        words=words,
    )


def _segments(words: list[tuple[float, float, str]], size: float) -> list[tuple[float, float, str]]:
    """Words grouped into segments separated by wide gaps (tab stops, margin notes, TOC page numbers)."""
    segs: list[list[Any]] = []
    for x0, x1, t in words:
        if segs and x0 - segs[-1][1] <= max(2.5 * size, 18.0):
            segs[-1][1] = x1
            segs[-1][2] += " " + t
        else:
            segs.append([x0, x1, t])
    return [(s[0], s[1], s[2]) for s in segs]


def _table_blocks(page: Any, number: int) -> tuple[list[Line], list[tuple[float, float, float, float]]]:
    """Ruled tables with ≥ 2 rows and ≥ 2 columns → one Line(kind='table') each, plus their bboxes."""
    if len(page.edges) < 8:
        return [], []
    from lgx_ingest.parse.common import table_block

    out: list[Line] = []
    boxes: list[tuple[float, float, float, float]] = []
    try:
        found = page.find_tables()
    except Exception:  # malformed drawing operators: no tables
        return [], []
    for t in found:
        rows = [[normalise_whitespace(_fix_cell(c)) for c in r] for r in t.extract()]
        rows = [r for r in rows if any(r)]
        width = max((len(r) for r in rows), default=0)
        filled = sum(1 for r in rows for c in r if c)
        if len(rows) < 2 or width < 2 or filled < 4:
            continue
        plain, md = table_block(rows)
        x0, top, x1, bottom = (float(v) for v in t.bbox)
        out.append(Line(number, top, bottom, x0, x1, plain, 0.0, kind="table", md=md))
        boxes.append((x0, top, x1, bottom))
    return out, boxes


def _fix_cell(text: str | None) -> str:
    t = (text or "").replace(" .", ".").replace(" ,", ",")
    for k, v in _LIGATURES.items():
        t = t.replace(k, v)
    return t


def _inside(c: dict[str, Any], boxes: Sequence[tuple[float, float, float, float]]) -> bool:
    cx, cy = (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2
    return any(x0 - 1 <= cx <= x1 + 1 and t - 1 <= cy <= b + 1 for x0, t, x1, b in boxes)


def dedupe_chars(chars: Sequence[dict[str, Any]], tolerance: float = 1.0) -> list[dict[str, Any]]:
    """Drop overprinted duplicates (faux bold: the same glyph drawn twice ≤ 1 pt apart).

    Same rule as ``pdfplumber.Page.dedupe_chars`` (text, font, size, position within ``tolerance``) but
    with a grid hash, O(n) instead of the quadratic library version (≈ 1.8 s per page on the CSS PDF).
    """
    grid: dict[tuple[Any, ...], list[tuple[float, float]]] = {}
    out: list[dict[str, Any]] = []
    for c in chars:
        key = (c.get("text"), c.get("fontname"), round(float(c.get("size") or 0), 1))
        x, y = float(c["x0"]), float(c["top"])
        gx, gy = int(x // tolerance), int(y // tolerance)
        dup = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py in grid.get((*key, gx + dx, gy + dy), ()):
                    if abs(px - x) <= tolerance and abs(py - y) <= tolerance:
                        dup = True
                        break
                if dup:
                    break
            if dup:
                break
        if not dup:
            grid.setdefault((*key, gx, gy), []).append((x, y))
            out.append(c)
    return out


def page_from_plumber(page: Any, number: int, *, tables: bool = True) -> Page:
    """Lines of one ``pdfplumber`` page (no document-level cleaning yet)."""
    allc = dedupe_chars(page.chars)
    rotated = sum(1 for c in allc if not c.get("upright", True))
    w, h = float(page.width), float(page.height)
    chars = [
        c
        for c in allc
        if c.get("upright", True)
        and str(c.get("text") or "")
        and c["x0"] >= -1
        and c["x1"] <= w + 1
        and c["top"] >= -1
        and c["bottom"] <= h + 1
    ]
    table_lines, boxes = _table_blocks(page, number) if tables else ([], [])
    if boxes:
        chars = [c for c in chars if not _inside(c, boxes)]
    visible = [c for c in chars if _char_text(c).strip()]
    pg = Page(number, float(page.width), float(page.height), [], len(visible), rotated, len(table_lines))
    if not visible:
        pg.lines = table_lines
        return pg
    body = statistics.mode(round(c["size"], 1) for c in visible)
    built: list[tuple[Line, list[dict[str, Any]]]] = []
    for cl in _merge_small_clusters(_cluster_lines(chars), body):
        line = _build_line(cl.chars, number, cl.top, cl.bottom)
        if line is not None:
            built.append((line, cl.chars))
    lines = _split_margins(built, float(page.height), body)
    lines.extend(table_lines)
    lines.sort(key=lambda ln: (ln.top, ln.x0))
    pg.lines = lines
    return pg


def _build_line(chars: list[dict[str, Any]], number: int, top: float, bottom: float) -> Line | None:
    if not any(_char_text(c).strip() for c in chars):
        return None
    runs, size = _runs(chars)
    return _line_from_runs(runs, size, number, top, bottom) if runs else None


def _split_margins(built: list[tuple[Line, list[dict[str, Any]]]], height: float, body: float) -> list[Line]:
    """Right-margin notes (characters beyond the text column), left-margin alinea numbers, TOC tails."""
    long_x1 = [round(ln.x1) for ln, _ in built if len(ln.text) >= 50]
    col_right: int | None = None
    if long_x1:
        edge, _ = collections.Counter(long_x1).most_common(1)[0]
        support = sum(1 for x in long_x1 if abs(x - edge) <= 1)
        if support >= max(5, 0.4 * len(long_x1)):  # a justified column edge, not ragged text
            col_right = edge
    starts = [ln.words[0][0] for ln, _ in built if abs(ln.size - body) < 0.3 and not _BARE_INT.match(ln.words[0][2])]
    col_left = min(starts) if starts else None
    out: list[Line] = []
    for ln, chars in built:
        in_body = 0.075 * height < ln.top and ln.bottom < 0.925 * height
        if col_right is not None and in_body:
            limit = col_right + 2
            if ln.x0 >= limit and len(ln.text) <= 60:
                ln.kind = "margin"
                ln.notes = [ln.text]
            elif ln.x1 > limit + 1:
                main = [c for c in chars if c["x0"] < limit]
                side = [c for c in chars if c["x0"] >= limit]
                gap = min((c["x0"] for c in side), default=0.0) - max((c["x1"] for c in main), default=0.0)
                rebuilt = _build_line(main, ln.page, ln.top, ln.bottom) if main and gap >= 1.5 else None
                note = _build_line(side, ln.page, ln.top, ln.bottom) if side and rebuilt else None
                if rebuilt is not None and note is not None:
                    rebuilt.markers += note.markers
                    rebuilt.notes = [note.text]
                    ln = rebuilt
        segs = _segments(ln.words, ln.size)
        if len(segs) > 1:
            ln.gap_split = True
            ln.last_seg = normalise_whitespace(segs[-1][2])
        w = ln.words
        if (
            ln.kind == "body"
            and col_left is not None
            and len(w) > 1
            and _BARE_INT.match(w[0][2])
            and w[0][0] < col_left - 4
            and w[1][0] - w[0][1] >= 5.0
        ):
            first, _, rest = ln.text.partition(" ")
            if first == w[0][2] and rest:
                ln.alinea, ln.text, ln.x0 = first, rest, w[1][0]
        ln.words = []
        out.append(ln)
    return out


def _extract_range(data: bytes, start: int, stop: int, tables: bool) -> list[Page]:
    import pdfplumber

    pages: list[Page] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i in range(start, stop):
            p = pdf.pages[i]
            pages.append(page_from_plumber(p, i + 1, tables=tables))
            p.close()
    return pages


_WORKER_DATA: bytes = b""


def _worker_init(data: bytes) -> None:
    global _WORKER_DATA
    _WORKER_DATA = data


def _worker_range(start: int, stop: int, tables: bool) -> list[Page]:
    return _extract_range(_WORKER_DATA, start, stop, tables)


def page_count(data: bytes) -> int:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return len(pdf.pages)


def extract_pages(data: bytes, *, workers: int = 1, tables: bool = True, chunk: int = 40) -> list[Page]:
    """All pages of a PDF, in order. ``workers > 1`` extracts page ranges in parallel *spawned* processes
    (the bytes are sent once per worker; fork is avoided in a multi-threaded parent). Same output either way."""
    n = page_count(data)
    ranges = [(a, min(n, a + chunk)) for a in range(0, n, chunk)]
    if workers <= 1 or len(ranges) <= 1:
        return [p for a, b in ranges for p in _extract_range(data, a, b, tables)]
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_worker_init, initargs=(data,)) as pool:
        futures = [pool.submit(_worker_range, a, b, tables) for a, b in ranges]
        return [p for f in futures for p in f.result()]


# --------------------------------------------------------------------------------- document cleaning
def _key(text: str) -> str:
    return unaccent(normalise_whitespace(text)).lower()


def _digits_key(text: str) -> str:
    return re.sub(r"\d+", "#", _key(text))


@dataclass(slots=True)
class CleanStats:
    pages: int = 0
    pages_without_text: int = 0
    header_lines: int = 0
    footer_lines: int = 0
    footnote_lines: int = 0
    margin_notes: int = 0
    alinea_numbers: int = 0
    markers: int = 0
    rotated_chars: int = 0
    toc_pages: int = 0
    toc_lines: int = 0
    tables: int = 0


def body_size(pages: Iterable[Page]) -> float:
    """Dominant font size of the document (weighted by text length)."""
    sizes: collections.Counter[float] = collections.Counter()
    for p in pages:
        for ln in p.lines:
            if ln.kind == "body" and ln.size:
                sizes[round(ln.size, 1)] += len(ln.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def clean_document(pages: list[Page]) -> tuple[list[Page], CleanStats, float]:
    """Remove running headers/footers and footnote zones, flag TOC pages (in place); return stats."""
    st = CleanStats(pages=len(pages))
    body = body_size(pages)
    exact: collections.Counter[str] = collections.Counter()
    shaped: collections.Counter[str] = collections.Counter()
    for p in pages:
        band = [ln for ln in p.lines if ln.kind == "body" and _in_band(ln, p)]
        exact.update({_key(ln.text) for ln in band})
        shaped.update({_digits_key(ln.text) for ln in band})
    min_shaped = max(3, int(0.3 * len(pages)))
    prev_footnotes = False
    for p in pages:
        st.rotated_chars += p.rotated
        st.tables += p.tables
        if not p.has_text and not p.lines:
            st.pages_without_text += 1
        headers: list[str] = []
        for ln in p.lines:
            if ln.kind != "body" or not _in_band(ln, p):
                continue
            if _PAGE_NO.match(ln.text) or exact[_key(ln.text)] >= 3 or shaped[_digits_key(ln.text)] >= min_shaped:
                if ln.top < p.height / 2:
                    ln.kind = "header"
                    headers.append(ln.text)
                    st.header_lines += 1
                else:
                    ln.kind = "footer"
                    st.footer_lines += 1
        p.header = " | ".join(h for h in headers if not _PAGE_NO.match(h)) or None
        carried = _mark_footnote_continuation(p, body) if prev_footnotes else 0
        p.footnote_lines = carried + _mark_footnotes(p, body)
        prev_footnotes = p.footnote_lines > 0 and _ends_in_footnotes(p)
        st.footnote_lines += p.footnote_lines
        p.is_toc = is_toc_page(p)
        st.toc_pages += p.is_toc
        if not p.is_toc:
            st.toc_lines += _mark_toc_box(p)
        for ln in p.lines:
            st.margin_notes += len(ln.notes)
            st.alinea_numbers += ln.alinea is not None
            st.markers += ln.markers
    return pages, st, body


def _in_band(ln: Line, p: Page) -> bool:
    return ln.bottom <= 0.075 * p.height or ln.top >= 0.925 * p.height


def _ends_in_footnotes(p: Page) -> bool:
    """The last text line of the page belongs to a footnote (footnotes may continue on the next page)."""
    rest = [ln for ln in p.lines if ln.kind in ("body", "footnote", "table")]
    return bool(rest) and rest[-1].kind == "footnote"


def _mark_footnote_continuation(p: Page, body: float) -> int:
    """Footnotes that run over from the previous page: the small-print lines at the top of this page."""
    n = 0
    for ln in p.lines:
        if ln.kind != "body":
            continue
        if not ln.size or ln.size > 0.9 * body:
            break
        ln.kind = "footnote"
        n += 1
    return n


def _mark_footnotes(p: Page, body: float) -> int:
    """The run of small-font lines at the bottom of the body that starts with a footnote marker."""
    lines = [ln for ln in p.lines if ln.kind == "body"]
    while lines and lines[-1].top >= 0.925 * p.height and lines[-1].size > 0.9 * body:
        lines.pop()  # an unrecognised footer line below the footnotes
    if not lines:
        return 0
    k = len(lines)
    while k > 0 and lines[k - 1].size and lines[k - 1].size <= 0.9 * body:
        k -= 1
    zone = lines[k:]
    first = next((i for i, ln in enumerate(zone) if _FOOTNOTE_START.match(ln.text)), None)
    if first is None:
        return 0
    zone = zone[first:]
    if zone[0].top < 0.25 * p.height and len(zone) > 0.8 * len(lines):  # a small-print page (tables, annexes)
        return 0
    for ln in zone:
        ln.kind = "footnote"
    return len(zone)


_TOC_BOX_TITLE = re.compile(r"^(?:sommaire|table des matieres)$")


def _mark_toc_box(p: Page) -> int:
    """A « Sommaire » box on a section title page: the lines after the title are a list of contents."""
    body = [ln for ln in p.lines if ln.kind == "body"]
    start = next((i for i, ln in enumerate(body) if _TOC_BOX_TITLE.match(_key(ln.text))), None)
    if start is None:
        return 0
    rest = body[start:]
    if any(_ARTICLE_LINE.match(ln.text) for ln in rest):  # real text follows on the same page
        return 0
    for ln in rest:
        ln.kind = "toc"
    return len(rest)


_ARTICLE_LINE = re.compile(r"^[«\"“]?\s*Art(?:icle)?\.?\s*(?:[LRAD]\.?\s*)?\d")


def is_toc_page(p: Page) -> bool:
    """Table of contents / « Sommaire »: dot leaders or right-aligned references on most lines."""
    lines = [ln for ln in p.lines if ln.kind == "body"]
    if len(lines) < 4:
        return False
    leaders = sum(1 for ln in lines if _DOT_LEADER.search(ln.text))
    trailing = sum(1 for ln in lines if ln.gap_split and _TRAILING_REF.match(ln.last_seg))
    titled = any(_TOC_TITLE.match(_key(ln.text)) and len(ln.text) <= 40 for ln in lines[:6])
    share = (leaders + trailing) / len(lines)
    return share >= 0.35 or (titled and share >= 0.15)


def body_lines(pages: Sequence[Page]) -> list[Line]:
    """Body and table lines of every non-TOC page, in reading order."""
    return [ln for p in pages if not p.is_toc for ln in p.lines if ln.kind in ("body", "table")]
