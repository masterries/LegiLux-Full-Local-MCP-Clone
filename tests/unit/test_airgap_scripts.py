"""Airgap export/import scripts (docs/ANLEITUNG.md §6-§7): syntax and the offline `verify` path.

The full export → install → update-data → rollback cycle needs Docker and root and was run by hand (ANLEITUNG.md,
Anhang A); here only what runs anywhere: `bash -n`, `--help`, and `airgap_import.sh verify` on a tiny fake bundle
(checksums ok, one flipped byte, one file not covered by SHA256SUMS).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "scripts" / "airgap_export.sh"
IMPORT = ROOT / "scripts" / "airgap_import.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("sha256sum") is None, reason="needs bash")


def run(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("script", [EXPORT, IMPORT])
def test_syntax_and_help(script: Path) -> None:
    assert run("bash", "-n", script).returncode == 0
    out = run("bash", script, "--help")
    assert out.returncode == 0 and "ANLEITUNG.md" in out.stdout


def fake_bundle(root: Path) -> Path:
    b = root / "legilux-airgap-2026.10.0-data-20261016-sample"
    files = {
        "MANIFEST.env": "LGX_BUNDLE_FORMAT=legilux-airgap/1\nLGX_PLATFORM=2026.10.0\n"
        "LGX_RELEASE_ID=data-20261016-sample\nLGX_DATA_STEM=data-2026-10-16-sample\nLGX_WITH_RERANK=0\n"
        "LGX_RELEASE_EMBEDDING=none\n",
        "MANIFEST.json": "{}\n",
        "images/IMAGES.tsv": "lgx-mcp\tlgx.local/lgx-mcp:2026.10.0\tsha256:00\timages/lgx-mcp.tar\n",
        "images/lgx-mcp.tar": "not a real image\n",
        "data/data-2026-10-16-sample.dump": "dump\n",
    }
    for rel, text in files.items():
        (b / rel).parent.mkdir(parents=True, exist_ok=True)
        (b / rel).write_text(text, encoding="utf-8")
    sums = "".join(f"{hashlib.sha256((b / r).read_bytes()).hexdigest()}  {r}\n" for r in sorted(files))
    (b / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return b


def test_verify_accepts_an_intact_bundle_and_refuses_tampering(tmp_path: Path) -> None:
    b = fake_bundle(tmp_path)
    ok = run("bash", IMPORT, "verify", b)
    assert ok.returncode == 0, ok.stderr
    assert "bundle ok: platform 2026.10.0, data data-20261016-sample" in ok.stderr

    (b / "data" / "data-2026-10-16-sample.dump").write_text("dumq\n", encoding="utf-8")  # one changed byte
    bad = run("bash", IMPORT, "verify", b)
    assert bad.returncode == 1 and "checksum mismatch" in bad.stderr

    (b / "data" / "data-2026-10-16-sample.dump").write_text("dump\n", encoding="utf-8")
    (b / "extra.txt").write_text("planted\n", encoding="utf-8")
    extra = run("bash", IMPORT, "verify", b)
    assert extra.returncode == 1 and "not covered by SHA256SUMS: extra.txt" in extra.stderr


def test_import_needs_a_command_and_a_bundle() -> None:
    assert run("bash", IMPORT, "install").returncode == 2
    assert run("bash", IMPORT, "frobnicate").returncode == 2
