"""S1 discover: flat, unsorted SPARQL pulls into data/meta/<T>/*.parquet (Appendix A).

Owner: agent A (M1). Contract: docs/dev/INTERFACES.md §3. Query texts: sparql/*.rq, rendered with
``lgx_ingest.sparql_templates.render_query``.
"""

from __future__ import annotations

from collections.abc import Sequence

from lgx_ingest.context import StageContext, StageResult


def run_discover(ctx: StageContext, queries: Sequence[str] | None = None) -> StageResult:
    """Run the full-table pulls (all, or the named query ids) and write the snapshot tables."""
    raise NotImplementedError("lgx discover: M1 (agent A)")
