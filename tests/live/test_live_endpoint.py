"""Live checks against Legilux (skipped by default; run with --run-live). One request each, polite UA."""

from __future__ import annotations

import time

import httpx
import pytest

from lgx_core.config import Settings
from lgx_ingest.sparql_templates import render_query

pytestmark = pytest.mark.live


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": settings.user_agent}, timeout=settings.http_timeout_s)


def test_completeness_query_runs() -> None:
    s = Settings.from_env()
    with _client(s) as c:
        r = c.post(
            s.sparql_url,
            data={"query": render_query("checks/q17_completeness")},
            headers={"Accept": "text/csv"},
        )
    assert r.status_code == 200
    assert r.text.splitlines()[0].replace('"', "") == "c,rows,distinct"
    time.sleep(s.rate_limit_s)


def test_filestore_is_not_disallowed_on_main_host() -> None:
    s = Settings.from_env()
    with _client(s) as c:
        r = c.get("https://legilux.public.lu/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /filestore" not in r.text and "Disallow: /eli/etat/adm/" in r.text
    time.sleep(s.rate_limit_s)
