"""MCP contract tests (Appendix D.3, PLAN §13 M1) over Streamable HTTP with the official mcp 2.3.0 client.

Both protocol eras are exercised with the same SDK: ``mode="auto"`` negotiates the 2026-07-28 per-request era,
``mode="legacy"`` the initialize handshake (2025-11-25, the era of Open WebUI's mcp 1.27.2). The real 1.27.2
client runs in ``test_mcp_legacy.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from contract.conftest import LiveServer, mcp_client
from lgx_mcp.schemas import TOOL_NAMES, TOOLS_BY_NAME

G0 = json.loads((Path(__file__).parent / "g0_smoke.json").read_text(encoding="utf-8"))
A686 = "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686"
MODES = ["auto", "legacy"]


def resolve_path(data: Any, path: str) -> Any:
    cur: list[Any] = [data]
    flat = False
    for part in path.split("."):
        name, _, idx = part.partition("[")
        nxt: list[Any] = []
        for c in cur:
            v = c.get(name) if isinstance(c, dict) else None
            if idx:
                if v is None:
                    continue
                if idx == "*]":
                    nxt.extend(v)
                    flat = True
                    continue
                i = int(idx[:-1])
                v = v[i] if isinstance(v, list) and len(v) > i else None
            nxt.append(v)
        cur = nxt
    return cur if flat else (cur[0] if cur else None)


def check(op: str, actual: Any, value: Any) -> bool:
    seq = actual if isinstance(actual, list) else [actual]
    if op == "equals":
        return actual == value
    if op == "contains":
        return value in seq
    if op == "any_contains":
        return any(isinstance(a, str) and value in a for a in seq)
    if op == "all_equals":
        return bool(seq) and all(a == value for a in seq)
    if op == "nonempty":
        return bool(actual)
    if op == "len_ge":
        return isinstance(actual, list) and len(actual) >= value
    raise ValueError(op)


def assert_result_shape(name: str, result: Any) -> dict[str, Any]:
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert result.content and result.content[0].type == "text" and result.content[0].text
    if not result.is_error:
        errors = list(Draft202012Validator(TOOLS_BY_NAME[name]["outputSchema"]).iter_errors(sc))
        assert not errors, errors[0].message
        codes = [w["code"] for w in sc["warnings"]]
        assert "W_NOT_OFFICIAL" in codes and "W_SNAPSHOT" in codes
        assert "CC BY 4.0" in sc["attribution"] and "Journal officiel" in sc["disclaimer"]
        assert sc["snapshot"]["release_id"].startswith("data-20261016")
    else:
        assert set(sc["error"]) >= {"code", "message", "hint", "candidates"}
    return sc


# ------------------------------------------------------------------------------------- listing
@pytest.mark.parametrize("mode", MODES)
async def test_list_tools_contract(live: LiveServer, mode: str) -> None:
    async with mcp_client(live, mode) as c:
        if mode == "legacy":
            assert c.protocol_version == "2025-11-25"
        else:
            assert c.protocol_version == "2026-07-28"
        tools = (await c.list_tools()).tools
    assert [t.name for t in tools] == TOOL_NAMES
    for t in tools:
        spec = TOOLS_BY_NAME[t.name]
        assert t.input_schema == spec["inputSchema"]
        assert t.output_schema == spec["outputSchema"]
        Draft202012Validator.check_schema(t.input_schema)
        Draft202012Validator.check_schema(t.output_schema)
        assert not {"oneOf", "anyOf", "allOf"} & set(t.input_schema), t.name
        assert t.annotations is not None
        assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False
        assert t.annotations.idempotent_hint is True


# ---------------------------------------------------------------------------- every tool, both eras
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_legislation", {"query": "durée du travail heures supplémentaires", "filters": {"doc_types": ["CODE"]}}),
    ("get_article", {"citation": "art. L. 121-1 C. trav."}),
    ("get_act", {"eli": A686, "include": ["toc", "versions", "relations", "amendments"]}),
    ("resolve_citation", {"text": "art. 5 de la loi du 1er août 2018 portant organisation de la Commission nationale"}),
    ("about_corpus", {}),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("name", "args"), CALLS, ids=[c[0] for c in CALLS])
async def test_every_tool(live: LiveServer, mode: str, name: str, args: dict[str, Any]) -> None:
    async with mcp_client(live, mode) as c:
        result = await c.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    assert_result_shape(name, result)


# ------------------------------------------------------------------------------------ M1 acceptance
async def test_m1_acceptance_get_article_code_du_travail(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("get_article", {"citation": "art. L. 121-1 C. trav."})
    sc = assert_result_shape("get_article", r)
    cit = sc["citation"]
    assert cit["article_eli"] == "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1"
    assert cit["version_eli"] == "http://data.legilux.public.lu/eli/etat/leg/code/travail/20260726"
    assert cit["version_label"] == "version consolidée applicable au 26/07/2026"
    assert cit["act_eli"] == "http://data.legilux.public.lu/eli/etat/leg/loi/2006/07/31/n2"
    assert cit["citation_string"].startswith(
        "Art. L. 121-1, Code du travail (version consolidée applicable au 26/07/2026)"
    )
    assert cit["resource_uri"] == "legilux://eli/etat/leg/loi/2006/07/31/n2/art/l121-1"
    assert cit["display_url"] == "https://legilux.public.lu/eli/etat/leg/code/travail/20260726"
    assert sc["text"].startswith("Sans préjudice des dispositions légales existantes")
    assert "<<<TEXTE C1" in r.content[0].text


@pytest.mark.parametrize("item", G0["items"], ids=[i["id"] for i in G0["items"]])
async def test_g0_smoke(live: LiveServer, item: dict[str, Any]) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool(item["tool"], item["arguments"])
    assert bool(r.is_error) == bool(item.get("expect_error")), r.content[0].text[:500]
    sc = assert_result_shape(item["tool"], r)
    for path, op, value in item["checks"]:
        actual = resolve_path(sc, path)
        assert check(op, actual, value), f"{item['id']}: {path} {op} {value!r} — got {actual!r}"


# ------------------------------------------------------------------------------- search filters
async def test_filter_doc_types_applies_to_exact_branch(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool(
            "search_legislation", {"query": "L. 121-1", "filters": {"doc_types": ["LOI"]}, "top_k": 10}
        )
    sc = assert_result_shape("search_legislation", r)
    assert sc["query_interpretation"]["exact_tokens"] == ["l. 121-1"]
    assert all(h["citation"]["doc_type"] == "LOI" for h in sc["hits"])


async def test_filter_act_eli_unknown_is_not_found(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("search_legislation", {"query": "commission", "filters": {"act_eli": A686 + "999"}})
    assert r.is_error and r.structured_content["error"]["code"] == "NOT_FOUND"


async def test_filter_in_force_only_false_adds_stored_future_versions(live: LiveServer) -> None:
    act = "http://data.legilux.public.lu/eli/etat/leg/loi/2014/07/04/n2"
    args: dict[str, Any] = {"query": "normalisation accréditation", "filters": {"act_eli": act}, "top_k": 20}
    async with mcp_client(live) as c:
        now = (await c.call_tool("search_legislation", args)).structured_content
        args["filters"]["in_force_only"] = False
        both = (await c.call_tool("search_legislation", args)).structured_content
    now_versions = {h["citation"]["version_eli"] for h in now["hits"]}
    both_versions = {h["citation"]["version_eli"] for h in both["hits"]}
    assert now_versions == {act + "/consolide/20241231"}
    assert act + "/consolide/20261020" in both_versions
    future = [h for h in both["hits"] if h["citation"]["version_eli"].endswith("/20261020")]
    assert all("W_FUTURE_VERSION" in h["citation"]["warnings"] for h in future)


async def test_time_travel_switches_version_on_the_right_day(live: LiveServer) -> None:
    act = "http://data.legilux.public.lu/eli/etat/leg/loi/2014/07/04/n2"
    async with mcp_client(live) as c:
        before = (await c.call_tool("get_act", {"eli": act, "as_of": "2026-10-19"})).structured_content
        after = (await c.call_tool("get_act", {"eli": act, "as_of": "2026-10-20"})).structured_content
    assert before["text_version"]["version_eli"].endswith("/consolide/20241231")
    assert before["next_version"]["applicable_from"] == "2026-10-20"
    assert "W_FUTURE_VERSION" in [w["code"] for w in before["warnings"]]
    assert after["text_version"]["version_eli"].endswith("/consolide/20261020")


async def test_cited_unit_is_boosted(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("search_legislation", {"query": "art. L. 121-1 C. trav. salarié", "top_k": 3})
    sc = r.structured_content
    det = sc["query_interpretation"]["citations_detected"]
    assert det and det[0]["unit_eli"] == "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1"
    assert sc["hits"][0]["citation"]["article_eli"] == det[0]["unit_eli"]
    assert sc["hits"][0]["match"]["cited_hit"] is True


async def test_out_of_scope_query_warns_but_searches(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("search_legislation", {"query": "règlement (UE) 2016/679 protection des données"})
    sc = assert_result_shape("search_legislation", r)
    assert "W_OUT_OF_SCOPE" in [w["code"] for w in sc["warnings"]]
    assert sc["hits"]


@pytest.mark.parametrize(
    ("mode", "used", "reason"),
    [("auto", "lexical", "vectors disabled: provider_none"), ("hybrid", "lexical", "vectors disabled: provider_none"),
     ("lexical", "lexical", None)],
)  # fmt: skip
async def test_retrieval_modes_without_vectors(live: LiveServer, mode: str, used: str, reason: str | None) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("search_legislation", {"query": "contrat de travail", "retrieval_mode": mode, "top_k": 2})
    ret = r.structured_content["retrieval"]
    assert (ret["mode_requested"], ret["mode_used"], ret["vector_status"], ret["reason"]) == (
        mode,
        used,
        "disabled:provider_none",
        reason,
    )
    assert ret["reranked"] is False and ret["rerank_status"] == "off" and ret["embedding_model"] is None


# --------------------------------------------------------------------------------- tool errors
@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("get_article", {"citation": "art. 5 C. trav.", "act_eli": A686, "article": "5"}, "BAD_INPUT"),
        ("get_article", {"citation": "loi du 1er août 2018, art. 5"}, "AMBIGUOUS"),
        ("get_article", {"citation": "art. L. 999-99 C. trav."}, "NOT_FOUND"),
        ("get_article", {"citation": "RGPD, art. 6"}, "OUT_OF_SCOPE"),
        ("get_article", {"citation": "Code du travail"}, "BAD_INPUT"),
        (
            "get_article",
            {"act_eli": "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a689", "article": "1"},
            "NOT_FOUND",
        ),
        ("get_act", {"eli": A686, "citation": "CNPD"}, "BAD_INPUT"),
        ("get_act", {"eli": "http://data.legilux.public.lu/eli/etat/leg/loi/1700/01/01/n1"}, "NOT_FOUND"),
        ("search_legislation", {"query": "x"}, "BAD_INPUT"),
        ("search_legislation", {"query": "travail", "top_k": 50}, "BAD_INPUT"),
        ("search_legislation", {"query": "travail", "as_of": "2026-13-45"}, "BAD_INPUT"),
        (
            "resolve_citation",
            {"text": "arrêt de la Cour de cassation du 3 mai 2001", "as_of": "2030-01-01"},
            "AS_OF_OUT_OF_RANGE",
        ),
    ],
)
async def test_tool_errors(live: LiveServer, name: str, args: dict[str, Any], code: str) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool(name, args)
    assert r.is_error, r.content[0].text
    assert r.structured_content["error"]["code"] == code
    assert r.content[0].text.startswith(f"Erreur {code}")


async def test_ambiguous_article_lists_candidates(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("get_article", {"citation": "loi du 1er août 2018, art. 5"})
    cands = r.structured_content["error"]["candidates"]
    assert len(cands) == 20 and A686 in [x["act_eli"] for x in cands]


async def test_metadata_only_act_has_card_but_no_article(live: LiveServer) -> None:
    eli = "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a689"
    async with mcp_client(live) as c:
        card = (await c.call_tool("get_act", {"eli": eli})).structured_content
    assert card["text_basis"] == "metadata_only" and card["metadata_reason"] == "not_loaded"
    assert card["metadata_reason_label"] == "texte non chargé dans cette base (échantillon)"


# --------------------------------------------------------------------------------- warnings
async def test_not_consolidated_lists_amending_acts(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("get_act", {"eli": "http://data.legilux.public.lu/eli/etat/leg/loi/1922/03/05/n1"})
    sc = r.structured_content
    w = next(w for w in sc["warnings"] if w["code"] == "W_NOT_CONSOLIDATED")
    assert w["refs"] == ["C1"] and w["severity"] == "high"
    elis = [a["eli"] for a in w["data"]["amending_acts"]]
    assert elis == [a["eli"] for a in sc["amending_acts_since_text_date"]]
    assert "http://data.legilux.public.lu/eli/etat/leg/loi/1965/05/26/n1/jo" in elis


async def test_data_stale_and_future_as_of(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("get_act", {"eli": A686, "as_of": "2027-03-01"})
    codes = {w["code"]: w for w in r.structured_content["warnings"]}
    assert "W_DATA_STALE" in codes and codes["W_DATA_STALE"]["severity"] == "high"
    assert r.structured_content["snapshot"]["staleness_level"] == 0  # the data itself is fresh; as_of is far


async def test_default_as_of_is_the_pinned_clock(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("about_corpus", {})
    assert r.structured_content["as_of"] == "2026-10-16"
    assert r.structured_content["snapshot"]["clock_ok"] is True


# --------------------------------------------------------------------------- resources, prompts
@pytest.mark.parametrize("mode", MODES)
async def test_resources_and_prompts(live: LiveServer, mode: str) -> None:
    async with mcp_client(live, mode) as c:
        res = await c.list_resources()
        uris = sorted(r.uri for r in res.resources)
        templates = sorted(t.uri_template for t in (await c.list_resource_templates()).resource_templates)
        disclaimer = (await c.read_resource("legilux://legal/disclaimer")).contents[0]
        card = (await c.read_resource("legilux://eli/etat/leg/loi/2018/08/01/a686")).contents[0]
        art = (await c.read_resource("legilux://eli/etat/leg/loi/2006/07/31/n2/art/l121-1")).contents[0]
        prompts = (await c.list_prompts()).prompts
        texts = {
            lang: (await c.get_prompt("legal_answer", {"lang": lang})).messages[0].content.text
            for lang in ("fr", "de", "en")
        }
    assert "legilux://legal/cc-by-4.0" in uris and "legilux://legal/attribution" in uris
    assert templates == ["legilux://eli/{+path}", "legilux://eli/{+path}/art/{n}"]
    assert "Journal officiel" in disclaimer.text
    assert json.loads(card.text)["act_eli"] == A686 and card.mime_type == "application/json"
    assert json.loads(art.text)["citation"]["article_eli"].endswith("/code/travail/art_l_121-1")
    assert [p.name for p in prompts] == ["legal_answer"]
    from mcp import MCPError

    async with mcp_client(live, mode) as c:
        with pytest.raises(MCPError, match="NOT_FOUND"):
            await c.read_resource("legilux://eli/etat/leg/loi/1700/01/01/n1")
    assert (
        texts["fr"].startswith("Tu es un assistant")
        and texts["de"].startswith("Du bist")
        and texts["en"].startswith("You are")
    )


# --------------------------------------------------------------------------------- audit
async def test_audit_lines_hash_arguments_without_question_text(live: LiveServer, audit_path: Path) -> None:
    async with mcp_client(live) as c:
        await c.call_tool("search_legislation", {"query": "question secrète sur le préavis"})
    lines = [json.loads(x) for x in audit_path.read_text(encoding="utf-8").splitlines()]
    last = [x for x in lines if x["tool"] == "search_legislation"][-1]
    assert set(last) >= {"ts", "tool", "latency_ms", "hits", "is_error", "args_sha256", "client", "protocol_version"}
    assert "query" not in last and "secrète" not in json.dumps(lines, ensure_ascii=False)
    assert len(last["args_sha256"]) == 64
