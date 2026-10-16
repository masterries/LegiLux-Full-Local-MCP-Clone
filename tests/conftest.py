"""Shared pytest configuration: live tests are opt-in; offline tests never touch the network."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run @pytest.mark.live tests (polite requests to Legilux)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") or os.environ.get("LGX_RUN_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test: use --run-live or LGX_RUN_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
