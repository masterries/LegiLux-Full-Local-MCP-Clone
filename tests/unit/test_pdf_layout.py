"""PDF layout layer (lgx_ingest.parse.pdf_layout) on synthetic pdfplumber pages: superscripts, glyph
repairs, margin notes, alinea numbers, running headers/footers, footnote zones, TOC pages, dedupe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lgx_ingest.parse import pdf_layout as L

W, H = 595.0, 842.0
REG, BOLD, IT = "AAA+Helvetica", "AAA+Helvetica-Bold", "AAA+Helvetica-Oblique"


def chars(text: str, x: float, top: float, size: float = 8.0, font: str = REG, *, raise_by: float = 0.0) -> list[dict]:
    """Characters of ``text`` laid out left to right (0.5·size per glyph, spaces as glyphs)."""
    out = []
    for ch in text:
        w = 0.5 * size
        out.append(
            {
                "text": ch,
                "x0": x,
                "x1": x + w,
                "top": top - raise_by,
                "bottom": top + size - raise_by,
                "size": size,
                "fontname": font,
                "upright": True,
            }
        )
        x += w
    return out


@dataclass
class FakePage:
    chars: list[dict[str, Any]]
    width: float = W
    height: float = H
    edges: list[Any] = field(default_factory=list)


def page(*parts: list[dict], number: int = 1) -> L.Page:
    return L.page_from_plumber(FakePage([c for p in parts for c in p]), number, tables=False)


def texts(p: L.Page, kind: str = "body") -> list[str]:
    return [ln.text for ln in p.lines if ln.kind == kind]


def test_superscript_ordinal_is_glued_and_marker_dropped() -> None:
    p = page(
        chars("Art. 1", 79, 100),
        chars("er", 79 + 6 * 4, 100, size=5.0, raise_by=3.0),
        chars(".", 79 + 6 * 4 + 5, 100),
        chars("1)", 79 + 6 * 4 + 9, 100, size=5.0, raise_by=3.0),
        chars(" Sont assurés obligatoirement", 79 + 6 * 4 + 13, 100),
    )
    assert texts(p) == ["Art. 1er. Sont assurés obligatoirement"]
    assert p.lines[0].markers == 1


def test_marker_before_punctuation_leaves_no_space() -> None:
    p = page(
        chars("professions libérales ", 79, 100),
        chars("5)", 79 + 22 * 4, 100, 5.0, raise_by=3),
        chars(";", 79 + 22 * 4 + 6, 100),
    )
    assert texts(p) == ["professions libérales;"]


def test_broken_period_glyph_and_ligature_are_repaired() -> None:
    cs = chars("Art", 77, 100)
    cs.append({**cs[-1], "text": " .", "x0": cs[-1]["x1"], "x1": cs[-1]["x1"] + 2})
    cs += chars("ﬁn", cs[-1]["x1"] + 3, 100)
    assert texts(page(cs)) == ["Art. fin"]


def test_rotated_and_off_page_characters_are_dropped() -> None:
    rotated = [{**c, "upright": False} for c in chars("Livre Ier", 566, 300)]
    off = chars("CABARETS", -530, 200)
    p = page(chars("Texte du corps", 79, 100), rotated, off)
    assert texts(p) == ["Texte du corps"] and p.rotated == len("Livre Ier")


def _justified(n: int, top0: float = 100.0) -> list[list[dict]]:
    """``n`` lines ending exactly at x = 400 (a justified column)."""
    line = "x" * 80  # 80 * 4 = 320 points wide from x = 80
    return [chars(line, 80, top0 + 12 * i) for i in range(n)]


def test_right_margin_note_goes_to_notes() -> None:
    lines = _justified(6)
    note = chars("L. 27.7.92", 410, 100)  # same baseline as the first line, beyond the column edge
    p = page(*lines, note)
    first = next(ln for ln in p.lines if ln.top == 100)
    assert first.notes == ["L. 27.7.92"] and "L. 27" not in first.text


def test_ragged_text_never_loses_words_to_the_margin() -> None:
    ragged = [
        chars("Loi du 2 mars 1963 (Mém. A - 13 du 16 mars 1963, p. 179; doc. parl. 773)" + "x" * i, 65, 100 + 12 * i)
        for i in range(6)
    ]
    p = page(*ragged)
    assert all(not ln.notes for ln in p.lines)


def test_left_margin_alinea_number_is_dropped() -> None:
    p = page(chars("1", 65, 100), chars("Art. 1er. Texte", 79, 100), chars("1) les personnes", 79, 112))
    first = p.lines[0]
    assert first.alinea == "1" and first.text == "Art. 1er. Texte"


def _doc(n: int = 4) -> list[L.Page]:
    pages = []
    for i in range(1, n + 1):
        parts = [
            chars("CODE DE LA ROUTE – Loi du 14 février 1955", 215, 33),
            *[chars(f"corps de la page {i} ligne {k}", 80, 100 + 12 * k) for k in range(5)],
            chars("1) Note de bas de page.", 65, 700, size=7.0, font=IT),
            chars(f"- {i} -", 292, 800),
        ]
        pages.append(page(*parts, number=i))
    return pages


def test_running_header_footer_and_footnotes_are_removed() -> None:
    pages, st, body = L.clean_document(_doc())
    assert body == 8.0
    p = pages[0]
    assert p.header == "CODE DE LA ROUTE – Loi du 14 février 1955"
    kinds = {ln.text: ln.kind for ln in p.lines}
    assert kinds["- 1 -"] == "footer" and kinds["1) Note de bas de page."] == "footnote"
    assert st.header_lines == 4 and st.footer_lines == 4 and st.footnote_lines == 4
    assert [ln.text for ln in L.body_lines(pages)][:1] == ["corps de la page 1 ligne 0"]


def test_footnotes_continue_on_the_next_page() -> None:
    p1 = page(
        *[chars(f"ligne {k}", 80, 100 + 12 * k) for k in range(5)],
        chars("2) Début de note", 65, 700, 7.0, IT),
        number=1,
    )
    p2 = page(chars("suite de la note", 79, 70, 7.0, IT), chars("Art. 5. Texte", 79, 100), number=2)
    pages, _, _ = L.clean_document([p1, p2])
    assert [ln.kind for ln in pages[1].lines] == ["footnote", "body"]


def test_toc_pages_are_flagged() -> None:
    toc = page(*[chars(f"Loi du {d} mai 2000 . . . . . . . . . . . {d + 10}", 65, 100 + 12 * d) for d in range(1, 8)])
    sommaire = page(
        chars("SOMMAIRE", 281, 69, 9.0, BOLD),
        *[
            chars(f"Chapitre {k}", 65, 100 + 12 * k) + chars(f"Art. {k} - {k + 3}", 380, 100 + 12 * k)
            for k in range(1, 7)
        ],
    )
    body = page(*[chars(f"Le texte {k} de l'article.", 79, 100 + 12 * k) for k in range(6)])
    for p in (toc, sommaire, body):
        p.is_toc = L.is_toc_page(p)
    assert toc.is_toc and sommaire.is_toc and not body.is_toc


def test_dedupe_chars_drops_faux_bold_overprint() -> None:
    cs = chars("Loi", 79, 212, 14.0, BOLD)
    doubled = cs + [{**c, "x0": c["x0"] + 0.3, "x1": c["x1"] + 0.3} for c in cs]
    assert len(L.dedupe_chars(doubled)) == 3
    assert len(L.dedupe_chars(cs + chars("Loi", 300, 212, 14.0, BOLD))) == 6  # same text elsewhere is kept
