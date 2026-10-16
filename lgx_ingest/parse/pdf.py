"""Born-digital PDF codes and consolidations (PLAN §6.3, §2.6). M2 — not part of the M1 sample."""

from __future__ import annotations

from lgx_core.models import ManifestEntry, ParseResult, RawFile

PARSER_VERSION = "pdf-0"


def parse_pdf(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    raise NotImplementedError("lgx_ingest.parse.pdf.parse_pdf: M2")
