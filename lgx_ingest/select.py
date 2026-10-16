"""S2 select: snapshot tables → manifest + metadata rows + selection report (PLAN §2.2).

Owner: agent A (M1). Thin I/O wrapper around the pure ``lgx_core.rules.select.select``.
Contract: docs/dev/INTERFACES.md §4.

Inputs: ``data/meta/<T>/*.parquet`` (or ``--opt snapshot=DIR``, e.g. the frozen golden snapshot) and the
robots-derived denylist ``data/robots/<T>/denylist.json``. Outputs (``data/manifest/<T>/``):
``manifest.jsonl``, ``acts.jsonl``, ``versions.jsonl``, ``relations.jsonl``, ``selection_report.json``,
``data_quality.jsonl`` and ``gdpr_review.json`` (lawyer review list; never shipped), plus the serving
plan ``data/meta/<T>/serving_plan.parquet`` (one row per act of the metadata scope).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from lgx_core.config import REPO_ROOT, ProjectConfig
from lgx_core.io import atomic_write_text, write_jsonl
from lgx_core.rules import golden
from lgx_core.rules.select import Selection, select
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.sparql import load_snapshot

GOLDEN_SNAPSHOT = REPO_ROOT / "tests" / "golden" / "snapshot-20261016"

SERVING_PLAN_SCHEMA = pa.schema(
    [
        ("eli_work", pa.string()),
        ("eli_jo", pa.string()),
        ("doc_type", pa.string()),
        ("act_type", pa.string()),
        ("status_at_t", pa.string()),
        ("text_basis", pa.string()),
        ("metadata_reason", pa.string()),
        ("reason", pa.string()),
        ("flags", pa.list_(pa.string())),
        ("served_version", pa.string()),
        ("served_kind", pa.string()),
        ("served_role", pa.string()),
        ("format", pa.string()),
        ("licence", pa.string()),
        ("licence_source", pa.string()),
        ("file_url", pa.string()),
        ("file_uri", pa.string()),
        ("text_valid_from", pa.date32()),
        ("text_valid_to", pa.date32()),
        ("window_version", pa.string()),
        ("window_is_stub", pa.bool_()),
        ("future_version", pa.string()),
        ("future_valid_from", pa.date32()),
        ("future_file_url", pa.string()),
        ("amending_acts", pa.list_(pa.string())),
        ("n_amending", pa.int32()),
        ("superseded_by", pa.string()),
        ("text_basis_without_licence", pa.string()),
        ("in_sample", pa.bool_()),
        ("sample_stage", pa.string()),
        ("title", pa.string()),
    ]
)


def serving_plan_path(ctx: StageContext) -> Path:
    return ctx.layout.meta_table("serving_plan")


def write_serving_plan(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(list(rows), schema=SERVING_PLAN_SCHEMA)
    tmp = path.with_name(f".{path.name}.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)


def robots_denylist(ctx: StageContext) -> tuple[list[str], str]:
    """ELI prefixes derived from robots.txt for T (``lgx robots``), and where they came from."""
    path = ctx.layout.robots_dir / "denylist.json"
    if not path.exists():
        return [], "missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [e["eli"] for e in data.get("entries", [])], str(path)


def write_selection(ctx: StageContext, sel: Selection) -> list[str]:
    layout = ctx.layout
    out = layout.manifest_dir
    write_jsonl(layout.manifest_path, sel.manifest)
    write_jsonl(layout.acts_path, sel.acts)
    write_jsonl(layout.versions_path, sel.versions)
    write_jsonl(layout.relations_path, sel.relations)
    write_jsonl(out / "data_quality.jsonl", sel.issues)
    atomic_write_text(out / "gdpr_review.json", json.dumps(sel.gdpr_review, indent=1, ensure_ascii=False) + "\n")
    atomic_write_text(
        layout.selection_report_path, json.dumps(sel.report, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    write_serving_plan(serving_plan_path(ctx), sel.plan)
    return [
        str(p)
        for p in (
            layout.manifest_path,
            layout.acts_path,
            layout.versions_path,
            layout.relations_path,
            out / "data_quality.jsonl",
            out / "gdpr_review.json",
            layout.selection_report_path,
            serving_plan_path(ctx),
        )
    ]


def run_select(ctx: StageContext) -> StageResult:
    """Write manifest.jsonl, acts.jsonl, versions.jsonl, relations.jsonl, selection_report.json."""
    meta = Path(ctx.opt("snapshot") or ctx.layout.meta_dir)
    snapshot = load_snapshot(meta)
    deny, deny_source = robots_denylist(ctx)
    sel = select(snapshot, ctx.sync_date, ctx.config, denylist=deny)
    sel.report["snapshot_dir"] = str(meta)
    sel.report["robots_denylist"] = {"source": deny_source, "entries": len(deny)}
    result = StageResult(stage="select")
    result.counts = {
        "acts": len(sel.acts),
        "versions": len(sel.versions),
        "relations": len(sel.relations),
        "manifest_entries": len(sel.manifest),
        **{f"text_basis.{k}": v for k, v in sel.report["counts"]["by_text_basis"].items()},
    }
    sample = sel.report["sample"]
    result.counts["sample_works"] = sample["works"]
    result.counts["sample_matched"] = sample["matched"]
    if deny_source == "missing":
        result.messages.append("no robots denylist for T: run `lgx robots` first (config denylist applied only)")
    for m in sample["mismatches"]:
        result.messages.append(f"sample mismatch {m['work']}: {', '.join(m['fields'])}")
    if sample["mismatches"] and ctx.opt("strict_sample") == "1":
        result.ok = False
    if ctx.dry_run:
        result.details["report"] = sel.report["counts"]
        return result
    result.outputs = write_selection(ctx, sel)
    result.details["golden_selection"] = {
        k: v for k, v in sel.report["golden"]["selection"].items() if not k.startswith(("flag.", "text_basis."))
    }
    return result


# PLAN §2.4 / §0 volume estimates, printed next to the selection for orientation (not asserted).
PLAN_ESTIMATES = {
    "loi.original_never_amended": "6,803 in-force LOI never modified [V] (research, flag-based)",
    "rgd.original_never_amended": "8,445 in-force RGD never modified [V] (research, flag-based)",
    "loi.original_possibly_outdated": "≈ 745–755 (721 + 16 + 18 − LOI stub fallbacks), before licence",
    "rgd.original_possibly_outdated": "≈ 790–800 (764 + 17 + 19 − RGD stub fallbacks), before licence",
    "text_basis.metadata_only": "≈ 35k of ≈ 58–59k acts in the metadata scope [E]",
    "is_amending_act_in_force": "5,773 pure amending acts in force [V] (scope-sizing)",
}


def golden_check(snapshot_dir: Path, config: ProjectConfig, sync_date: dt.date | None = None) -> tuple[str, bool]:
    """Expected vs actual golden numbers on one snapshot; ``ok`` is True when every exact one matches."""
    snapshot = load_snapshot(snapshot_dir)
    t = sync_date or snapshot.snapshot_date or golden.GOLDEN_DATE
    sel = select(snapshot, t, config)
    research = sel.report["golden"]["research"]
    selection = sel.report["golden"]["selection"]
    lines = [f"snapshot {snapshot_dir} (taken {snapshot.snapshot_date}), selection at T = {t}", ""]
    ok = True

    def table(title: str, rows: list[tuple[str, int, int | None, bool]], notes: dict[str, str]) -> None:
        nonlocal ok
        lines.append(f"{title}\n{'name':40} {'expected':>9} {'actual':>9}  status")
        for name, exp, act, good in rows:
            ok &= good
            note = f"  ({notes[name]})" if name in notes else ""
            lines.append(f"{name:40} {exp:>9} {act if act is not None else '-':>9}  {'ok' if good else 'DIFF'}{note}")
        lines.append("")

    table(
        "Research definitions (PLAN §11.5, all branches, raw flags where the research used them)",
        golden.compare(research, golden.EXPECTED),
        golden.RESEARCH_NOTES,
    )
    table(
        "Selection rules (leg branch only, dates win)",
        golden.compare(selection, golden.EXPECTED_SELECTION),
        golden.SELECTION_NOTES,
    )
    lines.append("Selection volumes vs plan estimates (informational)")
    for key, estimate in PLAN_ESTIMATES.items():
        lines.append(f"{key:40} {selection.get(key, 0):>9}  plan: {estimate}")
    for key in sorted(k for k in selection if k.endswith("_but_licence")):
        lines.append(f"{key:40} {selection[key]:>9}  (label it would have; text is licence-restricted)")
    for key in sorted(k for k in selection if k.startswith("text_basis.")):
        lines.append(f"{key:40} {selection[key]:>9}")
    lines += ["", "Reconciliation: research LOI/RGD class -> selection label [rule]"]
    for key, n in golden.reconcile(snapshot, sel.plan, t).items():
        lines.append(f"  {n:>6}  {key}")
    sample = sel.report["sample"]
    lines += ["", f"sample.yaml expectations: {sample['matched']} / {sample['works']} match"]
    for m in sample["mismatches"]:
        lines.append(f"  {m['work']}: {', '.join(m['fields'])} expected {m['expected']} actual {m['actual']}")
    for a in sel.report["anomalies"]:
        lines.append(f"anomaly {a['code']}: {a['count']} (e.g. {', '.join(a['examples'][:2])})")
    return "\n".join(lines), ok
