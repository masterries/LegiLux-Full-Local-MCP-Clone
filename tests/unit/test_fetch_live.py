"""Opt-in live check of the fetcher against the real filestore (``--run-live``; 3 polite requests)."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from lgx_core.models import CC_BY_4_0, DateWindow, FileRef, ManifestEntry, SourceFormat, TextBasis, UserFormat
from lgx_core.models import VersionRole as Role
from lgx_ingest.context import StageContext
from lgx_ingest.fetch import Fetcher, Status, Target
from lgx_ingest.robots import RobotsRules

# loi/2023/07/29/a504/jo: a 6 KB CC-BY XML of the sample
URL = (
    "https://legilux.public.lu/filestore/eli/etat/leg/loi/2023/07/29/a504/jo/fr/xml/"
    "eli-etat-leg-loi-2023-07-29-a504-jo-fr-xml.xml"
)


class LivePolicy:
    def __init__(self, rules: RobotsRules) -> None:
        self.rules = rules

    def allows(self, url: str) -> bool:
        return self.rules.allows(urlsplit(url).path)

    def is_denied_eli(self, eli: str) -> bool:
        return False


@pytest.mark.live
def test_live_get_then_conditional_304(tmp_path: Path) -> None:
    ctx = StageContext.create(sync_date=dt.date(2026, 10, 16), data_dir=tmp_path / "data")
    with httpx.Client(headers={"User-Agent": ctx.settings.user_agent}, timeout=30) as http:
        rules = RobotsRules.parse(http.get("https://legilux.public.lu/robots.txt").text)
    time.sleep(ctx.settings.rate_limit_s)  # keep <= 1 request/s across the robots and file requests
    entry = ManifestEntry(
        eli_work="http://data.legilux.public.lu/eli/etat/leg/loi/2023/07/29/a504",
        eli_version="http://data.legilux.public.lu/eli/etat/leg/loi/2023/07/29/a504/jo",
        role=Role.CURRENT,
        doc_type="LOI",
        text_basis=TextBasis.ORIGINAL_NEVER_AMENDED,
        file=FileRef(
            url=URL, file_uri=URL, user_format=UserFormat.XML, source_format=SourceFormat.XML, licence=CC_BY_4_0
        ),
        text_valid=DateWindow(lower=dt.date(2023, 8, 1)),
        reason="live-test",
    )
    with Fetcher(
        layout=ctx.layout,
        policy=LivePolicy(rules),
        cfg=ctx.config.scope.fetch,
        user_agent=ctx.settings.user_agent,
        spacing_s=ctx.settings.rate_limit_s,
    ) as f:
        first = f.fetch(Target(URL, entry))
        second = f.fetch(Target(URL, entry))
    assert first.status is Status.FETCHED and first.raw is not None and first.raw.last_modified is not None
    assert second.status is Status.NOT_MODIFIED and second.raw == first.raw
