"""PDF parser (lgx_ingest.parse.pdf) — heading matchers, line joining, and the committed CC-BY page excerpts of
the PDF-only code-recueils (tests/fixtures/legilux/pdf/, catalogue excerpts.json)."""

from __future__ import annotations

import collections
import datetime as dt
import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from lgx_core.models import (
    CC_BY_4_0,
    ActFlag,
    DateWindow,
    FileRef,
    ManifestEntry,
    ParseResult,
    RawFile,
    SourceFormat,
    TextBasis,
    UnitType,
    UserFormat,
    VersionKind,
    VersionRole,
    sha256_hex,
)
from lgx_ingest.parse import ParseError, parse_bytes
from lgx_ingest.parse import pdf as P

PDF_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "legilux" / "pdf"
CATALOGUE = json.loads((PDF_DIR / "excerpts.json").read_text(encoding="utf-8"))
MAX_EXCERPT_BYTES = 2_000_000


def excerpt_input(name: str) -> tuple[bytes, ManifestEntry, RawFile]:
    meta = CATALOGUE["excerpts"][name]
    data = (PDF_DIR.parent / meta["path"]).read_bytes()
    entry = ManifestEntry(
        eli_work=meta["eli_work"],
        eli_version=meta["eli_version"],
        kind=VersionKind(meta["kind"]),
        role=VersionRole.CURRENT,
        doc_type=meta["doc_type"],
        text_basis=TextBasis.CONSOLIDATED,
        flags=[ActFlag.PDF_EXTRACTED],
        file=FileRef(
            url=meta["url"],
            file_uri=meta["url"].replace("https://legilux.public.lu", "http://data.legilux.public.lu"),
            user_format=UserFormat.PDF,
            source_format=SourceFormat.PDF,
            licence=meta["licence"],
        ),
        text_valid=DateWindow.model_validate(meta["text_valid"]),
        reason="fixture",
        in_sample=True,
        sample_stage="m2",
    )
    raw = RawFile(
        url=meta["url"],
        sha256=sha256_hex(data),
        bytes=len(data),
        path=meta["path"],
        fetched_at=dt.datetime(2026, 10, 17, tzinfo=dt.UTC),
        eli_version=meta["eli_version"],
        source_format=SourceFormat.PDF,
    )
    return data, entry, raw


@cache
def parsed(name: str, mode: str = "articles") -> ParseResult:
    return parse_bytes(*excerpt_input(name), pdf_mode=mode)


def arts(res: ParseResult) -> dict[str, Any]:
    return {u.anchor: u for u in res.units if u.unit_type is UnitType.ARTICLE}


# ------------------------------------------------------------------------------------------ catalogue
def test_excerpts_are_catalogued_small_and_cc_by() -> None:
    total = 0
    for name, meta in CATALOGUE["excerpts"].items():
        data = (PDF_DIR.parent / meta["path"]).read_bytes()
        assert sha256_hex(data) == meta["sha256"] and len(data) == meta["bytes"], name
        assert meta["licence"] == CC_BY_4_0, f"{name}: only CC BY 4.0 excerpts may be committed"
        assert meta["url"].startswith("https://legilux.public.lu/filestore/eli/etat/leg/"), name
        total += len(data)
    assert total <= MAX_EXCERPT_BYTES
    on_disk = {p.name for p in PDF_DIR.glob("*.pdf")}
    assert on_disk == {Path(m["path"]).name for m in CATALOGUE["excerpts"].values()}


# ------------------------------------------------------------------------------------------ matchers
@pytest.mark.parametrize(
    ("text", "num_raw", "rest"),
    [
        ("Art. 1er. Sont assurés obligatoirement", "Art. 1er.", "Sont assurés obligatoirement"),
        ("Art. L. 121-1.", "Art. L. 121-1.", ""),
        ("« Art. 1bis.", "Art. 1bis.", ""),
        ("«Art. 43».", "Art. 43.", ""),
        ("Article unique.", "Article unique.", ""),
        ("Art. 24-1. Les entreprises", "Art. 24-1.", "Les entreprises"),
        ("Art. 17 à 20.", "Art. 17 à 20.", ""),
        ("Art. IX. et Art. X. (...) (abrogés)", "Art. IX. et Art. X.", "(...) (abrogés)"),
        ("Art. 41 et dernier.", "Art. 41 et dernier.", ""),
        ("Art. 55. (abrogé par la loi du 1er août 2019)", "Art. 55.", "(abrogé par la loi du 1er août 2019)"),
        ("Art 9. Aménagement de la procédure", "Art. 9.", "Aménagement de la procédure"),
    ],
)
def test_match_article(text: str, num_raw: str, rest: str) -> None:
    head = P.match_article(text)
    assert head is not None and head.num_raw == num_raw and head.rest == rest


@pytest.mark.parametrize("text", ["Art. 5 de la loi du 2 mars 1963", "Article 8 Règlement (CE) n° 1082", "Artisan"])
def test_citations_are_not_article_headings(text: str) -> None:
    assert P.match_article(text) is None


def test_match_member() -> None:
    mh = P.match_member("Arrêté grand-ducal du 23 novembre 1955 portant règlement de la circulation,1")
    assert mh is not None and (mh.act_type, mh.date, mh.key) == ("AGD", dt.date(1955, 11, 23), "agd_1955-11-23")
    assert mh.title.endswith("de la circulation")  # the footnote marker « ,1 » is not part of the title
    mod = P.match_member("Loi modifiée du 2 août 2023 concernant le soutien au développement durable")
    assert mod is not None and mod.key == "loi_2023-08-02"
    assert P.match_member("Règlement grand-ducal du 1er octobre 2018 fixant les modalités") is not None
    assert P.match_member("(Loi du 1er août 1971)") is None
    assert P.match_member("Loi du 30 février 2020 sur rien") is None  # invalid date


def test_article_candidates_count_line_starts() -> None:
    lines = ["Art. 1er.", "« Art. 2.", "Article premier", "Art. V.", "Le texte de l'Art. 3", "Artisans"]
    assert P.article_candidates(lines) == 4


def test_join_repairs_hyphenation_with_the_document_vocabulary() -> None:
    vocab = collections.Counter({"vingt-cinq": 3, "certain": 2})
    assert P._join("un cer-", "tain nombre", vocab) == "un certain nombre"
    assert P._join("plus de vingt-", "cinq pour cent", vocab) == "plus de vingt-cinq pour cent"
    assert P._join("au Grand-", "Duché", vocab) == "au Grand-Duché"
    assert P._join("l'article L. 121-", "1 du Code", vocab) == "l'article L. 121-1 du Code"
    assert P._join("les sanctions -", "les peines", vocab) == "les sanctions - les peines"


# ------------------------------------------------------------------------------------------ excerpts
def test_css_excerpt_margin_notes_alineas_footnotes_and_members() -> None:
    res = parsed("css")
    a = arts(res)
    art1 = a["art_1"]
    assert art1.num_raw == "Art. 1er." and (art1.page_from, art1.page_to) == (3, 4)
    assert art1.text_plain is not None and art1.text_plain.startswith("Sont assurés obligatoirement")
    assert art1.provenance is not None and art1.provenance.startswith("L. 27.7.92")  # right-margin notes
    assert "L. 27.7.92" not in art1.text_plain and "L. 15.12.17" not in art1.text_plain  # notes, footnotes
    assert "1) les personnes qui exercent" in art1.text_plain  # list labels kept, superscripts dropped
    parts = [u for u in res.units if u.unit_type is UnitType.PART]
    assert [p.anchor for p in parts] == ["loi_1950-02-25", "rgd_1993-06-21", "rgd_1993-07-13", "rgd_1993-12-23"]
    med = a["rgd_1993-06-21.art_1"]
    assert med.breadcrumb is not None and med.breadcrumb.startswith("Règlement grand-ducal du 21 juin 1993")
    assert "Chapitre I – Désignation du médiateur" in med.breadcrumb
    assert res.stats["toc_pages"] == 2 and res.stats["alinea_numbers"] >= 1  # the two SOMMAIRE pages
    assert res.stats["articles"] == res.stats["article_candidates"] == 14
    codes = {i.code for i in res.issues}
    assert "pdf_member_without_articles" in codes and "pdf_article_gate" not in codes


def test_route_excerpt_member_headings_inserted_articles_and_jurisprudence() -> None:
    res = parsed("route")
    a = arts(res)
    assert {"loi_1955-02-14.art_1", "loi_1955-02-14.art_1bis", "loi_1955-02-14.art_2"} <= set(a)
    loi = next(u for u in res.units if u.anchor == "loi_1955-02-14")
    assert loi.unit_type is UnitType.PART and loi.heading.startswith("Loi du 14 février 1955 concernant")  # type: ignore[union-attr]
    pre = next(u for u in res.units if u.anchor == "loi_1955-02-14.preamble")
    assert pre.text_plain is not None and pre.text_plain.startswith("(Mém. A - 15 du 7 mars 1955, p. 471)")
    assert "modifiée par:" in pre.text_plain
    art1 = a["loi_1955-02-14.art_1"]
    assert art1.provenance is not None and "(Loi du 1er août 1971)" in art1.provenance
    assert "à un certain nombre d’usagers" in (art1.text_plain or "")  # « cer-␤tain » repaired
    assert res.stats["inserted_articles"] >= 1 and res.stats["jurisprudence_paragraphs"] > 0
    assert not any("TA 10-10-07" in (u.text_plain or "") for u in res.units)  # case law is skipped
    assert any(u.anchor == "agd_1955-11-23" for u in res.units)


def test_educ_excerpt_repealed_stubs_sections_and_provenance() -> None:
    res = parsed("educ")
    a = arts(res)
    assert a["art_55"].is_repealed and a["art_55"].repeal_note == "(abrogé par la loi du 1er août 2019)"
    assert not a["art_61bis"].is_repealed and a["art_61bis"].num_raw == "Art. 61bis."
    sections = [u for u in res.units if u.unit_type is UnitType.SECTION]
    assert [s.num_raw for s in sections] == ["Section 17", "Section 18", "Section 19"]
    assert a["art_61"].parent_ordinal == sections[1].ordinal
    assert a["art_63"].provenance is not None and a["art_63"].provenance.startswith("(Loi du 8 juillet 2022)")


def test_communal_excerpt_sommaire_box_is_not_a_member_list() -> None:
    res = parsed("communal")
    parts = [u for u in res.units if u.unit_type is UnitType.PART]
    assert [p.anchor for p in parts] == ["agd_1904-01-16"]  # the « Sommaire » entries are skipped
    assert [u.anchor for u in arts(res).values()][:2] == ["agd_1904-01-16.art_1", "agd_1904-01-16.art_2"]
    assert res.stats["toc_box_lines"] >= 3


def test_secfin_consolidation_has_no_members_and_bold_headings() -> None:
    res = parsed("secfin")
    a = arts(res)
    assert not any(u.unit_type is UnitType.PART for u in res.units)  # not a recueil: no member parts
    assert a["art_5"].heading == "L'administration centrale et l'infrastructure."
    assert a["art_5"].text_plain is not None and a["art_5"].text_plain.startswith("(1) L'agrément est subordonné")
    assert res.stats["toc_pages"] == 1 and "art_24" not in a  # the TOC page lists Art. 24 …: skipped


def test_every_pdf_unit_carries_pages_and_the_pdf_flag() -> None:
    for name in CATALOGUE["excerpts"]:
        res = parsed(name)
        assert res.parser == "pdf" and res.parser_version == P.PARSER_VERSION and res.source_format is SourceFormat.PDF
        for u in res.units:
            assert u.page_from is not None and u.page_to is not None and u.page_from <= u.page_to, (name, u.anchor)
            assert ActFlag.PDF_EXTRACTED in u.flags
        assert len({u.anchor for u in res.units}) == len(res.units)


def test_pages_mode_gives_one_unit_per_member_page() -> None:
    res = parsed("route", "pages")
    pages = [u for u in res.units if u.unit_type is UnitType.PAGE]
    assert pages and res.parser_version == P.PARSER_VERSION + "+pages"
    assert all(u.num_raw == f"p. {u.page_from}" and u.anchor.endswith(f"p.{u.page_from}") for u in pages)
    assert not any(u.unit_type is UnitType.ARTICLE for u in res.units)
    p4 = next(u for u in pages if u.page_from == 4)
    assert p4.text_plain is not None and "Art. 1er." in p4.text_plain


def test_page_fallback_for_a_section_without_articles() -> None:
    data, entry, raw = excerpt_input("route")
    pages = P.extract_pages(data)
    for p in pages:  # remove every article heading: the member sections must fall back to page units
        p.lines = [ln for ln in p.lines if not P.match_article(ln.text)]
    res = P.parse_pages(pages, entry, raw)
    page_units = [u for u in res.units if u.unit_type is UnitType.PAGE]
    assert page_units and res.stats["page_fallback_sections"] >= 1
    assert all(u.anchor.startswith("loi_1955-02-14.p.") for u in page_units)


def test_not_a_pdf_and_scanned_pages_fail_clearly() -> None:
    data, entry, raw = excerpt_input("educ")
    with pytest.raises(ParseError, match="not a PDF"):
        P.parse_pdf(b"<html/>", entry, raw)
    pages = P.extract_pages(data)
    for p in pages:
        p.lines, p.chars = [], 0
    with pytest.raises(ParseError, match="no page has a text layer"):
        P.parse_pages(pages, entry, raw)


def test_parallel_extraction_is_identical() -> None:
    data, _entry, _raw = excerpt_input("css")
    one = P.extract_pages(data, workers=1, chunk=3)
    two = P.extract_pages(data, workers=2, chunk=3)
    assert [[ln.text for ln in p.lines] for p in one] == [[ln.text for ln in p.lines] for p in two]
