"""S10 package (minimal, M1): the release database as ONE dump artifact + its restore (PLAN §6.1, §10.4, §10.7).

* :func:`dump_database` — ``pg_dump -Fc -Z zstd:9 --exclude-schema=build --no-owner --no-privileges`` of the
  release database into ``data/bundles/data-<T>[-sample].dump`` with ``….release.json`` (the
  ``lgx_meta.release`` row + ``requires`` + dump size/sha256) and ``….sha256`` (``sha256sum -c`` format).
* :func:`restore_database` — fresh database, ``pg_restore -j N --exit-on-error --no-owner
  --no-privileges``, then the runtime finalisation: ``95_roles.sql`` (GRANTs for ``lgx_ro``), ``ANALYZE``
  and ``ALTER DATABASE … SET default_transaction_read_only = on``.
* :func:`swap_database` — rename-swap ``<name>_next`` → ``<name>`` keeping ``<name>_prev`` (PLAN §10.7).

The PostgreSQL client tools must be at least the server's major version. When the local ``pg_dump`` is
older (e.g. 16 against the PG18 ``lgx-db``), pass ``--container NAME`` and the tools inside that container
are used (``docker exec``; the dump is streamed to the host, the restore input copied in with ``docker cp``).
Signatures (Ed25519/cosign), ``smoke.json`` and gold sets are M5/M1b work.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from lgx_core.io import atomic_write_text
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.load import (
    ROLES_PART,
    SCHEMA_VERSION,
    LoadError,
    apply_schema,
    check_db_name,
    connect,
    default_db_name,
    maintenance_dsn,
    resolve_dsn,
    target_dsn,
)

DUMP_ARGS = ("-Fc", "-Z", "zstd:9", "--exclude-schema=build", "--no-owner", "--no-privileges")
SHIPPED_SCHEMAS = ("lgx", "lgx_meta")


@dataclass(frozen=True)
class PgTools:
    """How to run pg_dump/pg_restore: locally (libpq env from the DSN) or inside a container."""

    dsn: str
    container: str | None = None

    def _conn_args(self, dbname: str) -> tuple[list[str], dict[str, str]]:
        info = {k: str(v) for k, v in conninfo_to_dict(self.dsn).items()}
        if self.container:
            return ["-U", info.get("user", "postgres"), "-d", dbname], {}
        env = {"PGPASSWORD": info["password"]} if "password" in info else {}
        safe = {k: v for k, v in info.items() if k != "password"} | {"dbname": dbname}
        return ["--dbname", make_conninfo(**safe)], env

    def command(
        self, tool: str, dbname: str, *args: str, interactive: bool = False
    ) -> tuple[list[str], dict[str, str]]:
        conn_args, env = self._conn_args(dbname)
        if self.container:
            prefix = ["docker", "exec", *(["-i"] if interactive else []), self.container, tool]
        else:
            prefix = [tool]
        return [*prefix, *conn_args, *args], env


def local_major(tool: str = "pg_dump") -> int | None:
    if shutil.which(tool) is None:
        return None
    out = subprocess.run([tool, "--version"], capture_output=True, text=True, check=False).stdout
    m = re.search(r"(\d+)(?:\.\d+)?", out)
    return int(m.group(1)) if m else None


def server_major(dsn: str) -> int:
    with connect(dsn, autocommit=True) as conn:
        row = conn.execute("SHOW server_version_num").fetchone()
    return int(row[0]) // 10000 if row else 0


def pg_tools(dsn: str, container: str | None) -> PgTools:
    """Local tools when they are new enough for the server, else the container's (error if none)."""
    if container:
        return PgTools(dsn, container)
    server = server_major(dsn)
    local = local_major("pg_dump")
    if local is None or local < server:
        raise LoadError(
            f"local pg_dump {local or 'missing'} is older than server {server}: pass --container NAME "
            "(e.g. lgx-dev-db) to use the tools inside the database container"
        )
    return PgTools(dsn, None)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def bundle_name(sync_date: dt.date, sample: bool) -> str:
    return f"data-{sync_date.isoformat()}" + ("-sample" if sample else "")


# bundle files are not secret and are read by other users and containers (dev compose db-load runs with no
# capabilities, so a 0600 file of another uid is unreadable even for root there)
BUNDLE_MODE = 0o644


def dump_database(tools: PgTools, db_name: str, out: Path) -> dict[str, Any]:
    """``pg_dump -Fc -Z zstd:9`` of the shipped schemas into ``out`` (atomic: temp file + rename)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    cmd, env = tools.command("pg_dump", db_name, *DUMP_ARGS)
    start = time.monotonic()
    with tmp.open("wb") as fh:
        proc = subprocess.run(cmd, env={**_environ(), **env}, stdout=fh, stderr=subprocess.PIPE, check=False)
    seconds = time.monotonic() - start
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise LoadError(f"pg_dump failed ({proc.returncode}): {proc.stderr.decode(errors='replace').strip()[-500:]}")
    tmp.chmod(BUNDLE_MODE)
    tmp.replace(out)
    return {"file": str(out), "bytes": out.stat().st_size, "sha256": sha256_file(out), "seconds": round(seconds, 2)}


def _environ() -> dict[str, str]:
    return dict(os.environ)


def _recreate(dsn: str, name: str) -> None:
    check_db_name(name)
    with connect(maintenance_dsn(dsn, name), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')


def restore_database(
    tools: PgTools, dump: Path, target: str, *, jobs: int = 4, read_only: bool = True, embed_dim: int = 1024
) -> dict[str, Any]:
    """Restore ``dump`` into a fresh ``target`` and finalise it for the runtime (GRANTs, ANALYZE, read-only)."""
    _recreate(tools.dsn, target)
    start = time.monotonic()
    args = ("-j", str(jobs), "--exit-on-error", "--no-owner", "--no-privileges")
    if tools.container:
        inside = f"/tmp/{dump.name}"
        cp = subprocess.run(
            ["docker", "cp", str(dump), f"{tools.container}:{inside}"], capture_output=True, text=True, check=False
        )
        if cp.returncode != 0:
            raise LoadError(f"docker cp failed: {cp.stderr.strip()}")
        cmd, env = tools.command("pg_restore", target, *args, inside)
    else:
        cmd, env = tools.command("pg_restore", target, *args, str(dump))
    proc = subprocess.run(cmd, env={**_environ(), **env}, capture_output=True, text=True, check=False)
    if tools.container:
        subprocess.run(["docker", "exec", tools.container, "rm", "-f", f"/tmp/{dump.name}"], check=False)
    restore_s = time.monotonic() - start
    if proc.returncode != 0:
        raise LoadError(f"pg_restore into {target} failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
    start = time.monotonic()
    with connect(target_dsn(tools.dsn, target), autocommit=True) as conn:
        apply_schema(conn, embed_dim=embed_dim, only=[ROLES_PART])
        conn.execute("ANALYZE")
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        if read_only:
            conn.execute(f'ALTER DATABASE "{target}" SET default_transaction_read_only = on')
    return {
        "database": target,
        "restore_seconds": round(restore_s, 2),
        "finalise_seconds": round(time.monotonic() - start, 2),
        "database_bytes": int(size[0]) if size else None,
        "read_only": read_only,
        "jobs": jobs,
    }


def shipped_counts(dsn: str, db_name: str) -> dict[str, int]:
    with connect(target_dsn(dsn, db_name), autocommit=True) as conn:
        tables = conn.execute(
            "SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname = ANY(%s) ORDER BY 1",
            (list(SHIPPED_SCHEMAS),),
        ).fetchall()
        out: dict[str, int] = {}
        for (name,) in tables:
            row = conn.execute(f"SELECT count(*) FROM {name}").fetchone()
            out[str(name)] = int(row[0]) if row else 0
    return out


def smoke(dsn: str, db_name: str, sync_date: dt.date) -> dict[str, Any]:
    """Minimal post-restore smoke: invariants clean, release row, a canned lexical query, read-only role."""
    with connect(target_dsn(dsn, db_name), autocommit=True) as conn:
        violations = conn.execute("SELECT check_name, n FROM lgx_meta.invariant_violations WHERE n <> 0").fetchall()
        release = conn.execute("SELECT release_id, sync_date, content_digest FROM lgx_meta.release").fetchall()
        hits = conn.execute(
            "SELECT count(*) FROM lgx.search_candidates('travail', NULL, %s, '{}', NULL, NULL, 10)", (sync_date,)
        ).fetchone()
        grants = conn.execute(
            "SELECT has_table_privilege('lgx_ro', 'lgx.chunk', 'SELECT'), "
            "has_table_privilege('lgx_ro', 'lgx.chunk', 'INSERT')"
        ).fetchone()
    return {
        "invariant_violations": {str(r[0]): int(r[1]) for r in violations},
        "release": [(r[0], str(r[1]), r[2]) for r in release],
        "canned_query_hits": int(hits[0]) if hits else 0,
        "lgx_ro_select": bool(grants[0]) if grants else False,
        "lgx_ro_insert": bool(grants[1]) if grants else True,
    }


def swap_database(dsn: str, name: str, *, keep_prev: bool = True) -> dict[str, Any]:
    """Rename-swap ``<name>_next`` → ``<name>``; the old ``<name>`` becomes ``<name>_prev`` (or is dropped)."""
    nxt, prev = check_db_name(f"{name}_next"), check_db_name(f"{name}_prev")
    with connect(maintenance_dsn(dsn, name), autocommit=True) as conn:
        dbs = {r[0] for r in conn.execute("SELECT datname FROM pg_database").fetchall()}
        if nxt not in dbs:
            raise LoadError(f"swap: {nxt} does not exist")
        for db in (name, nxt, prev):
            conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (db,))
        if name in dbs:
            conn.execute(f'DROP DATABASE IF EXISTS "{prev}" WITH (FORCE)')
            if keep_prev:
                conn.execute(f'ALTER DATABASE "{name}" RENAME TO "{prev}"')
            else:
                conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        conn.execute(f'ALTER DATABASE "{nxt}" RENAME TO "{name}"')
    return {"database": name, "previous": prev if keep_prev and name in dbs else None}


def set_role_password(dsn: str, role: str, password_file: Path) -> None:
    """Set the login password of ``role`` (dev convenience; the airgap uses the pg_ro_pw Docker secret)."""
    password = password_file.read_text(encoding="utf-8").strip()
    if not password:
        raise LoadError(f"{password_file} is empty")
    with connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))


def release_manifest(dsn: str, db_name: str, dump: dict[str, Any], pg_version: int) -> dict[str, Any]:
    with connect(target_dsn(dsn, db_name), autocommit=True) as conn:
        cur = conn.execute("SELECT * FROM lgx_meta.release")
        row = cur.fetchone()
        if row is None or cur.description is None:
            raise LoadError(f"{db_name} has no lgx_meta.release row: load it first")
        rel = dict(zip([c.name for c in cur.description], row, strict=True))
        model = conn.execute(
            "SELECT provider, model_id, dim FROM lgx.embedding_model ORDER BY embedding_set_id LIMIT 1"
        ).fetchone()
    # PLAN §10.4: a release with vectors needs the runtime endpoint to serve the same model (startup probe)
    embedding = {"provider": model[0], "model_id": model[1], "dim": model[2]} if model else None
    gates = rel.get("gates") or {}
    return {
        "channel": "data",
        "release_id": rel["release_id"],
        "sync_date": str(rel["sync_date"]),
        "horizon": str(rel["horizon"]),
        "built_at": rel["built_at"].isoformat(),
        "schema_version": rel["schema_version"],
        "rules_version": rel["rules_version"],
        "parser_version": rel["parser_version"],
        "pipeline_git_sha": rel["pipeline_git_sha"],
        "inputs_digest": rel["inputs_digest"],
        "content_digest": rel["content_digest"],
        "release_kind": (rel.get("config") or {}).get("release_kind"),
        "counts": rel["counts"],
        "gates": {"status": gates.get("status"), "summary": gates.get("summary")},
        "requires": {"schema_version": SCHEMA_VERSION, "embedding": embedding, "postgres_major": pg_version},
        "dump": {
            "file": Path(dump["file"]).name,
            "bytes": dump["bytes"],
            "sha256": dump["sha256"],
            "format": "pg_dump " + " ".join(DUMP_ARGS),
        },
    }


def run_package(
    ctx: StageContext,
    *,
    dsn: str | None = None,
    db_name: str | None = None,
    out_dir: Path | None = None,
    container: str | None = None,
    restore_to: str | None = None,
) -> StageResult:
    """Dump the release database of T to ``data/bundles/`` and optionally restore it (``restore_to``).

    ``restore_to=NAME`` restores into ``NAME_next``, verifies counts + smoke, then rename-swaps it to ``NAME``
    (``--opt keep_prev=0`` drops the previous one). ``--opt ro_password_file=PATH`` sets the ``lgx_ro`` password.
    """
    result = StageResult(stage="package")
    try:
        resolved = resolve_dsn(ctx, dsn)
        source = check_db_name(db_name or ctx.opt("db_name") or default_db_name(ctx.sync_date, ctx.sample))
        container = container or ctx.opt("container")
        tools = pg_tools(target_dsn(resolved, source), container)
        tools = PgTools(resolved, tools.container)
        pg_version = server_major(target_dsn(resolved, source))
        out = (out_dir or ctx.layout.root / "bundles") / f"{bundle_name(ctx.sync_date, ctx.sample)}.dump"
        dump = dump_database(tools, source, out)
        manifest = release_manifest(resolved, source, dump, pg_version)
        rel_path = out.with_suffix(".release.json")
        manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n"
        atomic_write_text(rel_path, manifest_text, mode=BUNDLE_MODE)
        sums = out.with_suffix(".sha256")
        sums_text = f"{dump['sha256']}  {out.name}\n{sha256_file(rel_path)}  {rel_path.name}\n"
        atomic_write_text(sums, sums_text, mode=BUNDLE_MODE)
        result.outputs = [str(out), str(rel_path), str(sums)]
        result.counts = {"dump_bytes": dump["bytes"]}
        result.details = {"source_db": source, "dump": dump, "tools": "container" if tools.container else "local"}
        target = restore_to or ctx.opt("restore_to")
        if target:
            check_db_name(target)
            nxt = f"{target}_next"
            restored = restore_database(tools, out, nxt, embed_dim=ctx.config.embedding.dim)
            src_counts, dst_counts = shipped_counts(resolved, source), shipped_counts(resolved, nxt)
            diff = {k: (v, dst_counts.get(k)) for k, v in src_counts.items() if dst_counts.get(k) != v}
            check = smoke(resolved, nxt, ctx.sync_date)
            result.details["restore"] = restored | {"counts_equal": not diff, "diff": diff, "smoke": check}
            problems = []
            if diff:
                problems.append(f"row counts differ after restore: {diff}")
            if check["invariant_violations"] or check["canned_query_hits"] == 0 or not check["lgx_ro_select"]:
                problems.append(f"smoke failed: {check}")
            if check["release"] and check["release"][0][2] != manifest["content_digest"]:
                problems.append("content_digest differs between the dump and the release row")
            if problems:
                result.ok = False
                result.messages += problems
                return result
            result.details["swap"] = swap_database(resolved, target, keep_prev=ctx.opt("keep_prev") != "0")
            pw = ctx.opt("ro_password_file")
            if pw:
                set_role_password(target_dsn(resolved, "postgres"), "lgx_ro", Path(pw))
                result.messages.append("lgx_ro password set from " + pw)
            result.counts["restore_ms"] = int(restored["restore_seconds"] * 1000)
    except (LoadError, psycopg.Error, OSError) as exc:
        result.ok = False
        result.messages.append(f"package failed: {exc}")
    return result
