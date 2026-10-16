"""S9 anomaly gates (PLAN §6.8). Implemented with the loader stage (M1) and extended in M2/M4."""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_gates(ctx: StageContext) -> StageResult:
    raise NotImplementedError("lgx gates: M1 loader stage")
