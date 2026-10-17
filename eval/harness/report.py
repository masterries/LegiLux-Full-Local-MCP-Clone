"""JSON + Markdown reports under ``eval/reports/`` (one pair per run; ``compare`` renders several runs side by side)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

RETRIEVAL_COLS = ("recall@1", "recall@5", "recall@10", "recall@20", "mrr@10", "ndcg@10", "act_recall@10")


def write(report: Mapping[str, Any], out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / f"{name}.json"
    md = out_dir / f"{name}.md"
    js.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    md.write_text(markdown(report), encoding="utf-8")
    return js, md


def _fmt(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def run_label(r: Mapping[str, Any]) -> str:
    cfg = r.get("config") or {}
    label = f"{cfg.get('mode', '?')}/{cfg.get('ranker', '?')}"
    if cfg.get("expansion") is False:
        label += "/no-exp"
    if cfg.get("lexical_opts"):
        label += "/" + ",".join(f"{k}={v:g}" for k, v in sorted(cfg["lexical_opts"].items()))
    if cfg.get("mode") == "rerank":
        label += f" k={cfg.get('rerank_k')} {cfg.get('rerank_body_chars')}c"
    return label


def markdown(r: Mapping[str, Any]) -> str:
    s = r["summary"]
    lines = [
        f"# {r['set']} — {run_label(r)}",
        "",
        f"- release `{(r.get('config') or {}).get('release_id')}`, run {r.get('started_at')},"
        f" {r.get('items_total')} items ({r.get('gold_status')}), backend {(r.get('config') or {}).get('backend')}",
        "",
    ]
    if r["set"] == "G2":
        lines += ["| subset | n | " + " | ".join(RETRIEVAL_COLS) + " |", "|---" * (len(RETRIEVAL_COLS) + 2) + "|"]
        for key in ("all", "fr", "de", "en"):
            if key in s:
                row = s[key]
                lines.append(f"| {key} | {row['n']} | " + " | ".join(_fmt(row[c]) for c in RETRIEVAL_COLS) + " |")
        lat = s.get("latency") or {}
        lines += ["", f"Latency: tool {lat.get('tool')}, SQL {lat.get('sql')}, rerank {lat.get('rerank')}.", ""]
        misses = [i for i in r["items"] if not i["ranks"] or all(x is None or x > 10 for x in i["ranks"])]
        lines += [f"Targets not in the first 10 hits: {len(misses)} items.", ""]
        for i in misses[:40]:
            lines.append(f"- {i['id']} ({i['lang']}) « {i['query']} » → ranks {i['ranks']}")
    elif r["set"] == "G1":
        lines += ["| metric | value |", "|---|---|"]
        for k in (
            "n",
            "exact_accuracy",
            "citation_precision",
            "resolved_recall",
            "ambiguity_precision",
            "ambiguity_recall",
            "not_found_accuracy",
        ):
            lines.append(f"| {k} | {_fmt(s.get(k))} |")
        lines += [
            "",
            f"Outcomes: {s['outcomes']}; gates {s.get('gates')}",
            "",
            "| style | n | correct |",
            "|---|---|---|",
        ]
        for style, v in s["by_style"].items():
            lines.append(f"| {style} | {v['n']} | {v['correct']} |")
        bad = [i for i in r["items"] if i["outcome"] != "correct"]
        lines += ["", f"Not correct: {len(bad)}", ""]
        for i in bad[:60]:
            lines.append(
                f"- {i['id']} [{i['style']}] « {i['text']} »: expected {i['expect_status']}"
                f" {i['expect_unit'] or i['expect_act'] or ''}"
                f" — got {i['got_status']} {i['got_unit'] or i['got_act'] or ''} ({i['outcome']})"
            )
    elif r["set"] == "G3":
        lines += [
            f"Mandatory-warning coverage **{_fmt(s['mandatory_warning_coverage'])}**"
            f" ({s['expected_pairs'] - s['missing_pairs']}/{s['expected_pairs']}), items ok {s['items_ok']}/{s['n']},"
            f" forbidden seen {s['forbidden_seen']},"
            f" status statements {_fmt(s['status_correct'])}, skipped {s.get('skipped')}; gates {s.get('gates')}",
            "",
            "| code | expected | present |",
            "|---|---|---|",
        ]
        for code, v in s["per_code"].items():
            lines.append(f"| {code} | {v['expected']} | {v['present']} |")
        bad = [i for i in r["items"] if not i["ok"]]
        lines += ["", f"Failing items: {len(bad)}", ""]
        for i in bad:
            lines.append(
                f"- {i['id']} ({i['trigger']}): missing {i['missing']}, forbidden {i['forbidden_seen']},"
                f" status {i.get('got_status')}, error {i.get('got_error')}"
            )
    else:
        lines += [f"Passed {s['passed']}/{s['n']} (gate {s['gate']}: {s['gate_ok']})", ""]
        for i in r["items"]:
            if not i["ok"]:
                lines.append(f"- {i['id']}: {i['failed']}")
    return "\n".join(lines).rstrip() + "\n"


def restrict(report: Mapping[str, Any], ids: Sequence[str]) -> dict[str, Any]:
    """A G2 report scored again on a subset of its items (same per-item ranks; tool latency from the items,
    SQL / rerank latency are not per item and are dropped)."""
    from eval.harness import metrics

    want = set(ids)
    rows = [r for r in report["items"] if str(r["id"]) in want]
    summary: dict[str, Any] = {"all": metrics.summarise_retrieval(rows)}
    for lang in sorted({r["lang"] for r in rows}):
        summary[lang] = metrics.summarise_retrieval([r for r in rows if r["lang"] == lang])
    summary["errors"] = sum(1 for r in rows if r.get("error"))
    summary["reranked_items"] = sum(1 for r in rows if r.get("reranked"))
    summary["latency"] = {"tool": metrics.latency_summary([r["latency_ms"] for r in rows])}
    return {**report, "items": rows, "summary": summary, "subset": {"ids": sorted(want), "restricted": True}}


def compare_markdown(reports: Sequence[Mapping[str, Any]]) -> str:
    """One table per set, one row per run (e.g. lexical vs lexical + rerank)."""
    out: list[str] = []
    by_set: dict[str, list[Mapping[str, Any]]] = {}
    for r in reports:
        by_set.setdefault(r["set"], []).append(r)
    for name, runs in by_set.items():
        out += [f"### {name}", ""]
        if name == "G2":
            for subset in ("all", "fr", "de", "en"):
                rows = [r for r in runs if subset in r["summary"]]
                if not rows:
                    continue
                out += [f"**{subset}** (n = {rows[0]['summary'][subset]['n']})", ""]
                out += [
                    "| run | " + " | ".join(RETRIEVAL_COLS) + " | p50 tool ms |",
                    "|---" * (len(RETRIEVAL_COLS) + 2) + "|",
                ]
                for r in rows:
                    v = r["summary"][subset]
                    p50 = (r["summary"].get("latency") or {}).get("tool", {}).get("p50_ms")
                    out.append(
                        f"| {run_label(r)} | " + " | ".join(_fmt(v[c]) for c in RETRIEVAL_COLS) + f" | {_fmt(p50)} |"
                    )
                out.append("")
        elif name == "G1":
            out += [
                "| run | n | exact | citation precision | ambiguity P | ambiguity R | not-found |",
                "|---|---|---|---|---|---|---|",
            ]
            for r in runs:
                s = r["summary"]
                out.append(
                    f"| {run_label(r)} | {s['n']} | {_fmt(s['exact_accuracy'])} | {_fmt(s['citation_precision'])} |"
                    f" {_fmt(s['ambiguity_precision'])} | {_fmt(s['ambiguity_recall'])} |"
                    f" {_fmt(s['not_found_accuracy'])} |"
                )
            out.append("")
        elif name == "G3":
            out += ["| run | n | coverage | items ok | forbidden | status |", "|---|---|---|---|---|---|"]
            for r in runs:
                s = r["summary"]
                out.append(
                    f"| {run_label(r)} | {s['n']} | {_fmt(s['mandatory_warning_coverage'])} | {s['items_ok']} |"
                    f" {s['forbidden_seen']} | {_fmt(s['status_correct'])} |"
                )
            out.append("")
    return "\n".join(out)
