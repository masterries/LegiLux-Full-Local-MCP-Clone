"""PDF spot-check against pdftotext (lgx_ingest.parse.pdf_check, PLAN §11.4 item 4) and the large full-PDF
gates (opt-in: the full codes take ≈ 1 min each to parse)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from lgx_core.models import CC_BY_4_0, SourceFormat, TextBasis, Unit, UnitType
from lgx_ingest.parse import pdf_check as C
from unit.test_pdf_parse import CATALOGUE, PDF_DIR, parsed

E = "http://data.legilux.public.lu/eli/etat/leg/code/x"


def unit(num: str, text: str, *, provenance: str | None = None, pages: tuple[int, int] = (1, 1)) -> Unit:
    return Unit(
        eli_work=E,
        eli_version=E + "/20260101",
        ordinal=1,
        depth=0,
        unit_type=UnitType.ARTICLE,
        anchor="art_1",
        num_raw=num,
        text_plain=text,
        provenance=provenance,
        page_from=pages[0],
        page_to=pages[1],
        doc_type="CODE_RECUEIL",
        source_format=SourceFormat.PDF,
        source_url="https://legilux.public.lu/filestore/eli/etat/leg/code/x.pdf",
        source_sha256="0" * 64,
        licence=CC_BY_4_0,
        text_basis=TextBasis.CONSOLIDATED,
    )


def test_tokens_neutralise_hyphenation_markers_and_quotes() -> None:
    assert C.tokens("à un cer-\ntain nombre « usagers »1, p.", reference=True) == C.tokens(
        "à un certain nombre « usagers », p."
    )
    assert C.tokens("l’article 1) 2) Texte") == C.tokens("l'article Texte")


def test_check_unit_identical_text_scores_one() -> None:
    ref = "CODE DE LA ROUTE – Loi\n   Art. 2.\n  Le ministre délivre les per-\nmis de conduire.\n - 13 -"
    c = C.check_unit(unit("Art. 2.", "Le ministre délivre les permis de conduire."), ref)
    assert c.precision == 1.0 and c.similarity == 1.0 and c.numbering_ok


def test_check_unit_detects_dropped_and_invented_text() -> None:
    ref = "Art. 3. Les parties sont convoquées aux jour et heure fixés par le médiateur et comparaissent en personne."
    dropped = C.check_unit(unit("Art. 3.", "Les parties sont convoquées et comparaissent en personne."), ref)
    invented = C.check_unit(unit("Art. 3.", "Les parties sont convoquées par le ministre de la Justice seul."), ref)
    assert dropped.similarity < 0.9 and invented.precision < 0.8
    wrong_number = C.check_unit(unit("Art. 4.", "Les parties sont convoquées aux jour et heure fixés"), ref)
    assert not wrong_number.numbering_ok


def test_page_breaks_and_margin_notes_are_not_penalised() -> None:
    page1 = "   Art. 1er. Sont assurés obligatoirement            L. 27.7.92\n 1) Note de bas de page longue.\n 69"
    page2 = "Février 2026\n les personnes qui exercent une activité."
    u = unit(
        "Art. 1er.",
        "Sont assurés obligatoirement les personnes qui exercent une activité.",
        provenance="L. 27.7.92",
        pages=(1, 2),
    )
    c = C.check_unit(u, [page1, page2])
    assert c.similarity == 1.0 and c.precision == 1.0


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext (poppler-utils) not installed")
def test_spot_check_on_the_committed_excerpts() -> None:
    for name in ("css", "route", "educ", "secfin"):
        res = parsed(name)
        report = C.spot_check(PDF_DIR.parent / CATALOGUE["excerpts"][name]["path"], res.units, n=50, seed=1)
        assert report["sampled"] > 0, name
        assert report["precision_mean"] >= 0.99, (name, report["worst"])
        assert report["numbering_ok"] >= 0.98 and report["similarity_mean"] >= C.TARGET, (name, report["worst"])


# ----------------------------------------------------------------------------- full PDFs (opt-in, large)
FULL = os.environ.get("LGX_TEST_PDF_FULL") in ("1", "true", "yes")


@pytest.mark.large
@pytest.mark.skipif(not FULL, reason="full PDF codes: set LGX_TEST_PDF_FULL=1 (≈ 1 min per code)")
@pytest.mark.parametrize("name", ["css_pdf", "route_pdf", "communal_pdf", "educ_pdf", "secfin_pdf"])
def test_full_pdf_article_gate_and_spot_check(name: str) -> None:
    from lgx_core.models import sha256_hex
    from lgx_ingest.parse import pdf as P
    from unit.test_parse_fixtures import CATALOGUE as FIX
    from unit.test_parse_fixtures import LARGE_DIR

    meta = FIX["large"][name]
    path = LARGE_DIR / meta["path"]
    if not path.exists() or sha256_hex(path.read_bytes()) != meta["sha256"]:
        pytest.skip(f"large fixture {name} absent or changed: run scripts/fetch_fixtures.sh {name}")
    from unit.test_pdf_parse import excerpt_input

    key = name.removesuffix("_pdf")
    _d, entry, raw = excerpt_input(key)
    data = path.read_bytes()
    raw = raw.model_copy(update={"sha256": meta["sha256"], "bytes": len(data)})
    res = P.parse_pdf(data, entry, raw, workers=min(4, os.cpu_count() or 1))
    cand, arts = res.stats["article_candidates"], res.stats["articles"]
    assert abs(arts - cand) <= P.ARTICLE_GATE_TOLERANCE * cand, (arts, cand)
    report = C.spot_check(Path(path), res.units, n=50, seed=0)
    assert report["ok"], report


def test_excerpt_catalogue_names_large_sources() -> None:
    from unit.test_parse_fixtures import CATALOGUE as FIX

    for meta in CATALOGUE["excerpts"].values():
        src = FIX["large"][meta["derived_from"]]
        assert src["url"] == meta["url"] and src["licence"] == CC_BY_4_0 and src["source_format"] == "pdf"
