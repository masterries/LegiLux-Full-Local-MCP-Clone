"""Offline unit tests of the MCP server's pure logic: as_of range, envelope, warning bag, paging, ranking."""

from __future__ import annotations

import datetime as dt

import pytest

from lgx_core.citeparse import Citation
from lgx_core.models import ToolErrorCode, WarningCode
from lgx_core.warnings import load_catalogue
from lgx_mcp.annotate import Annotator, acts_phrase, article_short, clip, status_basis
from lgx_mcp.context import Clock, ToolFailure, WarningBag, make_context
from lgx_mcp.facts import Act, AmendingAct, Version, normalise_eli, short_title_of
from lgx_mcp.release import ReleaseInfo
from lgx_mcp.render import fit, render_error
from lgx_mcp.resolve import AMBIGUITY_MARGIN, Resolver
from lgx_mcp.settings import McpSettings
from lgx_mcp.tools.article import decode_cursor, encode_cursor, page_of, page_size, page_starts
from lgx_mcp.tools.search import snippet_of

T = dt.date(2026, 10, 16)


def release() -> ReleaseInfo:
    return ReleaseInfo(
        release_id="data-20261016-sample",
        sync_date=T,
        horizon=T + dt.timedelta(days=365),
        schema_version=1,
        rules_version="m1",
        parser_version="akn-1",
        pipeline_git_sha="x",
        built_at=dt.datetime(2026, 10, 16, tzinfo=dt.UTC),
        config={"scope": {"stub_fallback_max_age_years": 5}},
        counts={},
        gates={},
        legal_texts={"attribution": {"fr": "Source : Legilux …"}, "disclaimer": {"fr": "Information non officielle …"}},
        catalogue=load_catalogue(),
        aliases={},
    )


def settings() -> McpSettings:
    return McpSettings.from_env({"LGX_MCP_AUTH": "off"}, transport=None)


def act(**kw: object) -> Act:
    base: dict[str, object] = {
        "act_id": 1, "eli_work": "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686", "eli_jo": None,
        "doc_type": "LOI", "date_document": None, "publication_date": dt.date(2018, 8, 16),
        "dei_min": dt.date(2018, 8, 20), "dnl": None, "rdf_status": "in-force", "memorial_ref": None,
        "title": "Loi du 1er août 2018 portant organisation de la CNPD.", "title_short": None, "citation_title": None,
        "text_basis": "consolidated", "metadata_reason": None, "flags": (), "basis_version_id": None,
        "superseded_by_act_id": None,
    }  # fmt: skip
    base.update(kw)
    return Act(**base)  # type: ignore[arg-type]


def version(**kw: object) -> Version:
    base: dict[str, object] = {
        "version_id": 1, "act_id": 1, "eli_version": "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo",
        "kind": "original", "role": "current", "date_applicability": dt.date(2018, 8, 20),
        "date_end_applicability": None, "is_stub": False, "has_files": True, "has_text": True,
        "valid_from": dt.date(2018, 8, 20), "valid_to": None, "label": "Texte original", "source_format": "xml",
        "source_url": "https://…", "licence": "cc-by", "sha256": "ab",
    }  # fmt: skip
    base.update(kw)
    return Version(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------- as_of / clock
def test_default_as_of_is_today_when_clock_ok() -> None:
    ctx = make_context(release(), settings(), Clock(override="2026-10-20T10:00:00"), None)
    assert ctx.as_of == dt.date(2026, 10, 20) and ctx.clock_ok and ctx.age_days == 4
    codes = [w["code"] for w in ctx.envelope()["warnings"]]
    assert codes == ["W_NOT_OFFICIAL", "W_SNAPSHOT"]
    snap = ctx.envelope()["snapshot"]
    assert snap == {"release_id": "data-20261016-sample", "sync_date": "2026-10-16", "horizon": "2027-10-16",
                    "age_days": 4, "staleness_level": 0, "clock_ok": True}  # fmt: skip


def test_suspect_clock_falls_back_to_T_and_warns() -> None:
    for now in ("2026-01-01T10:00:00", "2028-01-01T10:00:00"):
        ctx = make_context(release(), settings(), Clock(override=now), None)
        assert ctx.as_of == T and not ctx.clock_ok
        assert "W_CLOCK_SUSPECT" in [w["code"] for w in ctx.envelope()["warnings"]]


def test_as_of_range_and_format() -> None:
    clock = Clock(override="2026-10-16T10:00:00")
    with pytest.raises(ToolFailure) as exc:
        make_context(release(), settings(), clock, "2027-10-17")
    assert exc.value.code is ToolErrorCode.AS_OF_OUT_OF_RANGE
    make_context(release(), settings(), clock, "2027-10-16")  # H itself is allowed
    assert make_context(release(), settings(), clock, "1990-01-01").as_of == dt.date(1990, 1, 1)  # past: metadata
    with pytest.raises(ToolFailure) as exc:
        make_context(release(), settings(), clock, "16.10.2026")
    assert exc.value.code is ToolErrorCode.BAD_INPUT


@pytest.mark.parametrize(
    ("as_of", "level", "sev"), [("2026-11-14", 0, None), ("2026-11-20", 1, "medium"), ("2027-02-01", 2, "high")]
)
def test_data_stale_levels(as_of: str, level: int, sev: str | None) -> None:
    ctx = make_context(release(), settings(), Clock(override="2026-10-16T10:00:00"), as_of)
    stale = [w for w in ctx.envelope()["warnings"] if w["code"] == "W_DATA_STALE"]
    assert (stale[0]["severity"] if stale else None) == sev
    assert ctx.snapshot()["staleness_level"] == 0  # the data is fresh; only as_of is far (warning only)
    aged = make_context(release(), settings(), Clock(override="2027-02-01T10:00:00"), None)
    assert aged.snapshot()["staleness_level"] == 2 and level in (0, 1, 2)


def test_warning_bag_dedupes_and_tracks_refs() -> None:
    bag = WarningBag(load_catalogue())
    bag.add(WarningCode.W_DERIVED_ELI, ref="C1")
    bag.add(WarningCode.W_DERIVED_ELI, ref="C2")
    bag.add(WarningCode.W_NOT_OFFICIAL)
    bag.add(WarningCode.W_ARTICLE_REPEALED, ref="C2")
    items = bag.as_list()
    assert [w["code"] for w in items] == ["W_NOT_OFFICIAL", "W_ARTICLE_REPEALED", "W_DERIVED_ELI"]
    assert items[2]["refs"] == ["C1", "C2"] and bag.codes_for("C2") == ["W_DERIVED_ELI", "W_ARTICLE_REPEALED"]


# ------------------------------------------------------------------------------------ helpers
def test_paging_and_cursor_roundtrip() -> None:
    text = "\n".join(f"Ligne {i} " + "x" * 90 for i in range(300))
    page1, nxt = page_of(text, 0)
    assert nxt is not None and len(page1) <= 8000 and page1.endswith("x")
    page2, nxt2 = page_of(text, nxt)
    assert text.startswith(page1) and text[nxt:].startswith(page2)
    pages, off = [page1, page2], nxt2
    while off is not None:
        p, off = page_of(text, off)
        pages.append(p)
    assert "\n".join(pages) == text
    assert page_of("court", 0) == ("court", None)
    starts = page_starts(text)
    assert starts[0] == 0 and starts[1] == nxt and len(starts) == len(pages)
    assert page_size(4000) == 8000 and page_size(2500) == 5750 and page_size(100) == 2000
    cur = encode_cursor(42, nxt, "2026-10-16")
    assert decode_cursor(cur) == (42, nxt, "2026-10-16")
    with pytest.raises(ToolFailure):
        decode_cursor("not-a-cursor!")


def test_snippet_clip_fit() -> None:
    s = snippet_of("mot " * 400)
    assert len(s) <= 600 and s.endswith("…")
    assert clip("a  b\nc", 10) == "a b c" and clip("x" * 50, 10) == "x" * 9 + "…" and clip(None, 5) == ""
    assert len(fit("y" * 50000, 100)) <= 400
    assert "Erreur NOT_FOUND" in render_error(
        {"code": "NOT_FOUND", "message": "m", "hint": "h", "candidates": []}, None
    )


def test_act_helpers() -> None:
    a = act()
    assert a.memorial == "Mémorial A n° 686 de 2018" and a.memorial_short == "Mém. A n° 686/2018"
    assert (
        act(
            memorial_ref="Mémorial A n° 15 de 1922",
            eli_work="http://data.legilux.public.lu/eli/etat/leg/loi/1922/03/05/n1",
        ).memorial_short
        == "Mém. A n° 15/1922"
    )
    assert status_basis(act()) == "dates"
    assert status_basis(act(rdf_status="no-longer-in-force-implicit")) == "rdf_flag"
    assert status_basis(act(rdf_status="no-longer-in-force", dnl=None)) == "rdf_flag"
    assert short_title_of(act()) == "Loi du 1er août 2018 portant organisation de la CNPD"
    rec = act(
        title=None, doc_type="RECUEIL", eli_work="http://data.legilux.public.lu/eli/etat/leg/recueil/presse_medias"
    )
    assert short_title_of(rec) == "Recueil presse medias"
    assert article_short("Art. L. 121-1.") == "Art. L. 121-1" and article_short(None) is None
    assert version().display_label == "texte original"
    v = version(kind="consolidated", label="Version consolidée applicable au 26/12/2025")
    assert v.display_label == "version consolidée applicable au 26/12/2025"
    assert v.window_contains(dt.date(2020, 1, 1)) and not v.window_contains(dt.date(2018, 1, 1))
    acts = [AmendingAct(f"e{i}", "modifies", dt.date(2020, 1, i + 1), f"Loi {i}") for i in range(7)]
    phrase = acts_phrase(acts)
    assert phrase.count("Loi") == 5 and phrase.endswith("et 2 autre(s) acte(s)") and "01/01/2020" in phrase
    assert acts_phrase([]) == "actes modificatifs postérieurs (voir get_act)"


def test_citation_string_formats() -> None:
    consolidated = version(kind="code", label="Version consolidée applicable au 26/07/2026",
                           eli_version="http://data.legilux.public.lu/eli/etat/leg/code/travail/20260726")  # fmt: skip
    s = Annotator.citation_string("Art. L. 121-1", "Code du travail", consolidated, act(), "consolidated",
                                  "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1")  # fmt: skip
    assert s == (
        "Art. L. 121-1, Code du travail (version consolidée applicable au 26/07/2026) — "
        "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1"
    )
    loi = act(memorial_ref="Mémorial A n° 82 de 2001")
    o = Annotator.citation_string(
        "Art. 5", "Loi du 20 juin 2001 sur l'extradition", version(), loi, "original_possibly_outdated", None
    )
    assert "(texte original, Mém. A n° 82/2001 — non consolidé)" in o and o.endswith("/a686/jo")


def test_normalise_eli_forms() -> None:
    e = "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686"
    assert normalise_eli("legilux://eli/etat/leg/loi/2018/08/01/a686") == e
    assert normalise_eli("https://legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/") == e
    assert normalise_eli("etat/leg/loi/2018/08/01/a686") == e
    assert normalise_eli(e) == e


# --------------------------------------------------------------------------------- ranking rule
def test_never_guess_margin() -> None:
    cit = Citation(span=(0, 1), text="x")
    a1, a2, a3 = act(act_id=1), act(act_id=2, eli_work="b"), act(act_id=3, eli_work="c")
    close = Resolver._ranked(cit, [(a1, 0.70), (a2, 0.70 - AMBIGUITY_MARGIN + 0.01)], "type_date_title")
    assert close.status == "ambiguous" and len(close.candidates) == 2
    clear = Resolver._ranked(cit, [(a1, 0.9), (a2, 0.5), (a3, 0.4)], "type_date_title")
    assert clear.status == "resolved" and clear.candidates[0].act is a1
    weak = Resolver._ranked(cit, [(a1, 0.15), (a2, 0.05)], "title_fuzzy", weak_is_ambiguous=True)
    assert weak.status == "ambiguous"
    single = Resolver._ranked(cit, [(a1, 0.3)], "alias")
    assert single.status == "resolved"
    assert Resolver._ranked(cit, [], "alias").status == "not_found"
