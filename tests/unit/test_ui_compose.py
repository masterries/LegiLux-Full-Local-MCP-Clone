"""Compose lint for the dev stack and the runtime base (PLAN §10.3). No LLM runtime is bundled (2026-10-17).

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


def test_dev_services_and_the_external_llm_endpoint() -> None:
    default = rendered(DEV)
    assert set(default["services"]) == {"db", "db-load", "mcp", "lgx-streamlit"}
    assert set(rendered(DEV, ("rerank",))["services"]) == {"db", "db-load", "mcp", "lgx-streamlit", "rerank"}
    ui = default["services"]["lgx-streamlit"]
    env = ui["environment"]
    assert env["OPENAI_BASE_URL"] == "https://api.mistral.ai/v1"  # the user's Mistral endpoint by default
    assert env["OPENAI_API_KEY_FILE"] == "/run/secrets/llm_api_key" and "OPENAI_API_KEY" not in env
    assert env["LGX_LLM_MODEL"] and env["NO_PROXY"] != "*"
    assert set(ui["networks"]) == {"backend", "edge", "llm-egress"}  # the external LLM needs a route
    assert default["networks"]["llm-egress"].get("internal") is not True


def test_dev_ports_are_loopback_only_and_backend_is_internal() -> None:
    cfg = rendered(DEV, ("rerank",))
    assert cfg["networks"]["backend"]["internal"] is True
    published = {name: s.get("ports", []) for name, s in cfg["services"].items() if s.get("ports")}
    assert set(published) == {"mcp", "lgx-streamlit", "rerank"}
    for name, ports in published.items():
        assert all(p["host_ip"] == "127.0.0.1" for p in ports), name
    for name in ("db", "db-load"):
        assert set(cfg["services"][name]["networks"]) == {"backend"}, name
    assert set(cfg["services"]["mcp"]["networks"]) == {"backend", "edge"}


def test_dev_hardening_and_no_secret_files_required() -> None:
    cfg = rendered(DEV)
    for name, svc in cfg["services"].items():
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
        if svc.get("read_only"):
            # compose copies inline-content configs into the container: impossible on a read-only rootfs
            assert not svc.get("configs"), f"{name}: read_only service with configs"
            assert svc.get("user") == "10001:10001", name
    # API keys are files only; by default /dev/null (= no key), so the dev stack starts without any secret file
    assert {k: v["file"] for k, v in cfg["secrets"].items() if k != "build_ca_bundle"} == {
        "llm_api_key": "/dev/null",
        "embed_api_key": "/dev/null",
    }
    ui = cfg["services"]["lgx-streamlit"]["environment"]
    assert ui["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] == "false"
    assert ui["STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE"] == "true"
    assert ui["STREAMLIT_BROWSER_SERVER_ADDRESS"]
    assert ui["LGX_MCP_TOKEN"] == cfg["services"]["mcp"]["environment"]["LGX_MCP_TOKEN"]
    mcp = cfg["services"]["mcp"]["environment"]
    assert mcp["LGX_EMBED_PROVIDER"] == "none"  # user decision: embeddings off
    assert (mcp["LGX_EMBED_MAX_CONCURRENCY"], mcp["LGX_EMBED_COOLDOWN_S"]) == ("5", "20")
    deps = cfg["services"]["mcp"]["depends_on"]["db-load"]["condition"]
    assert deps == "service_completed_successfully"


def test_runtime_base_is_offline_by_construction() -> None:
    cfg = rendered(RUNTIME, ("testui",))
    assert set(cfg["services"]) == {"db", "mcp", "lgx-streamlit"}  # no LLM runtime
    for name, svc in cfg["services"].items():
        assert svc.get("pull_policy") == "never", name
        assert not svc.get("ports"), f"{name}: only the proxy may publish ports in the airgap"
        assert svc["image"].startswith("lgx.local/"), name
        if svc.get("read_only"):
            assert not svc.get("configs"), name
    for net in ("backend", "front"):
        assert cfg["networks"][net]["internal"] is True
    # the only routed network of these services leads to the on-prem Mistral endpoint (DOCKER-USER allowlist)
    assert cfg["networks"]["enclave"]["driver_opts"]["com.docker.network.bridge.name"] == "br-lgx-encl"
    assert set(cfg["services"]["lgx-streamlit"]["networks"]) == {"backend", "front", "enclave"}
    assert set(cfg["services"]["mcp"]["networks"]) == {"backend", "enclave"}
    assert set(cfg["services"]["db"]["networks"]) == {"backend"}
    ui = cfg["services"]["lgx-streamlit"]
    assert ui["environment"]["OPENAI_API_KEY_FILE"] == "/run/secrets/llm_api_key"
    assert ui["extra_hosts"] == cfg["services"]["mcp"]["extra_hosts"]  # the domain resolves without upstream DNS
    assert cfg["services"]["mcp"]["environment"]["LGX_EMBED_PROVIDER"] == "none"


def test_dev_edge_network_has_no_outbound_nat() -> None:
    """Review 2026-10-17: lgx-streamlit / mcp reached the internet through `edge` (default route, NAT)."""
    cfg = rendered(DEV)
    opts = cfg["networks"]["edge"].get("driver_opts") or {}
    assert opts.get("com.docker.network.bridge.enable_ip_masquerade") == "false"
    assert "llm-egress" not in cfg["services"]["mcp"]["networks"]


def test_runtime_secret_ownership_table_matches_the_services() -> None:
    """Review 2026-10-17: bind-mounted secrets keep host owner/mode; uid-10001 services cannot read root 0600."""
    import re

    text = RUNTIME.read_text(encoding="utf-8")
    table = {
        m.group(1): (m.group(2), m.group(3))
        for m in re.finditer(r"^#   (\w+)\s+(\S+:\S+)\s+(0[0-7]{3})\s+\S", text, re.MULTILINE)
    }
    cfg = rendered(RUNTIME, ("testui",))
    assert set(table) == set(cfg["secrets"])
    for name, svc in cfg["services"].items():
        for sec in svc.get("secrets", []):
            secret = sec["source"] if isinstance(sec, dict) else sec
            owner, mode = table[secret]
            if svc.get("user") == "10001:10001":
                uid, gid = owner.split(":")
                readable = (uid == "10001" and mode[1] in "46") or (gid == "10001" and mode[2] in "46")
                assert readable, f"{name}: secret {secret} ({owner} {mode}) is not readable by uid 10001"


def test_rerank_is_an_opt_in_offline_internal_profile() -> None:
    """M2 reranker (PLAN §8.3, Appendix C): profile ``rerank`` only, offline, read-only model mount, no route out."""
    assert "rerank" not in rendered(DEV)["services"] and "rerank" not in rendered(RUNTIME)["services"]
    dev = rendered(DEV, ("rerank",))["services"]["rerank"]
    assert "@sha256:" in dev["image"] and dev["read_only"] is True
    assert all(p["host_ip"] == "127.0.0.1" for p in dev["ports"])
    runtime = rendered(RUNTIME, ("rerank",))["services"]["rerank"]
    assert set(runtime["networks"]) == {"backend"} and not runtime.get("ports")
    for svc in (dev, runtime):
        assert svc["profiles"] == ["rerank"] and svc["environment"]["HF_HUB_OFFLINE"] == "1"
        assert any(v["target"] == "/models/hf" and v.get("read_only") for v in svc["volumes"])
        assert "--auto-truncate" in svc["command"]


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_dev_down_stops_every_profile() -> None:
    """`make dev-down` covers every profile (before M2 it left the rerank container and the networks behind)."""
    out = subprocess.run(["make", "-n", "-s", "dev-down"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
    assert "--profile rerank" in out
