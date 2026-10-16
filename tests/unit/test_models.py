from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from lgx_core.models import (
    CC_BY_4_0,
    Act,
    ActVersion,
    Chunk,
    DateWindow,
    Manifestation,
    Memorial,
    MetadataReason,
    SourceFormat,
    TextBasis,
    Unit,
    UserFormat,
    VersionKind,
    VersionRole,
    WarningCode,
    sha256_hex,
)

E = "http://data.legilux.public.lu/eli/etat/leg/"
H = "a" * 64


def test_text_basis_has_exactly_the_five_plan_labels() -> None:
    assert [t.value for t in TextBasis] == [
        "consolidated",
        "stub_fallback",
        "original_never_amended",
        "original_possibly_outdated",
        "metadata_only",
    ]


def test_warning_codes_match_appendix_d() -> None:
    assert len(WarningCode) == 22
    assert all(c.value.startswith("W_") for c in WarningCode)


def test_date_window_half_open() -> None:
    w = DateWindow(lower=dt.date(2026, 4, 16), upper=dt.date(2027, 4, 16))
    assert w.contains(dt.date(2026, 4, 16)) and not w.contains(dt.date(2027, 4, 16))
    assert DateWindow(lower=dt.date(2027, 4, 16)).contains(dt.date(2100, 1, 1))
    assert w.overlaps(DateWindow(lower=dt.date(2027, 4, 15)))
    assert not w.overlaps(DateWindow(lower=dt.date(2027, 4, 16)))
    with pytest.raises(ValidationError):
        DateWindow(lower=dt.date(2026, 1, 2), upper=dt.date(2026, 1, 2))


def test_act_metadata_reason_iff_metadata_only() -> None:
    Act(
        eli_work=E + "loi/1976/08/29/n5",
        doc_type="LOI",
        text_basis=TextBasis.METADATA_ONLY,
        metadata_reason=MetadataReason.REPEALED,
    )
    with pytest.raises(ValidationError):
        Act(eli_work=E + "x", doc_type="LOI", text_basis=TextBasis.METADATA_ONLY)
    with pytest.raises(ValidationError):
        Act(eli_work=E + "x", doc_type="LOI", text_basis=TextBasis.CONSOLIDATED, metadata_reason="licence")


def test_act_version_mirrors_db_checks() -> None:
    base = {
        "eli_work": E + "w",
        "eli_version": E + "w/jo",
        "kind": VersionKind.ORIGINAL,
        "role": VersionRole.CURRENT,
    }
    ActVersion(**base)
    ok = ActVersion(
        **base,
        has_text=True,
        text_valid=DateWindow(lower=dt.date(2020, 1, 1)),
        source_url="https://legilux.public.lu/filestore/eli/etat/leg/x",
        licence=CC_BY_4_0,
        sha256=H.upper(),
    )
    assert ok.sha256 == H
    with pytest.raises(ValidationError):  # has_text without window
        ActVersion(**base, has_text=True)
    with pytest.raises(ValidationError):  # text without provenance
        ActVersion(**base, has_text=True, text_valid=DateWindow(lower=dt.date(2020, 1, 1)))
    with pytest.raises(ValidationError):  # stub with text
        ActVersion(
            **base,
            has_text=True,
            is_stub=True,
            text_valid=DateWindow(lower=dt.date(2020, 1, 1)),
            source_url="u",
            licence=CC_BY_4_0,
            sha256=H,
        )
    with pytest.raises(ValidationError):
        ActVersion(**base, sha256="not-a-hash")


def _unit(**kw: object) -> Unit:
    data: dict[str, object] = {
        "eli_work": E + "loi/2018/08/01/a686",
        "eli_version": E + "loi/2018/08/01/a686/consolide/20251226",
        "ordinal": 2,
        "depth": 1,
        "unit_type": "article",
        "anchor": "art_56",
        "doc_type": "LOI",
        "source_format": "xml",
        "source_url": "https://legilux.public.lu/filestore/eli/etat/leg/x",
        "source_sha256": H,
        "licence": CC_BY_4_0,
        "text_basis": "consolidated",
    }
    data.update(kw)
    return Unit.model_validate(data)


def test_unit_hash_default_and_parent_order() -> None:
    u = _unit(text_plain="Abrogé.", is_repealed=True)
    assert u.text_sha256 == sha256_hex("Abrogé.")
    assert _unit(text_plain="x", text_sha256=H).text_sha256 == H
    assert Unit.model_validate_json(u.model_dump_json()) == u
    with pytest.raises(ValidationError):
        _unit(parent_ordinal=2)
    with pytest.raises(ValidationError):
        _unit(unexpected_field=1)


def test_chunk_window_and_parts() -> None:
    base = {
        "eli_work": E + "w",
        "eli_version": E + "w/jo",
        "unit_ordinal": 1,
        "header": "h",
        "body": "b",
        "doc_type": "LOI",
        "text_basis": "consolidated",
        "valid_from": dt.date(2026, 1, 1),
        "content_sha256": H,
    }
    Chunk.model_validate(base)
    with pytest.raises(ValidationError):
        Chunk.model_validate({**base, "valid_to": dt.date(2026, 1, 1)})
    with pytest.raises(ValidationError):
        Chunk.model_validate({**base, "part_no": 2})


def test_memorial_from_uri() -> None:
    m = Memorial.from_uri(E + "memorial/1997/a36")
    assert (m.series, m.year, m.number, m.label) == ("A", 1997, "36", "Mémorial A n° 36 de 1997")
    with pytest.raises(ValueError):
        Memorial.from_uri(E + "loi/1997/01/01/n1")


def test_manifestation_formats_and_licence() -> None:
    m = Manifestation(
        eli=E + "w/jo",
        user_format="xml-akomantoso",
        licence=CC_BY_4_0,
        file_uri="http://data.legilux.public.lu/filestore/eli/etat/leg/w/jo/fr/xml/f.xml",
    )
    assert m.source_format is SourceFormat.XML and m.is_cc_by
    assert UserFormat.DOCX.source_format is None and UserFormat.PDFA.source_format is None
