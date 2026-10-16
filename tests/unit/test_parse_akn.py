"""AKN parser tests on committed CC-BY fixtures (+ large code fixtures, skipped when absent).

Acceptance numbers (PLAN §11.4 / §13 M1) verified on the real files of the 2026-10-16 snapshot.
"""

from __future__ import annotations

import re

import pytest
from lxml import etree

from lgx_core.models import RelationSource, RelType, UnitType
from lgx_ingest.chunk import chunk_units
from lgx_ingest.parse import ParseError, parse_bytes
from lgx_ingest.parse.akn import iter_article_ids, parse_akn
from lgx_ingest.parse.recueil import is_generated_id, member_work_of_wid, normalise_wid

from .test_parse_fixtures import fixture_input, large_input, parsed, parsed_large

AKN = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13}"
E = "http://data.legilux.public.lu/eli/etat/leg/"


def articles(name: str) -> dict[str, object]:
    return {u.anchor: u for u in parsed(name).units if u.unit_type is UnitType.ARTICLE}


def deleted_fragments(data: bytes) -> list[str]:
    """Texts of <del>/style="del" elements that do not also occur as live text elsewhere."""
    root = etree.fromstring(data, etree.XMLParser(huge_tree=True))
    out = []
    for el in root.iter():
        if isinstance(el.tag, str) and (el.tag == AKN + "del" or el.get("style") == "del"):
            text = " ".join("".join(el.itertext()).split())
            if len(text) > 25:
                out.append(text)
    for el in list(root.iter()):
        if (
            isinstance(el.tag, str)
            and (el.tag == AKN + "del" or el.get("style") == "del")
            and el.getparent() is not None
        ):
            el.getparent().remove(el)
    live = " ".join("".join(root.itertext()).split())
    return [d for d in out if d not in live]


# ------------------------------------------------------------------------------------ invariants
@pytest.mark.parametrize(
    "name", ["a686_conso", "aifm_conso", "constitution", "a88_jo", "n25_jo", "copyright_conso", "a203_jo"]
)
def test_unit_invariants(name: str) -> None:
    data, entry, raw = fixture_input(name)
    res = parsed(name)
    units = res.units
    assert [u.ordinal for u in units] == list(range(1, len(units) + 1))
    assert all(u.parent_ordinal is None or u.parent_ordinal < u.ordinal for u in units)
    assert len({u.anchor for u in units}) == len(units)
    assert all(u.eli_version == entry.eli_version and u.eli_work == entry.eli_work for u in units)
    assert all(u.source_sha256 == raw.sha256 and u.licence == entry.file.licence for u in units)  # type: ignore[union-attr]
    # article count == <article id> count outside embedded structures (PLAN §11.4 invariant 3)
    assert res.stats["articles"] == len(iter_article_ids(data))
    # no <del> text in text_plain (invariant 2)
    texts = "\n".join(u.text_plain or "" for u in units)
    assert not [d for d in deleted_fragments(data) if d in texts]
    # identity comes from the manifest, never from the stale <meta> (FRBRthis …/jo, docDate 2999-01-01)
    assert res.eli_version == entry.eli_version and "2999" not in texts


# ------------------------------------------------------------------------------ PLAN acceptance
def test_a686_article_56_is_a_repealed_stub_without_chunk() -> None:
    arts = articles("a686_conso")
    assert len(arts) == 77  # 78 <article> elements, one quoted inside an embeddedStructure
    a56 = arts["art_56"]
    assert a56.is_repealed and a56.text_plain is None and a56.num_raw == "Art. 56."  # type: ignore[attr-defined]
    assert a56.eli_unit == E + "loi/2018/08/01/a686/art_56"  # type: ignore[attr-defined]
    assert a56.amended_by == [E + "loi/2025/12/19/a619/jo"]  # type: ignore[attr-defined]
    assert sum(1 for u in arts.values() if u.is_repealed) == 6  # type: ignore[attr-defined]
    rels = [r for r in parsed("a686_conso").relations if r.source is RelationSource.AKN_TEXTUALMOD]
    assert len(rels) == 1 and rels[0].src_eli == E + "loi/2025/12/19/a619/jo"
    assert rels[0].dst_unit_hint == "art_56" and rels[0].rel_type is RelType.MODIFIES
    chunks = chunk_units(parsed("a686_conso").units, title_short="CNPD", version_label="26/12/2025")
    assert a56.ordinal not in {c.unit_ordinal for c in chunks}  # type: ignore[attr-defined]


def test_aifm_229_units_embedded_articles_and_textual_mods() -> None:
    res = parsed("aifm_conso")
    arts = articles("aifm_conso")
    assert len(arts) == 229 and all(a.startswith("art_") for a in arts)
    assert res.stats["embedded_articles"] == 59  # id-less quoted articles are never units
    assert res.stats["textual_mods"] == 140
    assert sum(1 for r in res.relations if r.source is RelationSource.AKN_TEXTUALMOD) == 140
    assert sum(1 for u in arts.values() if u.contains_quoted_amendment) == 85  # type: ignore[attr-defined]
    assert all(u.eli_unit and u.eli_unit_version for u in arts.values())  # type: ignore[attr-defined]
    a2 = arts["art_2"]
    assert a2.heading == "Objet et champ d’application"  # type: ignore[attr-defined]
    assert a2.text_plain.startswith("(1) La présente loi fixe les règles")  # type: ignore[attr-defined]
    assert a2.unit_version_date.isoformat() == "2026-04-03"  # type: ignore[attr-defined]
    assert E + "loi/2026/03/27/a145/jo" in a2.amended_by  # type: ignore[attr-defined]
    # quoted amending text stays inside its host article (and that article is not chunked)
    a60 = arts["art_60"]
    assert a60.is_modifying and "«(11bis)" in a60.text_plain  # type: ignore[attr-defined]
    # inline embeddedText gets the « » the HTML shows
    assert "les mots « augmenté des primes d’émission »" in arts["art_133"].text_plain  # type: ignore[attr-defined]
    annexes = [u for u in res.units if u.unit_type is UnitType.ANNEX]
    assert [a.num_raw for a in annexes] == ["ANNEXE I", "ANNEXE II", "ANNEXE III", "ANNEXE IV", "ANNEXE V"]


def test_constitution_132_articles_with_elis_and_paragraphs() -> None:
    arts = articles("constitution")
    assert len(arts) == 132 and all(u.eli_unit for u in arts.values())  # type: ignore[attr-defined]
    a15 = arts["art_15"].text_plain  # type: ignore[attr-defined]
    assert a15.startswith("(1) Les Luxembourgeois sont égaux devant la loi.")
    assert "\n(2) Nul ne peut être discriminé" in a15
    bc = arts["art_1er"].breadcrumb  # type: ignore[attr-defined]
    assert bc == "Chapitre Ier. – De l’État, de son territoire et de ses habitants > " + (
        "Section 1re. – De l’État, de sa forme politique et de la souveraineté"
    )


def test_a88_articles_annexes_preamble_and_derived_elis() -> None:
    res = parsed("a88_jo")
    by_type: dict[str, list] = {}
    for u in res.units:
        by_type.setdefault(u.unit_type.value, []).append(u)
    assert len(by_type["article"]) == 11 and len(by_type["preamble"]) == 1
    assert [a.num_raw for a in by_type["annex"]] == ["ANNEXE I", "ANNEXE II"]
    assert by_type["annex"][0].text_plain.startswith("FORMULAIRE TVP UE TYPE")
    a1 = by_type["article"][0]
    assert a1.num_raw == "Art. 1er." and a1.num_norm == "1" and a1.heading == "Définitions"
    assert a1.eli_unit == E + "loi/2026/02/25/a88/art_1er" and a1.eli_unit_derived  # JO: minted, flagged
    assert a1.text_plain.split("\n")[1].startswith("1° « arrêté grand-ducal du 29 juin 1923 »")
    assert by_type["preamble"][0].text_plain.startswith("Nous Guillaume, Grand-Duc de Luxembourg")
    assert "Mandons et ordonnons" not in "\n".join(u.text_plain or "" for u in res.units)


def test_a936_base64_images_become_placeholders() -> None:
    res = parsed("a936_jo_trimmed")
    texts = "\n".join(u.text_plain or "" for u in res.units)
    assert "[image: image 1]" in texts and "base64" not in texts and "iVBOR" not in texts
    assert res.stats["images"] == 1 and res.stats["articles"] == 7


def test_n25_article_5_disclaimer_text() -> None:
    a5 = articles("n25_jo")["art_5"]
    assert "Le Journal officiel publié fait seul foi et produit des effets juridiques." in a5.text_plain  # type: ignore[attr-defined]
    assert len(articles("n25_jo")) == 9


def test_copyright_list_labels_and_content_mod() -> None:
    arts = articles("copyright_conso")
    assert "\n8° les actes officiels de l'autorité" in arts["art_10"].text_plain  # type: ignore[attr-defined]
    a87 = arts["art_87"]
    assert a87.is_modifying and "«... ainsi que le délit prévu par l'article 84" in a87.text_plain  # type: ignore[attr-defined]
    assert "@Rol" not in "\n".join(u.text_plain or "" for u in parsed("copyright_conso").units)  # scl:comment dropped


def test_amending_act_articles_are_modifying() -> None:
    arts = articles("a203_jo")
    assert any(u.is_modifying and u.contains_quoted_amendment for u in arts.values())  # type: ignore[attr-defined]
    chunks = chunk_units(parsed("a203_jo").units, title_short="T", version_label="V")
    modifying = {u.ordinal for u in arts.values() if u.is_modifying}  # type: ignore[attr-defined]
    assert not modifying & {c.unit_ordinal for c in chunks}


def test_refs_become_refers_to_relations() -> None:
    rels = [r for r in parsed("a88_jo").relations if r.source is RelationSource.AKN_REF]
    assert rels and all(r.rel_type is RelType.REFERS_TO and r.src_version_eli for r in rels)
    assert E + "agd/1923/06/29/n1/jo" in {r.dst_eli for r in rels}
    assert "http://data.europa.eu/eli/dec/1996/409/oj" in {r.dst_eli for r in rels}


# ------------------------------------------------------------------------------- robustness
def test_recover_mode_on_truncated_xml() -> None:
    data, entry, raw = fixture_input("a88_jo")
    cut = data[: len(data) // 2]
    res = parse_akn(cut, entry, raw)
    assert res.stats["articles"] >= 1
    assert "xml_recovered" in {i.code for i in res.issues}


def test_not_akn_raises() -> None:
    _data, entry, raw = fixture_input("a88_jo")
    with pytest.raises(ParseError):
        parse_bytes(b"<html><body>SPA</body></html>", entry, raw)
    with pytest.raises(ParseError):
        parse_bytes(b"", entry, raw)


def test_recueil_helpers() -> None:
    assert is_generated_id("art_N10064") and is_generated_id("part_N10026") and not is_generated_id("art_l_121-1")
    wid = normalise_wid("/eli/etat/leg/loi/2017/01/01/n2//consolide/20260717")
    assert wid == E + "loi/2017/01/01/n2/consolide/20260717"
    assert member_work_of_wid(E + "loi/1979/04/16/n1/art_1er/consolide/20260601") == E + "loi/1979/04/16/n1"


# -------------------------------------------------------------------------------- large (codes)
@pytest.mark.large
def test_code_travail_art_l_121_1() -> None:
    res = parsed_large("code_travail")
    arts = {u.anchor: u for u in res.units if u.unit_type is UnitType.ARTICLE}
    assert len(arts) == 1196
    u = arts["art_l_121-1"]
    assert u.eli_version == E + "code/travail/20260726" and u.eli_work == E + "loi/2006/07/31/n2"
    assert u.eli_unit == E + "code/travail/art_l_121-1"
    assert u.eli_unit_version == E + "code/travail/art_l_121-1/20201101" and not u.eli_unit_derived
    assert u.unit_version_date.isoformat() == "2020-11-01"  # type: ignore[union-attr]
    assert u.num_norm == "l121-1" and u.provenance == "( L. 13 mai 2008 )" and u.heading is None
    assert "Contrat de travail" in u.index_terms
    assert u.breadcrumb and u.breadcrumb.startswith("LIVRE PREMIER.")
    # the in-XML ELIs: 1,124 well-formed, 55 malformed (« …/code/travail/art. l.143-1. », logged), 17 absent
    assert res.stats["articles_with_eli"] == 1124
    assert sum(1 for i in res.issues if i.code == "malformed_article_eli") == 110
    no_eli = [x for x in arts.values() if x.eli_unit is None]
    assert len(no_eli) == 23 and len(no_eli) == sum(1 for i in res.issues if i.code == "article_without_eli")


@pytest.mark.large
def test_code_civil_2301_articles_and_range_aliases() -> None:
    from lgx_core.normalise import num_aliases

    arts = {u.anchor: u for u in parsed_large("code_civil").units if u.unit_type is UnitType.ARTICLE}
    assert len(arts) == 2301 and all(u.eli_unit for u in arts.values())
    u = arts["art_9_et_10"]
    assert u.is_repealed and u.repeal_note == "Abrogés ( L. 23 avril 1934 )"
    assert u.eli_unit == E + "code/civil/art_9_10"
    assert {"9", "10", "9_10"} <= set(num_aliases(u.num_raw, akn_id=u.akn_id, eli_unit=u.eli_unit))
    v = arts["art_17_a_20"]
    assert {"17", "18", "19", "20", "17--20"} <= set(num_aliases(v.num_raw, akn_id=v.akn_id, eli_unit=v.eli_unit))
    assert arts["art_1er"].eli_unit == E + "code/civil/art_1"
    # « Disposition relative aux sociétés de commerce » (heading-only subchapter) parents art_1873
    assert arts["art_1873"].breadcrumb.endswith("> Disposition relative aux sociétés de commerce")  # type: ignore[union-attr]


@pytest.mark.large
def test_code_recueil_generated_ids_are_never_anchors() -> None:
    res = parsed_large("code_fonction_publique")
    arts = [u for u in res.units if u.unit_type is UnitType.ARTICLE]
    assert len(arts) > 2000
    assert not any(re.fullmatch(r"[a-z]+_N[0-9A-F]{4,}", u.anchor) for u in res.units)
    assert not any(u.akn_id and is_generated_id(u.akn_id) for u in res.units)
    with_wid = [u for u in arts if u.from_recueil_wid]
    assert len(with_wid) > 0.9 * len(arts) and all(u.member_act_eli for u in with_wid)
    assert all("//" not in u.from_recueil_wid.split("://", 1)[1] for u in with_wid)  # type: ignore[union-attr]


@pytest.mark.large
def test_a936_full_file_images_stripped() -> None:
    data, entry, raw = large_input("a936_jo_full")
    assert len(data) > 1_800_000
    res = parse_bytes(data, entry, raw)
    assert res.stats["chars"] < 20_000 and "base64" not in "".join(u.text_plain or "" for u in res.units)
