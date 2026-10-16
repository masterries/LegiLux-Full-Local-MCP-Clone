"""S3 fetch: polite, resumable, conditional GETs into the content-addressed raw cache.

Owner: agent B (M1). Contract: docs/dev/INTERFACES.md §5 (PLAN §3.3, §6.1 S3).
"""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_fetch(ctx: StageContext, *, limit: int | None = None, include_m2: bool = False) -> StageResult:
    """Fetch every manifest entry with a ``file`` (sample entries only when ``ctx.sample``)."""
    raise NotImplementedError("lgx fetch: M1 (agent B)")
