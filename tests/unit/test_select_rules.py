"""Selection rules of PLAN §2.2 on small synthetic snapshots (one edge case per test)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

import pytest

from lgx_core.config import ProjectConfig, RecueilMemberEntry, RecueilMembersConfig
from lgx_core.models import (
    CC_BY_4_0,
    LICENCE_SCL,
    ActFlag,
    ActStatus,
    DateWindow,
    MetadataReason,
    RelType,
    TextBasis,
    VersionKind,
    VersionRole,
)
from lgx_core.rules.licence import pick_format
from lgx_core.rules.select import Selection, select
from lgx_core.rules.serving import serving_at, views_from_manifest
from lgx_core.rules.snapshot import MetaSnapshot

T = dt.date(2026, 10, 16)
D = dt.timedelta(days=1)
E = "http://data.legilux.public.lu/eli/etat/leg/"
FS = "http://data.legilux.public.lu/filestore/eli/etat/leg/"
SCL = LICENCE_SCL
CC = CC_BY_4_0


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


class Snap:
    """Tiny builder for MetaSnapshot rows (column names = SPARQL variables, values already typed)."""

    def __init__(self) -> None:
        self.t: dict[str, list[dict[str, Any]]] = {
            k: []
            for k in (
                "acts",
                "consolidations",
                "conso_formats",
                "conso_base_acts",
                "rel_modifies_repeals",
                "rel_other",
                "titles",
                "memorial_refs",
                "vocab",
                "act_formats",
                "manifestations",
            )
        }

    def act(
        self,
        work: str,
        typ: str = "LOI",
        st: str = "in-force",
        dei: str | None = "2000-01-01",
        dnl: str | None = None,
        pub: str | None = None,
        title: str | None = None,
        formats: Iterable[tuple[str, str]] = (("xml", CC), ("html", CC)),
    ) -> Snap:
        a = E + work + "/jo"
        self.t["acts"].append(
            {
                "a": a,
                "cw": E + work,
                "type": typ,
                "st": st,
                "dei": d(dei) if dei else None,
                "dnl": d(dnl) if dnl else None,
                "pub": d(pub or dei or "2000-01-01"),
            }
        )
        self.t["titles"].append({"a": a, "title": title or f"Loi {work}"})
        for uf, lic in formats:
            self.t["act_formats"].append({"a": a, "uf": uf, "lic": lic})
        return self

    def version(
        self,
        work: str,
        date: str,
        end: str | None = None,
        files: Iterable[tuple[str, str]] = (("xml", CC), ("pdf", CC)),
        typ: str | None = None,
        st: str | None = None,
        eli: str | None = None,
    ) -> Snap:
        c = eli or f"{E}{work}/consolide/{date.replace('-', '')}"
        self.t["consolidations"].append(
            {"c": c, "cw": E + work, "d": d(date), "end": d(end) if end else None, "st": st, "type": typ}
        )
        for uf, lic in files:
            self.t["conso_formats"].append({"c": c, "uf": uf, "lic": lic, "lang": "FRA"})
        return self

    def edge(self, src: str, tgt: str, dei: str | None, rel: str = "modifies") -> Snap:
        self.t["rel_modifies_repeals"].append(
            {"src": E + src + "/jo", "rel": rel, "tgt": E + tgt + "/jo", "dei": d(dei) if dei else None}
        )
        return self

    def based_on(self, src: str, tgt: str) -> Snap:
        target = tgt if tgt.startswith("http") else E + tgt + "/jo"
        self.t["rel_other"].append({"src": E + src + "/jo", "rel": "basedOn", "tgt": target})
        return self

    def manif(self, w: str, uf: str, lic: str, file: str) -> Snap:
        self.t["manifestations"].append({"w": w, "uf": uf, "lic": lic, "file": file, "mod": None})
        return self

    def build(self, **info: Any) -> MetaSnapshot:
        return MetaSnapshot(tables=self.t, info={"sync_date": T.isoformat(), **info})


@pytest.fixture(scope="module")
def cfg() -> ProjectConfig:
    base = ProjectConfig.load()
    # no sample works: the synthetic ELIs must not collide with config/sample.yaml expectations
    return base.model_copy(update={"sample": base.sample.model_copy(update={"works": []})})


def knob(cfg: ProjectConfig, **scope: Any) -> ProjectConfig:
    return cfg.model_copy(update={"scope": cfg.scope.model_copy(update=scope)})


def run(snap: Snap, cfg: ProjectConfig, **kw: Any) -> Selection:
    return select(snap.build(), T, cfg, **kw)


def plan(sel: Selection, work: str) -> dict[str, Any]:
    return next(r for r in sel.plan if r["eli_work"] == E + work)


def entries(sel: Selection, work: str) -> dict[str | None, Any]:
    return {e.eli_version: e for e in sel.manifest if e.eli_work == E + work}


# ----------------------------------------------------------------------------------- step 2: window
def test_window_rule_current_future_and_metadata_versions(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2000/01/01/n1")
        .version("loi/2000/01/01/n1", "2000-01-01", "2010-01-01")
        .version("loi/2000/01/01/n1", "2010-01-01", st="applicable")
        .version("loi/2000/01/01/n1", "2027-01-01")
        .version("loi/2000/01/01/n1", "2027-06-01")
    )
    sel = run(s, cfg)
    p = plan(sel, "loi/2000/01/01/n1")
    assert (p["text_basis"], p["reason"], p["format"]) == ("consolidated", "2b:consolidated", "xml")
    es = entries(sel, "loi/2000/01/01/n1")
    cur = es[E + "loi/2000/01/01/n1/consolide/20100101"]
    fut = es[E + "loi/2000/01/01/n1/consolide/20270101"]
    assert cur.role is VersionRole.CURRENT and cur.text_valid == DateWindow(
        lower=d("2010-01-01"), upper=d("2027-01-01")
    )
    assert fut.role is VersionRole.FUTURE and fut.text_valid == DateWindow(lower=d("2027-01-01"))
    assert es[E + "loi/2000/01/01/n1/consolide/20000101"].role is VersionRole.METADATA
    # next_one: only the earliest future version is stored
    assert es[E + "loi/2000/01/01/n1/consolide/20270601"].role is VersionRole.METADATA
    assert sel.acts[0].basis_version_eli == cur.eli_version
    assert all(v.has_text is False for v in sel.versions)  # text appears only after fetch + load


def test_future_versions_knob_and_horizon(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2000/01/01/n1")
        .version("loi/2000/01/01/n1", "2010-01-01")
        .version("loi/2000/01/01/n1", "2027-01-01")
    )
    es = entries(run(s, knob(cfg, future_versions="none")), "loi/2000/01/01/n1")
    assert es[E + "loi/2000/01/01/n1/consolide/20100101"].text_valid == DateWindow(lower=d("2010-01-01"))
    assert es[E + "loi/2000/01/01/n1/consolide/20270101"].text_valid is None
    far = (
        Snap()
        .act("loi/2000/01/01/n1")
        .version("loi/2000/01/01/n1", "2010-01-01")
        .version("loi/2000/01/01/n1", "2028-01-01")
    )
    es = entries(run(far, cfg), "loi/2000/01/01/n1")
    assert es[E + "loi/2000/01/01/n1/consolide/20280101"].reason == "2e:future_no_files"  # beyond H = T + 365


def test_future_version_without_files_keeps_current_open(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2013/07/12/n1")
        .version("loi/2013/07/12/n1", "2026-04-16", "2027-04-16")
        .version("loi/2013/07/12/n1", "2027-04-16", files=())
    )
    sel = run(s, cfg)
    es = entries(sel, "loi/2013/07/12/n1")
    assert es[E + "loi/2013/07/12/n1/consolide/20260416"].text_valid == DateWindow(lower=d("2026-04-16"))
    stub = es[E + "loi/2013/07/12/n1/consolide/20270416"]
    assert stub.role is VersionRole.METADATA and stub.reason == "2e:future_no_files"
    # from the stub's dA on, the runtime reports stub_fallback (W_STUB)
    act = sel.acts[0]
    decision = serving_at(act, views_from_manifest(es.values()), [], d("2027-04-16"))
    assert decision.text_basis is TextBasis.STUB_FALLBACK
    assert decision.window_version == stub.eli_version


def test_multiple_window_versions_is_an_anomaly(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2000/01/01/n1")
        .version("loi/2000/01/01/n1", "2010-01-01")
        .version("loi/2000/01/01/n1", "2020-01-01")
    )
    sel = run(s, cfg)
    assert plan(sel, "loi/2000/01/01/n1")["served_version"].endswith("20200101")
    assert {a["code"] for a in sel.report["anomalies"]} == {"multiple_window_versions"}


# ------------------------------------------------------------------------------------- stubs
def test_stub_fallback_to_latest_older_version_with_files(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/1993/04/05/n1")
        .version("loi/1993/04/05/n1", "1999-01-01", "2003-10-01")
        .version("loi/1993/04/05/n1", "2003-10-01", "2026-07-11", files=(("pdf", CC),))
        .version("loi/1993/04/05/n1", "2026-07-11", files=())
        .edge("loi/2001/01/01/n1", "loi/1993/04/05/n1", "2001-02-01")
        .edge("loi/2020/01/01/n1", "loi/1993/04/05/n1", "2020-02-01")
        .edge("loi/2026/12/01/n1", "loi/1993/04/05/n1", "2026-12-01")
    )
    sel = run(s, cfg)
    p = plan(sel, "loi/1993/04/05/n1")
    assert (p["text_basis"], p["served_role"], p["format"]) == ("stub_fallback", "fallback", "pdf")
    assert ActFlag.PDF_EXTRACTED.value in p["flags"]
    assert p["window_is_stub"] is True and p["window_version"].endswith("20260711")
    assert p["amending_acts"] == [E + "loi/2020/01/01/n1/jo"]  # after the fallback date, in force by T
    es = entries(sel, "loi/1993/04/05/n1")
    fb = es[E + "loi/1993/04/05/n1/consolide/20031001"]
    assert fb.role is VersionRole.FALLBACK and fb.text_valid == DateWindow(lower=d("2003-10-01"))
    stub = next(v for v in sel.versions if v.eli_version.endswith("20260711"))
    assert stub.is_stub and not stub.has_files


def test_stub_without_any_files_serves_the_jo_original(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("rgd/2025/12/22/a629", typ="RGD", dei="2025-12-27")
        .version("rgd/2025/12/22/a629", "2025-12-27", files=())
    )
    sel = run(s, cfg)
    p = plan(sel, "rgd/2025/12/22/a629")
    assert (p["text_basis"], p["reason"], p["served_kind"]) == (
        "original_possibly_outdated",
        "2c:stub_no_text",
        "original",
    )
    assert ActFlag.STUB_NO_TEXT.value in p["flags"]
    jo = entries(sel, "rgd/2025/12/22/a629")[E + "rgd/2025/12/22/a629/jo"]
    assert jo.kind is VersionKind.ORIGINAL and jo.text_valid == DateWindow(lower=d("2025-12-27"))


def test_recueil_stub_without_base_act_is_metadata_only(cfg: ProjectConfig) -> None:
    s = Snap().version("recueil/x", "2026-01-01", files=(), typ="RECUEIL")
    p = plan(run(s, cfg), "recueil/x")
    assert (p["text_basis"], p["metadata_reason"], p["eli_jo"]) == ("metadata_only", "not_yet_text", None)


# -------------------------------------------------------------------------------- base act status
def test_repealed_base_act_makes_the_window_version_metadata_only(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/1999/05/31/n8", st="no-longer-in-force", dnl="2026-08-01")
        .version("loi/1999/05/31/n8", "2018-09-18")
    )
    sel = run(s, cfg)
    p = plan(sel, "loi/1999/05/31/n8")
    assert (p["text_basis"], p["metadata_reason"], p["reason"]) == (
        "metadata_only",
        "repealed_base",
        "2a:repealed_base",
    )
    assert all(e.text_valid is None for e in sel.manifest)
    assert next(iter(entries(sel, "loi/1999/05/31/n8").values())).reason == "2a:repealed_base"


def test_flag_no_longer_but_future_dnl_stays_consolidated_until_dnl(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2010/05/27/n2", st="no-longer-in-force", dnl="2026-10-20")
        .version("loi/2010/05/27/n2", "2014-08-01")
    )
    sel = run(s, cfg)
    p = plan(sel, "loi/2010/05/27/n2")
    assert p["text_basis"] == "consolidated" and p["text_valid_to"] == d("2026-10-20")
    assert ActFlag.STATUS_FLAG_CONFLICT.value in p["flags"]
    assert {i.code for i in sel.issues} >= {"flag_date_conflict"}
    act, es = sel.acts[0], entries(sel, "loi/2010/05/27/n2").values()
    for day, basis in ((d("2026-10-19"), TextBasis.CONSOLIDATED), (d("2026-10-20"), TextBasis.METADATA_ONLY)):
        assert serving_at(act, views_from_manifest(es), [], day).text_basis is basis


def test_repealed_act_without_window_version_is_plain_repealed(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/1976/08/29/n5", st="no-longer-in-force", dnl="1998-09-21")
        .version("loi/1976/08/29/n5", "1990-01-01", "1998-09-21")
    )
    p = plan(run(s, cfg), "loi/1976/08/29/n5")
    assert (p["metadata_reason"], p["reason"]) == ("repealed", "1:repealed")


# --------------------------------------------------------------------------------- discontinued
def test_discontinued_consolidation_serves_the_original(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/1922/03/05/n1", dei="1922-03-20")
        .version("loi/1922/03/05/n1", "1965-05-31", "2004-06-21")
        .edge("loi/2004/06/01/n1", "loi/1922/03/05/n1", "2004-06-21")
    )
    sel = run(s, cfg)
    p = plan(sel, "loi/1922/03/05/n1")
    assert (p["text_basis"], p["reason"], p["served_kind"]) == (
        "original_possibly_outdated",
        "2d:discontinued",
        "original",
    )
    assert ActFlag.CONSOLIDATION_DISCONTINUED.value in p["flags"] and p["window_version"] is None
    assert p["amending_acts"] == [E + "loi/2004/06/01/n1/jo"]
    alt = plan(run(s, knob(cfg, discontinued_policy="last_consolidation")), "loi/1922/03/05/n1")
    assert (alt["text_basis"], alt["served_role"]) == ("stub_fallback", "fallback")


def test_recueil_ended_without_base_act(cfg: ProjectConfig) -> None:
    s = Snap().version("code/cooperation_judiciaire", "2010-01-01", "2020-01-01", typ="CODE_RECUEIL")
    p = plan(run(s, cfg), "code/cooperation_judiciaire")
    assert (p["metadata_reason"], p["reason"], p["doc_type"]) == ("repealed", "2d:recueil_ended", "CODE_RECUEIL")


def test_only_future_versions(cfg: ProjectConfig) -> None:
    s = Snap().act("loi/2026/04/15/a211", dei="2027-01-01").version("loi/2026/04/15/a211", "2027-01-01")
    sel = run(s, cfg)
    p = plan(sel, "loi/2026/04/15/a211")
    assert (p["text_basis"], p["metadata_reason"], p["status_at_t"]) == (
        "metadata_only",
        "not_yet_text",
        "not-yet-in-force",
    )
    assert p["future_version"].endswith("20270101") and p["future_valid_from"] == d("2027-01-01")
    live = Snap().act("loi/2020/01/01/n1").version("loi/2020/01/01/n1", "2027-01-01")
    q = plan(run(live, cfg), "loi/2020/01/01/n1")
    assert (q["text_basis"], q["served_kind"], q["text_valid_to"]) == (
        "original_possibly_outdated",
        "original",
        d("2027-01-01"),
    )


# ------------------------------------------------------------------------------ JO originals (step 3)
def test_possibly_outdated_when_an_amending_act_is_in_force(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("rgd/2015/06/25/n1", typ="RGD", dei="2015-07-01")
        .act("rgd/2016/01/01/n1", typ="RGD", dei="2016-01-01")
        .act("rgd/2017/01/01/n1", typ="RGD", dei="2017-01-01")
        .act("rgd/2018/01/01/n1", typ="RGD", dei="2018-01-01")
        # repealed amending act: its amendments stay in effect
        .act("rgd/2020/01/10/a5", typ="RGD", dei="2020-01-20", st="no-longer-in-force", dnl="2021-01-01")
        .edge("rgd/2020/01/10/a5", "rgd/2015/06/25/n1", "2020-01-20")
        .edge("rgd/2030/01/01/n1", "rgd/2016/01/01/n1", "2030-01-01")  # not in force yet
        .edge("rgd/2019/01/01/n1", "rgd/2017/01/01/n1", None)  # undated edge is ignored
        .edge("rgd/2019/02/01/n1", "rgd/2018/01/01/n1", "2019-02-01", rel="repeals")  # partial repeal counts
    )
    sel = run(s, cfg)
    assert plan(sel, "rgd/2015/06/25/n1")["text_basis"] == "original_possibly_outdated"
    assert plan(sel, "rgd/2015/06/25/n1")["amending_acts"] == [E + "rgd/2020/01/10/a5/jo"]
    assert plan(sel, "rgd/2016/01/01/n1")["text_basis"] == "original_never_amended"
    assert plan(sel, "rgd/2017/01/01/n1")["text_basis"] == "original_never_amended"
    assert plan(sel, "rgd/2018/01/01/n1")["text_basis"] == "original_possibly_outdated"
    # the runtime flips never_amended -> possibly_outdated on the amending act's entry into force
    act = next(a for a in sel.acts if a.eli_work == E + "rgd/2016/01/01/n1")
    rels = [r for r in sel.relations if r.dst_eli == act.eli_jo]
    views = views_from_manifest(entries(sel, "rgd/2016/01/01/n1").values())
    assert serving_at(act, views, rels, d("2029-12-31")).text_basis is TextBasis.ORIGINAL_NEVER_AMENDED
    assert serving_at(act, views, rels, d("2030-01-01")).text_basis is TextBasis.ORIGINAL_POSSIBLY_OUTDATED


def test_not_yet_in_force_text_is_stored_from_its_entry_date(cfg: ProjectConfig) -> None:
    s = Snap().act("loi/2025/11/20/a510", dei="2026-10-20", st="not-yet-in-force")
    sel = run(s, cfg)
    p = plan(sel, "loi/2025/11/20/a510")
    assert (p["text_basis"], p["status_at_t"], p["text_valid_from"]) == (
        "original_never_amended",
        "not-yet-in-force",
        d("2026-10-20"),
    )
    act, views = sel.acts[0], views_from_manifest(entries(sel, "loi/2025/11/20/a510").values())
    before, on = serving_at(act, views, [], T), serving_at(act, views, [], d("2026-10-20"))
    assert (before.status, before.text_basis) == (ActStatus.NOT_YET_IN_FORCE, TextBasis.METADATA_ONLY)
    assert (on.status, on.text_basis) == (ActStatus.IN_FORCE, TextBasis.ORIGINAL_NEVER_AMENDED)


def test_dnl_caps_the_original_window(cfg: ProjectConfig) -> None:
    s = Snap().act("rgd/1978/02/28/n2", typ="RGD", st="no-longer-in-force", dei="1978-03-15", dnl="2027-01-20")
    p = plan(run(s, cfg), "rgd/1978/02/28/n2")
    assert (p["text_valid_from"], p["text_valid_to"]) == (d("1978-03-15"), d("2027-01-20"))


@pytest.mark.parametrize(
    ("formats", "policy", "fmt", "reason"),
    [
        ((("xml", CC), ("html", CC), ("pdf", SCL)), "cc_by_only", "xml", None),
        ((("xml-akomantoso", CC),), "cc_by_only", "xml", None),
        ((("xml", SCL), ("html", CC)), "cc_by_only", "html", None),  # 15,502 acts take this path
        ((("xml", SCL), ("html", SCL)), "cc_by_only", None, "licence"),
        ((("pdf", SCL),), "cc_by_only", None, "pdf_only"),
        ((("pdf", CC),), "cc_by_only", None, "pdf_only"),  # JO PDFs are not ingested in v1
        ((), "cc_by_only", None, "licence"),  # e.g. CC BY only on the robots-disallowed DOCX
        ((("xml", SCL), ("html", CC)), "allow_licence_scl", "xml", None),
    ],
)
def test_licence_aware_pick_for_originals(
    cfg: ProjectConfig, formats: tuple[tuple[str, str], ...], policy: str, fmt: str | None, reason: str | None
) -> None:
    s = Snap().act("loi/2001/06/20/n5", formats=formats)
    p = plan(run(s, knob(cfg, licence_policy=policy)), "loi/2001/06/20/n5")
    assert (p["format"], p["metadata_reason"]) == (fmt, reason)
    if fmt is not None:
        assert p["text_basis"] == "original_never_amended"
        assert (ActFlag.LICENCE_SCL_SOURCE.value in p["flags"]) is (policy == "allow_licence_scl")
    else:
        assert p["text_basis_without_licence"] == "original_never_amended"


def test_pick_format_prefers_cc_by_within_a_format() -> None:
    pick = pick_format({"xml": {SCL, CC}}, policy="allow_licence_scl")
    assert pick.licence == CC and pick.user_formats == ("xml",)
    assert pick_format({"pdf": {CC}}, allow_pdf=True).source_format is not None
    assert pick_format({"docx": {CC}}).reason is MetadataReason.LICENCE


def test_unknown_licence_keeps_the_label_and_reports_it(cfg: ProjectConfig) -> None:
    s = Snap().act("amin/2020/01/01/a1", typ="AMIN", formats=())
    sel = select(s.build(act_formats_coverage={"doc_types": ["LOI"], "rdf_status": "in-force"}), T, cfg)
    p = plan(sel, "amin/2020/01/01/a1")
    assert (p["text_basis"], p["format"], p["licence_source"]) == ("original_never_amended", None, "unknown")
    assert {a["code"] for a in sel.report["anomalies"]} == {"licence_unknown"}


def test_temporary_traffic_rgd(cfg: ProjectConfig) -> None:
    title = "Règlement grand-ducal du 11 juillet 2025 portant réglementation temporaire de la circulation sur la N7"
    s = Snap().act("rgd/2025/07/11/a319", typ="RGD", title=title)
    p = plan(run(s, cfg), "rgd/2025/07/11/a319")
    assert (p["metadata_reason"], p["reason"]) == ("temporary", "3:temporary")
    assert ActFlag.ACTE_TEMPORAIRE.value in p["flags"]
    q = plan(run(s, knob(cfg, include_temporary_traffic=True)), "rgd/2025/07/11/a319")
    assert q["text_basis"] == "original_never_amended" and ActFlag.ACTE_TEMPORAIRE.value in q["flags"]
    covid = "Règlement grand-ducal … mesures temporaires relatives à … la libre circulation des personnes"
    r = plan(run(Snap().act("rgd/2020/06/20/a1", typ="RGD", title=covid), cfg), "rgd/2020/06/20/a1")
    assert r["text_basis"] == "original_never_amended"  # not a traffic regulation


def test_type_outside_text_types_is_metadata_only(cfg: ProjectConfig) -> None:
    p = plan(run(Snap().act("a/1915/02/04/n1", typ="A"), cfg), "a/1915/02/04/n1")
    assert (p["metadata_reason"], p["reason"]) == ("type", "4:metadata_only")


def test_act_flags(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("rgd/2000/07/24/n3", typ="RGD")
        .act("loi/1990/01/01/n1", st="no-longer-in-force", dnl="2000-01-01")
        .based_on("rgd/2000/07/24/n3", "loi/1990/01/01/n1")
        .based_on("rgd/2000/07/24/n3", "http://data.europa.eu/eli/dir/2000/1/oj")
        .act("rgd/2001/01/01/n1", typ="RGD")
        .based_on("rgd/2001/01/01/n1", "http://data.europa.eu/eli/dir/2000/1/oj")
        .act("loi/1889/01/02/n1", dei="1889-01-10", pub="1889-01-05")
        .act("rgd/2026/06/12/a299", typ="RGD", dei="2026-01-01")
        .act(
            "loi/2023/04/19/a203", title="Loi du 19 avril 2023 portant modification du Nouveau Code de procédure civile"
        )
        .edge("loi/2023/04/19/a203", "loi/1889/01/02/n1", "2023-04-25")
    )
    s.t["acts"].append({**s.t["acts"][4], "dei": d("2017-01-01")})  # a second dateEntryInForce row
    sel = run(s, cfg)
    assert ActFlag.BASE_LEGALE_ABROGEE.value in plan(sel, "rgd/2000/07/24/n3")["flags"]
    assert ActFlag.BASE_LEGALE_ABROGEE.value not in plan(sel, "rgd/2001/01/01/n1")["flags"]  # EU target only
    assert ActFlag.OLD_ACT.value in plan(sel, "loi/1889/01/02/n1")["flags"]
    multi = plan(sel, "rgd/2026/06/12/a299")
    assert ActFlag.ENTRY_DATES_MULTIPLE.value in multi["flags"] and multi["text_valid_from"] == d("2017-01-01")
    assert ActFlag.IS_AMENDING_ACT.value in plan(sel, "loi/2023/04/19/a203")["flags"]
    assert ActFlag.IS_AMENDING_ACT.value not in plan(sel, "loi/1889/01/02/n1")["flags"]


def test_amending_acts_metadata_only_knob(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2023/04/19/a203", title="Loi du 19 avril 2023 modifiant la loi X")
        .act("loi/2000/01/01/n1")
        .edge("loi/2023/04/19/a203", "loi/2000/01/01/n1", "2023-05-01")
    )
    p = plan(run(s, knob(cfg, amending_acts_text="metadata_only")), "loi/2023/04/19/a203")
    assert (p["metadata_reason"], p["reason"]) == ("excluded_by_config", "3:amending_metadata_only")


# ----------------------------------------------------------------------------------- scope (step 0)
def test_scope_exclusions_and_privacy_of_relations(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2007/01/15/n2", title="Lois du 15 janvier 2007 conférant la naturalisation.")
        .act("loi/2001/01/01/n9", title="Loi du 1er janvier 2001 qui accorde la naturalisation au sieur X")
        .act("rc/1996/07/02/n5", typ="RC")
        .act("loi/2008/01/01/n1", title="Loi du 23 octobre 2008 sur la nationalité luxembourgeoise")
        .act("loi/2009/01/01/n1")
        .edge("loi/2008/01/01/n1", "loi/2007/01/15/n2", "2009-01-01")
        .edge("loi/2009/01/01/n1", "loi/2008/01/01/n1", "2009-01-01")
        .edge("loi/2009/01/01/n1", "rc/1996/07/02/n5", "2009-01-01")
    )
    s.t["acts"].append(
        {
            **s.t["acts"][-1],
            "a": "http://data.legilux.public.lu/eli/etat/adm/amin/2021/05/26/b2350/jo",
            "cw": "http://data.legilux.public.lu/eli/etat/adm/amin/2021/05/26/b2350",
        }
    )
    sel = run(s, cfg, denylist=["http://data.legilux.public.lu/eli/etat/leg/loi/2009/01/01/n1"])
    works = {a.eli_work for a in sel.acts}
    assert works == {E + "loi/2008/01/01/n1"}  # general nationality law stays in
    assert sel.report["counts"]["excluded"] == {"branch": 1, "denylist": 1, "doc_type:RC": 1, "naturalisation": 2}
    ends = {r.src_eli for r in sel.relations} | {r.dst_eli for r in sel.relations}
    assert not any("2007/01/15/n2" in e or "2009/01/01/n1" in e for e in ends)
    assert sel.gdpr_review == []  # both naturalisation titles matched the rule; nothing left to review


def test_relations_are_deduplicated_with_the_earliest_entry_date(cfg: ProjectConfig) -> None:
    s = Snap().act("loi/2000/01/01/n1").edge("loi/2001/01/01/n1", "loi/2000/01/01/n1", "2002-01-01")
    s.edge("loi/2001/01/01/n1", "loi/2000/01/01/n1", "2001-06-01")
    s.based_on("loi/2000/01/01/n1", "http://data.europa.eu/eli/dir/2000/1/oj")
    sel = run(s, cfg)
    mods = [r for r in sel.relations if r.rel_type is RelType.MODIFIES]
    assert len(mods) == 1 and mods[0].src_dei == d("2001-06-01")
    assert any(r.rel_type is RelType.BASED_ON and r.dst_eli.startswith("http://data.europa.eu/") for r in sel.relations)


# --------------------------------------------------------------------------- codes and recueils
def test_code_doc_type_kind_and_recueil_without_base(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("loi/2006/07/31/n2")
        .version("loi/2006/07/31/n2", "2026-07-26", typ="CODE", eli=E + "code/travail/20260726")
        .version("code/route", "2026-06-12", files=(("pdf", CC),), typ="CODE_RECUEIL", eli=E + "code/route/20260612")
        .version("code/environnement_annexes", "2026-01-01", files=(("pdf", CC),), typ="CODE_RECUEIL")
    )
    sel = run(s, cfg)
    travail = plan(sel, "loi/2006/07/31/n2")
    assert (travail["doc_type"], travail["act_type"], travail["served_kind"]) == ("CODE", "LOI", "code")
    route = plan(sel, "code/route")
    assert (route["text_basis"], route["format"], route["eli_jo"], route["served_kind"]) == (
        "consolidated",
        "pdf",
        None,
        "code_recueil",
    )
    assert ActFlag.PDF_EXTRACTED.value in route["flags"]
    annexes = plan(sel, "code/environnement_annexes")
    assert (annexes["metadata_reason"], annexes["reason"]) == ("excluded_by_config", "2b:excluded_by_config")
    on = plan(run(s, knob(cfg, include_env_annexes_text=True)), "code/environnement_annexes")
    assert on["text_basis"] == "consolidated"


def test_superseded_by_code_member(cfg: ProjectConfig) -> None:
    members = RecueilMembersConfig(
        members=[RecueilMemberEntry(recueil="etat/leg/code/route", member="etat/leg/agd/1955/11/23/n1")]
    )
    c = cfg.model_copy(update={"recueil_members": members})
    p = plan(run(Snap().act("agd/1955/11/23/n1", typ="AGD"), c), "agd/1955/11/23/n1")
    assert ActFlag.SUPERSEDED_BY_CODE.value in p["flags"] and p["superseded_by"] == E + "code/route"
    assert p["text_basis"] == "original_never_amended"


def test_history_mode_stores_previous_versions_without_overlap(cfg: ProjectConfig) -> None:
    s = Snap().act("loi/1804/03/21/n1")
    for date, end in (("2020-01-01", "2023-01-01"), ("2023-01-01", "2025-12-26"), ("2025-12-26", None)):
        s.version("loi/1804/03/21/n1", date, end, typ="CODE", eli=E + f"code/civil/{date.replace('-', '')}")
    hist = cfg.scope.history.model_copy(update={"mode": "full_text_last_n", "keep_versions": 1})
    es = entries(run(s, knob(cfg, history=hist)), "loi/1804/03/21/n1")
    h = es[E + "code/civil/20230101"]
    assert h.role is VersionRole.HISTORY_TEXT and h.text_valid == DateWindow(
        lower=d("2023-01-01"), upper=d("2025-12-26")
    )
    assert es[E + "code/civil/20200101"].text_valid is None
    assert es[E + "code/civil/20251226"].text_valid == DateWindow(lower=d("2025-12-26"))


# --------------------------------------------------------------------------------------- files
def test_file_choice_from_q_manif(cfg: ProjectConfig) -> None:
    jo = E + "rgd/2003/01/24/n2/jo"
    v = E + "loi/1993/04/05/n1/consolide/20031001"
    s = (
        Snap()
        .act("rgd/2003/01/24/n2", typ="RGD", formats=(("pdf", CC),))
        .manif(jo, "pdf", CC, FS + "memorial/2003/a19/fr/pdf/eli-etat-leg-memorial-2003-a19-fr-pdf.pdf")
        .manif(jo, "docx", CC, FS + "rgd/2003/01/24/n2/jo/fr/docx/x.docx")
        .act("loi/1993/04/05/n1")
        .version("loi/1993/04/05/n1", "2003-10-01", "2026-07-11", files=(("pdf", CC),))
        .version("loi/1993/04/05/n1", "2026-07-11", files=())
        .manif(v, "pdf", CC, FS + "memorial/2003/a164/fr/pdf/eli-etat-leg-memorial-2003-a164-fr-pdf.pdf")
        .act("loi/2016/12/23/n25")
        .manif(E + "loi/2016/12/23/n25/jo", "xml", CC, FS + "loi/2016/12/23/n25/jo/fr/xml/b.xml")
        .manif(E + "loi/2016/12/23/n25/jo", "xml", CC, FS + "loi/2016/12/23/n25/jo/fr/xml/a.xml")
        .manif(E + "loi/2016/12/23/n25/jo", "html", CC, FS + "loi/2016/12/23/n25/jo/fr/html/a.html")
    )
    sel = run(s, cfg)
    assert plan(sel, "rgd/2003/01/24/n2")["metadata_reason"] == "pdf_only"  # Q-MANIF wins over act_formats
    fb = plan(sel, "loi/1993/04/05/n1")
    assert (
        fb["file_url"]
        == "https://legilux.public.lu/filestore/eli/etat/leg/memorial/2003/a164/fr/pdf/eli-etat-leg-memorial-2003-a164-fr-pdf.pdf"
    )
    assert {a["code"] for a in sel.report["anomalies"]} == {"consolidation_file_is_memorial_issue"}
    n25 = entries(sel, "loi/2016/12/23/n25")[E + "loi/2016/12/23/n25/jo"]
    assert n25.file is not None and n25.file.url.endswith("/fr/xml/a.xml") and n25.file.licence == CC
    assert plan(sel, "loi/2016/12/23/n25")["licence_source"] == "q_manif"


# ------------------------------------------------------------------------------------- manifest
def test_manifest_shape_and_sample_flags(cfg: ProjectConfig) -> None:
    base = ProjectConfig.load()
    s = Snap().act("loi/1976/08/29/n5", st="no-longer-in-force", dnl="1998-09-21").act("loi/2016/12/23/n25")
    sel = select(s.build(), T, base)
    es = entries(sel, "loi/1976/08/29/n5")
    assert list(es) == [None]  # metadata-only act without any version: one entry, no version
    only = es[None]
    assert (only.role, only.reason, only.in_sample, only.sample_stage) == (
        VersionRole.METADATA,
        "1:repealed",
        True,
        "m1",
    )
    n25 = entries(sel, "loi/2016/12/23/n25")[E + "loi/2016/12/23/n25/jo"]
    assert n25.in_sample and n25.text_valid is not None and n25.file is None  # no Q-MANIF row: no URL
    sample = sel.report["sample"]
    assert sample["works"] == len(base.sample.works)  # every sample work is reported, present or not
    assert {m["work"] for m in sample["mismatches"]} >= {"etat/leg/constitution/1868/10/17/n1"}


def test_future_consolidation_is_stored_even_when_the_original_is_not(cfg: ProjectConfig) -> None:
    s = Snap().act("loi/2020/01/01/n1", formats=(("xml", SCL),)).version("loi/2020/01/01/n1", "2027-01-01")
    sel = run(s, cfg)
    p = plan(sel, "loi/2020/01/01/n1")
    assert (p["text_basis"], p["metadata_reason"]) == ("metadata_only", "licence")
    assert p["future_version"].endswith("20270101")
    views = views_from_manifest(entries(sel, "loi/2020/01/01/n1").values())
    assert serving_at(sel.acts[0], views, [], d("2027-01-01")).text_basis is TextBasis.CONSOLIDATED


def test_consolidation_types_knob(cfg: ProjectConfig) -> None:
    s = Snap().act("loi/2000/01/01/n1").version("loi/2000/01/01/n1", "2010-01-01")
    s.version("recueil/x", "2010-01-01", typ="RECUEIL")
    sel = run(s, knob(cfg, consolidation_types=["CODE"]))
    assert plan(sel, "loi/2000/01/01/n1")["text_basis"] == "original_never_amended"  # untyped -> base type LOI
    assert all(r["eli_work"] != E + "recueil/x" for r in sel.plan)  # its only versions are filtered out


def test_scheduled_end_without_successor_switches_to_the_original(cfg: ProjectConfig) -> None:
    s = (
        Snap()
        .act("rgd/2022/12/07/a606", typ="RGD", dei="2022-12-10")
        .version("rgd/2022/12/07/a606", "2024-12-02", "2026-12-01")
    )
    sel = run(s, cfg)
    p = plan(sel, "rgd/2022/12/07/a606")
    assert (p["text_basis"], p["text_valid_to"]) == ("consolidated", d("2026-12-01"))
    es = entries(sel, "rgd/2022/12/07/a606")
    jo = es[E + "rgd/2022/12/07/a606/jo"]
    assert (jo.role, jo.reason, jo.text_valid) == (
        VersionRole.FUTURE,
        "2e:original_after_end",
        DateWindow(lower=d("2026-12-01")),
    )
    act, views = sel.acts[0], views_from_manifest(es.values())
    assert serving_at(act, views, [], d("2026-11-30")).text_basis is TextBasis.CONSOLIDATED
    after = serving_at(act, views, [], d("2026-12-01"))
    assert (after.text_basis, after.window_version, after.text_version) == (
        TextBasis.ORIGINAL_POSSIBLY_OUTDATED,
        None,
        E + "rgd/2022/12/07/a606/jo",
    )
