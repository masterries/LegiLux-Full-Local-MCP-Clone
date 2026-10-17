"""G0 smoke set runner for the Eval tab (PLAN §11.1: deterministic tool calls, no LLM).

Gold file format (JSON; a list of items or ``{"items": [...]}``)::

    {"id": "G0-09", "description": "…", "tool": "get_article",
     "arguments": {"citation": "art. L. 121-1 C. trav."},
     "expect_error": false,
     "checks": [{"path": "citation.article_eli", "op": "equals", "value": "http://…/art_l_121-1"}]}

Paths address ``structuredContent``: ``a.b``, ``list[0]``, ``list[*].field`` (flattens). Operators:
``equals``, ``contains`` (list contains / substring), ``any_contains`` / ``all_equals`` / ``any_startswith``
(over a ``[*]`` path), ``exists``, ``nonempty``, ``in``, ``gte``, ``lte``.

Search order for the gold set: ``$LGX_EVAL_DIR/G0/*.json`` → ``$LGX_EVAL_DIR/smoke.json`` → the dev sample
packaged with the UI (``lgx_ui/gold/g0_dev_sample.json``, written against the 2026-10-16 sample release).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from lgx_ui.mcp_client import ToolOutcome, ToolSession

_MISSING = object()
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\*|\d+)\]")
# The server's rate limit (PLAN §8.8: 30 calls/min per client) answers BACKEND_UNAVAILABLE with a retry hint.
_RETRY_AFTER = re.compile(r"(?:réessayez dans|retry (?:in|after))\s+(\d+(?:[.,]\d+)?)\s*s", re.IGNORECASE)
MAX_RATE_WAIT_S = 65.0


@dataclass(frozen=True)
class GoldItem:
    id: str
    tool: str
    arguments: dict[str, Any]
    checks: list[dict[str, Any]]
    description: str = ""
    expect_error: bool = False


@dataclass
class CheckResult:
    path: str
    op: str
    expected: Any
    actual: Any
    ok: bool


@dataclass
class ItemResult:
    item: GoldItem
    ok: bool
    is_error: bool
    latency_s: float
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "tool": self.item.tool,
            "arguments": self.item.arguments,
            "ok": self.ok,
            "is_error": self.is_error,
            "latency_s": round(self.latency_s, 4),
            "error": self.error,
            "checks": [
                {"path": c.path, "op": c.op, "expected": c.expected, "actual": c.actual, "ok": c.ok}
                for c in self.checks
            ],
        }


def resolve_path(data: Any, path: str) -> Any:
    """Value at ``path``; ``[*]`` returns a flat list; a missing step returns the ``_MISSING`` sentinel."""
    values: list[Any] = [data]
    multi = False
    for name, index in _TOKEN.findall(path):
        nxt: list[Any] = []
        for v in values:
            if name:
                if isinstance(v, Mapping) and name in v:
                    nxt.append(v[name])
            elif index == "*":
                multi = True
                if isinstance(v, list):
                    nxt.extend(v)
            elif isinstance(v, list) and int(index) < len(v):
                nxt.append(v[int(index)])
        values = nxt
    if multi:
        return values
    return values[0] if values else _MISSING


def _contains(actual: Any, value: Any) -> bool:
    if isinstance(actual, str):
        return isinstance(value, str) and value in actual
    if isinstance(actual, list):
        return value in actual
    return False


def evaluate(op: str, actual: Any, value: Any) -> bool:
    if op == "exists":
        return (actual is not _MISSING) == bool(value if value is not None else True)
    if actual is _MISSING:
        return False
    if op == "equals":
        return actual == value
    if op == "contains":
        return _contains(actual, value)
    if op == "nonempty":
        return bool(actual)
    if op == "in":
        return isinstance(value, list) and actual in value
    if op in ("gte", "lte"):
        try:
            return float(actual) >= float(value) if op == "gte" else float(actual) <= float(value)
        except (TypeError, ValueError):
            return False
    items = actual if isinstance(actual, list) else [actual]
    if op == "any_contains":
        return any(isinstance(a, str) and isinstance(value, str) and value in a for a in items)
    if op == "any_startswith":
        return any(isinstance(a, str) and a.startswith(str(value)) for a in items)
    if op == "all_equals":
        return bool(items) and all(a == value for a in items)
    raise ValueError(f"unknown check operator {op!r}")


def _public(actual: Any) -> Any:
    return None if actual is _MISSING else actual


def parse_items(data: Any) -> list[GoldItem]:
    raw = data.get("items") if isinstance(data, Mapping) else data
    items = []
    for i, entry in enumerate(raw or [], 1):
        if not isinstance(entry, Mapping) or not entry.get("tool"):
            continue
        items.append(
            GoldItem(
                id=str(entry.get("id") or f"G0-{i:02d}"),
                tool=str(entry["tool"]),
                arguments=dict(entry.get("arguments") or {}),
                checks=[dict(c) for c in entry.get("checks") or []],
                description=str(entry.get("description") or ""),
                expect_error=bool(entry.get("expect_error", False)),
            )
        )
    return items


def load_gold(eval_dir: Path | None) -> tuple[list[GoldItem], str]:
    """(items, source description)."""
    if eval_dir is not None:
        g0 = eval_dir / "G0"
        files = sorted(g0.glob("*.json")) if g0.is_dir() else []
        if not files and (eval_dir / "smoke.json").is_file():
            files = [eval_dir / "smoke.json"]
        items: list[GoldItem] = []
        for f in files:
            items.extend(parse_items(json.loads(f.read_text(encoding="utf-8"))))
        if items:
            return items, ", ".join(str(f) for f in files)
    ref = resources.files("lgx_ui").joinpath("gold/g0_dev_sample.json")
    return parse_items(json.loads(ref.read_text(encoding="utf-8"))), "lgx_ui/gold/g0_dev_sample.json (échantillon dev)"


def retry_after(outcome: ToolOutcome) -> float | None:
    """Seconds to wait when the server rate-limited the call (its message carries the hint), else ``None``."""
    err = outcome.error if outcome.is_error else None
    if not err or err.get("code") not in ("BACKEND_UNAVAILABLE", "RATE_LIMITED"):
        return None
    match = _RETRY_AFTER.search(str(err.get("message") or ""))
    return float(match.group(1).replace(",", ".")) if match else None


async def run_item(
    session: ToolSession,
    item: GoldItem,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_wait_s: float = MAX_RATE_WAIT_S,
) -> ItemResult:
    start = time.perf_counter()
    outcome = await session.call_tool(item.tool, item.arguments)
    wait = retry_after(outcome)
    if wait is not None and wait <= max_wait_s:  # one bounded retry after the server's own hint
        await sleep(wait + 0.5)
        outcome = await session.call_tool(item.tool, item.arguments)
    latency = outcome.latency_s or (time.perf_counter() - start)
    data = outcome.structured if outcome.structured is not None else {}
    results = []
    for check in item.checks:
        path, op, value = str(check.get("path", "")), str(check.get("op", "equals")), check.get("value")
        actual = resolve_path(data, path) if path else data
        try:
            ok = evaluate(op, actual, value)
        except ValueError:
            ok = False
        results.append(CheckResult(path, op, value, _public(actual), ok))
    error_ok = outcome.is_error == item.expect_error
    err = outcome.error if outcome.is_error else None
    return ItemResult(
        item,
        error_ok and all(r.ok for r in results),
        outcome.is_error,
        latency,
        results,
        None if error_ok else (f"isError={outcome.is_error} (attendu {item.expect_error}) {err or ''}".strip()),
    )


async def run_g0(
    session: ToolSession,
    items: Sequence[GoldItem],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[ItemResult]:
    return [await run_item(session, item, sleep=sleep) for item in items]


def summary(results: Sequence[ItemResult]) -> dict[str, Any]:
    passed = sum(r.ok for r in results)
    latencies = sorted(r.latency_s for r in results)
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    return {
        "passed": passed,
        "total": len(results),
        "gate": "G0 20/20" if len(results) == 20 else f"G0 {len(results)} items",
        "gate_ok": bool(results) and passed == len(results),
        "latency_p50_s": round(p50, 4),
        "latency_max_s": round(latencies[-1], 4) if latencies else 0.0,
    }
