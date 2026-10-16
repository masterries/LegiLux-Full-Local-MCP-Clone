"""S2 select: snapshot tables → manifest + metadata rows + selection report (PLAN §2.2).

Owner: agent A (M1). Thin I/O wrapper around the pure ``lgx_core.rules.select.select``.
Contract: docs/dev/INTERFACES.md §4.
"""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_select(ctx: StageContext) -> StageResult:
    """Write manifest.jsonl, acts.jsonl, versions.jsonl, relations.jsonl, selection_report.json."""
    raise NotImplementedError("lgx select: M1 (agent A)")
