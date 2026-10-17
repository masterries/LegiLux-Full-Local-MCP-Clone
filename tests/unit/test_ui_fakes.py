# ruff: noqa: E501  (canned Appendix D payloads)
"""Fakes for the tester-UI tests (no network): Appendix D tool schemas, a fake MCP session, an in-process MCP
server (real ``mcp==2.3.0`` client path, also over Streamable HTTP via ``httpx2.ASGITransport``) and a scripted
OpenAI-compatible Chat Completions server on ``httpx2.MockTransport``.

``openai==3.24.0`` and ``mcp==2.3.0`` use ``httpx2``, which ``respx`` (httpx 0.28 only) cannot intercept, so
the fake LLM server is an ``httpx2.MockTransport`` handler; ``respx`` mocks the plain-httpx ``/readyz`` probe.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import mcp_types as types
from mcp.server.lowlevel import Server

from lgx_ui.mcp_client import ToolOutcome, ToolSpec

ELI = "http://data.legilux.public.lu/eli/etat/leg"
DOC_TYPES = ["Constitution", "CODE", "CODE_RECUEIL", "RECUEIL", "LOI", "RGD", "AGD", "RMIN", "AMIN", "RGC", "RILR",
             "RCSF", "RBCL", "ST", "ACC", "AGC", "DGC", "RI", "AGDR", "CONV", "PROT"]  # fmt: skip

SCHEMAS: dict[str, dict[str, Any]] = {
    "search_legislation": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 1000},
            "filters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "doc_types": {"type": "array", "uniqueItems": True, "items": {"enum": DOC_TYPES}},
                    "act_eli": {"type": "string", "pattern": "^http://data\\.legilux\\.public\\.lu/eli/etat/leg/"},
                    "in_force_only": {"type": "boolean", "default": True},
                },
            },
            "as_of": {"type": "string", "format": "date"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            "retrieval_mode": {"enum": ["auto", "lexical", "hybrid"], "default": "auto"},
        },
    },
    "get_article": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "citation": {"type": "string", "minLength": 3, "maxLength": 400},
            "act_eli": {"type": "string", "pattern": "^(http://data\\.legilux\\.public\\.lu/eli/|legilux://eli/)"},
            "article": {"type": "string", "maxLength": 40},
            "as_of": {"type": "string", "format": "date"},
            "cursor": {"type": "string", "maxLength": 200},
        },
    },
    "get_act": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "eli": {"type": "string", "pattern": "^(http://data\\.legilux\\.public\\.lu/eli/|legilux://eli/)"},
            "citation": {"type": "string", "minLength": 3, "maxLength": 400},
            "as_of": {"type": "string", "format": "date"},
            "include": {
                "type": "array",
                "uniqueItems": True,
                "default": ["amendments"],
                "items": {"enum": ["toc", "versions", "relations", "amendments"]},
            },
        },
    },
    "resolve_citation": {
        "type": "object",
        "additionalProperties": False,
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "minLength": 3, "maxLength": 400},
            "as_of": {"type": "string", "format": "date"},
        },
    },
    "about_corpus": {"type": "object", "additionalProperties": False, "properties": {}},
}

ATTRIBUTION = (
    "Source : Legilux (https://legilux.public.lu) – © Ministère d'État … CC BY 4.0 … copie locale du 16/10/2026."
)
DISCLAIMER = (
    "Information non officielle. « Le Journal officiel publié fait seul foi et produit des effets juridiques » …"
)
ENVELOPE_WARNINGS = [
    {"code": "W_NOT_OFFICIAL", "severity": "info", "message": "Information non officielle. (art. 5)"},
    {"code": "W_SNAPSHOT", "severity": "info", "message": "Données Legilux arrêtées au 16/10/2026."},
]
ART_TEXT = "Le contrat de travail est le contrat par lequel une personne s'engage à mettre son activité à la disposition d'une autre."


def envelope(as_of: str = "2026-10-16", warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "snapshot": {"release_id": "data-20261016-sample", "sync_date": "2026-10-16", "horizon": "2027-10-16",
                     "age_days": 0, "staleness_level": 0, "clock_ok": True},
        "as_of": as_of,
        "warnings": copy.deepcopy(ENVELOPE_WARNINGS) + (warnings or []),
        "attribution": ATTRIBUTION,
        "disclaimer": DISCLAIMER,
    }  # fmt: skip


def citation(key: str = "C1", **over: Any) -> dict[str, Any]:
    c = {
        "key": key,
        "act_eli": f"{ELI}/loi/2006/07/31/n2",
        "act_title": "Code du travail",
        "doc_type": "CODE",
        "article": "Art. L. 121-1.",
        "article_eli": f"{ELI}/code/travail/art_l_121-1",
        "article_eli_derived": False,
        "version_eli": f"{ELI}/code/travail/20260726",
        "version_label": "version consolidée applicable au 26/07/2026",
        "valid_from": "2026-07-26",
        "valid_to": None,
        "article_wording_since": "2020-11-01",
        "article_wording_since_inferred": True,
        "text_basis": "consolidated",
        "flags": [],
        "status_as_of": {"as_of": "2026-10-16", "status": "in-force", "basis": "dates"},
        "display_url": "https://legilux.public.lu/eli/etat/leg/code/travail/20260726",
        "licence": "http://creativecommons.org/licenses/by/4.0/",
        "warnings": ["W_NOT_OFFICIAL"],
    }
    c.update(over)
    return c


def search_result(args: Mapping[str, Any]) -> dict[str, Any]:
    mode = args.get("retrieval_mode", "auto")
    outdated = citation(
        "C2",
        act_eli=f"{ELI}/rgd/2000/07/24/n3",
        act_title="Règlement grand-ducal du 24 juillet 2000",
        doc_type="RGD",
        article="Art. 3.",
        article_eli=f"{ELI}/rgd/2000/07/24/n3/art_3",
        article_eli_derived=True,
        version_eli=f"{ELI}/rgd/2000/07/24/n3/jo",
        version_label="texte original",
        text_basis="original_possibly_outdated",
        warnings=["W_NOT_CONSOLIDATED", "W_DERIVED_ELI"],
    )
    hits = [
        {
            "rank": 1,
            "score": 0.0328,
            "citation": citation("C1"),
            "snippet": ART_TEXT,
            "breadcrumb": "Livre Ier > Titre II",
        },
        {"rank": 2, "score": 0.02, "citation": outdated, "snippet": "Texte de l'article 3 …", "breadcrumb": ""},
    ][: int(args.get("top_k", 8))]
    return {
        **envelope(
            args.get("as_of", "2026-10-16"),
            [{"code": "W_NOT_CONSOLIDATED", "severity": "high", "refs": ["C2"],
              "message": "Texte non consolidé : texte original, modifié depuis par : RGD du 10 janvier 2020."}],
        ),
        "hits": hits,
        "retrieval": {"mode_requested": mode, "mode_used": "lexical", "vector_status": "disabled:provider_none",
                      "reason": "embedding stage not enabled", "reranked": False, "rerank_status": "off",
                      "rerank_k": 16, "embedding_model": None},
        "query_interpretation": {"citations_detected": [], "exact_tokens": []},
    }  # fmt: skip


def article_result(args: Mapping[str, Any]) -> dict[str, Any]:
    c = citation("C1")
    return {
        **envelope(args.get("as_of", "2026-10-16")),
        "citation": c,
        "citation_string": "Art. L. 121-1, Code du travail (version consolidée applicable au 26/07/2026) — "
        f"{ELI}/code/travail/art_l_121-1",
        "breadcrumb": "Livre Ier > Titre II",
        "heading": None,
        "text": ART_TEXT,
        "next_cursor": None,
        "is_repealed": False,
        "amended_by": [],
    }


def act_result(args: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **envelope(args.get("as_of", "2026-10-16"), [{"code": "W_STUB", "severity": "high", "message": "Consolidation annoncée…"}]),
        "act_eli": args.get("eli", f"{ELI}/loi/2018/05/30/a446"),
        "title": "Loi du 30 mai 2018 relative aux marchés d'instruments financiers",
        "doc_type": "LOI",
        "text_basis": "stub_fallback",
        "flags": [],
        "status_as_of": {"as_of": args.get("as_of", "2026-10-16"), "status": "in-force", "basis": "dates", "reasons": []},
        "current_version": {"version_eli": f"{ELI}/loi/2018/05/30/a446/consolide/20240101", "valid_from": "2024-01-01", "is_stub": True},
        "amending_acts_since_text_date": [{"eli": f"{ELI}/loi/2024/01/01/a1/jo", "title": "Loi …", "rel_type": "modifies"}],
    }  # fmt: skip


def corpus_result() -> dict[str, Any]:
    return {
        **envelope(),
        "counts": {"acts": 58390, "versions": 29133, "chunks": 10220,
                   "by_text_basis": {"consolidated": 23, "stub_fallback": 1, "original_never_amended": 11,
                                     "original_possibly_outdated": 7, "metadata_only": 58348}},
        "known_gaps": {"stub_consolidations": [], "pdf_codes": ["code/route", "code/securite_sociale"]},
        "embedding": {"provider": "none", "model_id": None, "dim": None, "vector_status": "disabled:provider_none"},
        "retrieval_defaults": {"mode": "auto", "rerank": "off", "rerank_k": 16},
        "licences": {"data": "CC BY 4.0", "texts_path": "legilux://legal/cc-by-4.0"},
    }  # fmt: skip


def error_result(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "hint": None, "candidates": []}}


def respond(name: str, args: Mapping[str, Any]) -> tuple[Any, bool, str]:
    """(structuredContent, isError, text) of the fake server for one call."""
    if name == "search_legislation":
        return search_result(args), False, "Résultats : [C1] Art. L. 121-1 …"
    if name == "get_article":
        if not args.get("citation") and not (args.get("act_eli") and args.get("article")):
            return error_result("BAD_INPUT", "citation ou act_eli + article requis"), True, "BAD_INPUT"
        return article_result(args), False, f"<<<TEXTE {ART_TEXT} >>>"
    if name == "get_act":
        if args.get("as_of", "") > "2027-10-16":
            return error_result("AS_OF_OUT_OF_RANGE", "as_of au-delà de l'horizon"), True, "AS_OF_OUT_OF_RANGE"
        return act_result(args), False, "Fiche d'acte"
    if name == "resolve_citation":
        return (
            {**envelope(), "status": "ambiguous", "candidates": [{"act_eli": f"{ELI}/loi/2018/08/01/a686"}]},
            False,
            "",
        )
    if name == "about_corpus":
        return corpus_result(), False, "Corpus au 16/10/2026"
    return error_result("NOT_FOUND", f"unknown tool {name}"), True, ""


FAKE_PROMPT = "PROMPT SERVEUR legal_answer"


class FakeSession:
    """Implements ``lgx_ui.mcp_client.ToolSession``; records calls; optional per-tool overrides."""

    def __init__(self, *, fail: dict[str, ToolOutcome] | None = None, prompt: str | None = FAKE_PROMPT) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail or {}
        self.prompt = prompt
        self.prompt_requests: list[tuple[str, dict[str, str]]] = []

    async def list_tools(self) -> list[ToolSpec]:
        return fake_specs()

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        args = dict(arguments)
        self.calls.append((name, args))
        if name in self.fail:
            return self.fail[name]
        structured, is_error, text = respond(name, args)
        out = ToolOutcome(name, args, is_error, structured, text, latency_s=0.01)
        if is_error:
            out.error_code = structured["error"]["code"]
            out.error_message = structured["error"]["message"]
        return out

    async def get_prompt(self, name: str, arguments: Mapping[str, str] | None = None) -> str | None:
        self.prompt_requests.append((name, dict(arguments or {})))
        return self.prompt


def fake_specs() -> list[ToolSpec]:
    return [ToolSpec(name, f"Outil {name}", copy.deepcopy(schema), read_only=True) for name, schema in SCHEMAS.items()]


def session_factory(session: FakeSession) -> Callable[[Any], Any]:
    @asynccontextmanager
    async def open_session(_settings: Any):  # type: ignore[no-untyped-def]
        yield session

    return open_session


# ------------------------------------------------------------------------------------------ in-process MCP server


def build_fake_server() -> Server[Any]:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=f"Outil {name}",
                    input_schema=schema,
                    annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True),
                )
                for name, schema in SCHEMAS.items()
            ]
        )

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        structured, is_error, text = respond(params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(text=text)], structured_content=structured, is_error=is_error
        )

    async def on_list_prompts(ctx: Any, params: Any) -> types.ListPromptsResult:
        return types.ListPromptsResult(
            prompts=[types.Prompt(name="legal_answer", arguments=[types.PromptArgument(name="lang")])]
        )

    async def on_get_prompt(ctx: Any, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        if params.name != "legal_answer":
            raise ValueError(f"unknown prompt {params.name!r}")
        lang = (params.arguments or {}).get("lang", "fr")
        return types.GetPromptResult(
            messages=[types.PromptMessage(role="user", content=types.TextContent(text=f"{FAKE_PROMPT} ({lang})"))]
        )

    return Server(
        "fake-lgx-mcp",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_prompts=on_list_prompts,
        on_get_prompt=on_get_prompt,
    )


def bearer_guard(app: Any, token: str, seen: list[str | None]) -> Any:
    """ASGI wrapper: 401 unless ``Authorization: Bearer <token>`` (records every header seen)."""

    async def guarded(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization")
            seen.append(auth.decode() if auth else None)
            if auth != f"Bearer {token}".encode():
                await send(
                    {"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]}
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


# ------------------------------------------------------------------------------------------ fake OpenAI server


def chat_completion(message: dict[str, Any], finish_reason: str, model: str = "fake-model") -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def tool_call_message(*calls: tuple[str, str, Any]) -> dict[str, Any]:
    """Assistant message with tool calls: (id, name, arguments as dict or raw JSON string)."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}}
            for cid, name, args in calls
        ],
    }  # fmt: skip


class FakeOpenAIServer:
    """Scripted ``/v1/chat/completions``: each request pops the next scripted response.

    A script entry is a message dict (→ 200 with that assistant message), an ``int`` (→ that HTTP status with an
    OpenAI-style error body) or a callable ``(request_body) -> httpx2.Response``. When the script is exhausted
    the server answers with a final text message.
    """

    def __init__(self, script: list[Any] | None = None, *, error_message: str = "error") -> None:
        self.script = list(script or [])
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.error_message = error_message

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/chat/completions"), request.url
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        step = self.script.pop(0) if self.script else {"role": "assistant", "content": "Réponse finale."}
        if callable(step):
            return step(body)
        if isinstance(step, int):
            return httpx2.Response(
                step, json={"error": {"message": self.error_message, "type": "invalid_request_error"}}
            )
        finish = "tool_calls" if step.get("tool_calls") else "stop"
        return httpx2.Response(200, json=chat_completion(step, finish, body.get("model", "fake-model")))

    def http_client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))
