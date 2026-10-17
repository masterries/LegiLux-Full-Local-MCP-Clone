"""Headless Streamlit tests of the tester UI (``streamlit.testing.v1.AppTest``) and its offline hardening.

- The app and every page import and render with no MCP server and no network (sockets are restricted to
  loopback for the whole test), and with an injected fake MCP session + fake OpenAI-compatible server.
- ``ui/.streamlit/config.toml`` only uses option names that exist in streamlit 1.65.0, with the PLAN §9.3 values.
- A real ``streamlit run`` subprocess with the repo config makes **0** DNS lookups and 0 outbound TCP connects
  (Python audit hook as the sentinel; it also *blocks* them, so the negative control never reaches the network);
  without the config the same server looks up checkip.amazonaws.com, which proves the sentinel sees it; the
  ``lgx_ui.launch`` launcher removes the lookup even without any config.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
import tomllib
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from streamlit.testing.v1 import AppTest

from lgx_ui.app_support import UiDeps
from lgx_ui.llm import OpenAICompatChat
from lgx_ui.mcp_client import HealthStatus
from unit.test_ui_fakes import ART_TEXT, FakeOpenAIServer, FakeSession, session_factory, tool_call_message

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
APP = UI / "app.py"
PAGES = ["pages/1_Chat.py", "pages/2_Direct_search.py", "pages/3_Eval.py", "pages/4_Corpus.py"]
LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Any DNS lookup or connect to a non-loopback address fails the test (and is recorded)."""
    attempts: list[Any] = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in LOOPBACK and not host.startswith("127.") and not host.startswith("/"):
            attempts.append(("connect", address))
            raise OSError(f"test sandbox: connect to {address!r} blocked")
        return real_connect(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if name not in LOOPBACK and not (isinstance(name, str) and name.startswith("127.")):
            attempts.append(("getaddrinfo", name))
            raise OSError(f"test sandbox: lookup of {name!r} blocked")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield attempts
    assert attempts == [], f"network access attempted: {attempts}"


@pytest.fixture
def ui_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("LGX_", "OPENAI_", "STREAMLIT_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LGX_MCP_URL", f"http://127.0.0.1:{closed_port()}/mcp")
    monkeypatch.setenv("LGX_MCP_TIMEOUT_S", "3")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://llm-test:8080/v1")
    monkeypatch.setenv("LGX_LLM_MODEL", "fake-model")


def new_app(deps: UiDeps | None = None) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60)
    if deps is not None:
        at.session_state["lgx_deps"] = deps
    return at


def texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [e.value for e in at.error] + [w.value for w in at.warning] + [i.value for i in at.info]
    return "\n".join(str(p) for p in parts)


# ------------------------------------------------------------------------------------------ AppTest smoke


def test_app_and_every_page_render_without_a_server(ui_env: None, loopback_only: list[Any]) -> None:
    at = new_app().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("injoignable" in e.value for e in at.sidebar.error)
    assert "Information non officielle" in texts(at)
    seen = {}
    for page in PAGES:
        at.switch_page(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])
        seen[page] = texts(at)
    assert "Aucun outil disponible" in seen["pages/2_Direct_search.py"]
    assert "BACKEND_UNAVAILABLE" in seen["pages/4_Corpus.py"]
    at.switch_page("pages/3_Eval.py").run()
    assert len(at.dataframe[0].value) == 20, "the packaged dev G0 set is listed without a server"
    assert not [p for p in seen.values() if "Traceback" in p]


class _RecordingSidebar:
    """Stands in for ``st.sidebar``: records every widget call, returns a plausible value."""

    VALUES: ClassVar[dict[str, Any]] = {
        "button": False,
        "text_input": "x",
        "slider": 1,
        "number_input": 5,
        "radio": "auto",
        "selectbox": "auto",
        "toggle": False,
        "checkbox": True,
        "multiselect": [],
        "date_input": None,
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def widget(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return self.VALUES.get(name)

        return widget


def test_sidebar_widgets_keep_their_values_across_pages(ui_env: None) -> None:
    """Regression (e2e 2026-10-17, seen in a real browser; AppTest's switch_page does not reproduce it): streamlit
    1.65 drops a keyed widget's value on a page switch unless ``persist_state="session"``, and the first session after
    a server start sees a pseudo page switch on its second run. The sidebar's LLM base URL and model came back empty,
    which disabled the chat. Every keyed sidebar widget persists, takes its default from session_state (no ``value=``,
    which would warn once the value is re-saved) and is re-saved at the start of each run."""
    from types import SimpleNamespace

    from lgx_ui import app_support

    sidebar = _RecordingSidebar()
    fake_st = SimpleNamespace(session_state={}, sidebar=sidebar)
    fake_st.session_state["lgx_deps"] = UiDeps(
        open_session=session_factory(FakeSession()),
        make_llm=lambda cfg: OpenAICompatChat(cfg),
        readyz=lambda s: HealthStatus(True, True, 200, {"release_id": "r", "vector_status": "disabled:provider_none"}),
    )
    app_support.render_sidebar(fake_st)
    keyed = [(name, args, kw) for name, args, kw in sidebar.calls if "key" in kw and name != "button"]
    assert {kw["key"] for _, _, kw in keyed} == set(app_support.SIDEBAR_KEYS)
    assert [kw["key"] for _, _, kw in keyed if kw.get("persist_state") != "session"] == []
    assert [kw["key"] for _, _, kw in keyed if "value" in kw or "index" in kw] == []
    assert [kw["key"] for name, args, kw in keyed if name in ("slider", "number_input") and len(args) > 3] == []
    assert set(app_support.SIDEBAR_KEYS) <= set(fake_st.session_state)
    assert fake_st.session_state["llm_base_url"] == "http://llm-test:8080/v1"
    fake_st.session_state["llm_model"] = "kept-model"
    app_support.render_sidebar(fake_st)
    assert fake_st.session_state["llm_model"] == "kept-model"


def fake_deps(session: FakeSession, server: FakeOpenAIServer) -> UiDeps:
    return UiDeps(
        open_session=session_factory(session),
        make_llm=lambda cfg: OpenAICompatChat(cfg, http_client=server.http_client()),
        readyz=lambda s: HealthStatus(
            True, True, 200, {"release_id": "data-20261016-sample", "vector_status": "disabled:provider_none"}
        ),
    )


def test_chat_runs_the_tool_loop_with_sidebar_overrides(ui_env: None, loopback_only: list[Any]) -> None:
    session = FakeSession()
    server = FakeOpenAIServer(
        [
            tool_call_message(("c1", "search_legislation", {"query": "contrat de travail", "top_k": 15})),
            {
                "role": "assistant",
                "content": "Réponse [C1] « Le contrat de travail est le contrat par lequel une personne » "
                "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1",
            },
        ]
    )
    at = new_app(fake_deps(session, server)).run()
    assert not at.exception
    assert any("prêt" in s.value for s in at.sidebar.success)
    hybrid = at.sidebar.toggle(key="retrieval_hybrid")
    assert hybrid.proto.disabled, "hybrid is greyed out while vector_status != ok"
    assert "hybride indisponible : vecteurs désactivés (disabled:provider_none)" in texts(at)

    at.sidebar.multiselect(key="f_doc_types").set_value(["CODE"])
    at.sidebar.slider(key="f_top_k").set_value(3)
    at.run()
    at.chat_input[0].set_value("Qu'est-ce qu'un contrat de travail ?").run()
    assert not at.exception, [e.value for e in at.exception]

    search_calls = [args for name, args in session.calls if name == "search_legislation"]
    assert search_calls == [
        {
            "query": "contrat de travail",
            "top_k": 3,
            "filters": {"in_force_only": True, "doc_types": ["CODE"]},
            "retrieval_mode": "auto",
        }
    ]
    assert session.prompt_requests == [("legal_answer", {"lang": "fr"})]
    assert server.requests[0]["messages"][0]["content"] == "PROMPT SERVEUR legal_answer"
    body = texts(at)
    assert "Réponse [C1]" in body
    assert "[C1] Art. L. 121-1, Code du travail" in body and "consolidé" in body
    assert "TEXTE NON CONSOLIDÉ" in body and "W_NOT_CONSOLIDATED" in body
    assert "outils : search_legislation ×1" in body and "vecteurs désactivés : provider=none" in body
    assert "**V2** ok" in body and "**V3** ok" in body
    assert "CC BY 4.0" in body
    labels = [e.label for e in at.expander]
    assert any(label.startswith("Base légale — 2 source(s)") for label in labels)
    assert any(label.startswith("Appels d'outils — 1") for label in labels)


def test_direct_search_eval_and_corpus_with_fakes(ui_env: None, loopback_only: list[Any]) -> None:
    session = FakeSession()
    at = new_app(fake_deps(session, FakeOpenAIServer())).run()

    at.switch_page("pages/2_Direct_search.py").run()
    assert not at.exception
    at.selectbox(key="ds_tool").set_value("get_article").run()
    at.text_input(key="ds:get_article:citation").set_value("art. L. 121-1 C. trav.")
    next(b for b in at.button if b.label == "Appeler l'outil").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert session.calls[-1] == ("get_article", {"citation": "art. L. 121-1 C. trav."})
    assert ART_TEXT in [t.value for t in at.text]
    assert "Art. L. 121-1, Code du travail (version consolidée applicable au 26/07/2026)" in texts(at)

    at.switch_page("pages/3_Eval.py").run()
    at.button(key="eval_run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    metric = next(m for m in at.metric if m.label == "G0")
    assert metric.value.endswith("/20")
    assert len([c for c in session.calls if c[0] == "about_corpus"]) >= 1

    at.switch_page("pages/4_Corpus.py").run()
    assert not at.exception
    values = {m.label: m.value for m in at.metric}
    assert values["Données au (T)"] == "2026-10-16" and values["Vecteurs"] == "désactivés"
    assert "vecteurs désactivés : provider=none" in texts(at), "the reason stays visible under the metrics"


def test_external_llm_banner_and_any_free_typed_url(ui_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = fake_deps(FakeSession(), FakeOpenAIServer())
    at = new_app(deps).run()
    assert not any("LLM externe" in w.value for w in at.warning)  # ui_env: an internal endpoint
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.mistral.ai/v1")
    at = new_app(deps).run()
    assert any("LLM externe (api.mistral.ai)" in w.value for w in at.warning)
    at.sidebar.text_input(key="llm_base_url").set_value("https://other.example.org/v1").run()
    assert not at.exception and not at.sidebar.error  # any OpenAI-compatible URL may be used …
    assert "la clé API configurée n'est pas envoyée" in " ".join(c.value for c in at.sidebar.caption)  # … keyless


# ------------------------------------------------------------------------------------------ offline config


def test_streamlit_config_uses_existing_options_with_plan_values() -> None:
    from streamlit import config as st_config

    cfg = tomllib.loads((UI / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    known = st_config._config_options_template  # type: ignore[attr-defined]
    flat = {f"{section}.{key}": value for section, opts in cfg.items() for key, value in opts.items()}
    unknown = [k for k in flat if k not in known]
    assert unknown == [], f"options not in streamlit 1.65.0: {unknown}"
    assert flat["browser.gatherUsageStats"] is False
    assert flat["server.headless"] is True
    assert flat["logger.hideWelcomeMessage"] is True
    assert flat["browser.serverAddress"]
    assert flat["server.fileWatcherType"] == "none"
    assert flat["client.toolbarMode"] == "minimal"
    assert flat["client.showErrorDetails"] == "none"
    assert flat["server.enableStaticServing"] is False
    assert "theme" not in cfg, "no theme fonts (external URLs)"


def test_ui_code_has_no_remote_assets_or_maps() -> None:
    sources = [
        p.read_text(encoding="utf-8") for p in [APP, *(UI / "pages").glob("*.py"), *(UI / "lgx_ui").glob("*.py")]
    ]
    joined = "\n".join(sources)
    for banned in ("st.map(", "pydeck", "st.image(", "fonts.googleapis", "cdn.jsdelivr", "unpkg.com"):
        assert banned not in joined, banned


SENTINEL = textwrap.dedent(
    """
    import os, socket, sys
    LOOP = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::", "", None}
    def hook(event, args):
        if event == "socket.getaddrinfo":
            host = args[0].decode() if isinstance(args[0], bytes) else args[0]
            if host not in LOOP and not str(host).startswith("127."):
                sys.stderr.write(f"LGX-SENTINEL lookup {host}\\n"); sys.stderr.flush()
                raise OSError("blocked by the test sentinel")
        elif event == "socket.connect":
            sock, addr = args
            host = addr[0] if isinstance(addr, tuple) else addr
            if sock.type == socket.SOCK_STREAM and isinstance(host, str) and host not in LOOP \\
                    and not host.startswith("127.") and not host.startswith("/"):
                sys.stderr.write(f"LGX-SENTINEL connect {addr}\\n"); sys.stderr.flush()
                raise OSError("blocked by the test sentinel")
    sys.addaudithook(hook)
    if sys.argv[1] == "--launcher":
        from lgx_ui.launch import main
        main(sys.argv[2:])
    else:
        from streamlit.web import cli
        cli.main(sys.argv[1:], prog_name="streamlit")
    """
)


def run_streamlit(argv: list[str], cwd: Path, home: Path) -> str:
    """Start a real Streamlit server, wait for /_stcore/health, load the page shell, stop; return stderr."""
    port = closed_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STREAMLIT_", "LGX_", "OPENAI_"))}
    env.update(HOME=str(home), LGX_MCP_URL=f"http://127.0.0.1:{closed_port()}/mcp", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-c", SENTINEL, *argv, "--server.port", str(port)],
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError as exc:
                if proc.poll() is not None or time.monotonic() > deadline:
                    stderr = proc.stderr.read() if proc.stderr else ""
                    raise AssertionError(f"streamlit did not start: {stderr}") from exc
                time.sleep(0.3)
        for path in ("/", "/_stcore/host-config"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                assert r.status == 200
        time.sleep(1.5)  # the startup banner runs right after the server is up
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    return err or ""


def test_streamlit_server_makes_no_lookups_with_the_repo_config(tmp_path: Path) -> None:
    # 1) repo config (cwd = ui/ so .streamlit/config.toml applies): no lookup, no outbound connect
    err = run_streamlit(["run", "app.py", "--server.address", "127.0.0.1"], UI, tmp_path)
    assert "LGX-SENTINEL" not in err, err

    # 2) negative control: same server without any config -> the checkip.amazonaws.com lookup is seen (and blocked)
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "app.py").write_text("import streamlit as st\nst.write('x')\n")
    err = run_streamlit(["run", "app.py", "--server.headless", "true"], bare, tmp_path)
    assert "LGX-SENTINEL lookup checkip.amazonaws.com" in err, err

    # 3) the launcher alone (no config file, no STREAMLIT_* variables) also prevents it
    launcher_dir = tmp_path / "launcher"
    launcher_dir.mkdir()
    (launcher_dir / "app.py").write_text("import streamlit as st\nst.write('x')\n")
    err = run_streamlit(["--launcher"], launcher_dir, tmp_path)
    assert "LGX-SENTINEL" not in err, err


def test_harden_disables_the_external_ip_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from streamlit import net_util

    from lgx_ui import launch

    monkeypatch.setattr(net_util, "get_external_ip", net_util.get_external_ip)
    monkeypatch.setattr(net_util, "_make_blocking_http_get", lambda *a, **k: pytest.fail("network lookup attempted"))
    for key in [*launch.OFFLINE_ENV, "STREAMLIT_BROWSER_SERVER_ADDRESS"]:
        monkeypatch.setenv(key, "x")  # setenv records the original state, so harden()'s values are undone later
        monkeypatch.delenv(key)
    monkeypatch.setenv("STREAMLIT_SERVER_HEADLESS", "false")  # an explicit value wins
    launch.harden()
    assert net_util.get_external_ip() is None
    assert os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] == "false"
    assert os.environ["STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE"] == "true"
    assert os.environ["STREAMLIT_SERVER_HEADLESS"] == "false"


def test_tool_list_recovers_after_the_server_comes_up(ui_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from lgx_ui import app_support

    class Down:
        async def __aenter__(self) -> Any:
            from lgx_ui.mcp_client import McpUnavailableError

            raise McpUnavailableError("MCP down")

        async def __aexit__(self, *exc: object) -> None:
            return None

    state: dict[str, Any] = {}
    fake_st = type("St", (), {"session_state": state})()
    session = FakeSession()
    state["lgx_deps"] = UiDeps(open_session=lambda s: Down(), make_llm=lambda c: None, readyz=lambda s: None)  # type: ignore[arg-type,return-value]
    assert app_support.tools(fake_st) == [] and "MCP down" in (app_support.tools_error(fake_st) or "")
    state["lgx_deps"] = fake_deps(session, FakeOpenAIServer())
    assert app_support.tools(fake_st) == [], "no immediate retry storm"
    state["lgx_tools_at"] -= app_support.HEALTH_TTL_S + 1
    assert [t.name for t in app_support.tools(fake_st)][:1] == ["search_legislation"]
    assert app_support.tools_error(fake_st) is None
