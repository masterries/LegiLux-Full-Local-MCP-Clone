"""Offline launcher for lgx-streamlit: ``python -m lgx_ui.launch [streamlit run options]``.

The primary controls are ``.streamlit/config.toml`` and the ``STREAMLIT_*`` environment (PLAN §9.3). This
launcher adds defence in depth for the one server-side call those settings do not fully cover in 1.65.0:
``streamlit.net_util.get_external_ip`` (a GET to ``checkip.amazonaws.com``). Besides the startup banner it is
also reached by the websocket CORS check when a browser origin matches neither the Host header, localhost,
``browser.serverAddress`` nor the internal IP. :func:`harden` replaces it with a function that returns
``None`` without any network access.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

OFFLINE_ENV = {
    "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    "STREAMLIT_SERVER_HEADLESS": "true",
    "STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE": "true",
    "STREAMLIT_SERVER_FILE_WATCHER_TYPE": "none",
    "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false",
}


def _no_external_ip() -> None:
    return None


def harden() -> None:
    """Disable the external-IP lookup and fill the offline environment defaults (explicit values win)."""
    for key, value in OFFLINE_ENV.items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("STREAMLIT_BROWSER_SERVER_ADDRESS", os.environ.get("LGX_HOSTNAME", "localhost"))
    from streamlit import net_util

    net_util.get_external_ip = _no_external_ip  # type: ignore[assignment]


def default_app() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (Path.cwd() / "app.py", here.parent / "app.py", Path("/app/app.py")):
        if candidate.is_file():
            return candidate
    raise SystemExit("lgx_ui.launch: app.py not found (run from ui/ or /app)")


def main(argv: Sequence[str] | None = None) -> None:
    harden()
    from streamlit.web import cli

    args = list(sys.argv[1:] if argv is None else argv)
    app = str(default_app())
    cli.main(["run", app, *args], prog_name="streamlit")


if __name__ == "__main__":
    main()
