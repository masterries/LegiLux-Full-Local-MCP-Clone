"""Streamlit glue shared by ``ui/app.py`` and the pages: settings, dependencies, caches, sidebar, forms.

State lives in ``st.session_state`` only (per browser session, in memory; no server-side chat persistence).
Tests inject fakes through ``st.session_state["lgx_deps"]`` (a :class:`UiDeps`) before the first run.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from lgx_core.urls import is_internal_url
from lgx_ui.llm import ChatModel, LlmConfig, OpenAICompatChat
from lgx_ui.mcp_client import (
    HealthStatus,
    LgxMcpClient,
    McpUnavailableError,
    ToolOutcome,
    ToolSession,
    ToolSpec,
    fetch_readyz,
    run_sync,
)
from lgx_ui.prompts import fallback_prompt
from lgx_ui.settings import DOC_TYPES_FALLBACK, NO_KEY, UiSettings, load_settings
from lgx_ui.tools_bridge import (
    SEARCH_TOOL,
    LoopConfig,
    ProbeResult,
    SidebarOverrides,
    TurnResult,
    probe_tool_calling,
    run_chat_turn,
)

HEALTH_TTL_S = 30.0
CORPUS_TTL_S = 300.0
HISTORY_MESSAGES = 6  # prior user/assistant messages sent with a new question
# Sidebar widgets live in app.py and must keep their values across page switches: since streamlit 1.65 a keyed
# widget's value is dropped on a page switch unless persist_state="session" (the LLM fields came back empty).
KEEP = "session"
# Every keyed sidebar widget. Their defaults are set through st.session_state (never a widget `value=`), and each run
# re-saves them before any widget exists: the first session after a server start attributes its first run to the
# entrypoint and the next one to the default page, a pseudo page switch that persist_state does not cover (the chat
# then found an empty base URL and model: e2e 2026-10-17).
SIDEBAR_KEYS = (
    "llm_base_url",
    "llm_model",
    "llm_temperature",
    "llm_max_iter",
    "chat_mode",
    "prompt_lang",
    "retrieval_mode",
    "retrieval_hybrid",
    "f_in_force",
    "f_doc_types",
    "f_as_of",
    "f_model_as_of",
    "f_top_k",
)


@dataclass
class UiDeps:
    """Factories for everything that talks to the network (replaced by fakes in tests)."""

    open_session: Callable[[UiSettings], AbstractAsyncContextManager[ToolSession]]
    make_llm: Callable[[LlmConfig], AbstractAsyncContextManager[ChatModel]]
    readyz: Callable[[UiSettings], HealthStatus]


def default_deps() -> UiDeps:
    return UiDeps(
        open_session=lambda s: LgxMcpClient(s.mcp_url, token=s.mcp_token, timeout_s=s.mcp_timeout_s),
        make_llm=lambda cfg: OpenAICompatChat(cfg),
        readyz=lambda s: fetch_readyz(s.mcp_url, timeout_s=min(3.0, s.mcp_timeout_s)),
    )


@dataclass
class SidebarState:
    llm: LlmConfig | None
    chat_mode: str
    max_iterations: int
    prompt_lang: str
    overrides: SidebarOverrides
    vector_status: str | None
    key_withheld: bool = False

    def loop_config(self, settings: UiSettings) -> LoopConfig:
        return LoopConfig(
            max_iterations=self.max_iterations,
            time_budget_s=settings.turn_budget_s,
            tool_result_tokens=settings.tool_result_tokens,
        )


# ------------------------------------------------------------------------------------------- state


def settings(st: Any) -> UiSettings:
    if "lgx_settings" not in st.session_state:
        st.session_state["lgx_settings"] = load_settings()
    return st.session_state["lgx_settings"]


def deps(st: Any) -> UiDeps:
    if "lgx_deps" not in st.session_state:
        st.session_state["lgx_deps"] = default_deps()
    return st.session_state["lgx_deps"]


def health(st: Any, *, force: bool = False) -> HealthStatus:
    now = time.monotonic()
    cached = st.session_state.get("lgx_health")
    if not force and cached is not None and now - st.session_state.get("lgx_health_at", 0.0) < HEALTH_TTL_S:
        return cached
    status = deps(st).readyz(settings(st))
    st.session_state["lgx_health"], st.session_state["lgx_health_at"] = status, now
    return status


def call_tool(st: Any, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
    """One tool call through a short-lived MCP session (Direct search, Corpus)."""
    cfg = settings(st)

    async def go() -> ToolOutcome:
        async with deps(st).open_session(cfg) as session:
            return await session.call_tool(name, dict(arguments))

    try:
        return run_sync(go())
    except McpUnavailableError as exc:
        return ToolOutcome(name, dict(arguments), True, error_code="BACKEND_UNAVAILABLE", error_message=str(exc))


def tools(st: Any, *, force: bool = False) -> list[ToolSpec]:
    """``tools/list`` once per browser session (refresh button in the sidebar); after a failure it is retried
    automatically once ``HEALTH_TTL_S`` has passed, so the UI recovers when the server comes up later."""
    cached = st.session_state.get("lgx_tools")
    failed_recently = time.monotonic() - st.session_state.get("lgx_tools_at", 0.0) < HEALTH_TTL_S
    if not force and cached is not None and (cached or failed_recently):
        return cached
    cfg = settings(st)
    st.session_state["lgx_tools_at"] = time.monotonic()

    async def go() -> list[ToolSpec]:
        async with deps(st).open_session(cfg) as session:
            return await session.list_tools()

    try:
        specs = run_sync(go())
        st.session_state["lgx_tools_error"] = None
    except Exception as exc:  # unreachable server: the page still renders, with the reason
        specs = []
        st.session_state["lgx_tools_error"] = str(exc)
    st.session_state["lgx_tools"] = specs
    return specs


def tools_error(st: Any) -> str | None:
    return st.session_state.get("lgx_tools_error")


def tool_spec(specs: Sequence[ToolSpec], name: str) -> ToolSpec | None:
    return next((t for t in specs if t.name == name), None)


def corpus(st: Any, *, force: bool = False) -> ToolOutcome:
    now = time.monotonic()
    cached = st.session_state.get("lgx_corpus")
    if not force and cached is not None and now - st.session_state.get("lgx_corpus_at", 0.0) < CORPUS_TTL_S:
        return cached
    outcome = call_tool(st, "about_corpus", {})
    st.session_state["lgx_corpus"], st.session_state["lgx_corpus_at"] = outcome, now
    return outcome


def vector_status(st: Any) -> tuple[str | None, str | None]:
    """(vector_status, reason) from ``/readyz`` (cheap) or a cached ``about_corpus``."""
    status = st.session_state.get("lgx_health")
    if isinstance(status, HealthStatus) and status.vector_status:
        return status.vector_status, status.body.get("vector_reason") or status.body.get("reason")
    corpus_outcome = st.session_state.get("lgx_corpus")
    if isinstance(corpus_outcome, ToolOutcome) and isinstance(corpus_outcome.structured, Mapping):
        emb = corpus_outcome.structured.get("embedding")
        if isinstance(emb, Mapping) and emb.get("vector_status"):
            return str(emb["vector_status"]), emb.get("reason")
    return None, None


# ------------------------------------------------------------------------------------------- schema helpers


def search_schema_info(specs: Sequence[ToolSpec]) -> tuple[list[str], int, int, bool]:
    """(doc_type options, top_k max, top_k default, has retrieval_mode) from the search tool's schema."""
    spec = tool_spec(specs, SEARCH_TOOL)
    props = spec.properties if spec else {}
    doc_types = list(DOC_TYPES_FALLBACK)
    fprops = props.get("filters", {}).get("properties", {}) if isinstance(props.get("filters"), Mapping) else {}
    items = fprops.get("doc_types", {}).get("items", {}) if isinstance(fprops.get("doc_types"), Mapping) else {}
    if isinstance(items, Mapping) and isinstance(items.get("enum"), list):
        doc_types = [str(x) for x in items["enum"]]
    top = props.get("top_k", {}) if isinstance(props.get("top_k"), Mapping) else {}
    top_max = int(top.get("maximum", 20) or 20)
    top_default = int(top.get("default", 8) or 8)
    return doc_types, top_max, min(top_default, top_max), "retrieval_mode" in props or spec is None


# ------------------------------------------------------------------------------------------- sidebar


def render_sidebar(st: Any) -> SidebarState:
    state = st.session_state
    for key in SIDEBAR_KEYS:  # re-save (see SIDEBAR_KEYS): turns last run's widget values into set values
        if key in state:
            state[key] = state[key]
    cfg = settings(st)
    specs = tools(st)
    status = health(st)
    vstatus, vreason = vector_status(st)
    sb = st.sidebar

    sb.markdown("### Serveur MCP")
    if status.ready:
        release = status.body.get("release_id") or "?"
        sb.success(f"prêt · {release}")
    elif status.reachable:
        sb.warning(f"joignable mais pas prêt ({status.error})")
    else:
        sb.error(f"injoignable : {status.error}")
    sb.caption(f"{cfg.mcp_url} · {len(specs)} outil(s)" + (" · jeton configuré" if cfg.mcp_token else ""))
    if tools_error(st):
        sb.caption(f"tools/list : {tools_error(st)}")
    if sb.button("Actualiser (outils, état)", key="refresh_mcp"):
        tools(st, force=True)
        health(st, force=True)
        st.session_state.pop("lgx_corpus", None)
        st.session_state.pop("lgx_prompts", None)
        st.rerun()

    sb.markdown("### LLM (OpenAI-compatible)")
    st.session_state.setdefault("llm_base_url", cfg.llm_base_url)
    st.session_state.setdefault("llm_model", cfg.llm_model)
    base_url = sb.text_input("Base URL", key="llm_base_url", persist_state=KEEP).strip()
    model = sb.text_input("Modèle", key="llm_model", persist_state=KEEP).strip()
    state.setdefault("llm_temperature", float(cfg.llm_temperature))
    state.setdefault("llm_max_iter", min(10, cfg.max_tool_iterations))
    temperature = sb.slider("Température", 0.0, 1.0, step=0.05, key="llm_temperature", persist_state=KEEP)
    max_iter = int(sb.number_input("Itérations max", 1, 10, key="llm_max_iter", persist_state=KEEP))
    modes = {"auto": "outils, repli retrieve-first", "tools": "outils uniquement", "retrieve_first": "retrieve-first"}
    state.setdefault("chat_mode", "auto")
    chat_mode = sb.radio(
        "Mode", list(modes), format_func=lambda k: modes[k], key="chat_mode", horizontal=False, persist_state=KEEP
    )
    st.session_state.setdefault("prompt_lang", cfg.ui_lang if cfg.ui_lang in ("fr", "de", "en") else "fr")
    prompt_lang = sb.selectbox("Langue du prompt système", ["fr", "de", "en"], key="prompt_lang", persist_state=KEEP)
    key_ok = cfg.key_allowed(base_url)
    llm = (
        LlmConfig(
            base_url=base_url,
            model=model,
            api_key=cfg.llm_api_key if key_ok else NO_KEY,
            temperature=float(temperature),
            timeout_s=cfg.llm_timeout_s,
            trust_env=cfg.llm_trust_env,
        )
        if base_url and model
        else None
    )
    if llm is not None and not key_ok:
        sb.caption("URL saisie librement : la clé API configurée n'est pas envoyée.")
    if llm is not None and not is_internal_url(base_url):
        sb.caption(f"Endpoint externe : questions et textes récupérés sont envoyés à {urlsplit(base_url).hostname}.")
    probe: ProbeResult | None = st.session_state.get("lgx_probe")
    if sb.button("Tester l'appel d'outils", key="probe", disabled=llm is None or not specs):
        probe = run_probe(st, llm, specs) if llm is not None else None
        st.session_state["lgx_probe"] = probe
    if probe is not None:
        (sb.success if probe.tool_calling else sb.warning)(f"sonde : {probe.detail}")
    if chat_mode == "auto" and probe is not None and not probe.tool_calling:
        chat_mode = "retrieve_first"

    sb.markdown("### Recherche")
    doc_options, top_max, top_default, _ = search_schema_info(specs)
    state.setdefault("retrieval_mode", "auto")
    state.setdefault("retrieval_hybrid", False)
    base_mode = sb.selectbox("retrieval_mode", ["auto", "lexical"], key="retrieval_mode", persist_state=KEEP)
    vectors_ok = vstatus == "ok"
    hybrid = sb.toggle("hybride", key="retrieval_hybrid", disabled=not vectors_ok, persist_state=KEEP)
    if not vectors_ok:
        why = vstatus or "état inconnu"
        sb.caption(f"hybride indisponible : vecteurs désactivés ({why})" + (f" — {vreason}" if vreason else ""))
    retrieval_mode = "hybrid" if hybrid and vectors_ok else base_mode

    sb.markdown("### Filtres (surcharges)")
    state.setdefault("f_in_force", True)
    state.setdefault("f_doc_types", [])
    state.setdefault("f_as_of", None)
    state.setdefault("f_model_as_of", False)
    state.setdefault("f_top_k", top_default)
    state["f_top_k"] = max(1, min(int(state["f_top_k"]), top_max))
    in_force = sb.checkbox("En vigueur uniquement", key="f_in_force", persist_state=KEEP)
    doc_types = sb.multiselect("Types de document", doc_options, key="f_doc_types", persist_state=KEEP)
    as_of_value = sb.date_input(
        "as_of (vide = date du serveur)", key="f_as_of", format="YYYY-MM-DD", persist_state=KEEP
    )
    model_as_of = sb.checkbox(
        "as_of choisi par le modèle si vide",
        key="f_model_as_of",
        persist_state=KEEP,
        help="Désactivé : la date proposée par le modèle est ignorée (les petits modèles inventent souvent une date).",
    )
    top_k = int(sb.slider("top_k (plafond)", 1, top_max, key="f_top_k", persist_state=KEEP))
    as_of = as_of_value.isoformat() if isinstance(as_of_value, dt.date) else None
    sb.caption("Dans le chat, ces valeurs remplacent les arguments du modèle (outils qui les acceptent).")

    return SidebarState(
        llm=llm,
        chat_mode=chat_mode,
        max_iterations=max_iter,
        prompt_lang=prompt_lang,
        overrides=SidebarOverrides(
            as_of=as_of,
            model_as_of=bool(model_as_of),
            in_force_only=bool(in_force),
            doc_types=tuple(doc_types) or None,
            retrieval_mode=retrieval_mode,
            top_k_cap=top_k,
        ),
        vector_status=vstatus,
        key_withheld=llm is not None and not key_ok,
    )


def run_probe(st: Any, llm_cfg: LlmConfig, specs: Sequence[ToolSpec]) -> ProbeResult:
    async def go() -> ProbeResult:
        async with deps(st).make_llm(llm_cfg) as llm:
            return await probe_tool_calling(llm, specs)

    return run_sync(go())


# ------------------------------------------------------------------------------------------- chat turn


async def system_prompt(st: Any, session: ToolSession, lang: str) -> tuple[str, str]:
    cache: dict[str, tuple[str, str]] = st.session_state.setdefault("lgx_prompts", {})
    if lang in cache:
        return cache[lang]
    text = await session.get_prompt("legal_answer", {"lang": lang})
    value = (text, "server") if text else (fallback_prompt(lang), "fallback")
    if text:
        cache[lang] = value
    return value


def chat_history_messages(history: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    msgs = [
        {"role": h["role"], "content": str(h.get("content") or "")}
        for h in history
        if h.get("role") in ("user", "assistant")
    ]
    return msgs[-HISTORY_MESSAGES:]


def run_chat(st: Any, sb: SidebarState, question: str, history: Sequence[Mapping[str, Any]]) -> TurnResult:
    cfg = settings(st)
    if sb.llm is None:
        return TurnResult(answer="", mode="tool_calling", stop_reason="llm_error", error="aucun LLM configuré")
    llm_cfg = sb.llm
    specs = tools(st)
    started = time.perf_counter()

    async def go() -> TurnResult:
        async with deps(st).open_session(cfg) as session:
            current = specs or await session.list_tools()
            prompt, source = await system_prompt(st, session, sb.prompt_lang)
            async with deps(st).make_llm(llm_cfg) as llm:
                result = await run_chat_turn(
                    llm=llm,
                    session=session,
                    tools=current,
                    system_prompt=prompt,
                    history=chat_history_messages(history),
                    user_message=question,
                    overrides=sb.overrides,
                    config=sb.loop_config(cfg),
                    mode=sb.chat_mode,
                    lang=sb.prompt_lang,
                )
            result.prompt_source = source
            return result

    try:
        return run_sync(go())
    except McpUnavailableError as exc:
        return TurnResult(
            answer="",
            mode="tool_calling",
            stop_reason="mcp_error",
            error=str(exc),
            total_s=time.perf_counter() - started,
        )


# ------------------------------------------------------------------------------------------- schema forms


def _type_of(schema: Mapping[str, Any]) -> str | None:
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t is None and isinstance(schema.get("enum"), list):
        return "enum"
    return t if isinstance(t, str) else None


def schema_form(st: Any, spec: ToolSpec, defaults: Mapping[str, Any], *, key_prefix: str = "ds") -> dict[str, Any]:
    """Widgets generated from an ``inputSchema`` (one nesting level); empty fields are omitted."""
    args: dict[str, Any] = {}
    required = set(spec.input_schema.get("required") or [])
    for name, prop in spec.properties.items():
        if not isinstance(prop, Mapping):
            continue
        value = _field(st, f"{key_prefix}:{spec.name}:{name}", name, prop, defaults.get(name), name in required)
        if value is not None and value != "" and value != []:
            args[name] = value
    return args


def _field(st: Any, key: str, name: str, prop: Mapping[str, Any], default: Any, required: bool) -> Any:
    label = name + (" *" if required else "")
    help_text = prop.get("description")
    kind = _type_of(prop)
    enum = prop.get("enum") if isinstance(prop.get("enum"), list) else None
    if kind == "object" and isinstance(prop.get("properties"), Mapping):
        st.markdown(f"**{name}**")
        sub: dict[str, Any] = {}
        sub_defaults = default if isinstance(default, Mapping) else {}
        for sname, sprop in prop["properties"].items():
            if isinstance(sprop, Mapping):
                v = _field(st, f"{key}.{sname}", f"{name}.{sname}", sprop, sub_defaults.get(sname), False)
                if v is not None and v != "" and v != []:
                    sub[sname] = v
        return sub or None
    if enum is not None:
        options = ["", *[str(x) for x in enum]]
        index = options.index(str(default)) if default is not None and str(default) in options else 0
        choice = st.selectbox(label, options, index=index, key=key, help=help_text)
        return next((x for x in enum if str(x) == choice), None) if choice else None
    if kind == "array":
        items = prop.get("items") if isinstance(prop.get("items"), Mapping) else {}
        if isinstance(items.get("enum"), list):
            options = [str(x) for x in items["enum"]]
            preset = [str(x) for x in default or [] if str(x) in options] if isinstance(default, list) else []
            return st.multiselect(label, options, default=preset, key=key, help=help_text) or None
        raw = st.text_input(label + " (JSON)", value=json.dumps(default) if default else "", key=key, help=help_text)
        return _json_or_none(raw)
    if kind == "boolean":
        options = ["(défaut)", "true", "false"]
        index = 1 if default is True else 2 if default is False else 0
        choice = st.selectbox(label, options, index=index, key=key, help=help_text)
        return None if choice == "(défaut)" else choice == "true"
    if kind == "integer" or kind == "number":
        lo, hi = prop.get("minimum"), prop.get("maximum")
        value = default if isinstance(default, int | float) and not isinstance(default, bool) else None
        number = st.number_input(
            label,
            min_value=lo,
            max_value=hi,
            value=value,
            step=1 if kind == "integer" else None,
            key=key,
            help=help_text,
        )
        if number is None:
            return None
        return int(number) if kind == "integer" else float(number)
    if kind == "string" and prop.get("format") == "date":
        value = dt.date.fromisoformat(default) if isinstance(default, str) and default else None
        chosen = st.date_input(label, value=value, key=key, help=help_text, format="YYYY-MM-DD")
        return chosen.isoformat() if isinstance(chosen, dt.date) else None
    if kind == "string" or kind is None:
        long_text = int(prop.get("maxLength", 0) or 0) > 400
        widget = st.text_area if long_text else st.text_input
        return widget(label, value=str(default) if default is not None else "", key=key, help=help_text).strip()
    raw = st.text_input(label + " (JSON)", value=json.dumps(default) if default is not None else "", key=key)
    return _json_or_none(raw)


def _json_or_none(raw: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def sidebar_defaults(spec: ToolSpec, ov: SidebarOverrides) -> dict[str, Any]:
    """Direct search: prefill a tool's form with the sidebar values it accepts (the user may change them)."""
    props = spec.properties
    out: dict[str, Any] = {}
    if ov.as_of and "as_of" in props:
        out["as_of"] = ov.as_of
    if "filters" in props:
        out["filters"] = {"in_force_only": ov.in_force_only, "doc_types": list(ov.doc_types or [])}
    if ov.retrieval_mode and "retrieval_mode" in props:
        out["retrieval_mode"] = ov.retrieval_mode
    if ov.top_k_cap and "top_k" in props:
        default = props["top_k"].get("default") if isinstance(props["top_k"], Mapping) else None
        out["top_k"] = min(ov.top_k_cap, default) if isinstance(default, int) else ov.top_k_cap
    return out


def page_banner(st: Any) -> None:
    """Art. 5 disclaimer on every page (R26) and, for an LLM endpoint outside the stack, where questions go (R14)."""
    cfg = settings(st)
    if cfg.llm_external:
        st.warning(
            f"LLM externe ({urlsplit(cfg.llm_base_url).hostname}) : les questions et les textes publics récupérés "
            "sont envoyés à cet endpoint OpenAI-compatible (Mistral par défaut)."
        )
    st.caption(
        "Information non officielle — « Le Journal officiel publié fait seul foi et produit des effets juridiques » "
        "(loi du 23 décembre 2016, art. 5). Outil de test, pas un avis juridique."
    )
