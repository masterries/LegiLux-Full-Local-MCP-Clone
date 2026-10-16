"""Stage context passed by the ``lgx`` CLI to every pipeline stage, and the common stage result."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field
from rich.console import Console

from lgx_core.config import DataLayout, ProjectConfig, Settings


@dataclass(frozen=True)
class StageContext:
    """Everything a stage needs; stages must not read the environment or ``config/`` themselves.

    ``sample`` selects the dev/CI path (``config/sample.yaml``) instead of the full corpus.
    ``options`` carries free-form ``--opt KEY=VALUE`` pairs so a stage can grow knobs without
    editing the CLI.
    """

    sync_date: dt.date
    settings: Settings
    config: ProjectConfig
    layout: DataLayout
    sample: bool = True
    dry_run: bool = False
    options: Mapping[str, str] = field(default_factory=dict)
    console: Console = field(default_factory=lambda: Console(stderr=True))

    @classmethod
    def create(
        cls,
        *,
        sync_date: dt.date | None = None,
        settings: Settings | None = None,
        config_dir: Path | None = None,
        data_dir: Path | None = None,
        sample: bool = True,
        dry_run: bool = False,
        options: Mapping[str, str] | None = None,
    ) -> StageContext:
        settings = settings or Settings.from_env()
        if config_dir is not None:
            settings = settings.model_copy(update={"config_dir": config_dir})
        if data_dir is not None:
            settings = settings.model_copy(update={"data_dir": data_dir})
        config = ProjectConfig.load(settings.config_dir)
        t = sync_date or config.scope.resolve_sync_date()
        return cls(
            sync_date=t,
            settings=settings,
            config=config,
            layout=DataLayout(settings.data_dir, t),
            sample=sample,
            dry_run=dry_run,
            options=dict(options or {}),
        )

    def opt(self, key: str, default: str | None = None) -> str | None:
        return self.options.get(key, default)


class UrlPolicy(Protocol):
    """Robots + denylist guard consulted before every request (built by ``lgx_ingest.robots``).

    ``lgx_core.eli.assert_fetchable`` is applied in addition, as a last line of defence.
    """

    def allows(self, url: str) -> bool:
        """True if robots.txt of the URL's host permits ``url`` for our User-Agent."""
        ...

    def is_denied_eli(self, eli: str) -> bool:
        """True if ``eli`` (work, version or file path mapped to an ELI) is denylisted."""
        ...


class StageResult(BaseModel):
    """What a stage reports back to the CLI (printed as JSON, stored in reports)."""

    stage: str
    ok: bool = True
    counts: dict[str, int] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
