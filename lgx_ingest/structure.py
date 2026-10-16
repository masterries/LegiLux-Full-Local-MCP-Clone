"""S5 normalise + structure: ParseResult → data/units/<T>/<slug>.jsonl (owner: agent B, M1).

May be folded into ``run_parse`` for M1; the output contract (INTERFACES §5) is what matters.
"""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_structure(ctx: StageContext) -> StageResult:
    raise NotImplementedError("lgx structure: M1 (agent B)")
