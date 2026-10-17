"""Direct search: a form for each MCP tool (generated from its inputSchema), no LLM involved."""

from __future__ import annotations

import json

import streamlit as st

from lgx_ui import app_support as ui
from lgx_ui import render

sb: ui.SidebarState = st.session_state["lgx_sidebar"]
st.header("Recherche directe (sans LLM)")
specs = ui.tools(st)
if not specs:
    st.warning(f"Aucun outil disponible : {ui.tools_error(st) or 'tools/list vide'}")
    st.stop()

names = [t.name for t in specs]
labels = {t.name: f"{t.name} — {t.title}" if t.title else t.name for t in specs}
name = st.selectbox("Outil", names, format_func=lambda n: labels[n], key="ds_tool")
spec = ui.tool_spec(specs, name)
assert spec is not None
if spec.description:
    st.caption(spec.description)

with st.form(f"ds_form_{name}"):
    args = ui.schema_form(st, spec, ui.sidebar_defaults(spec, sb.overrides))
    raw = st.text_area(
        "Arguments JSON (facultatif : remplace le formulaire)", value="", key=f"ds_raw_{name}", height=80
    )
    submitted = st.form_submit_button("Appeler l'outil")

if submitted:
    if raw.strip():
        try:
            args = json.loads(raw)
        except ValueError as exc:
            st.error(f"JSON invalide : {exc}")
            st.stop()
    st.caption("arguments envoyés")
    st.json(args, expanded=False)
    with st.spinner(f"{name} …"):
        outcome = ui.call_tool(st, name, args)
    st.session_state["ds_last"] = outcome

last = st.session_state.get("ds_last")
if last is not None:
    render.show_outcome(st, last, vector_status=sb.vector_status)
