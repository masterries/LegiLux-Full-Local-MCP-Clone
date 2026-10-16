"""S8 load: fresh database per release, COPY, post-load, invariants (PLAN §6.1, Appendix B).

Owner: loader stage (after agents A and B). Contract: docs/dev/INTERFACES.md §6.
"""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_load(
    ctx: StageContext, *, dsn: str | None = None, db_name: str | None = None, recreate: bool = True
) -> StageResult:
    raise NotImplementedError("lgx load: M1 loader stage")
