"""recueil_members.yaml v1 derivation (lgx_ingest.recueil_members) and superseded_by_code routing (PLAN §2.6)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from lgx_core.config import ProjectConfig, RecueilMemberEntry, RecueilMembersConfig
from lgx_core.models import (
    CC_BY_4_0,
    Act,
    ActFlag,
    DateWindow,
    FileRef,
    ManifestEntry,
    MetadataReason,
    SourceFormat,
    TextBasis,
    Unit,
    UnitType,
    UserFormat,
    VersionKind,
    VersionRole,
)
from lgx_core.rules.select import select
from lgx_ingest import recueil_members as M
from lgx_ingest.sparql import load_snapshot

ELI = "http://data.legilux.public.lu/eli/"
E = ELI + "etat/leg/"
ROUTE, ROUTE_V = E + "code/route", E + "code/route/20260612"
FS = "https://legilux.public.lu/filestore/eli/etat/leg/code/route/20260612/fr/pdf/x.pdf"
T = dt.date(2026, 10, 16)
SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "meta" / "snapshot-20261016-sample"


def act(
    rel: str, title: str, doc_type: str, basis: TextBasis = TextBasis.ORIGINAL_POSSIBLY_OUTDATED, **kw: object
) -> Act:
    y, m, d = (int(x) for x in rel.split("/")[1:4])
    return Act(
        eli_work=E + rel,
        eli_jo=E + rel + "/jo",
        doc_type=doc_type,
        date_document=dt.date(y, m, d),
        title=title,
        text_basis=basis,
        **kw,  # type: ignore[arg-type]
    )


ACTS = [
    Act(eli_work=ROUTE, doc_type="CODE_RECUEIL", text_basis=TextBasis.CONSOLIDATED, basis_version_eli=ROUTE_V),
    act(
        "agd/1955/11/23/n1",
        "Arrêté grand-ducal du 23 novembre 1955 portant règlement de la circulation sur toutes les voies publiques.",
        "AGD",
        TextBasis.METADATA_ONLY,
        metadata_reason=MetadataReason.PDF_ONLY,
    ),
    act("agd/1955/11/23/n2", "Arrêté grand-ducal du 23 novembre 1955 concernant les taxes de chasse.", "AGD"),
    act(
        "loi/1955/02/14/n3",
        "Loi du 14 février 1955 concernant la réglementation de la circulation sur toutes les voies publiques.",
        "LOI",
    ),
    act("loi/1955/02/14/n1", "Loi du 14 février 1955 portant approbation d'un accord.", "LOI"),
    act(
        "rgd/2015/01/01/a1",
        "Règlement grand-ducal du 1er janvier 2015 relatif aux taxis.",
        "RGD",
        TextBasis.CONSOLIDATED,
        basis_version_eli=E + "rgd/2015/01/01/a1/consolide/20200101",
    ),
    act("rgd/2016/02/02/a2", "Règlement grand-ducal du 2 février 2016 relatif aux poids lourds.", "RGD"),
    act("rgd/2016/02/02/a3", "Règlement grand-ducal du 2 février 2016 relatif aux poids lourds.", "RGD"),  # same title
    act(
        "loi/2000/01/01/n1",
        "Loi du 1er janvier 2000 portant extraits divers.",
        "LOI",
        TextBasis.METADATA_ONLY,
        metadata_reason=MetadataReason.REPEALED,
    ),
]
ENTRY = ManifestEntry(
    eli_work=ROUTE,
    eli_version=ROUTE_V,
    kind=VersionKind.CODE_RECUEIL,
    role=VersionRole.CURRENT,
    doc_type="CODE_RECUEIL",
    text_basis=TextBasis.CONSOLIDATED,
    flags=[ActFlag.PDF_EXTRACTED],
    file=FileRef(url=FS, file_uri=FS, user_format=UserFormat.PDF, source_format=SourceFormat.PDF, licence=CC_BY_4_0),
    text_valid=DateWindow(lower=dt.date(2026, 6, 12)),
    reason="2b:consolidated",
)


def _u(ordinal: int, utype: UnitType, anchor: str, heading: str | None = None, parent: int | None = None) -> Unit:
    return Unit(
        eli_work=ROUTE,
        eli_version=ROUTE_V,
        ordinal=ordinal,
        parent_ordinal=parent,
        depth=1 if parent else 0,
        unit_type=utype,
        anchor=anchor,
        heading=heading,
        num_raw="Art. 1er." if utype is UnitType.ARTICLE else None,
        text_plain="t" if utype is UnitType.ARTICLE else None,
        doc_type="CODE_RECUEIL",
        source_format=SourceFormat.PDF,
        source_url=FS,
        source_sha256="0" * 64,
        licence=CC_BY_4_0,
        text_basis=TextBasis.CONSOLIDATED,
        page_from=1,
        page_to=1,
    )


def units() -> list[Unit]:
    parts = [
        (
            "agd_1955-11-23",
            "Arrêté grand-ducal du 23 novembre 1955 portant règlement de la circulation sur toutes les voies publiques",
        ),
        (
            "loi_1955-02-14",
            "Loi du 14 février 1955 concernant la réglementation de la circulation sur toutes les voies publiques",
        ),
        ("rgd_2015-01-01", "Règlement grand-ducal du 1er janvier 2015 relatif aux taxis"),
        ("rgd_2016-02-02", "Règlement grand-ducal du 2 février 2016 relatif aux poids lourds et aux"),
        ("loi_2000-01-01", "Loi du 1er janvier 2000 portant extraits, art. 3 et 4"),
        ("membre_1", "Statuts de la Mutualité"),
        ("rgd_2020-05-05", "Règlement grand-ducal du 5 mai 2020 inconnu"),
        ("loi_1999-09-09", "Loi du 9 septembre 1999 sans articles"),
    ]
    out: list[Unit] = []
    for anchor, heading in parts:
        p = len(out) + 1
        out.append(_u(p, UnitType.PART, anchor, heading))
        if anchor != "loi_1999-09-09":  # an empty title part: no member entry at all
            out.append(_u(p + 1, UnitType.ARTICLE, f"{anchor}.art_1", parent=p))
    return out


def derived() -> RecueilMembersConfig:
    return M.derive(acts=ACTS, entries=[ENTRY], units_of={ROUTE_V: units()}, generated="2026-10-16")


def test_title_score_prefers_the_matching_act() -> None:
    h = "Arrêté grand-ducal du 23 novembre 1955 portant règlement de la circulation sur toutes les voies publiques"
    assert M.title_score(h, ACTS[1].title or "") > 0.9 > M.title_score(h, ACTS[2].title or "")


def test_derive_resolves_routes_and_lists_the_rest() -> None:
    cfg = derived()
    by = {m.member.rsplit("/leg/", 1)[1]: m for m in cfg.members}
    agd = by["agd/1955/11/23/n1"]
    assert agd.method == "heading" and agd.route and not agd.reviewed and agd.member_key == "agd_1955-11-23"
    assert agd.confidence is not None and agd.confidence > 0.9 and agd.articles == 1  # PDF-only member: routed
    assert by["loi/1955/02/14/n3"].route  # JO original without consolidation: routed
    taxi = by["rgd/2015/01/01/a1"]
    assert not taxi.route and taxi.own_consolidation == "etat/leg/rgd/2015/01/01/a1/consolide/20200101"
    assert not by["loi/2000/01/01/n1"].route  # excerpt (« art. 3 et 4 ») + repealed
    assert by["loi/2000/01/01/n1"].coverage == "partial"
    unresolved = {u.member_key: u.reason for u in cfg.unresolved}
    assert unresolved == {
        "rgd_2016-02-02": "ambiguous or weak title match",  # a2 vs a3 within 0.05
        "membre_1": "undated title",
        "rgd_2020-05-05": "no act of that type and date",
    }
    assert "loi_1999-09-09" not in unresolved and len(cfg.members) == 4


def test_yaml_round_trip_and_pending() -> None:
    cfg = derived()
    text = M.to_yaml(cfg)
    assert text.startswith("# Code-recueil membership and routing") and "member_title" not in text
    back = RecueilMembersConfig.model_validate(yaml.safe_load(text))
    assert [(m.member, m.route) for m in back.members] == [(m.member, m.route) for m in cfg.members]
    sante = Act(
        eli_work=E + "code/sante",
        doc_type="CODE_RECUEIL",
        text_basis=TextBasis.CONSOLIDATED,
        basis_version_eli=E + "code/sante/20260810",
    )
    with_pending = M.derive(acts=[*ACTS, sante], entries=[ENTRY], units_of={ROUTE_V: units()}, generated="x")
    assert [(p.recueil, p.reason) for p in with_pending.pending] == [
        ("etat/leg/code/sante", "text not in the dev sample (derive after `lgx ingest --full`)")
    ]


def test_committed_members_file_is_v1_unreviewed() -> None:
    cfg = ProjectConfig.load().recueil_members
    assert cfg.version == 1 and cfg.members and all(not m.reviewed and m.reviewed_by is None for m in cfg.members)
    recueils = {m.recueil for m in cfg.members}
    assert {"etat/leg/code/route", "etat/leg/loi/1925/12/17/n1", "etat/leg/code/fonction_publique"} <= recueils
    route = {m.member for m in cfg.members if m.recueil == "etat/leg/code/route" and m.route}
    assert {"etat/leg/agd/1955/11/23/n1", "etat/leg/loi/1955/02/14/n3"} <= route  # PLAN §2.6 item 4 examples
    pending = {p.recueil for p in cfg.pending}
    assert "etat/leg/code/sante" in pending and "etat/leg/code/environnement" in pending


def test_routing_flags_superseded_by_code_only_for_routed_members() -> None:
    base = ProjectConfig.load()
    member = "etat/leg/rgd/2000/07/24/n3"  # a sample JO original (CC-BY HTML), member of the education code
    for route, flagged in ((True, True), (False, False)):
        members = RecueilMembersConfig(
            members=[RecueilMemberEntry(recueil="etat/leg/code/education_nationale", member=member, route=route)]
        )
        sel = select(load_snapshot(SNAPSHOT), T, base.model_copy(update={"recueil_members": members}))
        a = next(x for x in sel.acts if x.eli_work == ELI + member)
        assert (ActFlag.SUPERSEDED_BY_CODE in a.flags) is flagged
        assert a.superseded_by_eli == (E + "code/education_nationale" if flagged else None)
        assert a.text_basis is TextBasis.ORIGINAL_POSSIBLY_OUTDATED  # the label never changes


def test_excerpt_note_in_the_preamble_and_suffixed_member_keys() -> None:
    """M2 review: the PDFs print « Extrait: Art. 9 » under the Mémorial reference, not in the heading — such a member
    must not be routed (its other articles would be hidden). Member keys keep their occurrence suffix (~N)."""
    out = units()
    agd = next(u for u in out if u.anchor == "agd_1955-11-23")
    pre = _u(len(out) + 1, UnitType.PREAMBLE, "agd_1955-11-23.preamble", parent=agd.ordinal).model_copy(
        update={"text_plain": "(Mém. A - 18 du 27 mars 1987, p. 230)\nExtrait: Art. 9"}
    )
    cfg = M.derive(acts=ACTS, entries=[ENTRY], units_of={ROUTE_V: [*out, pre]}, generated="x")
    entry = next(m for m in cfg.members if m.member.endswith("agd/1955/11/23/n1"))
    assert not entry.route and entry.coverage == "partial" and entry.note and "excerpt" in entry.note
    twice = [
        *units(),
        _u(30, UnitType.PART, "loi_1955-02-14~2", "Loi du 14 février 1955 portant approbation d'un accord"),
    ]
    twice.append(_u(31, UnitType.ARTICLE, "loi_1955-02-14~2.art_1", parent=30))
    cfg2 = M.derive(acts=ACTS, entries=[ENTRY], units_of={ROUTE_V: twice}, generated="x")
    keys = {m.member.rsplit("/leg/", 1)[1]: m.member_key for m in cfg2.members}
    assert keys["loi/1955/02/14/n3"] == "loi_1955-02-14" and keys["loi/1955/02/14/n1"] == "loi_1955-02-14~2"


def test_dropped_pdf_copies_still_count_as_articles_of_their_part() -> None:
    only_part = [u for u in units() if u.anchor != "rgd_2015-01-01.art_1"]  # the copy was dropped by the dedup
    cfg = M.derive(
        acts=ACTS, entries=[ENTRY], units_of={ROUTE_V: only_part}, generated="x",
        dropped_of={ROUTE_V: ["rgd_2015-01-01.art_1"]},
    )  # fmt: skip
    taxi = next(m for m in cfg.members if m.member.endswith("rgd/2015/01/01/a1"))
    assert taxi.articles == 1 and not taxi.route
