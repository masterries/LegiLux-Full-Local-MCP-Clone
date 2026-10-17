"""Compose lint for the dev stack and the runtime base (PLAN §10.3, M1b "compose lint for the cloudllm profile").

``docker compose config`` only renders files locally (no daemon call, no pull); the test is skipped when the
docker CLI with the compose plugin is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "deploy" / "compose.dev.yml"
RUNTIME = ROOT / "deploy" / "compose.yml"


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _compose_available(), reason="docker compose CLI not available")


@cache
def rendered(file: Path, profiles: tuple[str, ...] = ()) -> dict[str, Any]:
    args = ["docker", "compose", "-f", str(file)]
    for p in profiles:
        args += ["--profile", p]
    proc = subprocess.run([*args, "config", "--format", "json"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_dev_default_command_never_starts_the_cloud_ui() -> None:
    default = rendered(DEV)
    assert set(default["services"]) == {"db", "db-load", "mcp", "lgx-streamlit"}
    cpu = rendered(DEV, ("cpu",))
    assert "ollama" in cpu["services"] and "lgx-streamlit-cloud" not in cpu["services"]
    cloud = rendered(DEV, ("cloudllm",))
    svc = cloud["services"]["lgx-streamlit-cloud"]
    assert svc["profiles"] == ["cloudllm"], "extends must not merge in a default profile"
    assert svc["environment"]["LGX_ALLOW_CLOUD_LLM"] == "1"
    assert set(svc["networks"]) == {"backend", "dev-egress"}
    assert [p["published"] for p in svc["ports"]] == ["8502"]


def test_dev_ports_are_loopback_only_and_backend_is_internal() -> None:
    cfg = rendered(DEV, ("cpu", "cloudllm", "ollama-pull"))
    assert cfg["networks"]["backend"]["internal"] is True
    published = {name: s.get("ports", []) for name, s in cfg["services"].items() if s.get("ports")}
    assert set(published) == {"mcp", "lgx-streamlit", "lgx-streamlit-cloud"}
    for name, ports in published.items():
        assert all(p["host_ip"] == "127.0.0.1" for p in ports), name
    for name in ("db", "db-load", "ollama"):
        assert set(cfg["services"][name]["networks"]) == {"backend"}, name
    assert set(cfg["services"]["ollama-pull"]["networks"]) == {"dev-egress"}


def test_dev_hardening_and_no_secrets_required() -> None:
    cfg = rendered(DEV)
    for name, svc in cfg["services"].items():
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
        if svc.get("read_only"):
            # compose copies inline-content configs into the container: impossible on a read-only rootfs
            assert not svc.get("configs"), f"{name}: read_only service with configs"
            assert svc.get("user") == "10001:10001", name
        assert not svc.get("secrets"), f"{name}: the default dev stack must start without secret files"
    ui = cfg["services"]["lgx-streamlit"]["environment"]
    assert ui["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] == "false"
    assert ui["STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE"] == "true"
    assert ui["STREAMLIT_BROWSER_SERVER_ADDRESS"]
    assert ui["LGX_MCP_TOKEN"] == cfg["services"]["mcp"]["environment"]["LGX_MCP_TOKEN"]
    assert cfg["services"]["mcp"]["environment"]["LGX_EMBED_PROVIDER"] == "none"
    deps = cfg["services"]["mcp"]["depends_on"]["db-load"]["condition"]
    assert deps == "service_completed_successfully"


def test_runtime_base_is_offline_by_construction() -> None:
    cfg = rendered(RUNTIME, ("cpu", "testui"))
    assert set(cfg["services"]) == {"db", "mcp", "llm-cpu", "lgx-streamlit"}
    for name, svc in cfg["services"].items():
        assert svc.get("pull_policy") == "never", name
        assert not svc.get("ports"), f"{name}: only the proxy may publish ports in the airgap"
        assert svc["image"].startswith("lgx.local/"), name
        if svc.get("read_only"):
            assert not svc.get("configs"), name
    for net in ("backend", "front"):
        assert cfg["networks"][net]["internal"] is True
    assert set(cfg["services"]["lgx-streamlit"]["networks"]) == {"backend", "front"}
    assert set(cfg["services"]["mcp"]["networks"]) == {"backend"}
