"""Shared typed data model of the LegiLux local clone.

Every later stage (selection, fetch, parsers, chunker, loader, MCP server, UI, evaluation) speaks these
types. Field names are the column names of the PostgreSQL schema (``db/schema/20_tables.sql``, PLAN
Appendix B) with two systematic exceptions, both resolved by the loader:

* integer surrogate keys (``act_id``, ``version_id``, ``unit_id`` …) do not exist before the load, so
  build-side records reference each other by ELI (``eli_work``, ``eli_version``) and, for units, by
  ``(eli_version, ordinal)``. A ``*_eli`` / ``*_ordinal`` field replaces the matching ``*_id`` column;
* ``bytea`` hashes are lower-case hex strings here (``sha256``, ``text_sha256``, ``content_sha256``).

``daterange`` columns (``act_version.text_valid``) are :class:`DateWindow` (half-open ``[lower, upper)``).
The vocabularies below mirror the SQL enums and CHECK constraints; ``tests/db/test_schema.py`` asserts
the parity.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------------------------- constants
CC_BY_4_0 = "http://creativecommons.org/licenses/by/4.0/"
LICENCE_SCL = "http://data.legilux.public.lu/resource/authority/license/licenceSCL"
DEFAULT_RIGHTS = "Ministère d'État du Grand-Duché de Luxembourg – Service central de législation"
BREADCRUMB_SEP = " > "

_HEX64 = r"^[0-9a-f]{64}$"


def sha256_hex(data: bytes | str) -> str:
    """Lower-case hex SHA-256 of bytes, or of a str encoded as UTF-8."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------------------ vocabularies
class TextBasis(StrEnum):
    """The 5 mutually exclusive serving labels (PLAN §2.4); SQL enum ``lgx.text_basis``."""

    CONSOLIDATED = "consolidated"
    STUB_FALLBACK = "stub_fallback"
    ORIGINAL_NEVER_AMENDED = "original_never_amended"
    ORIGINAL_POSSIBLY_OUTDATED = "original_possibly_outdated"
    METADATA_ONLY = "metadata_only"


class VersionKind(StrEnum):
    """SQL enum ``lgx.version_kind``."""

    ORIGINAL = "original"
    CONSOLIDATED = "consolidated"
    CODE = "code"
    CODE_RECUEIL = "code_recueil"
    RECUEIL = "recueil"


class VersionRole(StrEnum):
    """SQL enum ``lgx.version_role``. Future versions are a role, not a label (PLAN §2.4)."""

    CURRENT = "current"
    FUTURE = "future"
    FALLBACK = "fallback"
    HISTORY_TEXT = "history_text"
    METADATA = "metadata"


class ActFlag(StrEnum):
    """Orthogonal act flags (PLAN §2.4); CHECK on ``lgx.act.flags``."""

    CONSOLIDATION_LAGS = "consolidation_lags"
    PDF_EXTRACTED = "pdf_extracted"
    SUPERSEDED_BY_CODE = "superseded_by_code"
    CONSOLIDATION_DISCONTINUED = "consolidation_discontinued"
    BASE_LEGALE_ABROGEE = "base_legale_abrogee"
    ACTE_TEMPORAIRE = "acte_temporaire"
    STUB_NO_TEXT = "stub_no_text"
    ENTRY_DATES_MULTIPLE = "entry_dates_multiple"
    IS_AMENDING_ACT = "is_amending_act"
    OLD_ACT = "old_act"
    LICENCE_SCL_SOURCE = "licence_scl_source"
    STATUS_FLAG_CONFLICT = "status_flag_conflict"


class MetadataReason(StrEnum):
    """Why an act has no stored text; CHECK on ``lgx.act.metadata_reason``."""

    REPEALED = "repealed"
    REPEALED_BASE = "repealed_base"
    TYPE = "type"
    LICENCE = "licence"
    PDF_ONLY = "pdf_only"
    TEMPORARY = "temporary"
    ROBOTS = "robots"
    EXCLUDED_BY_CONFIG = "excluded_by_config"
    NOT_YET_TEXT = "not_yet_text"


class SourceFormat(StrEnum):
    """Format of a stored text (CHECK on ``lgx.act_version.source_format``)."""

    XML = "xml"
    HTML = "html"
    PDF = "pdf"


class UserFormat(StrEnum):
    """``jolux:userFormat`` codes (last URI segment of ``…/resource/authority/user-format/<code>``)."""

    XML = "xml"
    XML_AKOMANTOSO = "xml-akomantoso"
    HTML = "html"
    PDF = "pdf"
    PDFA = "pdfa"
    DOCX = "docx"
    DOC = "doc"

    @property
    def source_format(self) -> SourceFormat | None:
        """Parser format for this manifestation, or None if it is never ingested (pdfa/docx/doc)."""
        return {
            UserFormat.XML: SourceFormat.XML,
            UserFormat.XML_AKOMANTOSO: SourceFormat.XML,
            UserFormat.HTML: SourceFormat.HTML,
            UserFormat.PDF: SourceFormat.PDF,
        }.get(self)


class UnitType(StrEnum):
    """CHECK on ``lgx.unit.unit_type``."""

    BOOK = "book"
    PART = "part"
    TITLE = "title"
    CHAPTER = "chapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    ARTICLE = "article"
    PARAGRAPH = "paragraph"
    ALINEA = "alinea"
    POINT = "point"
    ANNEX = "annex"
    PREAMBLE = "preamble"
    VISA = "visa"
    PAGE = "page"


class RelType(StrEnum):
    """CHECK on ``lgx.relation.rel_type``."""

    MODIFIES = "modifies"
    REPEALS = "repeals"
    BASED_ON = "based_on"
    CITES = "cites"
    TRANSPOSES = "transposes"
    RECTIFIES = "rectifies"
    REFERS_TO = "refers_to"


class RelationSource(StrEnum):
    """CHECK on ``lgx.relation.source``."""

    SPARQL = "sparql"
    AKN_REF = "akn-ref"
    AKN_TEXTUALMOD = "akn-textualmod"
    AKN_NOTE = "akn-note"


class ActStatus(StrEnum):
    """Status computed from dates (``lgx.act_status`` / ``lgx_core.rules.status``)."""

    IN_FORCE = "in-force"
    NO_LONGER_IN_FORCE = "no-longer-in-force"
    NOT_YET_IN_FORCE = "not-yet-in-force"


class RdfActStatus(StrEnum):
    """Raw ``jolux:inForceStatus`` codes of acts (stored raw in ``lgx.act.rdf_status``)."""

    IN_FORCE = "in-force"
    NO_LONGER_IN_FORCE = "no-longer-in-force"
    NO_LONGER_IN_FORCE_IMPLICIT = "no-longer-in-force-implicit"
    NOT_YET_IN_FORCE = "not-yet-in-force"


class RdfConsolidationStatus(StrEnum):
    """Raw ``jolux:inForceStatus`` codes of consolidations (informational only, PLAN §2.3)."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not-applicable"
    NOT_YET_APPLICABLE = "not-yet-applicable"


class Severity(StrEnum):
    INFO = "info"
    MEDIUM = "medium"
    HIGH = "high"


class WarningCode(StrEnum):
    """Mandatory warning codes (PLAN §8.5, Appendix D.4, ``config/warnings.yaml``)."""

    W_NOT_OFFICIAL = "W_NOT_OFFICIAL"
    W_SNAPSHOT = "W_SNAPSHOT"
    W_DATA_STALE = "W_DATA_STALE"
    W_CLOCK_SUSPECT = "W_CLOCK_SUSPECT"
    W_NOT_CONSOLIDATED = "W_NOT_CONSOLIDATED"
    W_STUB = "W_STUB"
    W_CONSO_LAGGING = "W_CONSO_LAGGING"
    W_CONSO_DISCONTINUED = "W_CONSO_DISCONTINUED"
    W_REPEALED = "W_REPEALED"
    W_NOT_YET_IN_FORCE = "W_NOT_YET_IN_FORCE"
    W_SUNSET = "W_SUNSET"
    W_FUTURE_VERSION = "W_FUTURE_VERSION"
    W_ARTICLE_REPEALED = "W_ARTICLE_REPEALED"
    W_BASE_LEGALE_ABROGEE = "W_BASE_LEGALE_ABROGEE"
    W_TEMPORARY_ACT = "W_TEMPORARY_ACT"
    W_PDF_EXTRACTED = "W_PDF_EXTRACTED"
    W_SUPERSEDED_BY_CODE = "W_SUPERSEDED_BY_CODE"
    W_PARTIAL_REPEAL_UNKNOWN = "W_PARTIAL_REPEAL_UNKNOWN"
    W_DERIVED_ELI = "W_DERIVED_ELI"
    W_DATA_QUALITY = "W_DATA_QUALITY"
    W_TRANSLATION = "W_TRANSLATION"
    W_OUT_OF_SCOPE = "W_OUT_OF_SCOPE"


class ToolErrorCode(StrEnum):
    """MCP tool error codes (Appendix D.0)."""

    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    BAD_INPUT = "BAD_INPUT"
    AS_OF_OUT_OF_RANGE = "AS_OF_OUT_OF_RANGE"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"


class AliasKind(StrEnum):
    """CHECK on ``lgx.act_alias.kind``."""

    SHORT_TITLE = "short_title"
    CITATION_TITLE = "citation_title"
    CODE_ABBREV = "code_abbrev"
    ACRONYM = "acronym"
    MEMORIAL = "memorial"
    ELI = "eli"
    CURATED = "curated"


class ChangeType(StrEnum):
    """CHECK on ``lgx.change_log.change_type``."""

    ADDED = "added"
    CHANGED = "changed"
    REPEALED = "repealed"
    REMOVED = "removed"
    VERSION_SWITCH = "version_switch"


class RecueilMethod(StrEnum):
    """CHECK on ``lgx.recueil_member.method``."""

    WID = "wid"
    HEADING = "heading"
    CURATED = "curated"


class EmbeddingProvider(StrEnum):
    """Embedding adapters (PLAN §6.5). ``none`` is the default and the first shipped mode."""

    NONE = "none"
    MISTRAL_API = "mistral-api"
    MISTRAL_LOCAL = "mistral-local"
    OPENAI_COMPATIBLE = "openai-compatible"
    TEI = "tei"


class Lang(StrEnum):
    FR = "fr"
    DE = "de"
    EN = "en"


# ------------------------------------------------------------------------------------------ base model
class LgxModel(BaseModel):
    """Strict base: unknown fields are errors, enum values serialise as plain strings."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False, validate_assignment=True)


def _check_hex(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    v = value.lower()
    if len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
        raise ValueError(f"{field} must be a 64-char hex sha256")
    return v


class DateWindow(LgxModel):
    """Half-open date window ``[lower, upper)``; ``None`` bounds are open (SQL ``daterange``)."""

    lower: dt.date | None = None
    upper: dt.date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lower is not None and self.upper is not None and self.upper <= self.lower:
            raise ValueError(f"empty window [{self.lower}, {self.upper})")
        return self

    def contains(self, day: dt.date) -> bool:
        return (self.lower is None or self.lower <= day) and (self.upper is None or day < self.upper)

    def overlaps(self, other: DateWindow) -> bool:
        lo = max((d for d in (self.lower, other.lower) if d is not None), default=None)
        hi = min((d for d in (self.upper, other.upper) if d is not None), default=None)
        return lo is None or hi is None or lo < hi


# ------------------------------------------------------------------------------------- metadata records
class Memorial(LgxModel):
    """A Mémorial issue, e.g. ``…/eli/etat/leg/memorial/1997/a36`` → « Mémorial A n° 36 de 1997 »."""

    memorial_uri: str
    series: str = "A"
    year: int
    number: str

    @property
    def label(self) -> str:
        return f"Mémorial {self.series} n° {self.number} de {self.year}"

    @classmethod
    def from_uri(cls, uri: str) -> Memorial:
        """Parse a Mémorial issue URI (the documented way to render Q08 ``?mem``, Appendix A Q08)."""
        tail = uri.rstrip("/").split("/memorial/", 1)
        if len(tail) != 2:
            raise ValueError(f"not a Mémorial issue URI: {uri}")
        parts = tail[1].split("/")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1]:
            raise ValueError(f"unexpected Mémorial issue URI shape: {uri}")
        issue = parts[1]
        series = issue[0].upper() if issue[0].isalpha() else "A"
        number = issue[1:] if issue[0].isalpha() else issue
        return cls(memorial_uri=uri, series=series, year=int(parts[0]), number=number)


class Manifestation(LgxModel):
    """One file of a work/version (Q03 / Q-MANIF row): format, licence and file identifier."""

    eli: str = Field(description="Work or version IRI the manifestation embodies (Q-MANIF ?w)")
    user_format: UserFormat
    licence: str | None = None
    file_uri: str = Field(description="jolux:isExemplifiedBy, http://data.legilux.public.lu/filestore/…")
    manif_modified: dt.datetime | None = None
    lang: Lang = Lang.FR

    @property
    def source_format(self) -> SourceFormat | None:
        return self.user_format.source_format

    @property
    def is_cc_by(self) -> bool:
        return self.licence == CC_BY_4_0


class Act(LgxModel):
    """One ``jolux:ComplexWork`` in the metadata scope; row of ``lgx.act`` (status is never stored)."""

    eli_work: str
    eli_jo: str | None = None
    doc_type: str
    date_document: dt.date | None = None
    publication_date: dt.date | None = None
    dei_min: dt.date | None = None
    dei_count: int = 0
    dnl: dt.date | None = None
    rdf_status: str | None = Field(default=None, description="raw status code, e.g. 'in-force'")
    memorial_ref: str | None = None
    historical_legal_id: str | None = None
    title: str | None = None
    title_short: str | None = None
    citation_title: str | None = None
    subjects: list[str] = Field(default_factory=list)
    institutions: list[str] = Field(default_factory=list)
    text_basis: TextBasis
    metadata_reason: MetadataReason | None = None
    flags: list[ActFlag] = Field(default_factory=list)
    basis_version_eli: str | None = Field(default=None, description="-> lgx.act.basis_version_id")
    superseded_by_eli: str | None = Field(default=None, description="-> lgx.act.superseded_by_act_id")
    rank_prior: float = 1.0
    graph_modified: dt.datetime | None = None

    @model_validator(mode="after")
    def _reason_matches_basis(self) -> Self:
        if self.text_basis is TextBasis.METADATA_ONLY and self.metadata_reason is None:
            raise ValueError(f"{self.eli_work}: metadata_only requires metadata_reason")
        if self.text_basis is not TextBasis.METADATA_ONLY and self.metadata_reason is not None:
            raise ValueError(f"{self.eli_work}: metadata_reason is only valid for metadata_only")
        return self


class ActVersion(LgxModel):
    """A consolidation version or a JO original; row of ``lgx.act_version`` (mirrors its CHECKs)."""

    eli_work: str = Field(description="-> lgx.act_version.act_id")
    eli_version: str
    kind: VersionKind
    role: VersionRole
    date_applicability: dt.date | None = None
    date_end_applicability: dt.date | None = None
    rdf_status: str | None = None
    is_stub: bool = False
    has_files: bool = False
    has_text: bool = False
    text_valid: DateWindow | None = None
    label: str | None = None
    source_format: SourceFormat | None = None
    source_url: str | None = None
    licence: str | None = None
    rights: str | None = DEFAULT_RIGHTS
    legal_value: str | None = None
    http_last_modified: dt.datetime | None = None
    manif_modified: dt.datetime | None = None
    graph_modified: dt.datetime | None = None
    sha256: str | None = None
    bytes: int | None = None
    fetched_at: dt.datetime | None = None
    parser_version: str | None = None
    created: dt.datetime | None = None

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        return _check_hex(v, "sha256")

    @model_validator(mode="after")
    def _db_checks(self) -> Self:
        if self.has_text != (self.text_valid is not None):
            raise ValueError(f"{self.eli_version}: has_text must equal (text_valid is not None)")
        if self.has_text and not (self.source_url and self.licence and self.sha256):
            raise ValueError(f"{self.eli_version}: stored text needs source_url, licence and sha256")
        if self.has_text and self.is_stub:
            raise ValueError(f"{self.eli_version}: a stub cannot have text")
        return self


class Relation(LgxModel):
    """Edge of ``lgx.relation``. SPARQL edges use ``/jo`` act URIs on both ends (Appendix A Q05)."""

    src_eli: str
    rel_type: RelType
    dst_eli: str
    source: RelationSource
    src_dei: dt.date | None = None
    dst_unit_hint: str | None = None
    src_version_eli: str | None = Field(default=None, description="with src_unit_ordinal -> src_unit_id")
    src_unit_ordinal: int | None = None


class VocabConcept(LgxModel):
    """Row of ``lgx.vocab`` (SKOS concepts, Q10)."""

    concept_uri: str
    scheme: str
    code: str | None = None
    label_fr: str | None = None
    label_de: str | None = None
    label_en: str | None = None


class ActAlias(LgxModel):
    """Row of ``lgx.act_alias``; ``alias_norm`` is lower-case, unaccented, whitespace-collapsed."""

    alias_norm: str
    eli_work: str = Field(description="-> lgx.act_alias.act_id")
    kind: AliasKind
    reviewed: bool = False


class RecueilMember(LgxModel):
    """Row of ``lgx.recueil_member`` (derived + lawyer-reviewed code-recueil membership)."""

    recueil_version_eli: str
    member_eli_work: str
    part_heading: str | None = None
    member_version_eli: str | None = None
    method: RecueilMethod
    reviewed_by: str | None = None


class DataQualityIssue(LgxModel):
    """Row of ``lgx.data_quality_issue`` (surfaced as ``W_DATA_QUALITY``)."""

    entity_eli: str
    code: str
    detail: str | None = None
    first_seen: dt.date


class ChangeLogEntry(LgxModel):
    """Row of ``lgx.change_log`` (hashes only, never old text)."""

    release_id: str
    eli_unit: str
    change_type: ChangeType
    old_sha256: str | None = None
    new_sha256: str | None = None
    effective_date: dt.date | None = None


class WarningDef(LgxModel):
    """Row of ``lgx.warning_def``; texts are ``str.format`` templates, variants replace the base text."""

    code: WarningCode
    severity: Severity
    text_fr: str
    text_de: str
    text_en: str
    variants: dict[str, dict[Lang, str]] = Field(default_factory=dict)


class LegalText(LgxModel):
    """Row of ``lgx.legal_text`` (attribution, disclaimer, CC BY 4.0 code, notice)."""

    code: str
    lang: Lang
    body: str


class RenderedWarning(LgxModel):
    """Warning object of MCP results (Appendix D.1)."""

    code: WarningCode
    severity: Severity
    message: str
    refs: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------------------------ pipeline records
class FileRef(LgxModel):
    """The one file chosen for a version by the licence-aware picker (PLAN §2.2 step 3, §3.4)."""

    url: str = Field(description="https://legilux.public.lu/filestore/eli/etat/leg/… (fetch URL)")
    file_uri: str = Field(description="original identifier http://data.legilux.public.lu/filestore/…")
    user_format: UserFormat
    source_format: SourceFormat
    licence: str
    manif_modified: dt.datetime | None = None
    lang: Lang = Lang.FR


class ManifestEntry(LgxModel):
    """One row of ``manifest.jsonl`` written by ``lgx select`` (PLAN §2.2 OUTPUT).

    There is one entry per stored or metadata-only *version* of an act in scope, plus one entry per
    metadata-only act without any version. ``file`` is set exactly when text must be fetched.
    """

    eli_work: str
    eli_version: str | None = None
    kind: VersionKind | None = None
    role: VersionRole
    doc_type: str
    text_basis: TextBasis = Field(description="label of the act at T")
    flags: list[ActFlag] = Field(default_factory=list)
    metadata_reason: MetadataReason | None = None
    file: FileRef | None = None
    text_valid: DateWindow | None = None
    date_applicability: dt.date | None = None
    date_end_applicability: dt.date | None = None
    amending_acts: list[str] = Field(default_factory=list, description="source ELIs, mandatory list")
    reason: str = Field(description="rule id, e.g. '2b:consolidated', '3:original_never_amended'")
    in_sample: bool = False
    sample_stage: str | None = Field(default=None, description="'m1' | 'm2' when in_sample")

    @model_validator(mode="after")
    def _text_needs_file(self) -> Self:
        if self.file is not None and self.text_valid is None:
            raise ValueError(f"{self.eli_version}: an entry with a file needs text_valid")
        return self


class RawFile(LgxModel):
    """One line of ``data/raw/index.jsonl``: a fetched file in the content-addressed cache."""

    url: str
    sha256: str
    bytes: int
    path: str = Field(description="relative to data/raw/, e.g. 'ab/ab12….xml'")
    content_type: str | None = None
    http_status: int = 200
    last_modified: dt.datetime | None = None
    etag: str | None = Field(default=None, description="ETag if the server sends one (the filestore does not)")
    fetched_at: dt.datetime
    eli_version: str | None = None
    source_format: SourceFormat | None = None

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str) -> str:
        checked = _check_hex(v, "sha256")
        assert checked is not None
        return checked


class FetchLogEntry(LgxModel):
    """One line of ``data/logs/fetch_log.jsonl`` (every attempt; mirrors ``build.fetch_log``)."""

    url: str
    fetched_at: dt.datetime
    http_status: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    last_modified: dt.datetime | None = None
    elapsed_ms: int | None = None
    error: str | None = None


class Unit(LgxModel):
    """A parsed structural unit (article, annex, paragraph-level, page …): one line of units JSONL.

    Columns of ``lgx.unit`` plus denormalised version/act provenance (``source_*``, ``licence``,
    ``text_basis``, ``flags``, ``doc_type``) so a units file is self-contained for chunking and audit.
    """

    # identity (-> lgx.unit.version_id / act_id / parent_id / member_act_id)
    eli_work: str
    eli_version: str
    ordinal: int = Field(ge=1, description="document order, unique within the version")
    parent_ordinal: int | None = None
    depth: int = Field(ge=0)
    unit_type: UnitType
    member_act_eli: str | None = None
    # lgx.unit columns
    akn_id: str | None = None
    anchor: str
    eli_unit: str | None = None
    eli_unit_version: str | None = None
    eli_unit_derived: bool = False
    unit_version_date: dt.date | None = None
    num_raw: str | None = None
    num_norm: str | None = None
    heading: str | None = None
    provenance: str | None = None
    breadcrumb: str | None = Field(default=None, description=f"ancestors joined with {BREADCRUMB_SEP!r}")
    text_plain: str | None = None
    text_md: str | None = None
    text_sha256: str | None = None
    is_repealed: bool = False
    repeal_note: str | None = None
    is_modifying: bool = False
    contains_quoted_amendment: bool = False
    amended_by: list[str] = Field(default_factory=list)
    index_terms: list[str] = Field(default_factory=list)
    page_from: int | None = None
    page_to: int | None = None
    from_recueil_wid: str | None = None
    # denormalised provenance (lgx.act_version / lgx.act columns)
    doc_type: str
    source_format: SourceFormat
    source_url: str
    source_sha256: str
    licence: str
    text_basis: TextBasis
    flags: list[ActFlag] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_text_hash(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("text_plain") is not None and not data.get("text_sha256"):
            data = {**data, "text_sha256": sha256_hex(data["text_plain"])}
        return data

    @field_validator("text_sha256", "source_sha256")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        return _check_hex(v, "sha256")

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if self.parent_ordinal is not None and self.parent_ordinal >= self.ordinal:
            raise ValueError(f"{self.eli_version}#{self.anchor}: parent must precede the unit")
        return self


class ParseResult(LgxModel):
    """Parser output for one file: ``data/parsed/<sha256>.<parser_version>.json``."""

    eli_work: str
    eli_version: str
    parser: str = Field(description="'akn' | 'html' | 'pdf' | 'recueil'")
    parser_version: str
    source_sha256: str
    source_format: SourceFormat
    units: list[Unit]
    relations: list[Relation] = Field(default_factory=list)
    issues: list[DataQualityIssue] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)


class Chunk(LgxModel):
    """Retrieval chunk; one line of chunks JSONL, row of ``lgx.chunk`` (no embedding by default)."""

    eli_work: str
    eli_version: str
    unit_ordinal: int = Field(description="-> lgx.chunk.unit_id")
    part_no: int = 1
    part_count: int = 1
    header: str
    body: str
    n_tokens: int | None = None
    doc_type: str
    text_basis: TextBasis
    valid_from: dt.date
    valid_to: dt.date | None = None
    searchable: bool = True
    rank_prior: float = 1.0
    content_sha256: str

    @field_validator("content_sha256")
    @classmethod
    def _hex(cls, v: str) -> str:
        checked = _check_hex(v, "content_sha256")
        assert checked is not None
        return checked

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        if not 1 <= self.part_no <= self.part_count:
            raise ValueError("part_no must be within 1..part_count")
        return self
