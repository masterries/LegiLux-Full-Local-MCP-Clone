"""lgx-streamlit — tester UI of the LegiLux local clone (PLAN §9.3). Entry point: ``streamlit run ui/app.py``.

The sidebar (LLM endpoint, retrieval mode, filters) is rendered here on every run, so its values persist across
the four pages; the pages read them from ``st.session_state["lgx_sidebar"]``. The UI is read-only and talks only
to the MCP server and the configured LLM endpoint.
"""

from __future__ import annotations

import streamlit as st

from lgx_ui import app_support as ui

st.set_page_config(
    page_title="LegiLux — testeur",
    layout="wide",
    menu_items={"Get help": None, "Report a bug": None, "About": "LegiLux local clone — tester UI (unofficial)."},
)

ui.page_banner(st)
st.session_state["lgx_sidebar"] = ui.render_sidebar(st)

pages = [
    st.Page("pages/1_Chat.py", title="Chat", url_path="chat", default=True),
    st.Page("pages/2_Direct_search.py", title="Recherche directe", url_path="direct-search"),
    st.Page("pages/3_Eval.py", title="Évaluation (G0)", url_path="eval"),
    st.Page("pages/4_Corpus.py", title="Corpus", url_path="corpus"),
]
st.navigation(pages, position="top").run()
