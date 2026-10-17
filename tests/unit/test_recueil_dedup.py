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
    assert stats == {
        "member_parts_resolved": 2,
        "member_parts_unresolved": 1,
        "units_with_member": 6,
        "routed_members": 0,
        "routed_unattached": 0,
        "routed_unattached_examples": [],
    }


# ------------------------------------------------------------------------------- M2 review (2026-10-17)
def _pdf(ordinal: int, utype: UnitType, anchor: str, **kw: Any) -> Unit:
    return u(ordinal, utype, anchor, **kw).model_copy(update={"source_format": SourceFormat.PDF})


def test_apply_members_matches_truncated_headings_and_suffixed_anchors_and_reports_unattached_routes() -> None:
    """The YAML keeps the first 300 characters of a heading and two members of one date share a key: a long title
    never matched exactly and the key fallback was off, so routed members became unreachable."""
    long_title = (
        "Règlement grand-ducal du 26 janvier 2016 sur le contrôle technique des véhicules routiers " + "x" * 400
    )
    units = [
        _pdf(1, UnitType.PART, "rgd_2016-01-26", heading=long_title),
        _pdf(2, UnitType.ARTICLE, "rgd_2016-01-26.art_1", parent_ordinal=1, num_raw="Art. 1er.", text_plain="a"),
        _pdf(3, UnitType.PART, "rgd_2016-01-26~2", heading="Règlement grand-ducal du 26 janvier 2016 autre"),
        _pdf(4, UnitType.ARTICLE, "rgd_2016-01-26~2.art_1", parent_ordinal=3, num_raw="Art. 1er.", text_plain="b"),
    ]
    members = [
        R.MemberRef(E + "rgd/2016/01/26/n1", "rgd_2016-01-26", long_title[:300], route=True),
        R.MemberRef(E + "rgd/2016/01/26/n2", "rgd_2016-01-26~2", "Règlement grand-ducal du 26 janvier 2016 changé"),
        R.MemberRef(E + "rgd/2016/01/26/n9", "rgd_2016-01-26~3", "Règlement grand-ducal absent", route=True),
    ]
    out, stats = R.apply_members(units, members)
    member = {x.anchor: x.member_act_eli for x in out}
    assert member["rgd_2016-01-26.art_1"] == E + "rgd/2016/01/26/n1"  # heading prefix
    assert member["rgd_2016-01-26~2.art_1"] == E + "rgd/2016/01/26/n2"  # anchor with its occurrence suffix
    assert stats["routed_members"] == 2 and stats["routed_unattached"] == 1
    assert stats["routed_unattached_examples"] == [E + "rgd/2016/01/26/n9"]


def test_pdf_copies_of_members_with_a_stored_consolidation_are_dropped_and_differences_logged() -> None:
    """PLAN §2.6.2: the member's own consolidation is canonical; the CSS PDF reprints 217 Code du travail articles
    (106 with older wording) that competed with the code in search."""
    units = [
        _pdf(
            1,
            UnitType.PART,
            "loi_2006-07-31",
            heading="Loi du 31 juillet 2006 portant introduction d'un Code du travail",
        ),
        _pdf(
            2,
            UnitType.ARTICLE,
            "loi_2006-07-31.art_l411-1",
            parent_ordinal=1,
            num_raw="Art. L. 411-1.",
            num_norm="l411-1",
            text_plain="Texte Art. L. 411-1.",
            member_act_eli=TRAV_W,
        ),
        _pdf(
            3,
            UnitType.ARTICLE,
            "loi_2006-07-31.art_l999-1",
            parent_ordinal=1,
            num_raw="Art. L. 999-1.",
            num_norm="l999-1",
            text_plain="Inconnu",
            member_act_eli=TRAV_W,
        ),
        _pdf(4, UnitType.ARTICLE, "art_5", num_raw="Art. 5.", num_norm="5", text_plain="Code's own"),
    ]
    trav = u(1, UnitType.ARTICLE, "art_l_411-1", version=TRAV_V, work=TRAV_W, num_raw="Art. L. 411-1.",
             num_norm="l411-1", text_plain="Le salarié peut convenir autrement.")  # fmt: skip
    canon = {TRAV_V: R.Canonical.of(TRAV_V, TRAV_W, [trav])}
    d = R.dedup_pdf_copies(units, [], canonical=canon, served=SERVED, consolidated={TRAV_V}, first_seen=DAY)
    assert [x.anchor for x in d.units] == ["loi_2006-07-31", "loi_2006-07-31.art_l999-1", "art_5"]
    assert [x.ordinal for x in d.units] == [1, 2, 3] and d.units[1].parent_ordinal == 1
    assert d.report["counts"] == {"copy_differs": 1, "dropped_duplicate": 1, "kept_unmatched": 1}
    assert [i.code for i in d.issues] == ["recueil_copy_differs"] and d.issues[0].entity_eli.endswith(
        "#loi_2006-07-31.art_l411-1"
    )


def test_pdf_copies_of_a_member_served_from_its_jo_original_are_kept() -> None:
    """M2 review follow-up: rgd/2000/07/24/n3 is served from its JO original (no consolidation of its own, amended
    three times); the code's « Texte coordonné » copy is the current text and the routed member is answered from
    it — dropping it left the member part without articles."""
    jo_v = E + "rgd/2000/07/24/n3/jo"
    units = [
        _pdf(1, UnitType.PART, "rgd_2000-07-24", heading="Règlement grand-ducal du 24 juillet 2000"),
        _pdf(2, UnitType.ARTICLE, "rgd_2000-07-24.art_7", parent_ordinal=1, num_raw="Art. 7.", num_norm="7",
             text_plain="Texte coordonné.", member_act_eli=E + "rgd/2000/07/24/n3"),
    ]  # fmt: skip
    jo = u(1, UnitType.ARTICLE, "art_7", version=jo_v, work=E + "rgd/2000/07/24/n3", num_raw="Art. 7.",
           num_norm="7", text_plain="Texte original.")  # fmt: skip
    canon = {jo_v: R.Canonical.of(jo_v, E + "rgd/2000/07/24/n3", [jo])}
    served = {E + "rgd/2000/07/24/n3": jo_v}
    d = R.dedup_pdf_copies(units, [], canonical=canon, served=served, consolidated=set(), first_seen=DAY)
    assert [x.anchor for x in d.units] == ["rgd_2000-07-24", "rgd_2000-07-24.art_7"]
    assert d.report["counts"] == {"kept_member_original": 1} and d.report["kept"] == 1 and not d.issues


def test_structure_checks_routed_members_on_the_final_units() -> None:
    """A routed member whose code copies all go in the dedup is as unreachable as an unattached one: the check runs
    after the dedup (the copy of rgd/2000/07/24/n3 was dropped against its JO original and nothing noticed)."""
    from lgx_core.models import ManifestEntry, ParseResult, VersionRole
    from lgx_ingest.parse import structure_recueil

    code_v = E + "code/education_nationale/20260915"
    trav = u(1, UnitType.ARTICLE, "art_l_411-1", version=TRAV_V, work=TRAV_W, num_raw="Art. L. 411-1.",
             num_norm="l411-1", text_plain="Texte.")  # fmt: skip
    units = [
        _pdf(1, UnitType.PART, "loi_2006-07-31", heading="Loi du 31 juillet 2006 portant introduction"),
        _pdf(2, UnitType.ARTICLE, "loi_2006-07-31.art_l411-1", parent_ordinal=1, num_raw="Art. L. 411-1.",
             num_norm="l411-1", text_plain="Texte."),
    ]  # fmt: skip
    res = ParseResult(eli_work=E + "code/education_nationale", eli_version=code_v, parser="pdf", parser_version="pdf-2",
                      source_sha256="0" * 64, source_format=SourceFormat.PDF, units=units)  # fmt: skip
    entry = ManifestEntry(eli_work=res.eli_work, eli_version=code_v, role=VersionRole.CURRENT, doc_type="CODE_RECUEIL",
                          text_basis=TextBasis.CONSOLIDATED, reason="test")  # fmt: skip
    kw: dict[str, Any] = {
        "canonical": {TRAV_V: R.Canonical.of(TRAV_V, TRAV_W, [trav])},
        "served": {TRAV_W: TRAV_V},
        "version_work": {},
        "members": [R.MemberRef(TRAV_W, "loi_2006-07-31", "Loi du 31 juillet 2006 portant introduction", route=True)],
        "first_seen": DAY,
    }
    kept, _ = structure_recueil(res, entry, **kw)  # the member's version not known as a consolidation: copy kept
    assert kept.stats["routed_unattached"] == 0 and len(kept.units) == 2
    out, report = structure_recueil(res, entry, consolidated={TRAV_V}, **kw)
    assert out.stats["routed_unattached"] == 1 and len(out.units) == 1
    assert report is not None and [i["code"] for i in report["issues"]] == ["recueil_route_unattached"]


def test_the_same_article_printed_twice_in_one_xml_recueil_is_kept_once() -> None:
    """code/fonction_publique prints loi/2015/03/25 art. 42 under « I. Fonctionnaires » and « II. Employés » (same
    wId, same text): two search hits and an ambiguous lookup. The second position is dropped (logged)."""
    wid = E + "loi/2015/03/25/n2/art_42/consolide/20260717"
    units = [
        u(1, UnitType.PART, "_part.1", heading="I. Fonctionnaires de l'État"),
        art(2, wid, "Art. 42."),
        u(3, UnitType.PART, "_part.2", heading="II. Employés de l'État"),
        art(4, wid, "Art. 42.").model_copy(update={"parent_ordinal": 3}),
    ]
    d = R.deduplicate(units, [], canonical={}, served={}, version_work={}, first_seen=DAY)
    assert d.report["counts"]["dropped_repeat_in_recueil"] == 1 and d.report["dropped"] == 1
    assert [x.anchor for x in d.units if x.unit_type is UnitType.ARTICLE] == [units[1].anchor]
