"""S6 chunk: units → retrieval chunks, one chunk per article (PLAN §6.4).

* Header ``"{titre court} — {version label} > Livre … > Titre … > Art. X. heading"`` + `` | Index : …``
  (index terms), repeated on every part of a split article.
* Articles over ≈ 1,500 tokens (``lgx_core.textsize``: ``ceil(chars / 3.5)``) are split at paragraph
  boundaries (``(1)``, ``(2)`` … lines; then lines; then sentences), ``part_no/part_count`` set.
* Articles are never merged. No chunk for repealed stubs, preamble/visas, hierarchy nodes, modifying
  articles (``is_modifying``) or table/parcel annexes (digit ratio > 0.3 or > 50 % table lines).
* ``searchable = false`` for ``superseded_by_code`` / ``acte_temporaire``; ``rank_prior = 0.8`` for the
  autonomous articles of pure amending acts (PLAN §2.7).
* ``valid_from/valid_to`` = the version's ``text_valid``; ``content_sha256 = sha256(header + body)``.
* Output order is deterministic; a chunk is identified by ``(eli_version, unit_ordinal, part_no)``
  (:func:`chunk_id`).
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from lgx_core.eli import eli_slug, expand
from lgx_core.io import read_jsonl, write_jsonl
from lgx_core.models import (
    BREADCRUMB_SEP,
    Act,
    ActFlag,
    ActVersion,
    Chunk,
    DateWindow,
    ManifestEntry,
    Unit,
    UnitType,
    VersionKind,
    sha256_hex,
)
from lgx_core.textsize import estimate_tokens
from lgx_ingest.context import StageContext, StageResult

CHUNKER_VERSION = "chunk-1"
MAX_TOKENS = 1500
AMENDING_RANK_PRIOR = 0.8
CHUNKABLE = frozenset({UnitType.ARTICLE, UnitType.ANNEX, UnitType.PARAGRAPH, UnitType.PAGE})
OPEN_START = dt.date(1, 1, 1)  # a text window without a lower bound (never produced by selection)
_PARA_START = re.compile(r"^\(\d+[a-z]*(?:-\d+)?\)\s")  # « (1) », « (2bis) », « (2-1) »
_SENTENCE = re.compile(r"(?<=[.;:])\s+(?=[A-ZÀ-Ý«(])")


def chunk_id(chunk: Chunk) -> str:
    """Deterministic identifier ``<slug>:<unit ordinal>:<part>`` (the DB assigns its own surrogate key)."""
    return f"{eli_slug(chunk.eli_version)}:{chunk.unit_ordinal}:{chunk.part_no}"


def content_sha256(header: str, body: str) -> str:
    return sha256_hex(header + body)


def is_table_annex(unit: Unit) -> bool:
    """Table or parcel annex (digit ratio > 0.3, or > 50 % of the Markdown lines are table rows)."""
    if unit.unit_type is not UnitType.ANNEX:
        return False
    text = unit.text_plain or ""
    chars = [c for c in text if not c.isspace()]
    if chars and sum(c.isdigit() for c in chars) / len(chars) > 0.3:
        return True
    md_lines = [x for x in (unit.text_md or "").split("\n") if x.strip()]
    return bool(md_lines) and sum(x.lstrip().startswith("|") for x in md_lines) / len(md_lines) > 0.5


def skip_reason(unit: Unit) -> str | None:
    """Why a unit gets no chunk (None = chunk it)."""
    if unit.unit_type not in CHUNKABLE:
        return unit.unit_type.value
    if unit.is_repealed:
        return "repealed"
    if unit.is_modifying:
        return "modifying"
    if not (unit.text_plain or "").strip():
        return "empty"
    if is_table_annex(unit):
        return "table_annex"
    return None


def make_header(unit: Unit, *, title_short: str, version_label: str) -> str:
    parts = [f"{title_short} — {version_label}"]
    if unit.breadcrumb:
        parts.append(unit.breadcrumb)
    label = " ".join(p for p in (unit.num_raw, unit.heading) if p)
    if label:
        parts.append(label)
    header = BREADCRUMB_SEP.join(parts)
    if unit.index_terms:
        header += " | Index : " + "; ".join(unit.index_terms)
    return header


def _groups(body: str) -> list[str]:
    """Paragraph groups: a line « (n) … » starts a group, following alineas/points stay with it."""
    groups: list[list[str]] = []
    for line in body.split("\n"):
        if not groups or _PARA_START.match(line):
            groups.append([line])
        else:
            groups[-1].append(line)
    return ["\n".join(g) for g in groups]


def _pieces(text: str, budget: int) -> Iterator[str]:
    """Split one oversize block at line, then sentence, then hard character boundaries."""
    if estimate_tokens(text) <= budget:
        yield text
        return
    lines = text.split("\n")
    if len(lines) > 1:
        for line in lines:
            yield from _pieces(line, budget)
        return
    sentences = _SENTENCE.split(text)
    if len(sentences) > 1:
        for s in sentences:
            yield from _pieces(s, budget)
        return
    step = max(1, int(budget * 3.5))
    for i in range(0, len(text), step):
        yield text[i : i + step]


def split_body(body: str, header: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Greedy packing of paragraph groups so that header + part stays within ``max_tokens``."""
    budget = max(200, max_tokens - estimate_tokens(header) - 2)
    if estimate_tokens(body) <= budget:
        return [body]
    pieces = [p for g in _groups(body) for p in _pieces(g, budget)]
    parts: list[str] = []
    current: list[str] = []
    for piece in pieces:
        candidate = "\n".join([*current, piece])
        if current and estimate_tokens(candidate) > budget:
            parts.append("\n".join(current))
            current = [piece]
        else:
            current.append(piece)
    if current:
        parts.append("\n".join(current))
    return parts


def chunk_units(
    units: Sequence[Unit],
    *,
    title_short: str,
    version_label: str,
    text_valid: DateWindow | None = None,
    max_tokens: int = MAX_TOKENS,
) -> list[Chunk]:
    """Chunks for the units of one version (repealed stubs and modifying articles produce none).

    ``text_valid`` is the version's serving window (manifest entry); without it chunks are open-ended.
    """
    valid_from = text_valid.lower if text_valid and text_valid.lower else OPEN_START
    valid_to = text_valid.upper if text_valid else None
    out: list[Chunk] = []
    for unit in units:
        if skip_reason(unit) is not None:
            continue
        header = make_header(unit, title_short=title_short, version_label=version_label)
        body_full = unit.text_md or unit.text_plain or ""
        parts = split_body(body_full, header, max_tokens)
        searchable = not ({ActFlag.SUPERSEDED_BY_CODE, ActFlag.ACTE_TEMPORAIRE} & set(unit.flags))
        rank = AMENDING_RANK_PRIOR if ActFlag.IS_AMENDING_ACT in unit.flags else 1.0
        for i, body in enumerate(parts, 1):
            out.append(
                Chunk(
                    eli_work=unit.eli_work,
                    eli_version=unit.eli_version,
                    unit_ordinal=unit.ordinal,
                    part_no=i,
                    part_count=len(parts),
                    header=header,
                    body=body,
                    n_tokens=estimate_tokens(header + "\n" + body),
                    doc_type=unit.doc_type,
                    text_basis=unit.text_basis,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    searchable=searchable,
                    rank_prior=rank,
                    content_sha256=content_sha256(header, body),
                )
            )
    return out


# -------------------------------------------------------------------------------------------- stage
def short_title(act: Act | None, fallback: str, curated: Sequence[str] = ()) -> str:
    """« titre court »: ``title_short`` → curated short title (``aliases.yaml``, e.g. « Code du travail »)
    → ``citation_title`` → ``title`` cut at ~120 chars → the ELI."""
    if act is not None and act.title_short:
        return act.title_short
    if curated:
        return curated[0]
    if act is not None:
        for t in (act.citation_title, act.title):
            if t:
                t = " ".join(t.replace("\\n", " ").split())  # some SPARQL titles carry a literal '\n'
                t = re.sub(r"^Version consolidée applicable au \S+ : ", "", t).strip()
                if len(t) <= 120:
                    return t.rstrip(".")
                cut = t[:120].rsplit(" ", 1)[0]
                return cut.rstrip(" ,;:") + " …"
    return fallback


def version_label(entry: ManifestEntry, version: ActVersion | None) -> str:
    if version is not None and version.label:
        return version.label
    lower = entry.text_valid.lower if entry.text_valid else None
    if entry.kind in (VersionKind.ORIGINAL, None) and (entry.eli_version or "").endswith("/jo"):
        return "texte original"
    if lower is not None:
        return f"version consolidée applicable au {lower:%d/%m/%Y}"
    return "version consolidée"


def _load_index[M: Act | ActVersion](path: Path, model: type[M], key: str) -> dict[str, M]:
    if not path.exists():
        return {}
    return {getattr(r, key): r for r in read_jsonl(path, model)}


def run_chunk(ctx: StageContext) -> StageResult:
    """Chunk the units of every selected version of T (``--opt manifest=PATH``, ``--opt max_tokens=N``)."""
    from lgx_ingest.fetch import _entries, select_targets

    result = StageResult(stage="chunk")
    try:
        entries, manifest = _entries(ctx)
    except FileNotFoundError as exc:
        result.ok = False
        result.messages.append(str(exc))
        return result
    max_tokens = int(ctx.opt("max_tokens") or MAX_TOKENS)
    include_m2 = ctx.opt("include_m2") in ("1", "true", "yes")
    acts = _load_index(ctx.layout.acts_path, Act, "eli_work")
    curated: dict[str, list[str]] = collections.defaultdict(list)
    for alias in ctx.config.aliases.aliases:
        if alias.target and alias.kind.value == "short_title":
            curated[expand(alias.target)].append(alias.alias)
    versions = _load_index(ctx.layout.versions_path, ActVersion, "eli_version")
    wanted = {t.url for t in select_targets(entries, sample=ctx.sample, include_m2=include_m2)}
    counts: collections.Counter[str] = collections.Counter()
    from lgx_ingest.fetch import fetch_url

    for entry in entries:
        if entry.file is None or entry.eli_version is None or fetch_url(entry) not in wanted:
            continue
        units_path = ctx.layout.units_path(entry.eli_version)
        if not units_path.exists():
            counts["missing_units"] += 1
            continue
        units = list(read_jsonl(units_path, Unit))
        chunks = chunk_units(
            units,
            title_short=short_title(
                acts.get(entry.eli_work), entry.eli_work.rsplit("/eli/", 1)[-1], curated.get(entry.eli_work, ())
            ),
            version_label=version_label(entry, versions.get(entry.eli_version)),
            text_valid=entry.text_valid,
            max_tokens=max_tokens,
        )
        write_jsonl(ctx.layout.chunks_path(entry.eli_version), chunks)
        counts["versions"] += 1
        counts["units"] += len(units)
        counts["chunks"] += len(chunks)
        counts["split_articles"] += len({c.unit_ordinal for c in chunks if c.part_count > 1})
        for u in units:
            reason = skip_reason(u)
            if reason in ("repealed", "modifying", "table_annex", "preamble", "empty"):
                counts[f"skipped_{reason}"] += 1
    result.counts.update(counts)
    result.details["manifest"] = str(manifest)
    result.outputs = [str(ctx.layout.chunks_dir)]
    if counts["missing_units"]:
        result.messages.append(f"{counts['missing_units']} version(s) without units (run `lgx parse`)")
    return result
