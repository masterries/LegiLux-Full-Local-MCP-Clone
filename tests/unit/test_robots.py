"""robots.txt parsing, the hard gate, acknowledgement and the ELI denylist (offline)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
import respx

from lgx_ingest.context import StageContext
from lgx_ingest.robots import (
    DATA_HOST,
    MAIN_HOST,
    REVIEWED_SHA256,
    RobotsRules,
    acknowledge,
    denylist_entries,
    eli_prefix,
    evaluate,
    load_policy,
    run_robots,
    sha256_text,
    url_to_eli,
)
from lgx_ingest.sparql import PoliteHttp, RetryPolicy

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "robots"
MAIN = (FIXTURES / "robots-legilux-20261016.txt").read_text(encoding="utf-8")
DATA = (FIXTURES / "robots-data-20261016.txt").read_text(encoding="utf-8")
T = dt.date(2026, 10, 16)
E = "http://data.legilux.public.lu/eli/"


def test_fixtures_are_the_reviewed_baseline() -> None:
    assert sha256_text(MAIN) == REVIEWED_SHA256[MAIN_HOST]
    assert sha256_text(DATA) == REVIEWED_SHA256[DATA_HOST]


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/filestore/eli/etat/leg/loi/2016/12/23/n25/jo/fr/xml/eli-etat-leg-loi-2016-12-23-n25-jo-fr-xml.xml", True),
        (
            "/filestore/eli/etat/leg/loi/2023/04/19/a203/jo/fr/docx/eli-etat-leg-loi-2023-04-19-a203-jo-fr-docx.docx",
            False,
        ),
        ("/eli/etat/adm/amin/2021/05/26/b2350", False),
        ("/eli/etat/leg/loi/2007/01/15/n2/jo", False),
        ("/eli/etat/leg/loi/2007/01/15/n1/jo", True),
        ("/reg_ue/2016/679", False),
        ("/search?q=x", False),
        ("/assets/logo.svg", False),
        ("/publications-regroupees", False),
    ],
)
def test_main_host_rules(path: str, allowed: bool) -> None:
    assert RobotsRules.parse(MAIN).allows(path) is allowed


def test_data_host_disallows_eli_and_filestore() -> None:
    rules = RobotsRules.parse(DATA)
    assert not rules.allows("/filestore/eli/etat/leg/x.xml")
    assert not rules.allows("/eli/etat/leg/loi/2016/12/23/n25/jo")
    assert rules.allows("/sparqlendpoint")


def test_matching_semantics_longest_match_allow_wins_tie_and_named_group() -> None:
    rules = RobotsRules.parse(
        "User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /x$\nAllow: /t\nDisallow: /t\n\n"
        "User-agent: LegiLux-Local-Clone\nUser-agent: other\nDisallow: /only-us\n"
    )
    star = rules.rules_for("somebot")
    assert [r.path for r in star] == ["/a", "/a/b", "/x$", "/t", "/t"]
    assert rules.allows("/a/c", "somebot") is False
    assert rules.allows("/a/b/c", "somebot") is True
    assert rules.allows("/x", "somebot") is False and rules.allows("/xy", "somebot") is True
    assert rules.allows("/t", "somebot") is True  # tie: allow wins
    assert rules.allows("/only-us") is False and rules.allows("/a/c") is True  # our group replaces *


@pytest.mark.parametrize(
    ("path", "prefix"),
    [
        ("/eli/etat/leg/loi/2007/01/15/n2/jo", E + "etat/leg/loi/2007/01/15/n2"),
        ("/eli/etat/leg/loi/2007/01/15/n2/jo/fr/html", E + "etat/leg/loi/2007/01/15/n2"),
        ("/eli/etat/leg/memorial/2007/8/fr/pdf", E + "etat/leg/memorial/2007/8"),
        ("/eli/etat/adm/amin/2021/05/26/b2350", E + "etat/adm/amin/2021/05/26/b2350"),
        ("/eli/etat/adm/", None),
        ("/eli/*", None),
        ("/*.docx", None),
        ("/search", None),
    ],
)
def test_eli_prefix(path: str, prefix: str | None) -> None:
    assert eli_prefix(path) == prefix


def test_denylist_from_fixture() -> None:
    entries = denylist_entries({MAIN_HOST: RobotsRules.parse(MAIN), DATA_HOST: RobotsRules.parse(DATA)}, T)
    assert [e["eli"] for e in entries] == [
        E + "etat/adm/amin/2021/05/26/b2350",
        E + "etat/adm/pa/2017/01/10/b159",
        E + "etat/adm/pa/2017/01/16/b326",
        E + "etat/leg/loi/2007/01/15/n2",
        E + "etat/leg/memorial/2007/8",
    ]
    nat = next(e for e in entries if e["eli"].endswith("n2"))
    assert len(nat["paths"]) == 3 and nat["source"] == "robots" and nat["added"] == "2026-10-16"


def test_evaluate_baseline_change_and_hard_stop() -> None:
    state, _ = evaluate({MAIN_HOST: MAIN, DATA_HOST: DATA}, {}, T)
    assert not state["paused"] and state["filestore_allowed"] and not state["hard_stop"]
    changed = MAIN + "Disallow: /new-thing\n"
    state, _ = evaluate({MAIN_HOST: changed, DATA_HOST: DATA}, {}, T)
    assert state["paused"] and not state["hard_stop"] and not state["hosts"][MAIN_HOST]["acknowledged"]
    state, _ = evaluate({MAIN_HOST: changed, DATA_HOST: DATA}, {MAIN_HOST: [sha256_text(changed)]}, T)
    assert not state["paused"]
    for blocker in ("Disallow: /filestore/\n", "Disallow: /*.xml\n", "Disallow: /filestore/eli/etat/leg/loi/\n"):
        state, _ = evaluate({MAIN_HOST: MAIN + blocker, DATA_HOST: DATA}, {}, T)
        assert state["hard_stop"] and state["paused"], blocker


def test_url_to_eli() -> None:
    url = "https://legilux.public.lu/filestore/eli/etat/leg/loi/2007/01/15/n2/jo/fr/xml/eli-x.xml"
    assert url_to_eli(url) == E + "etat/leg/loi/2007/01/15/n2/jo/fr/xml/eli-x.xml"
    assert (
        url_to_eli("https://legilux.public.lu/eli/etat/leg/loi/2007/01/15/n2/jo") == E + "etat/leg/loi/2007/01/15/n2/jo"
    )
    assert url_to_eli("https://legilux.public.lu/search") is None


# ------------------------------------------------------------------------------- stage + policy
def _http() -> PoliteHttp:
    return PoliteHttp(user_agent="LegiLux-Local-Clone/0.1 (+test)", spacing_s=0.0, retry=RetryPolicy(max_retries=0))


def _mock(main: str = MAIN, data: str = DATA) -> None:
    respx.get("https://legilux.public.lu/robots.txt").mock(return_value=httpx.Response(200, text=main))
    respx.get("https://data.legilux.public.lu/robots.txt").mock(return_value=httpx.Response(200, text=data))


@respx.mock
def test_run_robots_writes_state_and_policy(tmp_path: Path) -> None:
    _mock()
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path)
    result = run_robots(ctx, http=_http())
    assert result.ok, result.messages
    assert result.counts["denylist_entries"] == 5
    assert (ctx.layout.robots_dir / "robots-legilux.txt").read_text(encoding="utf-8") == MAIN
    policy = load_policy(ctx)
    ok = "https://legilux.public.lu/filestore/eli/etat/leg/loi/2016/12/23/n25/jo/fr/xml/eli-etat-leg-loi-2016-12-23-n25-jo-fr-xml.xml"
    assert policy.allows(ok)
    assert not policy.allows("https://data.legilux.public.lu/filestore/eli/etat/leg/x.xml")
    assert not policy.allows("https://example.org/filestore/eli/etat/leg/x.xml")  # unknown host
    nat = "https://legilux.public.lu/filestore/eli/etat/leg/loi/2007/01/15/n2/jo/fr/xml/eli-etat-leg-loi-2007-01-15-n2-jo-fr-xml.xml"
    assert policy.allows(nat)  # robots lists the /eli/ page, not the filestore path …
    assert policy.is_denied_eli(nat)  # … but the takedown applies to its files too
    assert policy.is_denied_eli(E + "etat/leg/loi/2007/01/15/n2/jo")
    assert not policy.is_denied_eli(E + "etat/leg/loi/2007/01/15/n20/jo")


@respx.mock
def test_changed_robots_pauses_until_acknowledged(tmp_path: Path) -> None:
    changed = MAIN + "Disallow: /new-thing\n"
    _mock(main=changed)
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path)
    result = run_robots(ctx, http=_http())
    assert not result.ok and "robots-ack" in result.messages[0]
    assert not load_policy(ctx).allows("https://legilux.public.lu/filestore/eli/etat/leg/a.xml")
    assert not acknowledge(ctx, "0" * 64).ok
    ack = acknowledge(ctx, sha256_text(changed))
    assert ack.ok, ack.messages
    assert json.loads((tmp_path / "robots" / "ack.json").read_text())[MAIN_HOST] == [sha256_text(changed)]
    assert load_policy(ctx).allows("https://legilux.public.lu/filestore/eli/etat/leg/a.xml")
    assert run_robots(ctx, http=_http()).ok  # acknowledged content stays accepted on the next run


@respx.mock
def test_filestore_disallowed_is_a_hard_stop(tmp_path: Path) -> None:
    blocked = MAIN + "Disallow: /filestore/\n"
    _mock(main=blocked)
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path)
    result = run_robots(ctx, http=_http())
    assert not result.ok and any("HARD STOP" in m for m in result.messages)
    assert not acknowledge(ctx, sha256_text(blocked)).ok
    assert not load_policy(ctx).allows("https://legilux.public.lu/filestore/eli/etat/leg/a.xml")


@respx.mock
def test_unreachable_robots_keeps_fetch_paused(tmp_path: Path) -> None:
    respx.get("https://legilux.public.lu/robots.txt").mock(return_value=httpx.Response(503))
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path)
    assert not run_robots(ctx, http=_http()).ok
    with pytest.raises(FileNotFoundError):
        load_policy(ctx)


def test_manual_denylist_applies(tmp_path: Path) -> None:
    from lgx_core.config import DenylistConfig, DenylistEntry

    ctx = StageContext.create(sync_date=T, data_dir=tmp_path)
    out = ctx.layout.robots_dir
    out.mkdir(parents=True)
    state, _ = evaluate({MAIN_HOST: MAIN, DATA_HOST: DATA}, {}, T)
    (out / "robots.json").write_text(json.dumps(state))
    (out / "robots-legilux.txt").write_text(MAIN)
    (out / "robots-data.txt").write_text(DATA)
    manual = DenylistConfig(entries=[DenylistEntry(eli="etat/leg/rgd/2000/01/01/n9", reason="t", added=T)])
    ctx2 = StageContext(
        sync_date=T,
        settings=ctx.settings,
        config=ctx.config.model_copy(update={"denylist": manual}),
        layout=ctx.layout,
    )
    policy = load_policy(ctx2)
    assert policy.is_denied_eli(E + "etat/leg/rgd/2000/01/01/n9/jo")
    assert not policy.is_denied_eli(E + "etat/leg/loi/2007/01/15/n2/jo")  # no denylist.json written here


@pytest.mark.live
def test_live_robots_check(tmp_path: Path) -> None:
    """Two polite GETs (both robots.txt) — only with --run-live. A change pauses, it must not hard-stop."""
    ctx = StageContext.create(data_dir=tmp_path)
    result = run_robots(ctx)
    state = json.loads((ctx.layout.robots_dir / "robots.json").read_text())
    assert state["filestore_allowed"] and not state["hard_stop"]
    assert result.ok or any("robots-ack" in m for m in result.messages)
