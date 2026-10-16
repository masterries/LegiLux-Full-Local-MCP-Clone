"""S5 normalise + structure: ParseResult → data/units/<T>/<slug>.jsonl.

Folded into ``lgx parse`` for M1 (the parsers already emit normalised, structured units). This stage
re-emits the units files of T from the parse cache without re-parsing (e.g. after a new selection for
the same files). Recueil deduplication (PLAN §2.6) arrives with :mod:`lgx_ingest.parse.recueil` in M2.
"""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult


def run_structure(ctx: StageContext) -> StageResult:
    from lgx_ingest.parse import run_parse

    return run_parse(ctx, cache_only=True)
