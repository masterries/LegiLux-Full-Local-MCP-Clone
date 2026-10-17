"""PDF spot-check against ``pdftotext`` (PLAN §11.4 item 4: ≥ 98 % exact text and numbering on 50 random
articles per PDF code).

For each sampled article unit the reference is ``pdftotext -layout -enc UTF-8`` of its page range. Both
sides are tokenised the same way (whitespace, hyphenation and footnote markers neutralised: the reference
still contains running headers, footnotes and margin notes, ours does not). Per article:

* ``precision`` = share of our tokens found, in order, in the reference (our text is verbatim PDF text);
* ``similarity`` = ``2·M / (|ours| + |reference span|)`` where the span runs, page by page, from the first
  to the last matched reference token (``difflib`` matching blocks) — penalises dropped or invented text
  inside the article, not the footer/footnotes between two of its pages;
* ``numbering_ok`` = the heading (« Art. 12bis. ») is found at the start of the matched span.

The report gives the mean/median/minimum similarity and the share of articles ≥ 0.98. ``pdftotext`` is a
build-side tool (poppler-utils); the check is skipped with a message when it is missing.
"""

from __future__ import annotations

import difflib
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

TARGET = 0.98
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


def check_unit(unit: Unit, reference: str | Sequence[str]) -> ArticleCheck:
    """Compare one unit with the ``pdftotext`` text of its pages (a string, or one string per page).

    The reference span is measured page by page (first to last matched token on each page), so the
    footer, running header and footnotes between two pages of one article do not count; the unit's own
    margin notes (``provenance``, e.g. « L. 27.7.92 ») are removed from the reference first.
    """
    pages = [reference] if isinstance(reference, str) else list(reference)
    body = unit.text_plain or ""
    notes = [n.strip() for n in (unit.provenance or "").split(";") if n.strip() and n.strip() not in body]
    ref_pages: list[list[str]] = []
    for text in pages:
        for note in notes:
            text = text.replace(note, " ")
        ref_pages.append(tokens(text, reference=True))
    ref = [t for p in ref_pages for t in p]
    page_of = [i for i, p in enumerate(ref_pages) for _ in p]
    ours_text = " ".join(x for x in (unit.num_raw, unit.heading, unit.text_plain) if x)
    ours = tokens(ours_text)
    sm = difflib.SequenceMatcher(None, ours, ref, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    matched = sum(b.size for b in blocks)
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for b in blocks:
        for j in range(b.b, b.b + b.size):
            pg = page_of[j]
            first.setdefault(pg, j)
            last[pg] = j
    span = sum(last[p] - first[p] + 1 for p in first)
    precision = matched / len(ours) if ours else 0.0
    similarity = 2 * matched / (len(ours) + span) if ours and span else 0.0
    number = [t for t in tokens(unit.num_raw or "") if t.lower() not in ("art", "article", ".")]
    numbering_ok = False
    if number and blocks:
        start = blocks[0].b
        window = ref[max(0, start - 3) : start + len(number) + 6]
        it = iter(window)
        numbering_ok = all(any(w == t for w in it) for t in number)
    pages_s = f"{unit.page_from}" + (f"-{unit.page_to}" if unit.page_to and unit.page_to != unit.page_from else "")
    return ArticleCheck(unit.anchor, unit.num_raw, pages_s, len(ours), precision, similarity, numbering_ok)


def pdftotext_pages(pdf: Path, first: int, last: int, *, binary: str = "pdftotext") -> str:
    out = subprocess.run(
        [binary, "-layout", "-enc", "UTF-8", "-f", str(first), "-l", str(last), str(pdf), "-"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out.stdout.decode("utf-8", errors="replace")


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
    cache: dict[int, str] = {}
    for u in sample_articles(units, n, seed):
        assert u.page_from is not None
        texts = []
        for page in range(u.page_from, (u.page_to or u.page_from) + 1):
            if page not in cache:
                cache[page] = pdftotext_pages(pdf, page, page, binary=binary)
            texts.append(cache[page])
        checks.append(check_unit(u, texts))
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
    }
    report["ok"] = bool(sims) and report["similarity_mean"] >= TARGET and report["numbering_ok"] >= TARGET
    return report
