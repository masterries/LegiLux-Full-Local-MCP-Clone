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


# ------------------------------------------------------------------------------- M2 review (2026-10-17) fixtures
def test_alinea_number_before_a_heading_does_not_glue_the_article_to_the_previous_one() -> None:
    """CSS p. 72 « 1    Art. 7. Le bénéfice … »: the left-margin alinea number stayed in the line (the running footer
    « Février 2026 » set the column edge), so 173 of the code's articles were glued to their predecessor. Also
    p. 76 (a page whose dominant size is the footnotes') and p. 127 (two layouts on one page)."""
    res = parsed("css2")
    a = arts(res)
    assert {"art_6", "art_7", "art_8", "art_17", "art_18", "art_100", "art_101", "art_102", "art_103"} <= set(a)
    six = a["art_6"].text_plain or ""
    assert "Art. 7" not in six and "Extension de l’assurance" not in six and six.endswith("la Sécurité sociale.")
    assert a["art_7"].heading == "Extension de l’assurance"  # the title printed above belongs to art. 7
    assert (a["art_7"].text_plain or "").startswith("Le bénéfice de l’assurance obligatoire")
    assert a["art_17"].heading == "Prestations de soins de santé"
    assert res.stats["articles"] == res.stats["article_candidates"]


def test_repealed_article_followed_by_the_next_title_is_flagged() -> None:
    a = arts(parsed("css2"))
    assert a["art_22"].is_repealed and a["art_22"].text_plain == "(abrogé)"  # CSS p. 472, loi du 30 juillet 1960
    assert a["art_23"].heading == "Recours contre les décisions du fonds" and not a["art_23"].is_repealed


def test_a_wrapped_bold_title_is_one_member() -> None:
    """CSS p. 569: « …; 5.modification de la » + « loi du 12 septembre 2003 … » and « titre V relatif à … » are lines
    of the title of the loi du 28 novembre 2006, not a member heading and a hierarchy heading."""
    res = parsed("css2")
    parts = [u for u in res.units if u.unit_type is UnitType.PART]
    assert [p.anchor for p in parts] == ["loi_2006-11-28"]
    assert (
        (parts[0].heading or "")
        .rstrip(".")
        .endswith("5.modification de la loi du 12 septembre 2003 relative aux personnes handicapées")
    )
    assert {"loi_2006-11-28.art_1", "loi_2006-11-28.art_2", "loi_2006-11-28.art_3"} <= set(arts(res))
    assert P.capitalised("« Loi du 1er août") and not P.capitalised("loi du 12 septembre 2003")


def test_quoted_amending_provisions_stay_in_the_modifying_article() -> None:
    """Éducation p. 871: « … libellés comme suit: » + the indented « «Art. 8. … » «Art. 14. … » » and art. 63 (2)'s
    replacement « Titre II … Chapitre Ier … Art. 11. … » are text of the amending article (is_modifying, not
    chunked), never articles 8, 11, 14 of the amending law."""
    res = parsed("educ2")
    a = arts(res)
    art59, art63 = a["rgd_1980-09-09.art_59"], a["rgd_1980-09-09.art_63"]  # the excerpt lacks the law's title page
    assert art59.is_modifying and art59.contains_quoted_amendment
    assert "«Art. 8. Le régime professionnel" in (art59.text_plain or "") and "«Art. 14." in (art59.text_plain or "")
    assert art63.is_modifying and "Art. 11. Le cadre du personnel" in (art63.text_plain or "")
    quoted = ("Le régime professionnel comprend", "Le régime de la formation de technicien", "Le cadre du personnel du")
    assert not [u.anchor for u in a.values() if (u.text_plain or "").startswith(quoted)]  # no unit of their own
    assert "rgd_1980-09-09.art_64" in a and not a["rgd_1980-09-09.art_62"].is_modifying
    assert res.stats["quoted_headings"] >= 5 and res.stats["modifying"] == 3
    assert res.stats["articles"] == res.stats["article_candidates"]  # quoted headings are not gate candidates


def test_annex_after_a_repealed_article_is_its_own_unit() -> None:
    res = parsed("educ2")
    a = arts(res)
    assert a["art_20"].is_repealed and a["art_20"].text_plain == "(abrogé par la loi du 13 août 1992)"
    annex = next(u for u in res.units if u.unit_type is UnitType.ANNEX)
    assert (annex.text_plain or "").startswith("Domaines prioritaires de la formation continue")
    assert annex.provenance == "(Loi du 1er août 2019)"


def test_member_titles_of_one_date_and_dash_headings() -> None:
    res = parsed("educ2")
    a = arts(res)
    assert [k for k in a if k.startswith("rgd_1999-05-31.")] == [f"rgd_1999-05-31.art_{i}" for i in range(1, 7)]
    assert "rgd_1999-05-31~2.art_1" in a
    assert a["rgd_1980-09-09.art_7"].heading == "Dissolution" and a["rgd_1999-04-14.art_1"].heading is None


def test_eu_complement_is_an_out_of_scope_part() -> None:
    """Code communal pp. 139–141: « Complément » + « Règlement (CE) N° 1082/2006 … » is an EU text: its own part
    (``ue_1``), never articles 8–18 of the loi du 19 mai 2009 (7 articles), and not chunked."""
    from lgx_ingest.chunk import chunk_units, skip_reason

    res = parsed("communal2")
    a = arts(res)
    assert [k for k in a if k.startswith("loi_2009-05-19.")] == [f"loi_2009-05-19.art_{i}" for i in range(1, 8)]
    eu = next(u for u in res.units if u.anchor == "ue_1")
    assert eu.unit_type is UnitType.PART and (eu.heading or "").startswith("Règlement (CE) N° 1082/2006")
    assert any(k.startswith(P.EU_PART_PREFIX) for k in a) and res.stats["eu_parts"] == 1
    assert all(skip_reason(u) == "out_of_scope" for u in a.values() if u.anchor.startswith("ue_1."))
    chunks = chunk_units(res.units, title_short="Code communal", version_label="v")
    by_ordinal = {u.ordinal: u for u in res.units}
    assert chunks and not any(by_ordinal[c.unit_ordinal].anchor.startswith("ue_") for c in chunks)
    issue = next(i for i in res.issues if i.code == "pdf_out_of_scope_part")
    assert issue.entity_eli.endswith("#ue_1")


def test_part_level_issues_do_not_flag_the_whole_code() -> None:
    """A version-level data-quality row puts W_DATA_QUALITY on every article of the code: PDF part issues name the
    part (``<version>#<anchor>``)."""
    for name in ("css", "route", "css2", "educ2", "communal2"):
        for i in parsed(name).issues:
            if i.code in ("pdf_member_without_articles", "pdf_article_number_repeated", "pdf_out_of_scope_part"):
                assert "#" in i.entity_eli, (name, i)


def test_repeated_numbers_inside_a_member_part() -> None:
    res = parsed("route")
    assert P.repeated_numbers(res.units) == {}
    dup = [u.model_copy(update={"ordinal": 1000 + u.ordinal}) for u in res.units if u.anchor == "loi_1955-02-14.art_2"]
    clone = dup[0].model_copy(update={"anchor": "loi_1955-02-14.art_2b"})
    assert P.repeated_numbers([*res.units, clone]) == {"loi_1955-02-14": ["2"]}
    assert P.EU_PART_PREFIX == __import__("lgx_mcp.annotate", fromlist=["x"]).EU_PART_PREFIX
