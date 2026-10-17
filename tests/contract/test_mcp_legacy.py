"""The real handshake-era client ``mcp==1.27.2`` (Open WebUI 0.12.3) against the server, in an isolated env.

The 1.x SDK conflicts with the locked 2.3.0, so it never enters the project lock: ``uv run --isolated
--no-project --with mcp==1.27.2`` runs ``legacy_client_check.py``. Offline by default (``UV_OFFLINE=1``, the
package must already be in the uv cache); ``LGX_RUN_LEGACY_CLIENT=1`` allows uv to download it once.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from contract.conftest import LiveServer

CHECK = Path(__file__).with_name("legacy_client_check.py")


@pytest.mark.legacy_client
def test_mcp_1_27_2_client(live: LiveServer, tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")
    env = dict(os.environ)
    if env.get("LGX_RUN_LEGACY_CLIENT") != "1":
        env["UV_OFFLINE"] = "1"
    cmd = [uv, "run", "--isolated", "--no-project", "--with", "mcp==1.27.2", "python", str(CHECK), live.url, live.token]
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=180)
    if out.returncode != 0 and not out.stdout.strip():
        if "offline" in out.stderr.lower() or "network" in out.stderr.lower() or "cache" in out.stderr.lower():
            pytest.skip("mcp==1.27.2 is not in the uv cache (set LGX_RUN_LEGACY_CLIENT=1 once to download it)")
        raise AssertionError(out.stderr[-2000:])
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report["mcp_version"] == "1.27.2"
    assert report["protocol_version"] in ("2025-06-18", "2025-11-25")
    assert report["ok"], report
    assert report["prompts"] == ["legal_answer"]
