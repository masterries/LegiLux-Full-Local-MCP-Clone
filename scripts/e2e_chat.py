#!/usr/bin/env python3
"""Scripted chat end-to-end check through the tester UI's real code path (PLAN §9.3; docs/dev/e2e-2026-10-17.md).

Each question runs exactly what the Chat page runs: ``app_support.run_chat`` → ``LgxMcpClient`` (MCP, bearer) +
``OpenAICompatChat`` (OpenAI-compatible LLM) → ``tools_bridge.run_chat_turn`` (tool loop, sidebar overrides,
retrieve-first fallback) → ``verify_light.verify_turn`` and the ``render`` builders (sources, warnings). The
sidebar defaults of a fresh browser session are used (in force only, ``retrieval_mode`` auto, ``top_k`` cap =
the search tool's default). The LLM client is wrapped only to record per-call latency and token usage.

Run it where the UI runs (settings from the same ``LGX_*`` / ``OPENAI_*`` environment):

    make e2e                                                          # inside the dev stack's lgx-streamlit
    docker compose -f deploy/compose.dev.yml exec -T lgx-streamlit python - [options] < scripts/e2e_chat.py
    LGX_MCP_URL=http://127.0.0.1:8000/mcp LGX_MCP_TOKEN=… OPENAI_BASE_URL=… uv run python scripts/e2e_chat.py

Options: ``--question LANG:TEXT`` (repeatable, replaces the default set), ``--model``, ``--mode``
(auto | tools | retrieve_first), ``--max-iterations``. Progress goes to stderr, one JSON report to stdout.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from lgx_ui import app_support as ui
from lgx_ui.llm import ChatResponse, LlmConfig, OpenAICompatChat
from lgx_ui.render import build_citation_cards, build_envelope, build_warnings, vector_status_line
from lgx_ui.tools_bridge import CHAT_MODES, SidebarOverrides
from lgx_ui.verify_light import ELI_RE, KEY_RE, verify_turn

DEFAULT_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("fr", "Quel est le délai de préavis en cas de licenciement selon le Code du travail ?"),
    ("fr", "Que dit l'article 1384 du Code civil ?"),
    ("fr", "Quelles sont les obligations du responsable du traitement selon la loi du 1er août 2018 ?"),
    (
        "fr",
        "Le règlement grand-ducal du 24 juillet 2000 concernant le travail de candidature est-il encore en vigueur ?",
    ),
    ("de", "Was regelt Art. L. 121-1 des Code du travail?"),
)


class _SessionState:
    """The part of the ``streamlit`` module that ``app_support`` uses outside widgets."""

    def __init__(self) -> None:
        self.session_state: dict[str, Any] = {}


class RecordingChat:
    """Wraps the real OpenAI-compatible client; records latency, usage and tool calls of every LLM call."""

    def __init__(self, inner: OpenAICompatChat, log: list[dict[str, Any]]) -> None:
        self.inner = inner
        self.log = log

    async def __aenter__(self) -> RecordingChat:
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.inner.__aexit__(*exc_info)

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> ChatResponse:
        start = time.perf_counter()
        entry: dict[str, Any] = {"messages": len(messages), "with_tools": bool(tools)}
        try:
            resp = await self.inner.chat(messages, tools=tools, tool_choice=tool_choice)
        except Exception as exc:
            entry.update(error=str(exc)[:300], latency_s=round(time.perf_counter() - start, 2))
            self.log.append(entry)
            raise
        usage = resp.usage or {}
        entry.update(
            latency_s=round(resp.latency_s, 2),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=resp.finish_reason,
            tool_calls=[tc.name for tc in resp.tool_calls],
        )
        self.log.append(entry)
        return resp


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _elis(texts: Iterable[str]) -> set[str]:
    return {m.rstrip(".").rstrip("/") for text in texts for m in ELI_RE.findall(text)}


def _parse_question(raw: str) -> tuple[str, str]:
    lang, sep, text = raw.partition(":")
    if sep and lang in ("fr", "de", "en"):
        return lang, text.strip()
    return "fr", raw.strip()


def run(args: argparse.Namespace) -> dict[str, Any]:
    st = _SessionState()
    cfg = ui.settings(st)
    llm_log: list[dict[str, Any]] = []
    base = ui.default_deps()
    st.session_state["lgx_deps"] = ui.UiDeps(
        open_session=base.open_session,
        make_llm=lambda c: RecordingChat(OpenAICompatChat(c), llm_log),
        readyz=base.readyz,
    )
    health = ui.health(st)
    specs = ui.tools(st)
    if not specs:
        raise SystemExit(f"e2e: no MCP tools ({ui.tools_error(st)})")
    _, _, top_default, _ = ui.search_schema_info(specs)
    vstatus, _ = ui.vector_status(st)
    llm = LlmConfig(
        base_url=cfg.llm_base_url,
        model=args.model or cfg.llm_model,
        api_key=cfg.llm_api_key,
        temperature=cfg.llm_temperature,
        timeout_s=cfg.llm_timeout_s,
        trust_env=cfg.llm_trust_env,
    )
    questions = [_parse_question(q) for q in args.question] if args.question else list(DEFAULT_QUESTIONS)
    report: dict[str, Any] = {
        "started_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "model": llm.model,
        "llm_base_url": llm.base_url,
        "llm_timeout_s": llm.timeout_s,
        "mcp_url": cfg.mcp_url,
        "release_id": health.body.get("release_id"),
        "vector_status": vstatus,
        "tools": [t.name for t in specs],
        "mode": args.mode,
        "max_iterations": args.max_iterations or cfg.max_tool_iterations,
        "tool_result_tokens": cfg.tool_result_tokens,
        "questions": [],
    }
    for n, (lang, question) in enumerate(questions, 1):
        print(f"[{n}/{len(questions)}] ({lang}) {question}", file=sys.stderr, flush=True)
        sb = ui.SidebarState(
            llm=llm,
            preset_key="e2e",
            chat_mode=args.mode,
            max_iterations=args.max_iterations or cfg.max_tool_iterations,
            prompt_lang=lang,
            overrides=SidebarOverrides(in_force_only=True, retrieval_mode="auto", top_k_cap=top_default),
            vector_status=vstatus,
        )
        del llm_log[:]
        turn = ui.run_chat(st, sb, question, [])
        outcomes = turn.outcomes
        cards = build_citation_cards(outcomes)
        warnings = build_warnings(outcomes, cards)
        verifier = verify_turn(turn.answer, outcomes)
        returned = _elis(s for o in outcomes if not o.is_error for s in [*_strings(o.structured), o.text])
        cited = _elis([turn.answer])
        env = build_envelope(outcomes)
        entry = {
            "n": n,
            "lang": lang,
            "question": question,
            "answer": turn.answer,
            "mode": turn.mode,
            "stop_reason": turn.stop_reason,
            "error": turn.error,
            "notes": turn.notes,
            "prompt_source": turn.prompt_source,
            "iterations": turn.iterations,
            "llm_calls": list(llm_log),
            "latency": {
                "llm_s": round(turn.llm_latency_s, 2),
                "mcp_s": round(turn.mcp_latency_s, 3),
                "total_s": round(turn.total_s, 2),
            },
            "tool_calls": [
                {
                    "name": t.name,
                    "model_arguments": t.model_arguments,
                    "effective_arguments": t.effective_arguments,
                    "overrides_applied": t.overrides_applied,
                    "is_error": t.is_error,
                    "error": t.error_payload(),
                    "latency_s": round(t.latency_s, 3),
                    "hits": len(t.outcome.structured.get("hits") or [])
                    if t.outcome is not None and isinstance(t.outcome.structured, Mapping)
                    else None,
                }
                for t in turn.traces
            ],
            "cited_elis": sorted(cited),
            "cited_keys": sorted(set(KEY_RE.findall(turn.answer))),
            "cited_elis_not_returned": sorted(cited - returned),
            "returned_elis": len(returned),
            "sources": [
                {"label": c.label, "eli": c.eli, "text_basis": c.text_basis, "warnings": c.warnings} for c in cards
            ],
            "warnings": [{"code": w.code, "severity": w.severity, "refs": w.refs} for w in warnings],
            "verifier": [{"code": c.code, "ok": c.ok, "detail": c.detail} for c in verifier.checks],
            "retrieval": vector_status_line(env.retrieval, vstatus),
        }
        report["questions"].append(entry)
        calls = ", ".join(t["name"] for t in entry["tool_calls"]) or "none"
        checks = " ".join(f"{c.code}={'ok' if c.ok else '-' if c.ok is None else 'FAIL'}" for c in verifier.checks)
        print(
            f"    {turn.mode}/{turn.stop_reason} · tools: {calls} · cited ELIs {len(cited)} "
            f"(not returned: {len(entry['cited_elis_not_returned'])}) · keys {entry['cited_keys']} · {checks} · "
            f"{turn.total_s:.0f} s\n    answer: {' '.join(turn.answer.split())[:240]}",
            file=sys.stderr,
            flush=True,
        )
    report["finished_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="e2e_chat", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--question", action="append", default=[], help="LANG:TEXT (repeatable)")
    parser.add_argument("--model", help="LLM model (default LGX_LLM_MODEL)")
    parser.add_argument("--mode", choices=CHAT_MODES, default="auto")
    parser.add_argument("--max-iterations", type=int, default=None)
    args = parser.parse_args(argv)
    json.dump(run(args), sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
