"""CC-BY HTML parser (``div.richtext_article``; PLAN §6.3). Owner: agent B (M1 sample, M2 oracle)."""

from __future__ import annotations

from lgx_core.models import ManifestEntry, ParseResult, RawFile

PARSER_VERSION = "html-0"


def parse_html(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    raise NotImplementedError("lgx_ingest.parse.html.parse_html: M1/M2 (agent B)")
