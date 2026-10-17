"""G1 items for the PDF code-recueils from an **independent** source (M2 review, 2026-10-17).

``gen_g1`` derives its expectations from the release database, i.e. from the PDF parser's own segmentation: it could
not see that 173 articles of the Code de la sécurité sociale were glued to their predecessor. These items come from
``pdftotext`` alone (the oracle of :mod:`lgx_ingest.parse.pdf_check`: article heading lines, with an optional margin
number, small-print footnotes left out) and from the PDFs' own running headers and member titles; the member act
ELIs come from ``config/recueil_members.yaml`` (matched by title prefix). No database and no parser output is read.

* **CSS own articles** (pp. 69–247, the code before its « LOIS » section): « art. N du Code de la sécurité sociale »
  → ``resolved``, act ``loi/1925/12/17/n1``, article N on that page;
* **numbers printed only in the members' part** (pp. ≥ 249) → ``not_found`` (the code has no such article: the
  resolver must not answer with a member's article, PLAN §8.3 « never guess »);
* **route members** by running header (« CODE DE LA ROUTE – Arrêté grand-ducal du 23 novembre 1955 »): « article N de
  l'arrêté grand-ducal du 23 novembre 1955 … » → ``resolved`` to the member act, article N on that page (the code's
  copy: the member is routed);
* **communal**, the loi du 19 mai 2009 (between its title and the « Complément » EU regulation): its articles 1–7
  → ``resolved``; article 8 → ``not_found`` (articles 1–18 of Regulation (CE) 1082/2006 are not that law's).

Expectations carry ``article`` (number key) and ``page`` (1-based PDF page) instead of an article ELI (PDF units have
none). Left out: « JURISPRUDENCE » sections (case-law digests quote « Article 11bis » headings; skipped until the
running header changes, like the parser) and headings with a glued footnote marker (« Art. 14.³ », a superscript word
in ``pdftotext -bbox-layout``). Deterministic (md5 order). Every item is ``draft_unvalidated``.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from lgx_core.normalise import unaccent
from lgx_ingest.parse.pdf_check import (
    _BBOX_LINE,
    _BBOX_PAGE,
    _BBOX_WORD,
    _REF_HEAD,
    _head_key,
    pdftotext_all,
    pdftotext_bbox,
    reference_headings,
    small_print_headings,
)

ELI = "http://data.legilux.public.lu/eli/etat/leg/"
ROOT = Path(__file__).resolve().parents[2]
LARGE = ROOT / "data" / "fixtures-large"
PDFS = {
    "css": LARGE / "eli-etat-leg-code-securite_sociale-20260101-fr-pdf.pdf",
    "route": LARGE / "eli-etat-leg-code-route-20260612-fr-pdf.pdf",
    "communal": LARGE / "eli-etat-leg-code-communal-20260801-fr-pdf.pdf",
}
CSS_OWN = (69, 247)  # the code itself (Livres I–VI); the members (« LOIS ») start on p. 249
AGD_1955 = (
    "de l'arrêté grand-ducal du 23 novembre 1955 portant règlement de la circulation sur toutes les voies publiques"
)
LOI_1955 = "de la loi du 14 février 1955 concernant la réglementation de la circulation sur toutes les voies publiques"
ROUTE_MEMBERS = [  # running header -> member title as cited
    ("Arrêté grand-ducal du 23 novembre 1955", AGD_1955),
    ("Loi du 14 février 1955", LOI_1955),
]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # sampling order, not security


def _num(m: re.Match[str]) -> str:
    """The number as printed (« 1er », « 74bis », « L. 121-6 »), for the citation text."""
    return " ".join(m["num"].split()).rstrip(".")


_JURIS = re.compile(r"(?mi)^\s*jurisprudences?\s*$")


def cut_jurisprudence(pages: Sequence[str]) -> list[str]:
    """Page texts without their « JURISPRUDENCE » sections (from the marker until the running header changes)."""
    out: list[str] = []
    header, juris = None, False
    for text in pages:
        top = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
        if top != header:
            header, juris = top, False
        if juris:
            out.append("")
            continue
        m = _JURIS.search(text)
        if m is not None:
            juris = True
            text = text[: m.start()]
        out.append(text)
    return out


def superscripted(xhtml: str) -> list[set[str]]:
    """Per page, the heading keys whose line ends in a superscript word (a footnote marker: « Art. 14.³ » reads
    « Art. 14.3 » in the layout text)."""
    out: list[set[str]] = []
    for page in _BBOX_PAGE.findall(xhtml):
        keys: set[str] = set()
        for body in _BBOX_LINE.findall(page):
            words = [(float(b), float(d), html.unescape(t)) for _a, b, _c, d, t in _BBOX_WORD.findall(body)]
            if len(words) < 2 or not words[0][2].startswith("Art"):
                continue
            head = words[0][1] - words[0][0]
            if words[-1][1] - words[-1][0] < 0.75 * head:
                key = _head_key(" ".join(w[2] for w in words[:-1]) + words[-1][2])  # as the layout text glues it
                if key is not None:
                    keys.add(key)
        out.append(keys)
    return out


def _headings(
    pages: Sequence[str], small: Sequence[Any], first: int, last: int, marked: Sequence[set[str]] = ()
) -> list[tuple[int, str, str]]:
    """(page, key, printed number) of every non-quoted reference heading on pages ``first..last``."""
    out = []
    for page, key, quoted in reference_headings(pages[first - 1 : last], small[first - 1 : last]):
        if quoted:
            continue
        p = page + first - 1
        if p - 1 < len(marked) and key in marked[p - 1]:
            continue
        line = next(ln for ln in pages[p - 1].split("\n") if _head_key(ln) == key and _REF_HEAD.match(ln))
        m = _REF_HEAD.match(line)
        assert m is not None
        out.append((p, key, _num(m)))
    return out


def _member_eli(title_prefix: str, members: Sequence[dict[str, Any]]) -> str | None:
    want = unaccent(title_prefix).lower()
    hits = {m["member"] for m in members if unaccent(m.get("part_heading") or "").lower().startswith(want)}
    return ELI + hits.pop().split("etat/leg/", 1)[1] if len(hits) == 1 else None


def css_items(
    pages: Sequence[str], small: Sequence[Any], n_own: int = 30, n_missing: int = 6, marked: Sequence[set[str]] = ()
) -> list[dict[str, Any]]:
    own: dict[str, tuple[int, str]] = {}
    for p, key, num in _headings(pages, small, *CSS_OWN, marked):
        own.setdefault(key, (p, num))
    members = {key: num for _p, key, num in _headings(pages, small, CSS_OWN[1] + 2, len(pages), marked)}
    items = []
    for key in sorted(own, key=_md5)[:n_own]:
        page, num = own[key]
        items.append(
            {
                "text": f"art. {num} du Code de la sécurité sociale",
                "style": "pdf:css_own",
                "expect": {"status": "resolved", "act_eli": ELI + "loi/1925/12/17/n1", "article": key, "page": page},
            }
        )
    only_members = [k for k in members if k not in own and re.fullmatch(r"\d+[a-z]*", k)]
    for key in sorted(only_members, key=_md5)[:n_missing]:
        items.append(
            {
                "text": f"art. {members[key]} du Code de la sécurité sociale",
                "style": "pdf:css_member_only_number",
                "expect": {"status": "not_found"},
            }
        )
    return items


def route_items(
    pages: Sequence[str],
    small: Sequence[Any],
    members: Sequence[dict[str, Any]],
    n: int = 8,
    marked: Sequence[set[str]] = (),
) -> list[dict[str, Any]]:
    items = []
    for header, cited in ROUTE_MEMBERS:
        eli = _member_eli(header, members)
        if eli is None:
            continue
        found: dict[str, tuple[int, str]] = {}
        for i, text in enumerate(pages, 1):
            top = "\n".join(text.split("\n")[:3])
            if f"CODE DE LA ROUTE – {header}" not in top:
                continue
            for p, key, num in _headings(pages, small, i, i, marked):
                found.setdefault(key, (p, num))
        for key in sorted(found, key=_md5)[:n]:
            page, num = found[key]
            items.append(
                {
                    "text": f"article {num} {cited}",
                    "style": "pdf:route_member",
                    "expect": {"status": "resolved", "act_eli": eli, "article": key, "page": page},
                }
            )
    return items


def communal_items(
    pages: Sequence[str], small: Sequence[Any], members: Sequence[dict[str, Any]], marked: Sequence[set[str]] = ()
) -> list[dict]:
    title = "Loi du 19 mai 2009 portant diverses mesures d’application du règlement (CE) n° 1082/2006"
    eli = _member_eli("Loi du 19 mai 2009", members)
    start = None
    for i, text in enumerate(pages, 1):  # the title followed by its Mémorial reference (not a table-of-contents line)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        at = next((j for j, ln in enumerate(lines) if ln.startswith(title)), None)
        if at is not None and any(ln.startswith("(Mém. A") for ln in lines[at + 1 : at + 5]):
            start = i
            break
    if eli is None or start is None:
        return []
    end = next(i for i in range(start, len(pages) + 1) if re.search(r"(?m)^\s*Compl[ée]ment\s*$", pages[i - 1]))
    cited = "de la loi du 19 mai 2009 portant diverses mesures d'application du règlement (CE) n° 1082/2006"
    heads = _headings(pages, small, start, end - 1, marked)
    items = [
        {
            "text": f"article {num} {cited}",
            "style": "pdf:communal_member",
            "expect": {"status": "resolved", "act_eli": eli, "article": key, "page": p},
        }
        for p, key, num in heads
    ]
    last = max(int(k) for _p, k, _n in heads if k.isdigit())
    items.append(
        {
            "text": f"article {last + 1} {cited}",
            "style": "pdf:member_number_of_an_eu_complement",
            "expect": {"status": "not_found"},
        }
    )
    return items


def generate(members_yaml: Path = ROOT / "config" / "recueil_members.yaml") -> tuple[list[dict], dict[str, Any]]:
    members = (yaml.safe_load(members_yaml.read_text(encoding="utf-8")) or {}).get("members") or []
    items: list[dict[str, Any]] = []
    for name, path in PDFS.items():
        if not path.exists():
            raise FileNotFoundError(f"{path}: run scripts/fetch_fixtures.sh {name}_pdf")
        pages = cut_jurisprudence(pdftotext_all(path))
        bbox = pdftotext_bbox(path)
        small, marked = small_print_headings(bbox), superscripted(bbox)
        if name == "css":
            items += css_items(pages, small, marked=marked)
        elif name == "route":
            items += route_items(pages, small, members, marked=marked)
        else:
            items += communal_items(pages, small, members, marked=marked)
    for i, it in enumerate(items, 1):
        it["id"] = f"G1-P{i:03d}"
    items = [{"id": it.pop("id"), **it} for it in items]
    meta = {
        "release": "pdftotext of the cached sample PDFs (snapshot 2026-10-16)",
        "status": "draft_unvalidated",
        "note": "PDF code-recueil citations from an independent source (pdftotext headings, running headers, member "
        "titles; eval/harness/gen_g1_pdf.py) — not from the parser or the release database (M2 review)",
    }
    return items, meta
