"""Tool contracts (Appendix D.2) are published exactly and are valid JSON Schema 2020-12; OpenAI-bridge friendly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from lgx_mcp.schemas import ARTICLE_INPUT, SEARCH_INPUT, TOOL_NAMES, TOOLS, TOOLS_BY_NAME
from lgx_mcp.tools.base import validate_arguments

APPENDIX_D = Path(__file__).resolve().parents[2] / "docs" / "plan" / "appendix-d-mcp-tools.md"


def test_five_tools_in_contract_order() -> None:
    assert TOOL_NAMES == ["search_legislation", "get_article", "get_act", "resolve_citation", "about_corpus"]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_NAMES)
def test_schemas_are_valid_and_have_no_top_level_combinators(tool: dict) -> None:
    Draft202012Validator.check_schema(tool["inputSchema"])
    Draft202012Validator.check_schema(tool["outputSchema"])
    assert tool["inputSchema"]["type"] == "object" and tool["outputSchema"]["type"] == "object"
    assert not {"oneOf", "anyOf", "allOf", "not"} & set(tool["inputSchema"])
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "$ref" not in str(tool["outputSchema"])  # inline: every client validator resolves it
    # OpenAI function-calling conversion is the identity on parameters
    fn = {
        "type": "function",
        "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["inputSchema"]},
    }
    assert re.fullmatch(r"[a-z_]{1,64}", fn["function"]["name"])


def test_input_schemas_match_appendix_d_verbatim() -> None:
    """The ```json inputSchema blocks of Appendix D.2 equal what tools/list publishes."""
    import json

    text = APPENDIX_D.read_text(encoding="utf-8")
    blocks = re.findall(r"\*\*inputSchema\*\*\n```json\n(.*?)```", text, re.S)
    assert len(blocks) == len(TOOL_NAMES)
    for name, block in zip(TOOL_NAMES, blocks, strict=True):
        expected = json.loads(block)
        got = TOOLS_BY_NAME[name]["inputSchema"]
        assert json.dumps(got, sort_keys=True) == json.dumps(expected, sort_keys=True), name


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_legislation", {"query": "x"}),
        ("search_legislation", {"query": "travail", "top_k": 0}),
        ("search_legislation", {"query": "travail", "filters": {"doc_types": ["XYZ"]}}),
        ("search_legislation", {"query": "travail", "filters": {"act_eli": "https://example.org/x"}}),
        ("search_legislation", {"query": "travail", "unknown": 1}),
        ("search_legislation", {"query": "travail", "as_of": "16/10/2026"}),
        ("get_article", {"act_eli": "eli/etat/leg/loi/2018/08/01/a686", "article": "5"}),
        ("get_act", {"include": ["everything"]}),
        ("resolve_citation", {}),
        ("about_corpus", {"x": 1}),
    ],
)
def test_bad_arguments_are_bad_input(tool: str, args: dict) -> None:
    from lgx_mcp.context import ToolFailure

    with pytest.raises(ToolFailure) as exc:
        validate_arguments(tool, args)
    assert exc.value.code.value == "BAD_INPUT"


def test_good_arguments_pass() -> None:
    validate_arguments(
        "search_legislation", {"query": "contrat", "filters": {"doc_types": ["CODE"], "in_force_only": False}}
    )
    validate_arguments("get_article", {"act_eli": "legilux://eli/etat/leg/loi/2018/08/01/a686", "article": "5"})
    validate_arguments(
        "get_act", {"eli": "http://data.legilux.public.lu/eli/etat/leg/code/travail", "include": ["toc"]}
    )
    validate_arguments("about_corpus", {})
    assert SEARCH_INPUT["properties"]["top_k"]["maximum"] == 20
    assert ARTICLE_INPUT["properties"]["article"]["maxLength"] == 40
