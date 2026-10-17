"""lgx_ui.tools_bridge: schema conversion, sidebar overrides, validation, the tool loop (multi-step, parallel calls,
max-iteration stop, error propagation, time budget) and the retrieve-first fallback — against a scripted fake
OpenAI-compatible server (real ``openai`` client over ``httpx2.MockTransport``) and a fake MCP session."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from lgx_ui.llm import LlmConfig, OpenAICompatChat
from lgx_ui.mcp_client import ToolOutcome
from lgx_ui.tools_bridge import (
    COMBINATORS,
    LoopConfig,
    SidebarOverrides,
    compact_json,
    openai_tools,
    probe_tool_calling,
    run_chat_turn,
    run_tool_loop,
    sanitize_parameters,
    validate_arguments,
)
from lgx_ui.tools_bridge import apply_overrides as _apply
from unit.test_ui_fakes import ELI, SCHEMAS, FakeOpenAIServer, FakeSession, fake_specs, tool_call_message

SYSTEM = "PROMPT"
CFG = LlmConfig(base_url="http://llm:11434/v1", model="fake-model", api_key="sk-test", timeout_s=5)


def llm_for(server: FakeOpenAIServer) -> OpenAICompatChat:
    return OpenAICompatChat(CFG, http_client=server.http_client())


async def turn(
    server: FakeOpenAIServer,
    session: FakeSession | Any,
    *,
    overrides: SidebarOverrides | None = None,
    config: LoopConfig | None = None,
    mode: str = "auto",
    question: str = "Qu'est-ce qu'un contrat de travail ?",
) -> Any:
    async with llm_for(server) as llm:
        return await run_chat_turn(
            llm=llm,
            session=session,
            tools=fake_specs(),
            system_prompt=SYSTEM,
            history=[],
            user_message=question,
            overrides=overrides or SidebarOverrides(),
            config=config or LoopConfig(),
            mode=mode,
        )


# ------------------------------------------------------------------------------------- schema conversion


def test_all_appendix_d_tools_convert_to_valid_openai_functions() -> None:
    tools, notes = openai_tools(fake_specs())
    assert notes == []
    assert [t["function"]["name"] for t in tools] == list(SCHEMAS)
    for tool in tools:
        assert tool["type"] == "function"
        params = tool["function"]["parameters"]
        Draft202012Validator.check_schema(params)
        assert params["type"] == "object"
        assert not set(COMBINATORS) & set(params), "no top-level combinators (Appendix D.0 / D.3)"
        assert tool["function"]["description"]
    # nested structure is kept verbatim (the model sees the real enums and defaults)
    search = tools[0]["function"]["parameters"]
    assert search["properties"]["filters"]["properties"]["doc_types"]["items"]["enum"][0] == "Constitution"
    assert search["required"] == ["query"]


def test_sanitize_drops_top_level_combinators_and_forces_object() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "oneOf": [{"required": ["citation"]}, {"required": ["act_eli", "article"]}],
        "anyOf": [{}],
        "properties": {"citation": {"type": "string", "anyOf": [{"minLength": 3}]}},
    }
    out = sanitize_parameters(schema)
    assert out["type"] == "object"
    assert "oneOf" not in out and "anyOf" not in out and "$schema" not in out
    assert out["properties"]["citation"]["anyOf"] == [{"minLength": 3}], "nested keywords untouched"
    assert "oneOf" in schema, "the original schema is not mutated"
    assert sanitize_parameters(None) == {"type": "object", "properties": {}}


def test_invalid_schema_is_skipped_with_a_note() -> None:
    from lgx_ui.mcp_client import ToolSpec

    bad = ToolSpec("broken", "x", {"type": "object", "properties": {"a": {"type": 12}}})
    tools, notes = openai_tools([*fake_specs(), bad])
    assert "broken" not in [t["function"]["name"] for t in tools]
    assert notes and notes[0].startswith("broken")


# ------------------------------------------------------------------------------------- overrides


def apply(tool: str, args: Mapping[str, Any], ov: SidebarOverrides) -> Any:
    return _apply(args, SCHEMAS[tool], ov)


FULL = SidebarOverrides(
    as_of="2026-11-01",
    in_force_only=False,
    doc_types=("CODE", "LOI"),
    retrieval_mode="lexical",
    top_k_cap=5,
)


def test_overrides_win_over_the_model_for_search() -> None:
    model_args = {
        "query": "licenciement",
        "as_of": "2020-01-01",
        "filters": {"in_force_only": True, "doc_types": ["RGD"], "act_eli": f"{ELI}/loi/2018/08/01/a686"},
        "top_k": 15,
        "retrieval_mode": "hybrid",
    }
    res = apply("search_legislation", model_args, FULL)
    assert res.arguments == {
        "query": "licenciement",
        "as_of": "2026-11-01",
        "filters": {"in_force_only": False, "doc_types": ["CODE", "LOI"], "act_eli": f"{ELI}/loi/2018/08/01/a686"},
        "top_k": 5,
        "retrieval_mode": "lexical",
    }
    assert set(res.applied) == {
        "as_of=2026-11-01",
        'filters.doc_types=["CODE", "LOI"]',
        "filters.in_force_only=false",
        "retrieval_mode=lexical",
        "top_k 15→5",
    }
    assert model_args["top_k"] == 15, "the model's arguments are not mutated"


def test_overrides_are_scoped_to_the_arguments_each_tool_accepts() -> None:
    art = apply("get_article", {"citation": "art. L. 121-1 C. trav."}, FULL)
    assert art.arguments == {"citation": "art. L. 121-1 C. trav.", "as_of": "2026-11-01"}
    act = apply("get_act", {"eli": f"{ELI}/loi/2018/05/30/a446"}, FULL)
    assert set(act.arguments) == {"eli", "as_of"}
    cite = apply("resolve_citation", {"text": "loi du 1er août 2018"}, FULL)
    assert cite.arguments == {"text": "loi du 1er août 2018", "as_of": "2026-11-01"}
    about = apply("about_corpus", {}, FULL)
    assert about.arguments == {} and about.applied == []


def test_top_k_cap_and_unset_overrides() -> None:
    # model omitted top_k: the schema default (8) is larger than the cap -> cap applied
    res = apply("search_legislation", {"query": "bail"}, SidebarOverrides(top_k_cap=3))
    assert res.arguments == {"query": "bail", "top_k": 3}
    # cap above the default and the model's value: untouched
    res = apply("search_legislation", {"query": "bail", "top_k": 4}, SidebarOverrides(top_k_cap=20))
    assert res.arguments == {"query": "bail", "top_k": 4} and res.applied == []
    # no sidebar doc_types: the model's own filter stays; in_force_only forced
    res = apply(
        "search_legislation", {"query": "bail", "filters": {"doc_types": ["LOI"]}}, SidebarOverrides(in_force_only=True)
    )
    assert res.arguments["filters"] == {"doc_types": ["LOI"], "in_force_only": True}
    # nothing set: arguments identical
    res = apply("search_legislation", {"query": "bail"}, SidebarOverrides())
    assert res.arguments == {"query": "bail"} and res.applied == []


def test_override_values_outside_the_schema_enum_are_dropped() -> None:
    res = apply(
        "search_legislation",
        {"query": "bail"},
        SidebarOverrides(doc_types=("CODE", "NOPE"), retrieval_mode="dense"),
    )
    assert res.arguments == {"query": "bail", "filters": {"doc_types": ["CODE"]}}


def test_validate_arguments_reports_schema_errors() -> None:
    assert validate_arguments({"query": "bail"}, SCHEMAS["search_legislation"]) == []
    errors = validate_arguments(
        {"query": "b", "top_k": 50, "as_of": "16/10/2026", "bogus": 1}, SCHEMAS["search_legislation"]
    )
    joined = " | ".join(errors)
    assert "query" in joined and "top_k" in joined and "as_of" in joined
    assert "bogus" in joined  # additionalProperties: false
    assert validate_arguments({}, SCHEMAS["resolve_citation"]) == ["(racine): 'text' is a required property"]


def test_compact_json_respects_the_token_budget() -> None:
    from lgx_core.textsize import estimate_tokens

    big = {
        "hits": [{"rank": i, "snippet": "x" * 600, "citation": {"key": f"C{i}"}} for i in range(1, 41)],
        "attribution": "Source : Legilux",
    }
    small = compact_json({"a": 1}, 100)
    assert json.loads(small) == {"a": 1}
    text = compact_json(big, 1000)
    assert estimate_tokens(text) <= 1000
    data = json.loads(text)  # list tails were dropped, the JSON stays valid
    assert 1 <= len(data["hits"]) < 40 and data["hits"][0]["rank"] == 1 and data["attribution"]
    shortened = compact_json("y" * 50_000, 200)  # long strings are cut to 600 chars first, JSON stays valid
    assert estimate_tokens(shortened) <= 200 and len(json.loads(shortened)) == 600
    flat = {f"key{i}": "value" for i in range(5000)}  # no list to trim, no long string: hard cut with a marker
    cut = compact_json(flat, 200)
    assert estimate_tokens(cut) <= 200 and cut.endswith("tokens]")


# ------------------------------------------------------------------------------------- tool loop


async def test_multi_step_tool_loop_with_overrides() -> None:
    server = FakeOpenAIServer(
        [
            tool_call_message(("call_a", "search_legislation", {"query": "contrat de travail", "top_k": 12})),
            tool_call_message(("call_b", "get_article", {"citation": "art. L. 121-1 C. trav."})),
            {
                "role": "assistant",
                "content": "Réponse [C1] — http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1",
            },
        ]
    )
    session = FakeSession()
    ov = SidebarOverrides(as_of="2026-10-16", in_force_only=True, retrieval_mode="lexical", top_k_cap=6)
    result = await turn(server, session, overrides=ov)

    assert result.mode == "tool_calling" and result.stop_reason == "final"
    assert result.answer.startswith("Réponse [C1]")
    assert result.llm_calls == 3 and result.iterations == 3
    assert [c[0] for c in session.calls] == ["search_legislation", "get_article"]
    assert session.calls[0][1] == {
        "query": "contrat de travail",
        "top_k": 6,
        "as_of": "2026-10-16",
        "filters": {"in_force_only": True},
        "retrieval_mode": "lexical",
    }
    assert session.calls[1][1] == {"citation": "art. L. 121-1 C. trav.", "as_of": "2026-10-16"}
    t0 = result.traces[0]
    assert t0.model_arguments == {"query": "contrat de travail", "top_k": 12}
    assert "top_k 12→6" in t0.overrides_applied and not t0.is_error

    first = server.requests[0]
    assert first["model"] == "fake-model" and first["tool_choice"] == "auto" and first["temperature"] == 0.2
    assert [t["function"]["name"] for t in first["tools"]] == list(SCHEMAS)
    assert first["messages"][0] == {"role": "system", "content": SYSTEM}
    assert server.headers[0]["authorization"] == "Bearer sk-test"
    # second request: assistant tool call + tool result (compact structuredContent) with the call id
    second = server.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "call_a"
    tool_msg = second[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_a"
    payload = json.loads(tool_msg["content"])
    assert payload["hits"][0]["citation"]["key"] == "C1" and payload["retrieval"]["mode_used"] == "lexical"
    assert result.mcp_latency_s >= 0 and result.total_s >= result.llm_latency_s


async def test_parallel_tool_calls_run_concurrently_and_keep_order() -> None:
    started: list[str] = []
    gate = asyncio.Event()

    class SlowSession(FakeSession):
        async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
            started.append(name)
            if len(started) == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=2)  # only returns if both calls are in flight
            return await super().call_tool(name, arguments)

    server = FakeOpenAIServer(
        [
            tool_call_message(
                ("p1", "get_act", {"eli": f"{ELI}/loi/2018/05/30/a446"}),
                ("p2", "resolve_citation", {"text": "loi du 1er août 2018"}),
            ),
            {"role": "assistant", "content": "ok"},
        ]
    )
    result = await turn(server, SlowSession())
    assert result.stop_reason == "final" and len(result.traces) == 2
    msgs = server.requests[1]["messages"]
    assert [m["tool_call_id"] for m in msgs if m["role"] == "tool"] == ["p1", "p2"]
    assert len(msgs[-3]["tool_calls"]) == 2


async def test_max_iterations_stops_the_loop() -> None:
    always = [tool_call_message((f"c{i}", "search_legislation", {"query": f"requête {i}"})) for i in range(10)]
    server = FakeOpenAIServer(always)
    session = FakeSession()
    result = await turn(server, session, config=LoopConfig(max_iterations=3))
    assert result.stop_reason == "max_iterations"
    assert result.llm_calls == 3 and len(session.calls) == 3 and len(server.requests) == 3
    assert any("max_iterations" in n for n in result.notes)


async def test_errors_are_fed_back_to_the_model() -> None:
    client_failure = ToolOutcome(
        "resolve_citation", {}, True, error_code="CLIENT_ERROR", error_message="connexion impossible (ConnectError)"
    )
    session = FakeSession(fail={"resolve_citation": client_failure})
    server = FakeOpenAIServer(
        [
            tool_call_message(
                ("e1", "search_legislation", "{not json"),
                ("e2", "no_such_tool", {}),
                ("e3", "search_legislation", {"query": "x", "top_k": 99}),
                ("e4", "get_article", {"article": "5"}),
                ("e5", "resolve_citation", {"text": "loi du 1er août 2018"}),
                ("e6", "get_act", "[1, 2]"),
            ),
            {"role": "assistant", "content": "Les textes disponibles localement ne permettent pas de répondre."},
        ]
    )
    result = await turn(server, session)
    assert result.stop_reason == "final" and len(result.traces) == 6
    assert all(t.is_error for t in result.traces)
    # only the two calls with valid arguments reached the server
    assert [c[0] for c in session.calls] == ["get_article", "resolve_citation"]
    tool_msgs = {
        m["tool_call_id"]: json.loads(m["content"]) for m in server.requests[1]["messages"] if m["role"] == "tool"
    }
    assert "JSON illisibles" in tool_msgs["e1"]["error"]["message"]
    assert "outil inconnu" in tool_msgs["e2"]["error"]["message"]
    assert tool_msgs["e3"]["error"]["code"] == "BAD_INPUT"
    assert any("top_k" in d for d in tool_msgs["e3"]["error"]["details"])
    assert tool_msgs["e4"]["error"]["code"] == "BAD_INPUT"  # server-side isError (Appendix D.0 error object)
    assert tool_msgs["e5"]["error"] == {"code": "CLIENT_ERROR", "message": "connexion impossible (ConnectError)"}
    assert "objet JSON" in tool_msgs["e6"]["error"]["message"]


async def test_time_budget_stops_before_the_next_llm_call() -> None:
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])

    def clock() -> float:
        return next(ticks, 100.0)

    server = FakeOpenAIServer([tool_call_message(("t1", "about_corpus", {}))] * 3)
    async with llm_for(server) as llm:
        result = await run_tool_loop(
            llm=llm,
            session=FakeSession(),
            tools=fake_specs(),
            system_prompt=SYSTEM,
            history=[],
            user_message="?",
            overrides=SidebarOverrides(),
            config=LoopConfig(time_budget_s=50),
            clock=clock,
        )
    assert result.stop_reason == "time_budget" and result.llm_calls == 1


async def test_llm_http_error_is_reported() -> None:
    server = FakeOpenAIServer([500], error_message="internal server error")
    result = await turn(server, FakeSession())
    assert result.stop_reason == "llm_error" and "500" in (result.error or "")


# ------------------------------------------------------------------------------------- retrieve-first


async def test_endpoint_rejecting_tools_falls_back_to_retrieve_first() -> None:
    server = FakeOpenAIServer(
        [400, {"role": "assistant", "content": "Selon [C1] …"}], error_message="model does not support tools"
    )
    session = FakeSession()
    ov = SidebarOverrides(as_of="2026-10-16", doc_types=("CODE",), top_k_cap=4)
    result = await turn(server, session, overrides=ov, question="contrat de travail définition")
    assert result.mode == "retrieve_first" and result.stop_reason == "final"
    assert result.answer == "Selon [C1] …"
    assert session.calls == [
        (
            "search_legislation",
            {
                "query": "contrat de travail définition",
                "as_of": "2026-10-16",
                "filters": {"doc_types": ["CODE"]},
                "top_k": 4,
            },
        )
    ]
    second = server.requests[1]
    assert "tools" not in second, "the fallback call never sends tools"
    context = second["messages"][-1]["content"]
    assert "[C1] Art. L. 121-1, Code du travail" in context and "<<<TEXTE" in context
    assert "W_NOT_CONSOLIDATED" in context and "Attribution :" in context
    assert any("refuse les outils" in n for n in result.notes)


async def test_tools_only_mode_reports_the_rejection() -> None:
    server = FakeOpenAIServer([400], error_message="model does not support tools")
    result = await turn(server, FakeSession(), mode="tools")
    assert result.stop_reason == "llm_error" and "outils refusés" in (result.error or "")


async def test_retrieve_first_mode_and_search_errors() -> None:
    failing = FakeSession(
        fail={
            "search_legislation": ToolOutcome("search_legislation", {}, True, error_code="TIMEOUT", error_message="t")
        }
    )
    result = await turn(FakeOpenAIServer(), failing, mode="retrieve_first")
    assert result.mode == "retrieve_first" and result.stop_reason == "mcp_error" and "TIMEOUT" in (result.error or "")


async def test_probe_tool_calling() -> None:
    yes = FakeOpenAIServer([tool_call_message(("p", "search_legislation", {"query": "contrat de travail"}))])
    no = FakeOpenAIServer([{"role": "assistant", "content": "Le contrat de travail est …"}])
    refused = FakeOpenAIServer([400], error_message="does not support tools")
    async with llm_for(yes) as llm:
        assert (await probe_tool_calling(llm, fake_specs())).tool_calling is True
    async with llm_for(no) as llm:
        res = await probe_tool_calling(llm, fake_specs())
        assert res.tool_calling is False and "aucun appel" in res.detail
    async with llm_for(refused) as llm:
        res = await probe_tool_calling(llm, fake_specs())
        assert res.tool_calling is False and "refuse" in res.detail
    assert yes.requests[0]["tools"] and yes.requests[0]["tool_choice"] == "auto"


@pytest.mark.parametrize("arguments", [{"query": "x"}, '{"query": "x"}'])
async def test_tool_call_arguments_as_string_or_object(arguments: Any) -> None:
    def handler(body: dict[str, Any]) -> Any:
        import httpx2

        from unit.test_ui_fakes import chat_completion

        call = {"id": "s1", "type": "function", "function": {"name": "search_legislation", "arguments": arguments}}
        msg = {"role": "assistant", "content": None, "tool_calls": [call]}  # some servers send an object
        return httpx2.Response(200, json=chat_completion(msg, "tool_calls"))

    server = FakeOpenAIServer([handler, {"role": "assistant", "content": "fin"}])
    result = await turn(server, FakeSession())
    assert result.traces[0].model_arguments == {"query": "x"}
