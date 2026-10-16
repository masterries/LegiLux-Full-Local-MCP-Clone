"""Configuration: environment settings, the YAML files in ``config/`` and the build-side data layout.

* :class:`Settings` — process environment (``LGX_*``), never secrets in files.
* :class:`ProjectConfig` — all YAML files of ``config/``, validated; :meth:`ProjectConfig.digest` feeds
  ``release.json.inputs_digest``.
* :class:`DataLayout` — where every stage reads and writes under ``data/`` (contract in
  ``docs/dev/INTERFACES.md`` §2).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lgx_core import __version__
from lgx_core.eli import eli_slug, expand
from lgx_core.models import (
    ActFlag,
    AliasKind,
    EmbeddingProvider,
    Lang,
    MetadataReason,
    Severity,
    TextBasis,
    WarningCode,
    WarningDef,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
LUXEMBOURG_TZ = ZoneInfo("Europe/Luxembourg")
DEFAULT_USER_AGENT = (
    f"LegiLux-Local-Clone/{__version__.rsplit('.', 1)[0]} (+https://github.com/masterries/LegiLux-Full-Local-MCP-Clone)"
)


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def today_luxembourg() -> dt.date:
    return dt.datetime.now(LUXEMBOURG_TZ).date()


# ----------------------------------------------------------------------------------------- environment
class Settings(_Cfg):
    """Process settings from ``LGX_*`` environment variables (see ``.env.example``)."""

    data_dir: Path = DEFAULT_DATA_DIR
    config_dir: Path = DEFAULT_CONFIG_DIR
    sparql_url: str = "https://data.legilux.public.lu/sparqlendpoint"
    filestore_base: str = "https://legilux.public.lu/filestore/"
    user_agent: str = DEFAULT_USER_AGENT
    rate_limit_s: float = Field(default=1.2, ge=1.0, description="min spacing between requests (<=1 req/s)")
    http_timeout_s: float = 60.0
    pg_dsn: str | None = None
    embed_provider: EmbeddingProvider = EmbeddingProvider.NONE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        mapping = {
            "data_dir": "LGX_DATA_DIR",
            "config_dir": "LGX_CONFIG_DIR",
            "sparql_url": "LGX_SPARQL_URL",
            "filestore_base": "LGX_FILESTORE_BASE",
            "user_agent": "LGX_USER_AGENT",
            "rate_limit_s": "LGX_RATE_LIMIT_S",
            "http_timeout_s": "LGX_HTTP_TIMEOUT_S",
            "pg_dsn": "LGX_PG_DSN",
            "embed_provider": "LGX_EMBED_PROVIDER",
        }
        values = {field: env[var] for field, var in mapping.items() if env.get(var)}
        return cls.model_validate(values)


# --------------------------------------------------------------------------------------------- scope
class RegexRule(_Cfg):
    doc_types: list[str]
    title_regex: str
    requires_no_dnl: bool = False

    @field_validator("title_regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class Exclusions(_Cfg):
    branches: list[str] = Field(default_factory=lambda: ["etat/adm"])
    doc_types: list[str] = Field(default_factory=lambda: ["RC"])
    naturalisation: RegexRule
    note: str | None = None


class HistoryConfig(_Cfg):
    mode: Literal["none", "full_text_last_n", "since_date"] = "none"
    keep_versions: int = 0
    since: dt.date | None = None
    restrict_to: list[str] = Field(default_factory=lambda: ["CODE", "Constitution"])
    embed: bool = False


class FetchPolicy(_Cfg):
    """Politeness and safety limits (PLAN §3.3)."""

    min_spacing_s: float = Field(default=1.2, ge=1.0)
    max_retries: int = 5
    backoff_s: list[float] = Field(default_factory=lambda: [1, 2, 4, 8, 16, 32, 60])
    retry_on: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    circuit_breaker_consecutive_errors: int = 20
    circuit_breaker_403_per_minute: int = 5
    max_file_bytes: int = 64 * 1024 * 1024
    large_file_allowlist: list[str] = Field(default_factory=list)
    max_requests_per_hour: int = 2000
    max_requests_per_day: int = 30000
    nightly_window: str | None = "01:00-05:00 Europe/Luxembourg"


class ScopeConfig(_Cfg):
    """``config/scope.yaml`` (PLAN §2.9); hashed into ``release.json``."""

    version: int = 1
    sync_date: Literal["auto"] | dt.date = "auto"
    timezone: str = "Europe/Luxembourg"
    branches: list[str] = Field(default_factory=lambda: ["etat/leg"])
    metadata_scope: Literal["memorial_a_core", "memorial_a_all"] = "memorial_a_core"
    text_types: list[str]
    consolidation_types: list[str]
    exclusions: Exclusions
    temporary_traffic: RegexRule
    future_versions: Literal["none", "next_one", "all"] = "next_one"
    include_temporary_traffic: bool = False
    include_env_annexes_text: bool = False
    amending_acts_text: Literal["keep", "metadata_only"] = "keep"
    licence_policy: Literal["cc_by_only", "allow_licence_scl"] = "cc_by_only"
    stub_fallback_max_age_years: int = 5
    discontinued_policy: Literal["original", "last_consolidation"] = "original"
    pdf_codes: Literal["pages", "articles"] = "articles"
    recueil_dedup: bool = True
    horizon_days: int = 365
    old_act_before_year: int = 1900
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    denylist_files: list[str] = Field(default_factory=list)
    recueil_members_file: str | None = None
    fetch: FetchPolicy = Field(default_factory=FetchPolicy)

    @field_validator("branches")
    @classmethod
    def _leg_only(cls, v: list[str]) -> list[str]:
        if any(b.startswith("etat/adm") for b in v):
            raise ValueError("etat/adm is robots-disallowed and can never be in scope")
        return v

    def resolve_sync_date(self, today: dt.date | None = None) -> dt.date:
        """T: the configured date, or today in Europe/Luxembourg for ``auto``."""
        if isinstance(self.sync_date, dt.date):
            return self.sync_date
        return today or dt.datetime.now(ZoneInfo(self.timezone)).date()


# -------------------------------------------------------------------------------------------- sample
SampleGroup = Literal[
    "core", "code_recueil", "consolidation", "jo", "parser_fixture", "selection_fixture", "metadata_only"
]


class SampleExpect(_Cfg):
    """Expected selection outcome on the frozen snapshot (``SampleConfig.snapshot_date``)."""

    text_basis: TextBasis
    format: Literal["xml", "html", "pdf", "none"]
    flags: list[ActFlag] = Field(default_factory=list)
    metadata_reason: MetadataReason | None = None
    role: Literal["current", "future", "fallback"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.text_basis is TextBasis.METADATA_ONLY) != (self.format == "none"):
            raise ValueError("format 'none' iff text_basis metadata_only")
        return self


class SampleWork(_Cfg):
    work: str = Field(description="relative ELI of the jolux:ComplexWork (isMemberOf target)")
    version: str | None = Field(default=None, description="relative ELI of the expected served version")
    extra_versions: list[str] = Field(default_factory=list, description="e.g. a stored future version")
    group: SampleGroup
    stage: Literal["m1", "m2"] = "m1"
    expect: SampleExpect
    why: str

    @property
    def eli_work(self) -> str:
        return expand(self.work)

    @property
    def eli_version(self) -> str | None:
        return expand(self.version) if self.version else None


class SampleConfig(_Cfg):
    """``config/sample.yaml``: the stratified dev/CI sample (PLAN §6.2, D13)."""

    version: int = 1
    snapshot_date: dt.date
    description: str = ""
    works: list[SampleWork]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        seen: set[str] = set()
        for w in self.works:
            if w.work in seen:
                raise ValueError(f"duplicate sample work {w.work}")
            seen.add(w.work)
        return self

    def by_work(self) -> dict[str, SampleWork]:
        return {w.eli_work: w for w in self.works}


# ----------------------------------------------------------------------------------------- embedding
class RetryPolicy(_Cfg):
    on_status: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    backoff_s: list[float] = Field(default_factory=lambda: [2, 4, 8, 16, 32])
    honour_retry_after: bool = True


class EmbeddingConfig(_Cfg):
    """``config/embedding.yaml`` (PLAN §6.5). ``provider: none`` is the default: no vectors."""

    provider: EmbeddingProvider = EmbeddingProvider.NONE
    model_id: str = "mistral-embed-2312"
    model_version: str = "v23.12"
    model_aliases: list[str] = Field(default_factory=list)
    dim: int = 1024
    normalized: bool = True
    batch_size: int = 64
    max_tokens_per_request: int = 16000
    max_input_tokens: int = 8000
    rate_limit_rps: float = 1
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    parity_threshold: float = Field(default=0.999, gt=0, le=1)
    cost_budget_usd: float = 25
    base_url: str | None = None
    api_key_file: str | None = None

    @property
    def enabled(self) -> bool:
        return self.provider is not EmbeddingProvider.NONE


# ------------------------------------------------------------------------------------------ warnings
class WarningEntry(_Cfg):
    severity: Severity
    params: list[str] = Field(default_factory=list)
    fr: str
    de: str
    en: str
    variants: dict[str, dict[Lang, str]] = Field(default_factory=dict)
    note: str | None = None

    def to_def(self, code: WarningCode) -> WarningDef:
        return WarningDef(
            code=code,
            severity=self.severity,
            text_fr=self.fr,
            text_de=self.de,
            text_en=self.en,
            variants=self.variants,
        )


class WarningsConfig(_Cfg):
    """``config/warnings.yaml`` → ``lgx.warning_def`` (Appendix D.4)."""

    version: int = 1
    warnings: dict[WarningCode, WarningEntry]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        missing = set(WarningCode) - set(self.warnings)
        if missing:
            raise ValueError(f"warnings.yaml lacks {sorted(missing)}")
        for code, entry in self.warnings.items():
            for lang in (Lang.FR, Lang.DE, Lang.EN):
                used = set(re.findall(r"{(\w+)}", getattr(entry, lang.value)))
                for variant in entry.variants.values():
                    used |= set(re.findall(r"{(\w+)}", variant.get(lang, "")))
                if not used <= set(entry.params):
                    raise ValueError(f"{code}/{lang}: placeholders {used - set(entry.params)} not in params")
        return self

    def defs(self) -> dict[WarningCode, WarningDef]:
        return {code: entry.to_def(code) for code, entry in self.warnings.items()}


class LegalTextsConfig(_Cfg):
    """``config/legal_texts.yaml`` → ``lgx.legal_text`` (Appendix D.5)."""

    version: int = 1
    texts: dict[str, dict[Lang, str]]

    @model_validator(mode="after")
    def _required(self) -> Self:
        for code in ("attribution", "disclaimer", "cc_by_4_0", "notice"):
            if Lang.FR not in self.texts.get(code, {}):
                raise ValueError(f"legal_texts.yaml lacks {code}/fr")
        return self


# ------------------------------------------------------------------------- aliases, denylist, recueils
class AliasEntry(_Cfg):
    alias: str
    target: str | None = Field(description="relative work ELI, or null for out-of-scope references")
    kind: AliasKind
    reviewed: bool = False
    out_of_scope: str | None = None
    related: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _target_or_oos(self) -> Self:
        if (self.target is None) == (self.out_of_scope is None):
            raise ValueError(f"alias {self.alias!r}: exactly one of target / out_of_scope")
        return self


class AliasesConfig(_Cfg):
    """``config/aliases.yaml``: lawyer-curated abbreviations and acronyms (PLAN §8.3)."""

    version: int = 1
    aliases: list[AliasEntry]


class DenylistEntry(_Cfg):
    eli: str = Field(description="relative or absolute ELI prefix (work level)")
    reason: str
    added: dt.date
    source: Literal["manual", "robots", "gdpr_rule", "lawyer"] = "manual"


class DenylistConfig(_Cfg):
    """Manual takedowns (``config/denylist.yaml``) or the lawyer-reviewed GDPR list (never committed)."""

    version: int = 1
    entries: list[DenylistEntry] = Field(default_factory=list)


class RecueilMemberEntry(_Cfg):
    recueil: str
    member: str
    part_heading: str | None = None
    method: Literal["wid", "heading", "curated"] = "curated"
    reviewed_by: str | None = None


class RecueilMembersConfig(_Cfg):
    """``config/recueil_members.yaml``: reviewed code-recueil membership/routing (PLAN §2.6)."""

    version: int = 1
    members: list[RecueilMemberEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------- aggregate
class ProjectConfig(_Cfg):
    """All YAML configuration, loaded and validated together."""

    config_dir: Path
    scope: ScopeConfig
    sample: SampleConfig
    embedding: EmbeddingConfig
    warnings: WarningsConfig
    legal_texts: LegalTextsConfig
    aliases: AliasesConfig
    denylist: DenylistConfig
    recueil_members: RecueilMembersConfig

    @classmethod
    def load(cls, config_dir: Path = DEFAULT_CONFIG_DIR) -> ProjectConfig:
        config_dir = config_dir.resolve()

        def y(name: str) -> Any:
            return load_yaml(config_dir / name)

        scope = ScopeConfig.model_validate(y("scope.yaml"))
        entries: list[DenylistEntry] = []
        for name in scope.denylist_files:
            path = _resolve_config_path(config_dir, name)
            if path.exists():  # denylist_gdpr.yaml is lawyer-made and never committed
                entries.extend(DenylistConfig.model_validate(load_yaml(path) or {}).entries)
        members_path = (
            _resolve_config_path(config_dir, scope.recueil_members_file) if scope.recueil_members_file else None
        )
        members = (
            RecueilMembersConfig.model_validate(load_yaml(members_path) or {})
            if members_path and members_path.exists()
            else RecueilMembersConfig()
        )
        return cls(
            config_dir=config_dir,
            scope=scope,
            sample=SampleConfig.model_validate(y("sample.yaml")),
            embedding=EmbeddingConfig.model_validate(y("embedding.yaml")),
            warnings=WarningsConfig.model_validate(y("warnings.yaml")),
            legal_texts=LegalTextsConfig.model_validate(y("legal_texts.yaml")),
            aliases=AliasesConfig.model_validate(y("aliases.yaml")),
            denylist=DenylistConfig(entries=entries),
            recueil_members=members,
        )

    def digest(self) -> str:
        """sha256 over the canonical JSON of the scope-relevant configuration (no secrets in it)."""
        payload = self.model_dump(mode="json", exclude={"config_dir", "denylist"})
        payload["denylist_count"] = len(self.denylist.entries)  # never hash/ship personal-data ELIs
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _resolve_config_path(config_dir: Path, name: str) -> Path:
    """Paths in scope.yaml are repo-relative (``config/x.yaml``) or relative to the config dir."""
    p = Path(name)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "config":
        return config_dir.joinpath(*p.parts[1:])
    return config_dir / p


# --------------------------------------------------------------------------------------- data layout
class DataLayout:
    """Build-side directory contract under ``data/`` (gitignored, never shipped).

    ``T`` partitions every snapshot-dependent output; the raw cache and the parsed cache are
    content-addressed and shared across runs.
    """

    META_TABLES = (
        "acts",
        "consolidations",
        "conso_formats",
        "conso_base_acts",
        "rel_modifies_repeals",
        "rel_other",
        "titles",
        "memorial_refs",
        "vocab",
        "manifestations",
        "act_formats",  # Q19 (agent A, M1): formats/licences of JO acts for the licence-aware pick
        "serving_plan",  # written by `lgx select` (one row per act of the metadata scope)
    )

    def __init__(self, root: Path, sync_date: dt.date) -> None:
        self.root = root
        self.sync_date = sync_date
        self.t = sync_date.isoformat()

    # S0 robots
    @property
    def robots_dir(self) -> Path:
        return self.root / "robots" / self.t

    # S1 discover: SPARQL snapshot tables (parquet) + _snapshot.json (queries, hashes, counts, timings)
    @property
    def meta_dir(self) -> Path:
        return self.root / "meta" / self.t

    def meta_table(self, name: str) -> Path:
        if name not in self.META_TABLES:
            raise KeyError(f"unknown snapshot table {name!r}")
        return self.meta_dir / f"{name}.parquet"

    @property
    def snapshot_info(self) -> Path:
        return self.meta_dir / "_snapshot.json"

    # S2 select
    @property
    def manifest_dir(self) -> Path:
        return self.root / "manifest" / self.t

    @property
    def manifest_path(self) -> Path:
        return self.manifest_dir / "manifest.jsonl"

    @property
    def selection_report_path(self) -> Path:
        return self.manifest_dir / "selection_report.json"

    @property
    def acts_path(self) -> Path:
        """``Act`` rows of the metadata scope (JSONL), written by ``lgx select``."""
        return self.manifest_dir / "acts.jsonl"

    @property
    def versions_path(self) -> Path:
        """``ActVersion`` rows (all consolidation versions + JO originals), written by ``lgx select``."""
        return self.manifest_dir / "versions.jsonl"

    @property
    def relations_path(self) -> Path:
        """SPARQL ``Relation`` rows in scope, written by ``lgx select``."""
        return self.manifest_dir / "relations.jsonl"

    # S3 fetch: content-addressed cache shared by all T
    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    def raw_path(self, sha256: str, suffix: str = "") -> Path:
        return self.raw_dir / sha256[:2] / f"{sha256}{suffix}"

    @property
    def raw_index_path(self) -> Path:
        return self.raw_dir / "index.jsonl"

    @property
    def fetch_log_path(self) -> Path:
        return self.root / "logs" / "fetch_log.jsonl"

    # S4 parse: cache keyed by (file sha256, parser version)
    @property
    def parsed_dir(self) -> Path:
        return self.root / "parsed"

    def parsed_path(self, sha256: str, parser_version: str) -> Path:
        return self.parsed_dir / f"{sha256}.{parser_version}.json"

    # S5 structure/normalise output: one JSONL of Unit per stored version
    @property
    def units_dir(self) -> Path:
        return self.root / "units" / self.t

    def units_path(self, eli_version: str) -> Path:
        return self.units_dir / f"{eli_slug(eli_version)}.jsonl"

    def unit_relations_path(self, eli_version: str) -> Path:
        """AKN-derived ``Relation`` rows of one version (refers_to, textualMod, notes)."""
        return self.units_dir / f"{eli_slug(eli_version)}.relations.jsonl"

    # S6 chunk
    @property
    def chunks_dir(self) -> Path:
        return self.root / "chunks" / self.t

    def chunks_path(self, eli_version: str) -> Path:
        return self.chunks_dir / f"{eli_slug(eli_version)}.jsonl"

    # S9 gates / reports
    @property
    def reports_dir(self) -> Path:
        return self.root / "reports" / self.t

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "robots": str(self.robots_dir),
            "meta": str(self.meta_dir),
            "manifest": str(self.manifest_path),
            "selection_report": str(self.selection_report_path),
            "acts": str(self.acts_path),
            "versions": str(self.versions_path),
            "relations": str(self.relations_path),
            "raw": str(self.raw_dir),
            "raw_index": str(self.raw_index_path),
            "fetch_log": str(self.fetch_log_path),
            "parsed": str(self.parsed_dir),
            "units": str(self.units_dir),
            "chunks": str(self.chunks_dir),
            "reports": str(self.reports_dir),
        }
