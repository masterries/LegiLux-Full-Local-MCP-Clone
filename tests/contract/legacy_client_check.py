"""Handshake-era client check with the real ``mcp==1.27.2`` (Open WebUI 0.12.3's SDK), run in an isolated env.

Standalone on purpose (no repository imports): the 1.x SDK conflicts with the locked 2.3.0, so it runs as

    uv run --isolated --no-project --with mcp==1.27.2 python tests/contract/legacy_client_check.py URL TOKEN

and prints one JSON object; exit code 0 when every tool answered as expected. ``ClientSession.call_tool`` of
1.27.2 validates ``structuredContent`` against each tool's ``outputSchema`` itself.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import sys
from typing import Any

CALLS: list[tuple[str, dict[str, Any], bool]] = [
    ("about_corpus", {}, False),
    (
        "search_legislation",
        {"query": "licenciement avec préavis", "filters": {"doc_types": ["CODE"]}, "top_k": 3},
        False,
    ),
    ("get_article", {"citation": "art. L. 121-1 C. trav."}, False),
    ("get_act", {"eli": "http://data.legilux.public.lu/eli/etat/leg/loi/1922/03/05/n1"}, False),
    ("resolve_citation", {"text": "loi du 1er août 2018, art. 5"}, False),
    ("get_article", {"article": "5"}, True),
]


async def main(url: str, token: str) -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    report: dict[str, Any] = {"mcp_version": importlib.metadata.version("mcp"), "calls": []}
    ok = True
    async with (
        streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        report["protocol_version"] = init.protocolVersion
        report["server"] = init.serverInfo.name
        tools = await session.list_tools()
        report["tools"] = [t.name for t in tools.tools]
        if report["tools"] != ["search_legislation", "get_article", "get_act", "resolve_citation", "about_corpus"]:
            ok = False
        for name, args, expect_error in CALLS:
            res = await session.call_tool(name, args)
            sc = res.structuredContent or {}
            entry = {"tool": name, "is_error": res.isError, "has_text": bool(res.content)}
            if name == "get_article" and not expect_error:
                entry["article_eli"] = sc.get("citation", {}).get("article_eli")
                ok &= entry["article_eli"] == "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1"
            if expect_error:
                entry["error_code"] = (sc.get("error") or {}).get("code")
                ok &= entry["error_code"] == "BAD_INPUT"
            ok &= bool(res.isError) == expect_error and bool(res.content)
            report["calls"].append(entry)
        prompts = await session.list_prompts()
        report["prompts"] = [p.name for p in prompts.prompts]
        templates = await session.list_resource_templates()
        report["resource_templates"] = [t.uriTemplate for t in templates.resourceTemplates]
    report["ok"] = ok
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
