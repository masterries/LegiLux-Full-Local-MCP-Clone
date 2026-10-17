"""XML code-recueil deduplication by article wId and PDF member mapping (lgx_ingest.parse.recueil, PLAN §2.6)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from lgx_core.models import CC_BY_4_0, Relation, RelationSource, RelType, SourceFormat, TextBasis, Unit, UnitType
from lgx_ingest.parse import recueil as R

E = "http://data.legilux.public.lu/eli/etat/leg/"
REC = E + "code/fonction_publique"
REC_V = REC + "/20261011"
CONST, CONST_V = E + "constitution/1868/10/17/n1", E + "constitution/1868/10/17/n1/consolide/20260829"
TRAV_W, TRAV_V = E + "loi/2006/07/31/n2", E + "code/travail/20260726"
STAT, STAT_V = E + "loi/1979/04/16/n1", E + "loi/1979/04/16/n1/consolide/20260601"
OLD = E + "rgd/2015/09/30/n3"
DAY = dt.date(2026, 10, 17)


def u(ordinal: int, utype: UnitType, anchor: str, *, version: str = REC_V, work: str = REC, **kw: Any) -> Unit:
    return Unit(
        eli_work=work,
        eli_version=version,
        ordinal=ordinal,
        depth=1 if kw.get("parent_ordinal") else 0,
        unit_type=utype,
        anchor=anchor,
        doc_type="CODE_RECUEIL",
        source_format=SourceFormat.XML,
        source_url="https://legilux.public.lu/filestore/eli/etat/leg/code/x.xml",
        source_sha256="0" * 64,
        licence=CC_BY_4_0,
        text_basis=TextBasis.CONSOLIDATED,
        **kw,
    )


def art(ordinal: int, wid: str | None, num: str, parent: int = 1) -> Unit:
    anchor = R.normalise_wid(wid).split("/eli/", 1)[-1] if wid else f"_art.{ordinal}"
    return u(
        ordinal,
        UnitType.ARTICLE,
        anchor,
        parent_ordinal=parent,
        num_raw=num,
        from_recueil_wid=wid and R.normalise_wid(wid),
        text_plain=f"Texte {num}",
    )


def recueil_units() -> list[Unit]:
    return [
        u(1, UnitType.PART, "_part.1", heading="I. Fonctionnaires de l'État"),
        art(2, "/eli/etat/leg/constitution/1868/10/17/n1/art_11/consolide/20260829", "Art. 11."),
        art(3, E + "code/travail/20260726/art_l_411_1", "Art. L. 411-1."),
        art(4, E + "rgd/2004/03/05/n7/art_1er/jo", "Art. 1er."),
        art(5, E + "loi/1979/04/16/n1/art_2/consolide/20260601", "Art. 2."),
        art(6, E + "rgd/2015/09/30/n3/art_1er/consolide/20250901", "Art. 1er."),
        art(7, E + "constitution/1868/10/17/n1/art_99/consolide/20260829", "Art. 99."),
        art(8, "/eli/etat/leg/loi/2015/03/25/n2//consolide/20260717", "Art. 7."),
        art(9, "nonsense", "Art. 3."),
        art(10, None, "Art. 4."),
    ]


def canonical() -> dict[str, R.Canonical]:
    const = [
        u(
            1,
            UnitType.ARTICLE,
            "art_11",
            version=CONST_V,
            work=CONST,
            akn_id="art_11",
            eli_unit=CONST + "/art_11",
            num_raw="Art. 11.",
        )
    ]
    trav = [
        u(
            1,
            UnitType.ARTICLE,
            "art_l_411-1",
            version=TRAV_V,
            work=TRAV_W,
            akn_id="art_l_411-1",
            eli_unit=E + "code/travail/art_l_411-1",
            num_raw="Art. L. 411-1.",
        )
    ]
    return {CONST_V: R.Canonical.of(CONST_V, CONST, const), TRAV_V: R.Canonical.of(TRAV_V, TRAV_W, trav)}


SERVED = {CONST: CONST_V, TRAV_W: TRAV_V, STAT: STAT_V}
VERSION_WORK = {CONST_V: CONST, TRAV_V: TRAV_W, STAT_V: STAT}


@pytest.mark.parametrize(
    ("wid", "kind", "member", "version", "article", "normalised"),
    [
        (CONST + "/art_11/consolide/20260829", "consolide", CONST, CONST_V, "art_11", False),
        (
            "/eli/etat/leg/rgd/2004/03/05/n7/art_1er/jo",
            "jo",
            E + "rgd/2004/03/05/n7",
            E + "rgd/2004/03/05/n7/jo",
            "art_1er",
            False,
        ),
        (TRAV_V + "/art_l_411_1", "code_version", E + "code/travail", TRAV_V, "art_l_411_1", False),
        (
            "/eli/etat/leg/loi/2015/03/25/n2//consolide/20260717",
            "consolide",
            E + "loi/2015/03/25/n2",
            E + "loi/2015/03/25/n2/consolide/20260717",
            None,
            True,
        ),
        (
            E + "rgd/1967/07/15/n2/art_2_à_art_5/consolide/20180602",
            "consolide",
            E + "rgd/1967/07/15/n2",
            E + "rgd/1967/07/15/n2/consolide/20180602",
            "art_2_à_art_5",
            True,
        ),
        ("art_N10064", "malformed", None, None, None, True),
    ],
)
def test_parse_wid(
    wid: str, kind: str, member: str | None, version: str | None, article: str | None, normalised: bool
) -> None:
    ref = R.parse_wid(wid)
    assert (ref.kind, ref.member_work, ref.member_version, ref.article, ref.normalised) == (
        kind,
        member,
        version,
        article,
        normalised,
    )


def test_fold_article_matches_underscore_and_hyphen_forms() -> None:
    assert R.fold_article("art_l_411_1") == R.fold_article(E + "code/travail/art_l_411-1") == "l4111"
    assert R.is_generated_id("art_N10064") and not R.is_generated_id("art_10")


def test_deduplicate_outcomes_and_renumbering() -> None:
    units = recueil_units()
    rels = [
        Relation(
            src_eli=REC,
            rel_type=RelType.REFERS_TO,
            dst_eli=CONST,
            source=RelationSource.AKN_REF,
            src_version_eli=REC_V,
            src_unit_ordinal=2,
        ),
        Relation(
            src_eli=REC,
            rel_type=RelType.REFERS_TO,
            dst_eli=STAT,
            source=RelationSource.AKN_REF,
            src_version_eli=REC_V,
            src_unit_ordinal=5,
        ),
    ]
    d = R.deduplicate(units, rels, canonical=canonical(), served=SERVED, version_work=VERSION_WORK, first_seen=DAY)
    outcome = {
        a["anchor"].rsplit("/", 2)[-2] if "/art" in a["anchor"] else a["anchor"]: a["outcome"]
        for a in d.report["articles"]
    }
    by_num = {a["num"]: a["outcome"] for a in d.report["articles"]}
    assert d.report["counts"] == {
        "dropped_duplicate": 2,  # Constitution art 11, Code du travail L. 411-1 (both stored in this build)
        "kept_canonical_not_loaded": 1,  # statut général: served, but not in this build (sample)
        "kept_jo": 1,
        "kept_malformed_wid": 1,
        "kept_member_not_served": 2,  # the repealed-base RGD, and the « …/n2//consolide/… » member
        "kept_no_wid": 1,
        "kept_unmatched": 1,  # Constitution art 99 does not exist in the canonical version
    }
    assert outcome and by_num["Art. 11."] == "dropped_duplicate" and by_num["Art. L. 411-1."] == "dropped_duplicate"
    assert d.report["dropped"] == 2 and d.report["kept"] == 7 and d.report["wid_normalised"] == 1
    # nothing silently dropped: 10 units - 2 duplicates, ordinals renumbered 1..8, parents re-pointed
    assert [x.ordinal for x in d.units] == list(range(1, 9))
    assert all(x.parent_ordinal in (None, 1) for x in d.units)
    assert [x.num_raw for x in d.units[1:3]] == ["Art. 1er.", "Art. 2."]
    # the relation of the dropped Constitution copy goes, the other one follows its unit (5 -> 3)
    assert [r.src_unit_ordinal for r in d.relations] == [3] and d.report["relations_dropped"] == 1
    codes = {i.code for i in d.issues}
    assert codes == {"recueil_wid_malformed", "recueil_article_unmatched", "recueil_wid_normalised"}


def test_version_mismatch_is_kept() -> None:
    units = [u(1, UnitType.PART, "_part.1"), art(2, CONST + "/art_11/consolide/20230701", "Art. 11.")]
    d = R.deduplicate(units, [], canonical=canonical(), served=SERVED, version_work=VERSION_WORK, first_seen=DAY)
    assert d.report["counts"] == {"kept_version_mismatch": 1} and len(d.units) == 2


def test_apply_members_by_heading_and_key() -> None:
    units = [
        u(1, UnitType.PART, "agd_1955-11-23", heading="Arrêté grand-ducal du 23 novembre 1955 portant règlement"),
        u(2, UnitType.PREAMBLE, "agd_1955-11-23.preamble", parent_ordinal=1, text_plain="(Mémorial A - 66)"),
        u(3, UnitType.CHAPTER, "agd_1955-11-23.chapitre_i", parent_ordinal=1, num_raw="Chapitre I"),
        u(4, UnitType.ARTICLE, "agd_1955-11-23.art_1", parent_ordinal=3, num_raw="Art. 1er.", text_plain="x"),
        u(5, UnitType.PART, "loi_1955-02-14", heading="Loi du 14 février 1955 (telle qu'elle a été modifiée)"),
        u(6, UnitType.ARTICLE, "loi_1955-02-14.art_1", parent_ordinal=5, num_raw="Art. 1er.", text_plain="y"),
        u(7, UnitType.PART, "membre_1", heading="Statuts de la Mutualité"),
        u(8, UnitType.ARTICLE, "membre_1.art_1", parent_ordinal=7, num_raw="Art. 1er.", text_plain="z"),
    ]
    members = [
        R.MemberRef(
            E + "agd/1955/11/23/n1", "agd_1955-11-23", "Arrêté grand-ducal du 23 novembre 1955 portant règlement"
        ),
        R.MemberRef(E + "loi/1955/02/14/n3", "loi_1955-02-14", "Loi du 14 février 1955 concernant la circulation"),
    ]
    out, stats = R.apply_members(units, members)
    member = {x.anchor: x.member_act_eli for x in out}
    assert member["agd_1955-11-23.art_1"] == member["agd_1955-11-23.preamble"] == E + "agd/1955/11/23/n1"
    assert member["loi_1955-02-14.art_1"] == E + "loi/1955/02/14/n3"  # by key (the heading differs)
    assert member["membre_1.art_1"] is None
    assert stats == {"member_parts_resolved": 2, "member_parts_unresolved": 1, "units_with_member": 6}
