from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from lgx_core.config import DEFAULT_CONFIG_DIR, EmbeddingConfig, ProjectConfig
from lgx_core.eli import assert_fetchable
from lgx_core.embed import EmbeddingDisabledError, get_embedder
from lgx_core.io import append_jsonl, atomic_write_text, read_jsonl, write_jsonl
from lgx_core.models import FetchLogEntry, ManifestEntry, TextBasis

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "manifest" / "bootstrap_sample.jsonl"


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    entries = list(read_jsonl(FIXTURE, ManifestEntry))
    out = tmp_path / "m" / "manifest.jsonl"
    assert write_jsonl(out, entries) == len(entries)
    assert list(read_jsonl(out, ManifestEntry)) == entries
    log = tmp_path / "logs" / "fetch_log.jsonl"
    append_jsonl(log, FetchLogEntry(url="u", fetched_at=dt.datetime(2026, 10, 16, tzinfo=dt.UTC), http_status=304))
    append_jsonl(log, FetchLogEntry(url="v", fetched_at=dt.datetime(2026, 10, 16, tzinfo=dt.UTC), http_status=200))
    assert [e.url for e in read_jsonl(log, FetchLogEntry)] == ["u", "v"]


def test_atomic_write_mode(tmp_path: Path) -> None:
    """Review 2026-10-17: mkstemp's 0600 made the bundle sidecars unreadable for the capability-less db-load."""
    atomic_write_text(tmp_path / "private.json", "{}")
    atomic_write_text(tmp_path / "bundle.sha256", "x  y\n", mode=0o644)
    assert (tmp_path / "private.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "bundle.sha256").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "bundle.sha256").read_text() == "x  y\n" and not list(tmp_path.glob(".*.tmp"))


def test_read_jsonl_reports_line(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"url": "u"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad.jsonl:1"):
        list(read_jsonl(bad, FetchLogEntry))


def test_bootstrap_manifest_is_valid_and_in_sample() -> None:
    entries = list(read_jsonl(FIXTURE, ManifestEntry))
    sample = ProjectConfig.load(DEFAULT_CONFIG_DIR).sample.by_work()
    assert len(entries) >= 10
    for e in entries:
        assert e.file is not None and e.text_valid is not None
        assert assert_fetchable(e.file.url) == e.file.url
        assert e.eli_work in sample, e.eli_work
        expected = sample[e.eli_work]
        assert e.text_basis == expected.expect.text_basis
        assert e.file.source_format.value == expected.expect.format
        if e.text_basis is TextBasis.ORIGINAL_POSSIBLY_OUTDATED:
            assert e.amending_acts, "amending-act list is mandatory"


def test_embedding_provider_none_computes_nothing() -> None:
    emb = get_embedder(EmbeddingConfig())
    assert emb.vector_status() == "disabled:provider_none"
    with pytest.raises(EmbeddingDisabledError):
        emb.embed(["texte"], "doc")
    with pytest.raises(NotImplementedError):
        get_embedder(EmbeddingConfig(provider="mistral-api"))
