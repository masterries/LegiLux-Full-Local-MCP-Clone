"""PostgreSQL for the schema tests: LGX_TEST_PG_DSN, else a throwaway lgx-db container, else skip.

The container is built from db/Dockerfile (base image pinned by digest, pulled via mirror.gcr.io) and
removed at the end of the session. The schema is applied with scripts/db_apply.sh, exactly as in dev.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

REPO = Path(__file__).resolve().parents[2]
APPLY = REPO / "scripts" / "db_apply.sh"
TEST_IMAGE = os.environ.get("LGX_TEST_DB_IMAGE", "lgx.local/lgx-db:test")


def _run(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, check=False)


@dataclass
class PgServer:
    host: str
    port: int
    user: str
    password: str
    container: str | None = None
    databases: list[str] = field(default_factory=list)

    def dsn(self, dbname: str) -> str:
        return make_conninfo(host=self.host, port=self.port, user=self.user, password=self.password, dbname=dbname)

    def apply_schema(self, dbname: str, *extra: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        cmd = ["bash", str(APPLY), "--dbname", dbname, *extra]
        if self.container:
            cmd += ["--container", self.container]
        else:
            env.update(PGHOST=self.host, PGPORT=str(self.port), PGUSER=self.user, PGPASSWORD=self.password)
        result = _run(cmd, env=env)
        if result.returncode != 0:
            raise AssertionError(f"db_apply.sh failed:\n{result.stdout}\n{result.stderr}")
        self.databases.append(dbname)
        return result

    def connect(self, dbname: str) -> psycopg.Connection:
        return psycopg.connect(self.dsn(dbname), autocommit=True)


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return _run(["docker", "info"], timeout=20).returncode == 0


def _ensure_image() -> None:
    """(Re)build the test image every session; the layer cache makes this a no-op when unchanged."""
    build = _run(["docker", "build", "-q", "-t", TEST_IMAGE, str(REPO / "db")], timeout=600)
    if build.returncode != 0:
        pytest.skip(f"cannot build {TEST_IMAGE}: {build.stderr.strip()[-300:]}")


def _wait_tcp(server: PgServer, deadline_s: float = 90) -> None:
    end = time.monotonic() + deadline_s
    last: Exception | None = None
    while time.monotonic() < end:
        try:
            with psycopg.connect(server.dsn("postgres"), connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            # the image restarts PostgreSQL once after initdb; require two successes 1 s apart
            time.sleep(1)
            with psycopg.connect(server.dsn("postgres"), connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise AssertionError(f"PostgreSQL did not become ready: {last}")


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    dsn = os.environ.get("LGX_TEST_PG_DSN")
    if dsn:
        if shutil.which("psql") is None:
            pytest.skip("LGX_TEST_PG_DSN is set but psql is not installed")
        info = conninfo_to_dict(dsn)
        server = PgServer(
            host=str(info.get("host", "localhost")),
            port=int(info.get("port", 5432)),
            user=str(info.get("user", "postgres")),
            password=str(info.get("password", "")),
            # the server's container (CI: job.services.postgres.id): pg_dump / pg_restore run there, so the
            # package -> restore -> rename-swap path is tested too
            container=os.environ.get("LGX_TEST_PG_CONTAINER") or None,
        )
        _wait_tcp(server)
        yield server
        with server.connect("postgres") as conn:
            for db in set(server.databases):
                conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        return
    if not _docker_ok():
        pytest.skip("no Docker daemon and no LGX_TEST_PG_DSN: schema tests skipped")
    _ensure_image()
    name = f"lgx-db-test-{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    started = _run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-p",
            "127.0.0.1::5432",
            TEST_IMAGE,
        ]
    )
    if started.returncode != 0:
        pytest.skip(f"cannot start PostgreSQL container: {started.stderr.strip()[-300:]}")
    try:
        port_line = _run(["docker", "port", name, "5432/tcp"]).stdout.strip().splitlines()[0]
        server = PgServer(
            host="127.0.0.1",
            port=int(port_line.rsplit(":", 1)[1]),
            user="postgres",
            password=password,
            container=name,
        )
        _wait_tcp(server)
        yield server
    finally:
        _run(["docker", "rm", "-f", name])
