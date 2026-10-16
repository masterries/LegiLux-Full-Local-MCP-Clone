"""Akoma Ntoso CSD13 + scl: parser (PLAN §6.3; port of the prototype akn_parse.py). Owner: agent B (M1)."""

from __future__ import annotations

from lgx_core.models import ManifestEntry, ParseResult, RawFile

PARSER_VERSION = "akn-0"


def parse_akn(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    raise NotImplementedError("lgx_ingest.parse.akn.parse_akn: M1 (agent B)")
