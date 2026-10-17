"""A tiny synthetic release on disk (the loader's inputs for T), shared by the unit and db loader tests.

Import as ``from unit.load_fixture import build_tiny_release`` (pytest puts ``tests/`` on ``sys.path``).

Contents (T = 2026-10-16, ELIs under ``etat/leg/``):

* ``loi/2020/01/01/a1`` — consolidated; current version ``consolide/20250101`` (text: a title unit, Art. 1er,
  a repealed Art. 2, « Art. 9 et 10. »), stored future version ``consolide/20270101`` (text), one old
  metadata version ``consolide/20200101``; AKN relations: a textualMod from ``rgd/2021/02/02/a2/jo`` on
  ``art_1``, an akn-ref from Art. 1er to the RGD and to the article ELI ``…/a1/art_1``.
* ``rgd/2021/02/02/a2`` — JO original (HTML text), never amended; it modifies ``a1`` (SPARQL).
* ``loi/2022/03/03/a3`` — consolidated, but its file was never fetched → relabelled ``not_loaded``.
* ``loi/1990/04/04/n4`` — metadata_only (repealed), no version.
* a SPARQL edge from the denylisted ``loi/2007/01/15/n2/jo`` (robots denylist) → dropped by the loader.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from lgx_core.config import DEFAULT_CONFIG_DIR, REPO_ROOT
from lgx_core.io import atomic_write_text, write_jsonl
from lgx_core.models import (
    CC_BY_4_0,
    Act,
    ActFlag,
    ActVersion,
    Chunk,
    DataQualityIssue,
    DateWindow,
    FileRef,
    ManifestEntry,
    MetadataReason,
    RawFile,
    Relation,
    RelationSource,
    RelType,
    SourceFormat,
    TextBasis,
    Unit,
    UnitType,
    UserFormat,
    VersionKind,
    VersionRole,
    sha256_hex,
)

T = dt.date(2026, 10, 16)
E = "http://data.legilux.public.lu/eli/etat/leg/"
FS = "https://legilux.public.lu/filestore/eli/etat/leg/"
A1, A2, A3, A4 = E + "loi/2020/01/01/a1", E + "rgd/2021/02/02/a2", E + "loi/2022/03/03/a3", E + "loi/1990/04/04/n4"
V1_OLD, V1, V1_FUT = A1 + "/consolide/20200101", A1 + "/consolide/20250101", A1 + "/consolide/20270101"
A2_JO, A3_V = A2 + "/jo", A3 + "/consolide/20240101"
DENIED = E + "loi/2007/01/15/n2"


def _file(eli_version: str, fmt: SourceFormat) -> FileRef:
    rel = eli_version.removeprefix(E)
    slug = "eli-etat-leg-" + rel.replace("/", "-")
    url = f"{FS}{rel}/fr/{fmt.value}/{slug}-fr-{fmt.value}.{fmt.value}"
    return FileRef(
        url=url,
        file_uri=url.replace("https://legilux.public.lu/", "http://data.legilux.public.lu/"),
        user_format=UserFormat.XML if fmt is SourceFormat.XML else UserFormat.HTML,
        source_format=fmt,
        licence=CC_BY_4_0,
    )


def _units(
    version: str, work: str, raw: RawFile, fmt: SourceFormat, *, basis: TextBasis, articles: list[dict[str, Any]]
) -> list[Unit]:
    common = {
        "eli_work": work,
        "eli_version": version,
        "doc_type": "LOI" if "/loi/" in work else "RGD",
        "source_format": fmt,
        "source_url": raw.url,
        "source_sha256": raw.sha256,
        "licence": CC_BY_4_0,
        "text_basis": basis,
    }
    out = [
        Unit(
            ordinal=1,
            depth=0,
            unit_type=UnitType.TITLE,
            anchor="titre_1",
            num_raw="Titre Ier",
            heading="Général",
            **common,
        )
    ]
    for i, a in enumerate(articles, 2):
        out.append(
            Unit(
                ordinal=i,
                parent_ordinal=1,
                depth=1,
                unit_type=UnitType.ARTICLE,
                breadcrumb="Titre Ier – Général",
                **common,
                **a,
            )
        )
    return out


def _chunk(unit: Unit, *, label: str, window: DateWindow) -> Chunk:
    header = f"Loi test — {label} > {unit.num_raw}"
    body = unit.text_plain or ""
    assert window.lower is not None
    return Chunk(
        eli_work=unit.eli_work,
        eli_version=unit.eli_version,
        unit_ordinal=unit.ordinal,
        header=header,
        body=body,
        n_tokens=len(body) // 4 + 1,
        doc_type=unit.doc_type,
        text_basis=unit.text_basis,
        valid_from=window.lower,
        valid_to=window.upper,
        content_sha256=sha256_hex(header + "\n" + body),
    )


def build_tiny_release(root: Path, *, sync_date: dt.date = T, fetch_a3: bool = False) -> dict[str, Any]:
    """Write the tiny release under ``root`` (a data dir) and return a dict of its key objects."""
    t = sync_date.isoformat()
    w_cur, w_fut = (
        DateWindow(lower=dt.date(2025, 1, 1), upper=dt.date(2027, 1, 1)),
        DateWindow(lower=dt.date(2027, 1, 1)),
    )
    w_jo, w_a3 = DateWindow(lower=dt.date(2021, 2, 10)), DateWindow(lower=dt.date(2024, 1, 1))
    acts = [
        Act(
            eli_work=A1,
            eli_jo=A1 + "/jo",
            doc_type="LOI",
            dei_min=dt.date(2020, 1, 10),
            dei_count=1,
            rdf_status="in-force",
            title="Loi du 1er janvier 2020 relative aux essais,\\net aux contrôles.",
            text_basis=TextBasis.CONSOLIDATED,
            basis_version_eli=V1,
            memorial_ref="Mémorial A n° 1 de 2020",
        ),
        Act(
            eli_work=A2,
            eli_jo=A2_JO,
            doc_type="RGD",
            dei_min=dt.date(2021, 2, 10),
            rdf_status="in-force",
            title="Règlement grand-ducal du 2 février 2021 modifiant la loi relative aux essais.",
            text_basis=TextBasis.ORIGINAL_NEVER_AMENDED,
            flags=[ActFlag.IS_AMENDING_ACT],
            basis_version_eli=A2_JO,
        ),
        Act(
            eli_work=A3,
            eli_jo=A3 + "/jo",
            doc_type="LOI",
            dei_min=dt.date(2022, 3, 10),
            rdf_status="in-force",
            title="Loi du 3 mars 2022 sur les contrôles techniques.",
            text_basis=TextBasis.CONSOLIDATED,
            basis_version_eli=A3_V,
        ),
        Act(
            eli_work=A4,
            eli_jo=A4 + "/jo",
            doc_type="LOI",
            dei_min=dt.date(1990, 4, 10),
            dnl=dt.date(2010, 1, 1),
            rdf_status="no-longer-in-force",
            title="Loi du 4 avril 1990 abrogée.",
            text_basis=TextBasis.METADATA_ONLY,
            metadata_reason=MetadataReason.REPEALED,
        ),
    ]
    files = {V1: _file(V1, SourceFormat.XML), V1_FUT: _file(V1_FUT, SourceFormat.XML)}
    files[A2_JO] = _file(A2_JO, SourceFormat.HTML)
    files[A3_V] = _file(A3_V, SourceFormat.XML)
    versions = [
        ActVersion(
            eli_work=A1,
            eli_version=V1_OLD,
            kind=VersionKind.CONSOLIDATED,
            role=VersionRole.METADATA,
            date_applicability=dt.date(2020, 1, 1),
            date_end_applicability=dt.date(2025, 1, 1),
            has_files=True,
            label="Version consolidée applicable au 01/01/2020",
        ),
        ActVersion(
            eli_work=A1,
            eli_version=V1,
            kind=VersionKind.CONSOLIDATED,
            role=VersionRole.CURRENT,
            date_applicability=dt.date(2025, 1, 1),
            date_end_applicability=dt.date(2027, 1, 1),
            has_files=True,
            label="Version consolidée applicable au 01/01/2025",
            source_format=SourceFormat.XML,
            licence=CC_BY_4_0,
        ),
        ActVersion(
            eli_work=A1,
            eli_version=V1_FUT,
            kind=VersionKind.CONSOLIDATED,
            role=VersionRole.FUTURE,
            date_applicability=dt.date(2027, 1, 1),
            has_files=True,
            label="Version consolidée applicable au 01/01/2027",
            source_format=SourceFormat.XML,
            licence=CC_BY_4_0,
        ),
        ActVersion(
            eli_work=A2,
            eli_version=A2_JO,
            kind=VersionKind.ORIGINAL,
            role=VersionRole.CURRENT,
            date_applicability=dt.date(2021, 2, 10),
            has_files=True,
            label="Texte original",
            source_format=SourceFormat.HTML,
            licence=CC_BY_4_0,
        ),
        ActVersion(
            eli_work=A3,
            eli_version=A3_V,
            kind=VersionKind.CONSOLIDATED,
            role=VersionRole.CURRENT,
            date_applicability=dt.date(2024, 1, 1),
            has_files=True,
            label="Version consolidée applicable au 01/01/2024",
            source_format=SourceFormat.XML,
            licence=CC_BY_4_0,
        ),
    ]
    windows = {V1: w_cur, V1_FUT: w_fut, A2_JO: w_jo, A3_V: w_a3}
    basis = {A1: TextBasis.CONSOLIDATED, A2: TextBasis.ORIGINAL_NEVER_AMENDED, A3: TextBasis.CONSOLIDATED}
    manifest: list[ManifestEntry] = []
    for v in versions:
        manifest.append(
            ManifestEntry(
                eli_work=v.eli_work,
                eli_version=v.eli_version,
                kind=v.kind,
                role=v.role,
                doc_type="RGD" if v.eli_work == A2 else "LOI",
                text_basis=basis[v.eli_work],
                file=files.get(v.eli_version),
                text_valid=windows.get(v.eli_version),
                date_applicability=v.date_applicability,
                date_end_applicability=v.date_end_applicability,
                reason="2b:consolidated" if v.eli_version in windows else "2f:metadata_version",
                in_sample=True,
                sample_stage="m1",
            )
        )
    manifest.append(
        ManifestEntry(
            eli_work=A4,
            role=VersionRole.METADATA,
            doc_type="LOI",
            text_basis=TextBasis.METADATA_ONLY,
            metadata_reason=MetadataReason.REPEALED,
            reason="1:repealed",
        )
    )
    relations = [
        Relation(
            src_eli=A2_JO,
            rel_type=RelType.MODIFIES,
            dst_eli=A1 + "/jo",
            source=RelationSource.SPARQL,
            src_dei=dt.date(2021, 2, 10),
        ),
        Relation(
            src_eli=E.replace("/leg/", "/adm/") + "amin/2021/01/01/b1/jo",
            rel_type=RelType.MODIFIES,
            dst_eli=A1 + "/jo",
            source=RelationSource.SPARQL,
            src_dei=dt.date(2021, 1, 5),
        ),
        Relation(
            src_eli=DENIED + "/jo",
            rel_type=RelType.MODIFIES,
            dst_eli=A1 + "/jo",
            source=RelationSource.SPARQL,
            src_dei=dt.date(2007, 2, 1),
        ),
        Relation(
            src_eli=A3 + "/jo", rel_type=RelType.CITES, dst_eli=E + "reg_ue/2016/679/jo", source=RelationSource.SPARQL
        ),
    ]
    issues = [
        DataQualityIssue(entity_eli=A3, code="flag_date_conflict", detail="test", first_seen=sync_date),
        DataQualityIssue(entity_eli=DENIED, code="flag_date_conflict", detail="denylisted", first_seen=sync_date),
    ]

    # raw files (content-addressed) + index
    raws: dict[str, RawFile] = {}
    lines = []
    for eli_version, f in files.items():
        if eli_version == A3_V and not fetch_a3:
            continue
        data = f"<doc>{eli_version}</doc>".encode()
        sha = sha256_hex(data)
        ext = ".html" if f.source_format is SourceFormat.HTML else ".xml"
        path = root / "raw" / sha[:2] / f"{sha}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        raw = RawFile(
            url=f.url,
            sha256=sha,
            bytes=len(data),
            path=f"{sha[:2]}/{sha}{ext}",
            http_status=200,
            fetched_at=dt.datetime(2026, 10, 16, 18, 0, tzinfo=dt.UTC),
            eli_version=eli_version,
            source_format=f.source_format,
            last_modified=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC),
        )
        raws[eli_version] = raw
        lines.append(raw.model_dump_json())
    atomic_write_text(root / "raw" / "index.jsonl", "".join(x + "\n" for x in lines))

    # units, chunks, AKN relations per text version
    units_dir, chunks_dir = root / "units" / t, root / "chunks" / t
    art_v1 = [
        {
            "anchor": "art_1er",
            "akn_id": "art_1er",
            "num_raw": "Art. 1er.",
            "num_norm": "1",
            "eli_unit": A1 + "/art_1",
            "text_plain": "Les essais techniques sont soumis à un contrôle annuel des véhicules.",
        },
        {
            "anchor": "art_2",
            "akn_id": "art_2",
            "num_raw": "Art. 2.",
            "num_norm": "2",
            "is_repealed": True,
            "repeal_note": "Abrogé (R. 2 février 2021)",
            "text_plain": None,
        },
        {
            "anchor": "art_9_10",
            "akn_id": "art_9_10",
            "num_raw": "Art. 9 et 10.",
            "num_norm": "9_et_10",
            "text_plain": "Le règlement (UE) 2016/679 s'applique aux données des contrôles.",
        },
    ]
    out: dict[str, Any] = {"acts": acts, "versions": versions, "raws": raws, "units": {}, "chunks": {}}
    for eli_version, work, fmt, arts, label in (
        (V1, A1, SourceFormat.XML, art_v1, "Version consolidée applicable au 01/01/2025"),
        (V1_FUT, A1, SourceFormat.XML, art_v1[:1], "Version consolidée applicable au 01/01/2027"),
        (
            A2_JO,
            A2,
            SourceFormat.HTML,
            [
                {
                    "anchor": "art_1",
                    "num_raw": "Art. 1er.",
                    "num_norm": "1",
                    "eli_unit": A2 + "/art_1",
                    "eli_unit_derived": True,
                    "text_plain": "La loi relative aux essais est modifiée.",
                }
            ],
            "texte original",
        ),
    ):
        raw = raws[eli_version]
        units = _units(eli_version, work, raw, fmt, basis=basis[work], articles=arts)
        slug = "eli-" + eli_version.removeprefix("http://data.legilux.public.lu/eli/").replace("/", "-")
        write_jsonl(units_dir / f"{slug}.jsonl", units)
        chunks = [
            _chunk(u, label=label, window=windows[eli_version])
            for u in units
            if u.unit_type is UnitType.ARTICLE and not u.is_repealed
        ]
        write_jsonl(chunks_dir / f"{slug}.jsonl", chunks)
        rels: list[Relation] = []
        if eli_version == V1:
            rels = [
                Relation(
                    src_eli=A2_JO,
                    rel_type=RelType.MODIFIES,
                    dst_eli=A1,
                    source=RelationSource.AKN_TEXTUALMOD,
                    dst_unit_hint="art_1er",
                ),
                Relation(
                    src_eli=A2_JO,
                    rel_type=RelType.MODIFIES,
                    dst_eli=A1,
                    source=RelationSource.AKN_TEXTUALMOD,
                    dst_unit_hint="art_1er",
                ),  # duplicate: collapsed by the loader
                Relation(
                    src_eli=A1,
                    rel_type=RelType.REFERS_TO,
                    dst_eli=A2_JO,
                    source=RelationSource.AKN_REF,
                    src_version_eli=V1,
                    src_unit_ordinal=2,
                ),
                Relation(
                    src_eli=A1,
                    rel_type=RelType.REFERS_TO,
                    dst_eli=A1 + "/art_1",
                    source=RelationSource.AKN_REF,
                    src_version_eli=V1,
                    src_unit_ordinal=4,
                ),
            ]
        write_jsonl(units_dir / f"{slug}.relations.jsonl", rels)
        out["units"][eli_version] = units
        out["chunks"][eli_version] = chunks
    atomic_write_text(
        units_dir / "_parse_report.json",
        json.dumps(
            {
                "stage": "parse",
                "ok": True,
                "counts": {"parsed": 3, "cached": 0, "failed": 0},
                "details": {"failures": []},
            }
        ),
    )

    # selection outputs
    mdir = root / "manifest" / t
    write_jsonl(mdir / "acts.jsonl", acts)
    write_jsonl(mdir / "versions.jsonl", versions)
    write_jsonl(mdir / "manifest.jsonl", manifest)
    write_jsonl(mdir / "relations.jsonl", relations)
    write_jsonl(mdir / "data_quality.jsonl", issues)
    report = {
        "rules_version": "test",
        "sync_date": t,
        "config_digest": "sha256:test",
        "snapshot_digest": "sha256:snapshot-test",
        "counts": {
            "acts": len(acts),
            "by_text_basis": {"consolidated": 2, "original_never_amended": 1, "metadata_only": 1},
            "by_flag": {"is_amending_act": 1},
            "stored_texts": 4,
        },
        "sample": {"works": 3, "matched": 3, "mismatches": []},
    }
    atomic_write_text(mdir / "selection_report.json", json.dumps(report))
    meta = root / "meta" / t
    snapshot = {
        "sync_date": t,
        "snapshot_digest": "sha256:snapshot-test",
        "completeness": {"checks": {"acts": {"ok": True, "distinct_pulled": 4, "distinct_endpoint": 4}}},
        "tables": {"acts": {"rows": 4}, "consolidations": {"rows": 4}},
    }
    atomic_write_text(meta / "_snapshot.json", json.dumps(snapshot))
    robots = root / "robots" / t
    atomic_write_text(
        robots / "robots.json",
        json.dumps({"paused": False, "hard_stop": False, "hosts": {"legilux.public.lu": {"sha256": "ab" * 32}}}),
    )
    atomic_write_text(robots / "denylist.json", json.dumps({"entries": [{"eli": DENIED, "reason": "robots"}]}))
    out["manifest"], out["relations"], out["report"] = manifest, relations, report
    return out


# --------------------------------------------------------------------- real pipeline on committed fixtures
FIXTURES = REPO_ROOT / "tests" / "fixtures"
E2E_WORKS = {  # sample works whose files are committed fixtures (CC BY 4.0) -> key in fixtures.json
    "etat/leg/constitution/1868/10/17/n1": "constitution",
    "etat/leg/loi/2018/08/01/a686": "a686_conso",
    "etat/leg/loi/2016/12/23/n25": "n25_jo",
    "etat/leg/loi/1978/07/14/n1": "l1978_jo_html",
    "etat/leg/loi/2026/02/25/a88": "a88_jo",
}


def build_fixture_sample(tmp: Path, works: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Config dir with a sample.yaml reduced to ``works`` + a data dir with the committed SPARQL snapshot
    fixture and the committed CC-BY files seeded into ``raw/`` (no network). Returns (config_dir, data_dir)."""
    works = works or E2E_WORKS
    cfg_dir, data = tmp / "config", tmp / "data"
    shutil.copytree(DEFAULT_CONFIG_DIR, cfg_dir, ignore=shutil.ignore_patterns("denylist_gdpr.yaml"))
    sample = yaml.safe_load((cfg_dir / "sample.yaml").read_text(encoding="utf-8"))
    sample["works"] = [w for w in sample["works"] if w["work"] in works]
    (cfg_dir / "sample.yaml").write_text(yaml.safe_dump(sample, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copytree(FIXTURES / "meta" / "snapshot-20261016-sample", data / "meta" / T.isoformat())
    files = json.loads((FIXTURES / "legilux" / "fixtures.json").read_text(encoding="utf-8"))["files"]
    lines = []
    for key in works.values():
        f = files[key]
        payload = (FIXTURES / "legilux" / f["path"]).read_bytes()
        sha = sha256_hex(payload)
        ext = ".html" if f["source_format"] == "html" else ".xml"
        target = data / "raw" / sha[:2] / f"{sha}{ext}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        raw = RawFile(
            url=f["url"],
            sha256=sha,
            bytes=len(payload),
            path=f"{sha[:2]}/{sha}{ext}",
            fetched_at=dt.datetime(2026, 10, 16, 18, 0, tzinfo=dt.UTC),
            eli_version=f["eli_version"],
            source_format=f["source_format"],
        )
        lines.append(raw.model_dump_json())
    (data / "raw" / "index.jsonl").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    return cfg_dir, data
