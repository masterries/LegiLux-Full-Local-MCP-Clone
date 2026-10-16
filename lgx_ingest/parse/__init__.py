"""S4 parse: raw files → ParseResult (units, relations, issues). Owner: agent B (M1).

Dispatch on ``entry.file.source_format``: ``akn`` (XML, M1), ``html`` (M1/M2), ``pdf`` (M2),
``recueil`` (XML code-recueil dedup, M2). Contract: docs/dev/INTERFACES.md §5.
"""

from __future__ import annotations

from pathlib import Path

from lgx_core.models import ManifestEntry, ParseResult, RawFile
from lgx_ingest.context import StageContext, StageResult


def parse_file(path: Path, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Parse one cached file for one manifest entry (pure apart from reading ``path``)."""
    raise NotImplementedError("lgx_ingest.parse.parse_file: M1 (agent B)")


def run_parse(ctx: StageContext) -> StageResult:
    """Parse every fetched manifest entry; write data/parsed/ and data/units/<T>/."""
    raise NotImplementedError("lgx parse: M1 (agent B)")
