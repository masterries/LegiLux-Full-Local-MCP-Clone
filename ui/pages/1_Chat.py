"""Chat: any OpenAI-compatible model drives the MCP tools; every answer shows its tool calls and sources."""

from __future__ import annotations

import streamlit as st

from lgx_ui import app_support as ui
from lgx_ui import render
from lgx_ui.verify_light import verify_turn

sb: ui.SidebarState = st.session_state["lgx_sidebar"]
st.header("Chat")
if sb.llm is None:
    st.info(
        "Aucun LLM configuré (base URL et modèle dans la barre latérale). La recherche directe fonctionne sans LLM."
    )
else:
    st.caption(f"LLM : {sb.llm.model} @ {sb.llm.base_url} · mode : {sb.chat_mode} · itérations max {sb.max_iterations}")

history: list[dict] = st.session_state.setdefault("lgx_chat", [])
if history and st.button("Nouvelle conversation", key="chat_reset"):
    history.clear()
    st.rerun()

for entry in history:
    with st.chat_message(entry["role"]):
        if entry["role"] == "user":
            st.markdown(entry["content"])
        else:
            render.show_turn(st, entry["turn"], vector_status=sb.vector_status, verifier=entry.get("verifier"))

question = st.chat_input("Votre question (de préférence en termes juridiques français)", disabled=sb.llm is None)
if question:
    prior = list(history)
    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Recherche et rédaction …"):
            turn = ui.run_chat(st, sb, question, prior)
        verifier = verify_turn(turn.answer, turn.outcomes)
        render.show_turn(st, turn, vector_status=sb.vector_status, verifier=verifier)
    history.append({"role": "assistant", "content": turn.answer, "turn": turn, "verifier": verifier})
