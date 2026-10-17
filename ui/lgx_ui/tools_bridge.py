"""MCP tools → OpenAI tool definitions, and the chat tool loop (PLAN §9.3 "Tool loop").

1. ``tools/list`` once per session (cached by the caller), converted with :func:`openai_tools`.
2. ``tool_choice="auto"``.
3. Each tool call: parse the JSON arguments, merge the sidebar values as **hard overrides** for the arguments
   that tool accepts (:func:`apply_overrides`, driven by the tool's own ``inputSchema``), validate against
   the schema (errors go back to the model), then ``tools/call``. Parallel tool calls of one model turn run
   concurrently.
4. The compact ``structuredContent`` goes back as a ``role: tool`` message, cut to a token budget estimated
   as ``ceil(chars / 3.5)`` (``lgx_core.textsize``; no tokenizer library).
5. Repeat until the model stops calling tools, ``max_iterations`` or the time budget is reached.

**Retrieve-first fallback:** when the endpoint rejects ``tools`` (or the probe shows the model never calls
one), the UI calls ``search_legislation(question)`` itself and injects the hits as ``[C1…Cn]`` context.
Nothing here depends on Streamlit.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from lgx_core.textsize import estimate_tokens, max_chars
from lgx_ui.llm import ChatModel, ChatResponse, LlmError, ToolCallRequest, ToolsUnsupportedError
from lgx_ui.mcp_client import ToolOutcome, ToolSession, ToolSpec
from lgx_ui.prompts import RETRIEVE_FIRST_INSTRUCTION

COMBINATORS = ("oneOf", "anyOf", "allOf", "not")
SEARCH_TOOL = "search_legislation"
MAX_DESCRIPTION_CHARS = 1024

# ------------------------------------------------------------------------------------- schema conversion


def sanitize_parameters(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """An ``inputSchema`` as OpenAI ``function.parameters``: object root, no top-level combinators.

    Appendix D.0 already forbids top-level ``oneOf``/``anyOf`` (some OpenAI-compatible parsers reject them);
    this is the client-side guard. Nested keywords are left alone. The original schema is still used for
    validation, so the server's rules are enforced either way.
    """
    out: dict[str, Any] = copy.deepcopy(dict(schema)) if isinstance(schema, Mapping) else {}
    for key in ("$schema", "$id", *COMBINATORS):
        out.pop(key, None)
    out["type"] = "object"
    if not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    if "required" in out and not isinstance(out["required"], list):
        out.pop("required")
    return out


def to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    description = (spec.description or spec.title or spec.name).strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[: MAX_DESCRIPTION_CHARS - 1] + "…"
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": description,
            "parameters": sanitize_parameters(spec.input_schema),
        },
    }


def openai_tools(specs: Sequence[ToolSpec]) -> tuple[list[dict[str, Any]], list[str]]:
    """Convert every listed tool; a tool whose schema is not valid JSON Schema is skipped with a note."""
    tools: list[dict[str, Any]] = []
    notes: list[str] = []
    for spec in specs:
        tool = to_openai_tool(spec)
        try:
            Draft202012Validator.check_schema(tool["function"]["parameters"])
        except SchemaError as exc:
            notes.append(f"{spec.name}: schéma ignoré ({exc.message})")
            continue
        tools.append(tool)
    return tools, notes


# ------------------------------------------------------------------------------------- sidebar overrides


@dataclass(frozen=True)
class SidebarOverrides:
    """Sidebar values that win over the model's tool arguments (PLAN §9.3 step 4).

    ``None`` (or an empty ``doc_types``) means "no override": the model's value, or the server default, stays.
    """

    as_of: str | None = None
    in_force_only: bool | None = None
    doc_types: tuple[str, ...] | None = None
    retrieval_mode: str | None = None
    top_k_cap: int | None = None


@dataclass
class OverrideResult:
    arguments: dict[str, Any]
    applied: list[str] = field(default_factory=list)


def _props(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _enum_of(schema: Mapping[str, Any] | None) -> list[Any] | None:
    if not isinstance(schema, Mapping):
        return None
    enum = schema.get("enum")
    return list(enum) if isinstance(enum, list) else None


def apply_overrides(
    arguments: Mapping[str, Any], input_schema: Mapping[str, Any], overrides: SidebarOverrides
) -> OverrideResult:
    """Merge the sidebar values into ``arguments`` for the properties this tool's schema declares.

    - ``as_of`` → every tool with an ``as_of`` property;
    - ``filters.in_force_only`` / ``filters.doc_types`` → tools whose ``filters`` object declares them
      (``doc_types`` values outside the schema's enum are dropped);
    - ``retrieval_mode`` → tools with that property (only a value the enum allows);
    - ``top_k`` is **capped** at the sidebar value (also when the model omitted it and the schema default is
      larger).
    """
    args = copy.deepcopy(dict(arguments))
    applied: list[str] = []
    props = _props(input_schema)

    if overrides.as_of and "as_of" in props:
        if args.get("as_of") != overrides.as_of:
            applied.append(f"as_of={overrides.as_of}")
        args["as_of"] = overrides.as_of

    fschema = props.get("filters")
    fprops = _props(fschema)
    if fprops:
        filters = args.get("filters")
        filters = dict(filters) if isinstance(filters, Mapping) else {}
        before = dict(filters)
        if overrides.in_force_only is not None and "in_force_only" in fprops:
            filters["in_force_only"] = overrides.in_force_only
        if overrides.doc_types and "doc_types" in fprops:
            items = fprops["doc_types"].get("items") if isinstance(fprops["doc_types"], Mapping) else None
            allowed = _enum_of(items)
            values = [d for d in overrides.doc_types if allowed is None or d in allowed]
            if values:
                filters["doc_types"] = values
        if filters != before:
            for key in sorted(filters):
                if before.get(key) != filters[key]:
                    applied.append(f"filters.{key}={json.dumps(filters[key], ensure_ascii=False)}")
        if filters or "filters" in args:
            args["filters"] = filters

    if overrides.retrieval_mode and "retrieval_mode" in props:
        allowed_modes = _enum_of(props["retrieval_mode"])
        if allowed_modes is None or overrides.retrieval_mode in allowed_modes:
            if args.get("retrieval_mode") != overrides.retrieval_mode:
                applied.append(f"retrieval_mode={overrides.retrieval_mode}")
            args["retrieval_mode"] = overrides.retrieval_mode

    cap = overrides.top_k_cap
    if cap is not None and "top_k" in props:
        current = args.get("top_k")
        default = props["top_k"].get("default") if isinstance(props["top_k"], Mapping) else None
        if isinstance(current, int) and not isinstance(current, bool) and current > cap:
            applied.append(f"top_k {current}→{cap}")
            args["top_k"] = cap
        elif current is None and isinstance(default, int) and default > cap:
            applied.append(f"top_k (défaut {default})→{cap}")
            args["top_k"] = cap

    return OverrideResult(args, applied)


def validate_arguments(arguments: Any, input_schema: Mapping[str, Any]) -> list[str]:
    """JSON Schema errors (draft 2020-12, ``format`` checked) as short messages; empty when valid."""
    try:
        validator = Draft202012Validator(dict(input_schema), format_checker=Draft202012Validator.FORMAT_CHECKER)
        errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.absolute_path))
    except SchemaError as exc:  # a broken schema is the server's problem; let the server answer
        return [] if isinstance(arguments, dict) else [f"(racine): {exc.message}"]
    out = []
    for err in errors[:10]:
        path = "/".join(str(p) for p in err.absolute_path) or "(racine)"
        out.append(f"{path}: {err.message}")
    return out


# ------------------------------------------------------------------------------------- tool result budget


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _shorten_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, list):
        return [_shorten_strings(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: _shorten_strings(v, limit) for k, v in value.items()}
    return value


def _longest_list(value: Any) -> list[Any] | None:
    best: list[Any] | None = None
    best_len = 0

    def walk(node: Any) -> None:
        nonlocal best, best_len
        if isinstance(node, list):
            if len(node) > 1:
                size = len(_dumps(node))
                if size > best_len:
                    best, best_len = node, size
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)

    walk(value)
    return best


def compact_json(value: Any, max_tokens: int) -> str:
    """Compact JSON within ``max_tokens`` (estimate): long strings first, then list tails, then a hard cut."""
    text = _dumps(value)
    if estimate_tokens(text) <= max_tokens:
        return text
    work = _shorten_strings(copy.deepcopy(value), 4000)
    text = _dumps(work)
    while estimate_tokens(text) > max_tokens:
        lst = _longest_list(work)
        if lst is None:
            break
        lst.pop()
        text = _dumps(work)
    if estimate_tokens(text) > max_tokens:
        text = _dumps(_shorten_strings(work, 600))
    if estimate_tokens(text) > max_tokens:
        marker = f" …[tronqué : budget de {max_tokens} tokens]"
        text = text[: max(0, max_chars(max_tokens) - len(marker))] + marker
    return text


# ------------------------------------------------------------------------------------- traces and results


@dataclass
class ToolTrace:
    """One tool call of the model, as shown in the tool panel."""

    iteration: int
    call_id: str
    name: str
    model_arguments: Any
    effective_arguments: dict[str, Any] | None = None
    overrides_applied: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    outcome: ToolOutcome | None = None
    latency_s: float = 0.0
    client_error: str | None = None

    @property
    def is_error(self) -> bool:
        return bool(self.validation_errors or self.client_error or (self.outcome and self.outcome.is_error))

    def error_payload(self) -> dict[str, Any] | None:
        if self.client_error:
            return {"code": "BAD_INPUT", "message": self.client_error}
        if self.validation_errors:
            return {"code": "BAD_INPUT", "message": "arguments invalides", "details": self.validation_errors}
        if self.outcome is not None and self.outcome.is_error:
            return self.outcome.error
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "call_id": self.call_id,
            "name": self.name,
            "model_arguments": self.model_arguments,
            "effective_arguments": self.effective_arguments,
            "overrides_applied": self.overrides_applied,
            "validation_errors": self.validation_errors,
            "latency_s": round(self.latency_s, 4),
            "is_error": self.is_error,
            "error": self.error_payload(),
            "result": self.outcome.structured if self.outcome is not None else None,
        }


@dataclass
class TurnResult:
    answer: str
    mode: str  # tool_calling | retrieve_first
    stop_reason: str  # final | max_iterations | time_budget | llm_error | mcp_error | no_search_tool
    traces: list[ToolTrace] = field(default_factory=list)
    llm_latency_s: float = 0.0
    mcp_latency_s: float = 0.0
    total_s: float = 0.0
    llm_calls: int = 0
    iterations: int = 0
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    prompt_source: str = "server"  # server | fallback
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def outcomes(self) -> list[ToolOutcome]:
        return [t.outcome for t in self.traces if t.outcome is not None]

    def tool_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.traces:
            counts[t.name] = counts.get(t.name, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "mode": self.mode,
            "stop_reason": self.stop_reason,
            "iterations": self.iterations,
            "llm_calls": self.llm_calls,
            "latency": {
                "llm_s": round(self.llm_latency_s, 3),
                "mcp_s": round(self.mcp_latency_s, 3),
                "total_s": round(self.total_s, 3),
            },
            "notes": self.notes,
            "error": self.error,
            "prompt_source": self.prompt_source,
            "tool_calls": [t.to_dict() for t in self.traces],
        }


@dataclass(frozen=True)
class LoopConfig:
    max_iterations: int = 5
    time_budget_s: float = 600.0
    tool_result_tokens: int = 4000
    parallel_tool_calls: bool = True


def tool_message_content(trace: ToolTrace, max_tokens: int) -> str:
    """What the model sees for one call: the compact structuredContent, or ``{"error": …}``."""
    err = trace.error_payload()
    if err is not None:
        payload: Any = {"error": err}
        if trace.outcome is not None and trace.outcome.text and not trace.client_error:
            payload["text"] = trace.outcome.text
        return compact_json(payload, max_tokens)
    assert trace.outcome is not None
    if trace.outcome.structured is not None:
        return compact_json(trace.outcome.structured, max_tokens)
    return compact_json(trace.outcome.text, max_tokens)


async def execute_tool_call(
    call: ToolCallRequest,
    *,
    iteration: int,
    session: ToolSession,
    specs: Mapping[str, ToolSpec],
    overrides: SidebarOverrides,
) -> ToolTrace:
    """Parse → override → validate → ``tools/call``. Never raises; every problem ends up in the trace."""
    start = time.perf_counter()
    raw = call.arguments_json.strip() if call.arguments_json else ""
    try:
        parsed: Any = json.loads(raw) if raw else {}
    except ValueError as exc:
        return ToolTrace(
            iteration, call.id, call.name, raw, client_error=f"arguments JSON illisibles : {exc.msg} (pos. {exc.pos})"
        )
    trace = ToolTrace(iteration, call.id, call.name, parsed)
    if not isinstance(parsed, dict):
        trace.client_error = "les arguments doivent être un objet JSON"
        return trace
    spec = specs.get(call.name)
    if spec is None:
        trace.client_error = f"outil inconnu : {call.name!r} ; outils disponibles : {', '.join(sorted(specs))}"
        return trace
    result = apply_overrides(parsed, spec.input_schema, overrides)
    trace.effective_arguments = result.arguments
    trace.overrides_applied = result.applied
    trace.validation_errors = validate_arguments(result.arguments, spec.input_schema)
    if trace.validation_errors:
        trace.latency_s = time.perf_counter() - start
        return trace
    trace.outcome = await session.call_tool(call.name, result.arguments)
    trace.latency_s = trace.outcome.latency_s or (time.perf_counter() - start)
    return trace


async def run_tool_loop(
    *,
    llm: ChatModel,
    session: ToolSession,
    tools: Sequence[ToolSpec],
    system_prompt: str,
    history: Sequence[Mapping[str, Any]],
    user_message: str,
    overrides: SidebarOverrides,
    config: LoopConfig,
    clock: Callable[[], float] = time.perf_counter,
) -> TurnResult:
    """The chat tool loop. Raises :class:`ToolsUnsupportedError` when the endpoint rejects ``tools``."""
    start = clock()
    oa_tools, notes = openai_tools(tools)
    specs = {t.name: t for t in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *(dict(m) for m in history),
        {"role": "user", "content": user_message},
    ]
    result = TurnResult(answer="", mode="tool_calling", stop_reason="final", notes=notes)
    last: ChatResponse | None = None
    for iteration in range(1, config.max_iterations + 1):
        if clock() - start > config.time_budget_s:
            result.stop_reason = "time_budget"
            break
        result.iterations = iteration
        try:
            last = await llm.chat(messages, tools=oa_tools or None, tool_choice="auto" if oa_tools else None)
        except ToolsUnsupportedError:
            raise
        except LlmError as exc:
            result.stop_reason, result.error = "llm_error", str(exc)
            break
        result.llm_calls += 1
        result.llm_latency_s += last.latency_s
        if not last.tool_calls:
            messages.append(last.assistant_message())
            result.answer = last.content or ""
            result.stop_reason = "final"
            break
        messages.append(last.assistant_message())
        batch_start = clock()
        coros = [
            execute_tool_call(tc, iteration=iteration, session=session, specs=specs, overrides=overrides)
            for tc in last.tool_calls
        ]
        # Parallel tool calls of one model turn run concurrently (results keep the model's order).
        batch = list(await asyncio.gather(*coros)) if config.parallel_tool_calls else [await c for c in coros]
        result.mcp_latency_s += clock() - batch_start
        for trace in batch:
            result.traces.append(trace)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": trace.call_id,
                    "content": tool_message_content(trace, config.tool_result_tokens),
                }
            )
    else:
        result.stop_reason = "max_iterations"
    if result.stop_reason in ("max_iterations", "time_budget") and last is not None and not result.answer:
        result.answer = last.content or ""
    if result.stop_reason == "max_iterations":
        result.notes.append(f"arrêt après {config.max_iterations} itérations (max_iterations)")
    elif result.stop_reason == "time_budget":
        result.notes.append(f"arrêt : budget de temps de {config.time_budget_s:g} s atteint")
    result.total_s = clock() - start
    result.messages = messages
    return result


# ------------------------------------------------------------------------------------- retrieve-first


def _citation_label(citation: Mapping[str, Any]) -> str:
    parts = [str(citation.get("article") or "").strip().rstrip("."), str(citation.get("act_title") or "").strip()]
    label = ", ".join(p for p in parts if p)
    if citation.get("version_label"):
        label += f" ({citation['version_label']})"
    eli = citation.get("article_eli") or citation.get("version_eli") or citation.get("act_eli")
    if eli:
        label += f" — {eli}"
    return label or "?"


def build_context(outcome: ToolOutcome, max_tokens: int) -> str:
    """``[C1] …`` context lines from a ``search_legislation`` result (legal text wrapped as data)."""
    sc = outcome.structured if isinstance(outcome.structured, dict) else {}
    hits = sc.get("hits") if isinstance(sc.get("hits"), list) else []
    lines: list[str] = []
    for i, hit in enumerate(hits, 1):
        if not isinstance(hit, Mapping):
            continue
        citation = hit.get("citation") if isinstance(hit.get("citation"), Mapping) else {}
        key = citation.get("key") or f"C{i}"
        basis = citation.get("text_basis")
        status = (citation.get("status_as_of") or {}).get("status") if isinstance(citation, Mapping) else None
        meta = ", ".join(str(x) for x in (basis, status) if x)
        warn = citation.get("warnings") or []
        lines.append(f"[{key}] {_citation_label(citation)}" + (f" [{meta}]" if meta else ""))
        if warn:
            lines.append("    avertissements : " + ", ".join(str(w) for w in warn))
        snippet = str(hit.get("snippet") or "").strip()
        if snippet:
            lines.append(f"<<<TEXTE\n{snippet}\n>>>")
    for w in sc.get("warnings") or []:
        if isinstance(w, Mapping) and w.get("message"):
            lines.append(f"[!] {w.get('code', '')} : {w['message']}")
    if sc.get("disclaimer"):
        lines.append(f"Avertissement : {sc['disclaimer']}")
    if sc.get("attribution"):
        lines.append(f"Attribution : {sc['attribution']}")
    if not hits:
        lines.insert(0, "Aucun résultat pour cette recherche.")
    text = "\n".join(lines)
    limit = max_chars(max_tokens)
    return text if len(text) <= limit else text[: limit - 12] + "\n…[tronqué]"


async def run_retrieve_first(
    *,
    llm: ChatModel,
    session: ToolSession,
    tools: Sequence[ToolSpec],
    system_prompt: str,
    history: Sequence[Mapping[str, Any]],
    user_message: str,
    overrides: SidebarOverrides,
    config: LoopConfig,
    reason: str,
    lang: str = "fr",
    clock: Callable[[], float] = time.perf_counter,
) -> TurnResult:
    start = clock()
    result = TurnResult(answer="", mode="retrieve_first", stop_reason="final", notes=[f"retrieve-first : {reason}"])
    spec = next((t for t in tools if t.name == SEARCH_TOOL), None)
    if spec is None:
        result.stop_reason, result.error = "no_search_tool", f"le serveur ne liste pas {SEARCH_TOOL}"
        result.total_s = clock() - start
        return result
    query = user_message.strip()[:1000]
    call = ToolCallRequest("retrieve_first_1", SEARCH_TOOL, json.dumps({"query": query}, ensure_ascii=False))
    mcp_start = clock()
    trace = await execute_tool_call(call, iteration=0, session=session, specs={SEARCH_TOOL: spec}, overrides=overrides)
    result.mcp_latency_s = clock() - mcp_start
    result.traces.append(trace)
    if trace.outcome is None or trace.outcome.is_error:
        err = trace.error_payload() or {}
        result.stop_reason, result.error = "mcp_error", f"{err.get('code')}: {err.get('message')}"
        result.total_s = clock() - start
        return result
    context = build_context(trace.outcome, config.tool_result_tokens)
    instruction = RETRIEVE_FIRST_INSTRUCTION.get(lang, RETRIEVE_FIRST_INSTRUCTION["fr"])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *(dict(m) for m in history),
        {"role": "user", "content": f"{instruction}\n\n{context}\n\nQuestion : {user_message}"},
    ]
    try:
        resp = await llm.chat(messages)
    except LlmError as exc:
        result.stop_reason, result.error = "llm_error", str(exc)
    else:
        result.llm_calls = 1
        result.llm_latency_s = resp.latency_s
        result.answer = resp.content or ""
        messages.append(resp.assistant_message())
    result.iterations = 1
    result.messages = messages
    result.total_s = clock() - start
    return result


CHAT_MODES = ("auto", "tools", "retrieve_first")


async def run_chat_turn(
    *,
    llm: ChatModel,
    session: ToolSession,
    tools: Sequence[ToolSpec],
    system_prompt: str,
    history: Sequence[Mapping[str, Any]],
    user_message: str,
    overrides: SidebarOverrides,
    config: LoopConfig,
    mode: str = "auto",
    lang: str = "fr",
) -> TurnResult:
    """One chat turn. ``auto``: tool calling, retrieve-first when the endpoint rejects tools; ``tools``: tool
    calling only (a rejection is reported); ``retrieve_first``: always the fallback path."""
    common: dict[str, Any] = {
        "llm": llm,
        "session": session,
        "tools": tools,
        "system_prompt": system_prompt,
        "history": history,
        "user_message": user_message,
        "overrides": overrides,
        "config": config,
    }
    if mode == "retrieve_first":
        return await run_retrieve_first(**common, reason="mode choisi dans la barre latérale", lang=lang)
    try:
        return await run_tool_loop(**common)
    except ToolsUnsupportedError as exc:
        if mode == "auto":
            return await run_retrieve_first(**common, reason=f"l'endpoint refuse les outils ({exc})", lang=lang)
        return TurnResult(answer="", mode="tool_calling", stop_reason="llm_error", error=f"outils refusés : {exc}")


# ------------------------------------------------------------------------------------- probe


PROBE_QUESTION = "Recherche dans la législation : définition du contrat de travail (Code du travail)."


@dataclass(frozen=True)
class ProbeResult:
    tool_calling: bool
    detail: str
    latency_s: float = 0.0


async def probe_tool_calling(llm: ChatModel, tools: Sequence[ToolSpec]) -> ProbeResult:
    """Does this endpoint + model emit a tool call for an obvious retrieval question? (no MCP call is made)"""
    oa_tools, _ = openai_tools(tools)
    if not oa_tools:
        return ProbeResult(False, "aucun outil MCP listé")
    messages = [
        {"role": "system", "content": "Tu dois utiliser les outils pour répondre."},
        {"role": "user", "content": PROBE_QUESTION},
    ]
    try:
        resp = await llm.chat(messages, tools=oa_tools, tool_choice="auto")
    except ToolsUnsupportedError as exc:
        return ProbeResult(False, f"l'endpoint refuse les outils : {exc}")
    except LlmError as exc:
        return ProbeResult(False, f"erreur LLM : {exc}")
    if resp.tool_calls:
        names = ", ".join(tc.name for tc in resp.tool_calls)
        return ProbeResult(True, f"appel d'outil émis : {names}", resp.latency_s)
    return ProbeResult(False, "aucun appel d'outil émis (le modèle a répondu en texte)", resp.latency_s)
