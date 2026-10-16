"""Licence-aware format choice (PLAN §2.2 step 3, §3.4, D4).

Order: XML (``xml``/``xml-akomantoso``) → HTML → PDF, each only under an acceptable licence. Under
``cc_by_only`` that is CC BY 4.0; ``allow_licence_scl`` (after written SCL confirmation, OD5) also
accepts ``licenceSCL`` but still prefers CC BY within a format. PDF is accepted only where the caller
allows it: born-digital PDF-only consolidations, codes and recueils. JO PDFs often point to whole
Mémorial issues and are never act text in v1.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lgx_core.models import CC_BY_4_0, LICENCE_SCL, MetadataReason, SourceFormat, UserFormat

FORMAT_ORDER: tuple[tuple[SourceFormat, tuple[str, ...]], ...] = (
    (SourceFormat.XML, (UserFormat.XML.value, UserFormat.XML_AKOMANTOSO.value)),
    (SourceFormat.HTML, (UserFormat.HTML.value,)),
    (SourceFormat.PDF, (UserFormat.PDF.value,)),
)
TEXT_USER_FORMATS = frozenset(uf for _, ufs in FORMAT_ORDER for uf in ufs)

Formats = Mapping[str, Iterable[str | None]]  # user-format code -> licences seen on its manifestations


@dataclass(frozen=True)
class Pick:
    """Outcome of the picker: a format + licence, or the metadata reason when nothing is usable."""

    source_format: SourceFormat | None
    user_formats: tuple[str, ...] = ()
    licence: str | None = None
    reason: MetadataReason | None = None

    @property
    def ok(self) -> bool:
        return self.source_format is not None

    @property
    def scl(self) -> bool:
        return self.licence == LICENCE_SCL


def acceptable_licences(policy: str) -> tuple[str, ...]:
    return (CC_BY_4_0,) if policy == "cc_by_only" else (CC_BY_4_0, LICENCE_SCL)


def pick_format(formats: Formats, *, policy: str = "cc_by_only", allow_pdf: bool = False) -> Pick:
    """Choose the format to ingest from the formats/licences of one version (Q03, Q19 or Q-MANIF)."""
    seen = {uf: set(lics) for uf, lics in formats.items() if uf in TEXT_USER_FORMATS}
    accepted = acceptable_licences(policy)
    for fmt, ufs in FORMAT_ORDER:
        if fmt is SourceFormat.PDF and not allow_pdf:
            continue
        for licence in accepted:
            matching = tuple(uf for uf in ufs if licence in seen.get(uf, ()))
            if matching:
                return Pick(fmt, matching, licence)
    if not seen:
        return Pick(None, reason=MetadataReason.LICENCE)
    pdf_ok = any(lic in accepted for lic in seen.get(UserFormat.PDF.value, ()))
    only_pdf = set(seen) == {UserFormat.PDF.value}
    if pdf_ok or only_pdf:
        return Pick(None, reason=MetadataReason.PDF_ONLY)
    return Pick(None, reason=MetadataReason.LICENCE)
