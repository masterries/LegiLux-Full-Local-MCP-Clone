"""PDF spot-check against ``pdftotext`` (PLAN §11.4 item 4: ≥ 98 % exact text and numbering on 50 random
articles per PDF code).

For each sampled article unit the reference is ``pdftotext -layout -enc UTF-8`` of its page range. Both
sides are tokenised the same way (whitespace, hyphenation and footnote markers neutralised: the reference
still contains running headers, footnotes and margin notes, ours does not). Per article:

* ``precision`` = share of our tokens found, in order, in the reference (our text is verbatim PDF text);
* ``similarity`` = ``2·M / (|ours| + |reference span|)`` where the span runs, page by page, from the first
  to the last matched reference token (``difflib`` matching blocks) — penalises dropped or invented text
  inside the article, not the footer/footnotes between two of its pages. The reference article **ends at the
  next article heading of the reference** (a line « [n] [«] Art. N. … »): matches beyond it do not count, so an
  article that swallowed its neighbour scores ≈ |own| / |ours|, not 1.0 (M2 review 2026-10-17);
* ``numbering_ok`` = the heading (« Art. 12bis. ») is found at the start of the matched span.

**Heading recall** (:func:`heading_recall`, independent of the parser's own line handling): every article
heading line of ``pdftotext -layout`` of the whole PDF (an optional left-margin alinea number and opening quote
allowed; table-of-contents lines with dot leaders or a trailing page number left out) must start an article
unit numbered alike on that page; headings inside a unit flagged ``contains_quoted_amendment`` (quoted
replacement text) are counted apart. Missing headings are listed with the unit that holds them.

The report gives the mean/median/minimum similarity, the share of articles ≥ 0.98 and the heading recall
(target ≥ 0.99). ``pdftotext`` is a build-side tool (poppler-utils); the check is skipped with a message when it
is missing.
"""

from __future__ import annotations

import collections
import difflib
import html
import random
import re
import shutil
import statistics
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lgx_core.models import Unit, UnitType
from lgx_core.normalise import num_norm

TARGET = 0.98
RECALL_TARGET = 0.99
# an article heading line of the reference: optional margin number, optional quote, « Art. N. » / « Article N »
_REF_HEAD = re.compile(
    r"^\s*(?:\d{1,3}\s{2,})?(?P<q>[«\"“]\s*)?(?P<kw>Art\.|Art(?=\s)|Article)\s*"
    r"(?P<num>(?:[LRAD]\s?\.\s?(?=\d))?(?:\d+(?:\s?(?:er|re|ère))?|[IVXLC]+(?:er)?(?![a-zA-Z])|unique|premier)"
    r"(?:[-–./]\d+[a-z]?)*(?:\s?(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies)(?![a-z])|[A-Z](?![a-zA-Z.]))*)"
    r"\s*[»”\"]?\s*(?:\.(?!\d)|\.(?=\d{1,2}\))|\s[–—-]\s|:|$|(?=\s\())"  # « Art. 1er.1) Sont … » (footnote mark)
)
_HEAD_MARK = "\ue000"  # private-use sentinel: one token of its own, never in a PDF text layer
_TOC_LINE = re.compile(r"(?:\.\s?){4,}|…{2,}|\s{3,}(?:p\.\s*)?\d{1,4}\s*$")
_MARKER = re.compile(r"^\(?\d{1,3}\)$|^\*+\)?$")
_PUNCT = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"', "–": "-", "—": "-", " ": " "})


def tokens(text: str, *, reference: bool = False) -> list[str]:
    """Comparison tokens: punctuation unified, hyphens removed (end-of-line breaks re-joined in the
    reference), footnote markers dropped."""
    t = text.translate(_PUNCT)
    t = re.sub(r"(?<=\d)\s?(?:er|re|ère)\b", "", t)  # « 1er » ~ « 1 » (pdftotext puts superscripts elsewhere)
    if reference:
        t = re.sub(r"(?m)^\s*(?:er|re|ère|e|ème)\s*$", "", t)  # a superscript ordinal on a line of its own
        t = re.sub(r"(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])", "", t)  # « cer-␤tain »
        t = re.sub(r"(?<=[\"a-zà-ÿ])\d{1,2}(?=[\s,.;:)]|$)", "", t)  # footnote markers « … 8 »1 », « dû2 »
    t = t.replace("-", "")
    toks = re.findall(r"\(?\d{1,3}\)|[^\W_]+|[^\w\s]", t)
    return [x for x in toks if not _MARKER.match(x)]


@dataclass(slots=True)
class ArticleCheck:
    anchor: str
    num_raw: str | None
    pages: str
    tokens: int
    precision: float
    similarity: float
    numbering_ok: bool


def check_unit(
    unit: Unit, reference: str | Sequence[str], small: Sequence[collections.Counter[str]] | None = None
) -> ArticleCheck:
    """Compare one unit with the ``pdftotext`` text of its pages (a string, or one string per page).

    The reference span is measured page by page (first to last matched token on each page), so the
    footer, running header and footnotes between two pages of one article do not count; the unit's own
    margin notes (``provenance``, e.g. « L. 27.7.92 ») are removed from the reference first. ``small``: per page,
    the heading keys printed in small type (footnotes quoting other texts) — not article boundaries.
    """
    pages = [reference] if isinstance(reference, str) else list(reference)
    body = unit.text_plain or ""
    notes = [n.strip() for n in (unit.provenance or "").split(";") if n.strip() and n.strip() not in body]
    ref: list[str] = []
    page_of: list[int] = []
    heads: list[tuple[int, str]] = []  # (token index, number key) where a reference article heading line starts
    for i, text in enumerate(pages):
        for note in notes:
            text = text.replace(note, " ")
        lines = text.split("\n")  # mark heading lines, tokenise the page as a whole (hyphenation across lines)
        page_keys = _page_heading_keys(lines, small[i] if small and i < len(small) else None)
        keys = iter([k for k in page_keys if k is not None])
        marked = "\n".join(_HEAD_MARK + ln if k is not None else ln for ln, k in zip(lines, page_keys, strict=True))
        for tok in tokens(marked, reference=True):
            if tok == _HEAD_MARK:
                heads.append((len(ref), next(keys)))
                continue
            ref.append(tok)
            page_of.append(i)
    key = unit.num_norm or (num_norm(unit.num_raw) if unit.num_raw else None)
    # headings inside our own quoted replacement text (« …libellés comme suit: «Art. 8. … » ») do not end it
    inner = (
        collections.Counter(k for ln in (unit.text_plain or "").split("\n") if (k := _head_key(ln)) is not None)
        if unit.contains_quoted_amendment
        else collections.Counter()
    )
    # a title printed above the heading (« V. Présences et mandats ») or a section title joined to the article's own
    # heading (« I. Champ d'application … – Champ d'application »): try the orders the PDF can print them in
    above, _sep, below = (unit.heading or "").partition(" – ")
    orders = [(unit.num_raw, unit.heading), (unit.heading, unit.num_raw)]
    if below:
        orders.append((above, f"{unit.num_raw} {below}"))
    best: tuple[float, float, int, bool, int] | None = None
    for first_part, second_part in orders:
        ours_try = tokens(" ".join(x for x in (first_part, second_part, unit.text_plain) if x))
        scored = _score(ours_try, ref, page_of, heads, key, inner)
        if best is None or scored[0] > best[0]:
            best = (*scored, len(ours_try))
    assert best is not None
    similarity, precision, head_at, own, n_ours = best
    number = [t for t in tokens(unit.num_raw or "") if t.lower() not in ("art", "article", ".")]
    numbering_ok = False
    if number and own:
        window = ref[head_at : head_at + len(number) + 6]  # our number opens the reference article
        it = iter(window)
        numbering_ok = all(any(w == t for w in it) for t in number)
    pages_s = f"{unit.page_from}" + (f"-{unit.page_to}" if unit.page_to and unit.page_to != unit.page_from else "")
    return ArticleCheck(unit.anchor, unit.num_raw, pages_s, n_ours, precision, similarity, numbering_ok)


def _score(
    ours: Sequence[str],
    ref: Sequence[str],
    page_of: Sequence[int],
    heads: Sequence[tuple[int, str]],
    key: str | None,
    inner: collections.Counter[str],
) -> tuple[float, float, int, bool]:
    """(similarity, precision, index of our heading in the reference, our heading found) of one token order."""
    blocks, start, own = _locate(ours, ref, heads, key)
    head_at = start  # the reference heading line of this article
    if own and start > 0:  # matched text just above the heading (a title printed there) belongs to the article
        lead = [b for b in blocks if b.a == 0 and b.b < start <= b.b + b.size + 4]
        if lead:
            start = lead[0].b
    left = collections.Counter(inner)
    end = len(ref)
    for h, k in heads:
        if h <= head_at:
            continue
        if left[k] > 0:
            left[k] -= 1
            continue
        end = h
        break
    matched = 0
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for b in blocks:
        for j in range(b.b, b.b + b.size):
            if not start <= j < end:
                continue
            matched += 1
            pg = page_of[j]
            first.setdefault(pg, j)
            last[pg] = j
    span = sum(last[p] - first[p] + 1 for p in first)
    precision = matched / len(ours) if ours else 0.0
    similarity = 2 * matched / (len(ours) + span) if ours and span else 0.0
    return similarity, precision, head_at, bool(own)


def _locate(
    ours: Sequence[str], ref: Sequence[str], heads: Sequence[tuple[int, str]], key: str | None
) -> tuple[list[Any], int, list[int]]:
    """Matching blocks, and where the reference article starts: the heading with our number nearest to the main
    match (else the last heading before it)."""
    blocks = [b for b in difflib.SequenceMatcher(None, ours, ref, autojunk=False).get_matching_blocks() if b.size]
    main = max(blocks, key=lambda b: b.size).b if blocks else 0
    own = [h for h, k in heads if k == key]
    before = [h for h, _ in heads if h <= main]
    start = min(own, key=lambda h: (h > main, abs(h - main))) if own else (before[-1] if before else 0)
    return blocks, start, own


def pdftotext_pages(pdf: Path, first: int, last: int, *, binary: str = "pdftotext") -> str:
    out = subprocess.run(
        [binary, "-layout", "-enc", "UTF-8", "-f", str(first), "-l", str(last), str(pdf), "-"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out.stdout.decode("utf-8", errors="replace")


def _head_key(line: str) -> str | None:
    """Number key of a reference article heading line (``None`` for any other line, TOC lines included)."""
    m = _REF_HEAD.match(line)
    if m is None or _TOC_LINE.search(line):
        return None
    return num_norm(f"Art. {m['num']}") or None


def _page_heading_keys(lines: Sequence[str], small: collections.Counter[str] | None) -> list[str | None]:
    """Heading key per line of one page; the last ``small[key]`` occurrences of a key (footnotes are printed at
    the bottom) are small print and not headings."""
    keys = [_head_key(ln) for ln in lines]
    if small:
        left = collections.Counter(small)
        for i in range(len(keys) - 1, -1, -1):
            k = keys[i]
            if k is not None and left[k] > 0:
                left[k] -= 1
                keys[i] = None
    return keys


def reference_headings(
    text_by_page: Sequence[str], small: Sequence[collections.Counter[str]] | None = None
) -> list[tuple[int, str, bool]]:
    """(1-based page, number key, quoted) of every article heading line of the reference text (small print —
    footnotes quoting other texts — left out when ``small`` is given)."""
    out: list[tuple[int, str, bool]] = []
    for i, text in enumerate(text_by_page, 1):
        lines = text.split("\n")
        for line, key in zip(lines, _page_heading_keys(lines, small[i - 1] if small else None), strict=True):
            if key is not None:
                m = _REF_HEAD.match(line)
                out.append((i, key, bool(m and m["q"])))
    return out


_BBOX_PAGE = re.compile(r"<page [^>]*>(.*?)</page>", re.S)
_BBOX_LINE = re.compile(r"<line [^>]*>(.*?)</line>", re.S)
_BBOX_WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S)


def small_print_headings(xhtml: str) -> list[collections.Counter[str]]:
    """Per page, the article heading keys set in small type (``pdftotext -bbox-layout``: word height below 92 % of
    the document's dominant word height) — footnotes that quote another text's articles, also on pages that hold
    nothing but footnotes continued from the previous page (CSS p. 73, p. 145)."""
    weights: collections.Counter[float] = collections.Counter()
    pages: list[list[tuple[float, str]]] = []
    for page in _BBOX_PAGE.findall(xhtml):
        lines = []
        for body in _BBOX_LINE.findall(page):
            words = [
                (float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in _BBOX_WORD.findall(body)
            ]
            if not words:
                continue
            heights = sorted(w[3] - w[1] for w in words)
            for w in words:
                weights[round(w[3] - w[1], 1)] += len(w[4])
            gap = len(words) > 1 and words[1][0] - words[0][2] > 4  # a margin number set apart
            text = (words[0][4] + ("   " if gap else " ") + " ".join(w[4] for w in words[1:])).strip()
            lines.append((heights[len(heights) // 2], text))
        pages.append(lines)
    dominant = weights.most_common(1)[0][0] if weights else 0.0
    out: list[collections.Counter[str]] = []
    for lines in pages:
        small: collections.Counter[str] = collections.Counter()
        for h, text in lines:
            key = _head_key(text)
            if key is not None and h < 0.92 * dominant:
                small[key] += 1
        out.append(small)
    return out


def pdftotext_bbox(pdf: Path, *, binary: str = "pdftotext") -> str:
    out = subprocess.run([binary, "-bbox-layout", str(pdf), "-"], check=True, capture_output=True, timeout=900)
    return out.stdout.decode("utf-8", errors="replace")


def heading_recall(
    units: Sequence[Unit], text_by_page: Sequence[str], small: Sequence[collections.Counter[str]] | None = None
) -> dict[str, Any]:
    """Every reference heading must start an article unit with that number on that page (PLAN §11.4, recall).
    Headings on pages that no unit covers (tables of contents, title pages) are counted apart."""
    arts = [u for u in units if u.unit_type is UnitType.ARTICLE and u.page_from]
    starts = {(u.page_from, u.num_norm) for u in arts}
    keys_on_page: dict[int, set[str]] = {}
    for u in arts:
        for k in _keys(u):
            keys_on_page.setdefault(u.page_from or 0, set()).add(k)
    quoted_units = [u for u in arts if u.contains_quoted_amendment]
    covered = {  # pages holding text units (a TOC page is only inside a preamble's page range)
        p
        for u in units
        if u.page_from and u.unit_type in (UnitType.ARTICLE, UnitType.ANNEX, UnitType.PAGE)
        for p in range(u.page_from, (u.page_to or u.page_from) + 1)
    }
    ref = reference_headings(text_by_page, small)
    in_small_print = sum(sum(c.values()) for c in small) if small else 0
    matched = absorbed = uncovered = 0
    missing: list[dict[str, Any]] = []
    for page, key, _quoted in ref:
        if (page, key) in starts or key in keys_on_page.get(page, set()):
            matched += 1
        elif page not in covered:
            uncovered += 1
        elif any((u.page_from or 0) <= page <= (u.page_to or u.page_from or 0) for u in quoted_units):
            absorbed += 1
        else:
            holder = next(
                (u.anchor for u in arts if (u.page_from or 0) <= page <= (u.page_to or u.page_from or 0)), None
            )
            missing.append({"page": page, "article": key, "inside": holder})
    expected = len(ref) - absorbed - uncovered
    by_part: dict[str, list[str]] = {}
    for m in missing:
        part = (m["inside"] or "").split(".", 1)[0] if m["inside"] and "." in m["inside"] else "(code)"
        by_part.setdefault(part, []).append(f"{m['article']} (p. {m['page']})")
    return {
        "reference_headings": len(ref),
        "matched": matched,
        "in_quoted_amendments": absorbed,
        "on_pages_without_units": uncovered,
        "small_print_left_out": in_small_print,
        "missing": len(missing),
        "recall": round(matched / expected, 4) if expected else None,
        "missing_by_part": {k: v[:20] for k, v in sorted(by_part.items(), key=lambda kv: -len(kv[1]))[:30]},
        "missing_examples": missing[:40],
    }


def _keys(u: Unit) -> set[str]:
    """Number keys an article answers to (« Art. 9 et 10. » → 9_et_10, 9, 10; « 1er » → 1)."""
    k = u.num_norm or ""
    out = {k}
    for part in k.replace("_et_", " ").replace("_a_", " ").replace("_", " ").split():
        out.add(part)
    return out


def pdftotext_all(pdf: Path, *, binary: str = "pdftotext") -> list[str]:
    """``pdftotext -layout`` of every page (pages are separated by form feeds)."""
    out = subprocess.run(
        [binary, "-layout", "-enc", "UTF-8", str(pdf), "-"], check=True, capture_output=True, timeout=600
    )
    pages = out.stdout.decode("utf-8", errors="replace").split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


def sample_articles(units: Sequence[Unit], n: int = 50, seed: int = 0) -> list[Unit]:
    """``n`` random non-repealed article units with text and a page range (deterministic for a seed)."""
    pool = [
        u
        for u in units
        if u.unit_type is UnitType.ARTICLE and not u.is_repealed and (u.text_plain or "").strip() and u.page_from
    ]
    rng = random.Random(seed)
    return sorted(rng.sample(pool, min(n, len(pool))), key=lambda u: u.ordinal)


def spot_check(pdf: Path, units: Sequence[Unit], *, n: int = 50, seed: int = 0) -> dict[str, Any]:
    """Compare ``n`` random articles with ``pdftotext`` output; returns the report dict."""
    binary = shutil.which("pdftotext")
    if binary is None:
        return {"ok": None, "skipped": "pdftotext not installed (poppler-utils)"}
    checks: list[ArticleCheck] = []
    all_pages = pdftotext_all(pdf, binary=binary)
    small = small_print_headings(pdftotext_bbox(pdf, binary=binary))
    for u in sample_articles(units, n, seed):
        assert u.page_from is not None
        a, b = u.page_from - 1, u.page_to or u.page_from
        checks.append(check_unit(u, all_pages[a:b], small[a:b]))
    sims = [c.similarity for c in checks]
    report: dict[str, Any] = {
        "pdf": str(pdf),
        "sampled": len(checks),
        "seed": seed,
        "target": TARGET,
        "similarity_mean": round(statistics.fmean(sims), 4) if sims else None,
        "similarity_median": round(statistics.median(sims), 4) if sims else None,
        "similarity_min": round(min(sims), 4) if sims else None,
        "precision_mean": round(statistics.fmean(c.precision for c in checks), 4) if checks else None,
        "share_at_target": round(sum(s >= TARGET for s in sims) / len(sims), 4) if sims else None,
        "numbering_ok": round(sum(c.numbering_ok for c in checks) / len(checks), 4) if checks else None,
        "worst": [asdict(c) for c in sorted(checks, key=lambda c: c.similarity)[:5]],
        "recall_target": RECALL_TARGET,
        "headings": heading_recall(units, all_pages, small),
    }
    recall = report["headings"]["recall"]
    report["ok"] = (
        bool(sims)
        and report["similarity_mean"] >= TARGET
        and report["numbering_ok"] >= TARGET
        and (recall is None or recall >= RECALL_TARGET)
    )
    return report
