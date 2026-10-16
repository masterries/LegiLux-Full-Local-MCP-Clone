"""Fixture catalogue (tests/fixtures/legilux/fixtures.json) and helpers shared by the parser tests.

Small fixtures are committed CC BY 4.0 files (≤ ~6 MB in total); large ones are pinned by sha256 and
fetched by ``scripts/fetch_fixtures.sh`` into ``data/fixtures-large/`` — their tests are marked ``large``
and skip when the file is absent or differs from the pin.
"""

from __future__ import annotations

import datetime as dt
import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from lgx_core.models import (
    CC_BY_4_0,
    DateWindow,
    FileRef,
    ManifestEntry,
    ParseResult,
    RawFile,
    SourceFormat,
    TextBasis,
    UserFormat,
    VersionKind,
    VersionRole,
    sha256_hex,
)
from lgx_ingest.parse import parse_bytes

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "legilux"
CATALOGUE = json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))
LARGE_DIR = REPO / CATALOGUE["large_dir"]
MAX_COMMITTED_BYTES = 6_500_000


def _entry(meta: dict[str, Any]) -> ManifestEntry:
    fmt = SourceFormat(meta["source_format"])
    tv = meta.get("text_valid") or {"lower": "2000-01-01", "upper": None}
    kind = VersionKind(meta["kind"]) if meta.get("kind") else None
    return ManifestEntry(
        eli_work=meta["eli_work"],
        eli_version=meta["eli_version"],
        kind=kind,
        role=VersionRole.CURRENT,
        doc_type=meta.get("doc_type") or "LOI",
        text_basis=TextBasis(meta.get("text_basis") or "consolidated"),
        file=FileRef(
            url=meta["url"],
            file_uri=meta["url"].replace("https://legilux.public.lu", "http://data.legilux.public.lu"),
            user_format=UserFormat.HTML if fmt is SourceFormat.HTML else UserFormat.XML,
            source_format=fmt,
            licence=meta["licence"],
        ),
        text_valid=DateWindow.model_validate(tv),
        reason="fixture",
        in_sample=True,
        sample_stage="m1",
    )


def _raw(meta: dict[str, Any], data: bytes) -> RawFile:
    return RawFile(
        url=meta["url"],
        sha256=sha256_hex(data),
        bytes=len(data),
        path=meta["path"],
        fetched_at=dt.datetime(2026, 10, 16, tzinfo=dt.UTC),
        eli_version=meta["eli_version"],
        source_format=SourceFormat(meta["source_format"]),
    )


def fixture_input(name: str) -> tuple[bytes, ManifestEntry, RawFile]:
    """(bytes, manifest entry, raw file) of a committed fixture."""
    meta = CATALOGUE["files"][name]
    data = (FIXTURES / meta["path"]).read_bytes()
    return data, _entry(meta), _raw(meta, data)


def large_input(name: str) -> tuple[bytes, ManifestEntry, RawFile]:
    """Same for a large fixture; skips the test when it is absent or differs from its sha256 pin."""
    meta = CATALOGUE["large"][name]
    path = LARGE_DIR / meta["path"]
    if not path.exists():
        pytest.skip(f"large fixture {name} absent: run scripts/fetch_fixtures.sh")
    data = path.read_bytes()
    if sha256_hex(data) != meta["sha256"]:
        pytest.skip(f"large fixture {name} differs from its sha256 pin")
    return data, _entry(meta), _raw(meta, data)


@cache
def parsed(name: str) -> ParseResult:
    return parse_bytes(*fixture_input(name))


@cache
def parsed_large(name: str) -> ParseResult:
    return parse_bytes(*large_input(name))


# ------------------------------------------------------------------------------------------- tests
def test_committed_fixtures_match_catalogue() -> None:
    total = 0
    for name, meta in CATALOGUE["files"].items():
        path = FIXTURES / meta["path"]
        data = path.read_bytes()
        assert sha256_hex(data) == meta["sha256"], name
        assert len(data) == meta["bytes"], name
        assert meta["licence"] == CC_BY_4_0, f"{name}: only CC BY 4.0 files may be committed"
        assert meta["url"].startswith("https://legilux.public.lu/filestore/eli/etat/leg/"), name
        total += len(data)
    assert total <= MAX_COMMITTED_BYTES
    on_disk = {p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*") if p.suffix in (".xml", ".html")}
    assert on_disk == {m["path"] for m in CATALOGUE["files"].values()}, "every committed file is catalogued"


def test_large_fixtures_are_pinned_cc_by() -> None:
    for name, meta in CATALOGUE["large"].items():
        assert len(meta["sha256"]) == 64 and meta["licence"] == CC_BY_4_0, name
        assert meta["url"].startswith("https://legilux.public.lu/filestore/eli/etat/leg/"), name


def test_licence_scl_xml_is_never_a_fixture() -> None:
    # loi/1978/07/14/n1/jo: XML is licenceSCL, only its HTML is CC BY (INTERFACES §8)
    paths = [m["path"] for m in CATALOGUE["files"].values()]
    assert not any("1978-07-14-n1-jo-fr-xml" in p for p in paths)
    assert any("1978-07-14-n1-jo-fr-html" in p for p in paths)
