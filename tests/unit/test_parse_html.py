"""HTML parser tests: the CC-BY HTML path must give the same units as the AKN XML (HTML-twin oracle)."""

from __future__ import annotations

import difflib

import pytest

from lgx_core.models import UnitType
from lgx_ingest.parse import ParseError, parse_bytes

from .test_parse_fixtures import fixture_input, parsed, parsed_large

E = "http://data.legilux.public.lu/eli/etat/leg/"


def arts(res) -> dict:  # type: ignore[no-untyped-def]
    return {u.anchor: u for u in res.units if u.unit_type is UnitType.ARTICLE}


def twin(xml_res, html_res, *, min_similarity: float) -> None:  # type: ignore[no-untyped-def]
    a, b = arts(xml_res), arts(html_res)
    assert list(a) == list(b), "same article anchors in the same order"
    for k in a:
        assert a[k].num_raw == b[k].num_raw, k
        assert a[k].is_repealed == b[k].is_repealed, k
        assert a[k].is_modifying == b[k].is_modifying, k
        ta, tb = a[k].text_plain or "", b[k].text_plain or ""
        sim = difflib.SequenceMatcher(None, ta.split(), tb.split(), autojunk=False).ratio() if ta != tb else 1.0
        assert sim >= min_similarity, (k, sim)


def test_a88_html_equals_xml() -> None:
    twin(parsed("a88_jo"), parsed("a88_html"), min_similarity=1.0)
    h = parsed("a88_html")
    assert [u.num_raw for u in h.units if u.unit_type is UnitType.ANNEX] == ["ANNEXE I", "ANNEXE II"]
    a1 = arts(h)["art_1er"]
    assert a1.heading == "Définitions" and a1.eli_unit == E + "loi/2026/02/25/a88/art_1er" and a1.eli_unit_derived
    pre = [u for u in h.units if u.unit_type is UnitType.PREAMBLE]
    assert len(pre) == 1 and pre[0].text_plain.startswith("Nous Guillaume")  # type: ignore[union-attr]
    texts = "\n".join(u.text_plain or "" for u in h.units)
    for noise in ("Le contenu de la version HTML", "Mandons et ordonnons", "legislationLegalForce", "highlightMod"):
        assert noise not in texts


def test_aifm_html_twin_hidden_widgets_and_deleted_rows() -> None:
    xml, html = parsed("aifm_conso"), parsed("aifm_conso_html")
    twin(xml, html, min_similarity=0.98)
    a = arts(html)
    assert len(a) == 229
    assert a["art_213"].is_repealed and a["art_213"].text_plain is None  # div style="display: none;del"
    assert sum(1 for u in a.values() if u.is_modifying) == 85  # table.richtext_embeddedStructurequote
    texts = "\n".join(u.text_plain or "" for u in html.units)
    assert "(L du 15 mars 2016)" not in texts  # hidden « ref_text » spans
    assert "Modifications" not in texts  # hidden badge


def test_constitution_html_twin() -> None:
    twin(parsed("constitution"), parsed("constitution_html"), min_similarity=1.0)
    assert not [u for u in parsed("constitution_html").units if u.unit_type is UnitType.PREAMBLE]


@pytest.mark.parametrize(
    ("name", "n_articles"),
    [
        ("l1978_jo_html", 14),
        ("n5_jo_html", 30),
        ("n3_rgd_jo_html", 14),
        ("agd1939_jo_html", 10),
        ("rgd1989_jo_html", 9),
    ],
)
def test_cc_by_html_jo_originals(name: str, n_articles: int) -> None:
    res = parsed(name)
    a = arts(res)
    assert len(a) == n_articles
    work = res.eli_work
    assert all(u.eli_unit == f"{work}/{u.anchor}" and u.eli_unit_derived for u in a.values())
    assert all(u.text_plain for u in a.values() if not u.is_repealed)
    assert [u.ordinal for u in res.units] == list(range(1, len(res.units) + 1))
    texts = "\n".join(u.text_plain or "" for u in res.units)
    assert "​" not in texts and "onmouseover" not in texts


def test_l1978_html_text() -> None:
    a = arts(parsed("l1978_jo_html"))
    assert a["art_1er"].num_raw == "Art. 1er." and a["art_1er"].num_norm == "1"


def test_html_quoted_amendments_are_modifying() -> None:
    a = arts(parsed("rgd1989_jo_html"))
    assert sum(1 for u in a.values() if u.is_modifying) == 4


def test_not_legilux_html_raises() -> None:
    _d, entry, raw = fixture_input("a88_html")
    with pytest.raises(ParseError):
        parse_bytes(b"<html><body><app-root></app-root></body></html>", entry, raw)


@pytest.mark.large
@pytest.mark.parametrize(("xml", "html"), [("code_travail", "code_travail_html"), ("code_civil", "code_civil_html")])
def test_code_html_twins(xml: str, html: str) -> None:
    twin(parsed_large(xml), parsed_large(html), min_similarity=0.99)
