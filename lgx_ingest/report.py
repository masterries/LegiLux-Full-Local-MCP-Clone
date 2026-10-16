"""Sizing report from the sample: articles, text bytes, chunks per stratum (PLAN §4.1, §6.2)."""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_report(ctx: StageContext) -> StageResult:
    raise NotImplementedError("lgx report: M1 (after parse/chunk)")
