"""Run one gold set through a backend and score it (PLAN §11.1–11.2)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from eval.harness import metrics
from eval.harness.backends import Backend
from eval.harness.gen_g2 import hit_key

G2_TOP_K = 20
Progress = Callable[[int, int, str], None]


def _codes(sc: Mapping[str, Any]) -> list[str]:
    return [w.get("code") for w in sc.get("warnings") or [] if isinstance(w, Mapping)]


def _public(value: Any) -> Any:
    from lgx_ui.evalg0 import _MISSING

    return None if value is _MISSING else value


def _get(data: Any, path: str) -> Any:
    from lgx_ui.evalg0 import resolve_path

    return resolve_path(data, path)


# ---------------------------------------------------------------------------------------------- G0
async def run_g0(backend: Backend, items: Sequence[Mapping[str, Any]], progress: Progress | None = None) -> dict:
    from lgx_ui.evalg0 import _MISSING, evaluate

    rows = []
    for i, it in enumerate(items, 1):
        call = await backend.call(it["tool"], it["arguments"])
        checks = []
        for c in it["checks"]:
            actual = _get(call.structured, c.get("path", ""))
            try:
                ok = evaluate(c.get("op", "equals"), actual, c.get("value"))
            except ValueError:
                ok = False
            checks.append({**c, "actual": None if actual is _MISSING else actual, "ok": ok})
        error_ok = call.is_error == bool(it.get("expect_error"))
        rows.append(
            {
                "id": it["id"],
                "ok": error_ok and all(c["ok"] for c in checks),
                "is_error": call.is_error,
                "latency_ms": round(call.latency_ms, 1),
                "failed": [c for c in checks if not c["ok"]],
            }
        )
        if progress:
            progress(i, len(items), it["id"])
    passed = sum(r["ok"] for r in rows)
    summary = {"n": len(rows), "passed": passed, "gate": "G0 20/20", "gate_ok": passed == len(rows) == 20}
    return {"summary": summary, "items": rows}


# ---------------------------------------------------------------------------------------------- G1
async def run_g1(backend: Backend, items: Sequence[Mapping[str, Any]], progress: Progress | None = None) -> dict:
    rows = []
    for i, it in enumerate(items, 1):
        args: dict[str, Any] = {"text": it["text"]}
        if it.get("as_of"):
            args["as_of"] = it["as_of"]
        call = await backend.call("resolve_citation", args)
        sc = call.structured
        err_code = (sc.get("error") or {}).get("code") if call.is_error else None
        status = ("out_of_scope" if err_code == "OUT_OF_SCOPE" else "error") if call.is_error else sc.get("status")
        got = {"status": status, "candidates": sc.get("candidates") or []}
        outcome = metrics.resolver_outcome(it["expect"], got)
        top = got["candidates"][0] if got["candidates"] else {}
        rows.append(
            {
                "id": it["id"],
                "style": it.get("style"),
                "text": it["text"],
                "expect_status": it["expect"]["status"],
                "got_status": got["status"],
                "outcome": outcome,
                "expect_act": it["expect"].get("act_eli"),
                "expect_unit": it["expect"].get("unit_eli"),
                "got_act": top.get("act_eli"),
                "got_unit": top.get("unit_eli"),
                "candidates": len(got["candidates"]),
                "latency_ms": round(call.latency_ms, 1),
                "error": (sc.get("error") or {}).get("code") if call.is_error else None,
            }
        )
        if progress:
            progress(i, len(items), it["id"])
    summary = metrics.summarise_resolver(rows)
    summary["latency"] = metrics.latency_summary([r["latency_ms"] for r in rows])
    summary["gates"] = {
        "exact_accuracy>=0.95": (summary["exact_accuracy"] or 0) >= 0.95,
        "ambiguity_recall>=0.90": (summary["ambiguity_recall"] or 0) >= 0.90,
    }
    return {"summary": summary, "items": rows}


# ---------------------------------------------------------------------------------------------- G2
def _targets(it: Mapping[str, Any]) -> list[metrics.Target]:
    """Frozen targets carry ``key`` (eval/harness/gen_g2.py); an unfrozen one falls back to its ELI."""
    return [
        metrics.Target(str(t.get("key") or t["eli"]), int(t.get("grade", 2)), t.get("act_eli")) for t in it["targets"]
    ]


async def run_g2(backend: Backend, items: Sequence[Mapping[str, Any]], progress: Progress | None = None) -> dict:
    rows = []
    sql_ms: list[float] = []
    rr_ms: list[float] = []
    for i, it in enumerate(items, 1):
        args: dict[str, Any] = {"query": it["query"], "top_k": int(it.get("top_k") or G2_TOP_K)}
        if it.get("filters"):
            args["filters"] = it["filters"]
        call = await backend.call("search_legislation", args)
        sc = call.structured
        hits = sc.get("hits") or []
        keys = [hit_key(h.get("citation") or {}) for h in hits]
        acts = [(h.get("citation") or {}).get("act_eli") for h in hits]
        targets = _targets(it)
        m = metrics.retrieval_item(targets, keys, acts)
        ret = sc.get("retrieval") or {}
        lat = ret.get("latency_ms") or {}
        if lat:
            sql_ms.append(float(lat.get("sql") or 0))
            if ret.get("reranked"):
                rr_ms.append(float(lat.get("rerank") or 0))
        rows.append(
            {
                "id": it["id"],
                "lang": it["lang"],
                "query": it["query"],
                **m,
                "targets": [t.key for t in targets],
                "top5": keys[:5],
                "reranked": ret.get("reranked"),
                "rerank_status": ret.get("rerank_status"),
                "lexical_ranker": ret.get("lexical_ranker") or ret.get("lexical_order"),
                "error": (sc.get("error") or {}).get("code") if call.is_error else None,
                "latency_ms": round(call.latency_ms, 1),
            }
        )
        if progress:
            progress(i, len(items), it["id"])
    summary: dict[str, Any] = {"all": metrics.summarise_retrieval(rows)}
    for lang in sorted({r["lang"] for r in rows}):
        summary[lang] = metrics.summarise_retrieval([r for r in rows if r["lang"] == lang])
    summary["errors"] = sum(1 for r in rows if r["error"])
    summary["reranked_items"] = sum(1 for r in rows if r["reranked"])
    summary["latency"] = {
        "tool": metrics.latency_summary([r["latency_ms"] for r in rows]),
        "sql": metrics.latency_summary(sql_ms),
        "rerank": metrics.latency_summary(rr_ms),
    }
    return {"summary": summary, "items": rows}


# ---------------------------------------------------------------------------------------------- G3
async def run_g3(backend: Backend, items: Sequence[Mapping[str, Any]], progress: Progress | None = None) -> dict:
    rows = []
    for i, it in enumerate(items, 1):
        exp = it["expect"]
        call = await backend.call(it["tool"], it["arguments"], clock=it.get("clock"))
        if call.skipped:
            rows.append(
                {
                    "id": it["id"],
                    "trigger": it["trigger"],
                    "skipped": call.skipped,
                    **metrics.warning_item(
                        exp.get("warnings") or [], exp.get("absent") or [], exp.get("warnings") or []
                    ),
                }
            )
            continue
        sc = call.structured
        codes = _codes(sc)
        row = {
            "id": it["id"],
            "trigger": it["trigger"],
            "tool": it["tool"],
            **metrics.warning_item(exp.get("warnings") or [], exp.get("absent") or [], codes),
            "got": codes,
        }
        status_ok = None
        if exp.get("status"):
            got_status = _public(_get(sc, exp.get("status_path") or "status_as_of.status"))
            status_ok = got_status == exp["status"]
            row["got_status"] = got_status
        if exp.get("error_code") is not None:
            err = (sc.get("error") or {}).get("code") if call.is_error else None
            ok_err = err == exp["error_code"]
            status_ok = ok_err if status_ok is None else (status_ok and ok_err)
            row["got_error"] = err
        elif call.is_error:
            row["got_error"] = (sc.get("error") or {}).get("code")
            status_ok = False
        row["status_ok"] = status_ok
        row["ok"] = row["ok"] and status_ok is not False
        rows.append(row)
        if progress:
            progress(i, len(items), it["id"])
    scored = [r for r in rows if not r.get("skipped")]
    summary = metrics.summarise_warnings(scored)
    summary["skipped"] = len(rows) - len(scored)
    summary["gates"] = {
        "mandatory_warnings==100%": summary["mandatory_warning_coverage"] == 1.0 and summary["forbidden_seen"] == 0,
        "status_statements>=98%": (summary["status_correct"] or 0) >= 0.98,
    }
    return {"summary": summary, "items": rows}


RUNNERS: dict[str, Callable[..., Awaitable[dict]]] = {"G0": run_g0, "G1": run_g1, "G2": run_g2, "G3": run_g3}


def stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
