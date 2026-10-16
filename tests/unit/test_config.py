from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from lgx_core.config import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_USER_AGENT,
    DataLayout,
    EmbeddingConfig,
    ProjectConfig,
    ScopeConfig,
    Settings,
    load_yaml,
)
from lgx_core.models import EmbeddingProvider, TextBasis


@pytest.fixture(scope="module")
def cfg() -> ProjectConfig:
    return ProjectConfig.load(DEFAULT_CONFIG_DIR)


def test_project_config_loads(cfg: ProjectConfig) -> None:
    assert cfg.scope.history.mode == "none"
    assert cfg.scope.sync_date == "auto"
    assert cfg.scope.licence_policy == "cc_by_only"
    assert len(cfg.scope.text_types) == 18
    assert cfg.embedding.provider is EmbeddingProvider.NONE and not cfg.embedding.enabled
    assert cfg.digest() == ProjectConfig.load(DEFAULT_CONFIG_DIR).digest()
    assert cfg.digest().startswith("sha256:")


def test_sync_date_resolution() -> None:
    scope = ScopeConfig.model_validate(load_yaml(DEFAULT_CONFIG_DIR / "scope.yaml"))
    assert scope.resolve_sync_date(today=dt.date(2026, 10, 16)) == dt.date(2026, 10, 16)
    fixed = scope.model_copy(update={"sync_date": dt.date(2026, 10, 16)})
    assert fixed.resolve_sync_date(today=dt.date(2030, 1, 1)) == dt.date(2026, 10, 16)


def test_adm_branch_can_never_be_in_scope() -> None:
    data = load_yaml(DEFAULT_CONFIG_DIR / "scope.yaml")
    data["branches"] = ["etat/leg", "etat/adm"]
    with pytest.raises(ValidationError):
        ScopeConfig.model_validate(data)


def test_scope_rules_match_known_titles() -> None:
    import re

    scope = ScopeConfig.model_validate(load_yaml(DEFAULT_CONFIG_DIR / "scope.yaml"))
    natur = re.compile(scope.exclusions.naturalisation.title_regex)
    assert natur.search("Lois du 15 janvier 2007 conférant la naturalisation.")
    assert natur.search("Loi du 12 mai 1905, accordant la naturalisation à M. X")
    assert not natur.search("Loi du 23 octobre 2008 sur la nationalité luxembourgeoise")
    traffic = re.compile(scope.temporary_traffic.title_regex)
    assert traffic.search(
        "Règlement grand-ducal du 6 juillet 2026 concernant la réglementation temporaire de la circulation"
    )


def test_embedding_yaml_retry_key_is_not_yaml_boolean() -> None:
    # PLAN §6.5 writes `retry: {on: [...]}`; YAML 1.1 parses `on` as True. The config uses on_status.
    assert list(yaml.safe_load("{on: [1]}")) == [True]
    cfg = EmbeddingConfig.model_validate(load_yaml(DEFAULT_CONFIG_DIR / "embedding.yaml"))
    assert cfg.retry.on_status == [429, 500, 502, 503, 504]


def test_settings_from_env(tmp_path: Path) -> None:
    s = Settings.from_env({"LGX_DATA_DIR": str(tmp_path), "LGX_RATE_LIMIT_S": "2"})
    assert s.data_dir == tmp_path and s.rate_limit_s == 2.0
    assert Settings.from_env({}).user_agent == DEFAULT_USER_AGENT
    assert DEFAULT_USER_AGENT == (
        "LegiLux-Local-Clone/0.1 (+https://github.com/masterries/LegiLux-Full-Local-MCP-Clone)"
    )
    with pytest.raises(ValidationError):  # never faster than 1 request per second
        Settings.from_env({"LGX_RATE_LIMIT_S": "0.5"})


def test_data_layout(tmp_path: Path) -> None:
    lay = DataLayout(tmp_path, dt.date(2026, 10, 16))
    v = "http://data.legilux.public.lu/eli/etat/leg/code/travail/20260726"
    assert lay.meta_table("acts") == tmp_path / "meta/2026-10-16/acts.parquet"
    assert lay.manifest_path == tmp_path / "manifest/2026-10-16/manifest.jsonl"
    assert lay.raw_path("ab" + "0" * 62, ".xml") == tmp_path / "raw/ab" / ("ab" + "0" * 62 + ".xml")
    assert lay.units_path(v).name == "eli-etat-leg-code-travail-20260726.jsonl"
    assert lay.parsed_path("f" * 64, "akn-1").name == "f" * 64 + ".akn-1.json"
    with pytest.raises(KeyError):
        lay.meta_table("nope")
    assert set(lay.as_dict()) >= {"meta", "manifest", "raw", "units", "chunks"}


def test_sample_covers_required_strata(cfg: ProjectConfig) -> None:
    by_work = {w.work: w for w in cfg.sample.works}
    assert 30 <= len(by_work) <= 60
    for work in (
        "etat/leg/constitution/1868/10/17/n1",  # Constitution
        "etat/leg/loi/2006/07/31/n2",  # Code du travail
        "etat/leg/loi/1804/03/21/n1",  # Code civil
        "etat/leg/loi/2018/08/01/a686",  # GDPR / CNPD law
        "etat/leg/loi/2013/07/12/n1",  # AIFM law
    ):
        assert work in by_work
    works = cfg.sample.works
    assert any(w.group == "jo" and w.expect.format == "html" for w in works)  # CC-BY HTML path
    assert any(w.expect.text_basis is TextBasis.ORIGINAL_POSSIBLY_OUTDATED for w in works)
    assert any(w.expect.text_basis is TextBasis.STUB_FALLBACK for w in works)
    assert any("stub_no_text" in w.expect.flags for w in works)
    assert any(w.expect.metadata_reason == "repealed" for w in works)
    assert any(w.group == "code_recueil" and w.stage == "m2" and w.expect.format == "pdf" for w in works)
    for w in works:
        if w.version:
            assert w.eli_version and w.eli_version.startswith("http://data.legilux.public.lu/eli/etat/leg/")
        assert "naturalisation" not in w.why.lower()
