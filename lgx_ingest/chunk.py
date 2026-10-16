"""S6 chunk: units → retrieval chunks, one chunk per article (PLAN §6.4). Owner: agent B (M1)."""

from __future__ import annotations

from collections.abc import Sequence

from lgx_core.models import Chunk, Unit
from lgx_ingest.context import StageContext, StageResult

CHUNKER_VERSION = "chunk-0"


def chunk_units(units: Sequence[Unit], *, title_short: str, version_label: str) -> list[Chunk]:
    """Chunks for the units of one version (repealed stubs and modifying articles produce none)."""
    raise NotImplementedError("lgx_ingest.chunk.chunk_units: M1 (agent B)")


def run_chunk(ctx: StageContext) -> StageResult:
    raise NotImplementedError("lgx chunk: M1 (agent B)")
