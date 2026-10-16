"""Born-digital PDF codes and consolidations (PLAN §6.3, §2.6). **M2** — not part of the M1 sample.

PDF entries of ``config/sample.yaml`` are ``stage: m2``; ``lgx fetch`` skips them unless ``--include-m2``
and ``lgx parse`` reports them as ``skipped_pdf``.
"""

from __future__ import annotations

from lgx_core.models import ManifestEntry, ParseResult, RawFile

PARSER_VERSION = "pdf-0"


def parse_pdf(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Parse a PDF-only code or consolidation into article (or page) units."""
    # TODO(M2): pdftotext -layout page by page (or pdfplumber), drop running headers/footers (lines on
    # > 50 % of pages), segment on ^Art\. ([LR]\. )?\d+[-\w]*\. with member-act headings as context
    # (code-recueils: unit key (code_version, member_act, num_norm)), marginal notes -> provenance,
    # low-confidence segments -> page units (anchor 'p.N-M'), pages without text layer logged + skipped.
    raise NotImplementedError("lgx_ingest.parse.pdf.parse_pdf: M2 (PDF-only codes)")
