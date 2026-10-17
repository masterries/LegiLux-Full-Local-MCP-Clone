"""Eval: the G0 smoke set (deterministic tool calls, no LLM); results download as JSON."""

from __future__ import annotations

import json

import streamlit as st

from lgx_ui import app_support as ui
from lgx_ui import evalg0
from lgx_ui.mcp_client import McpUnavailableError, run_sync

st.header("Évaluation — G0 (smoke)")
cfg = ui.settings(st)
items, source = evalg0.load_gold(cfg.eval_dir)
st.caption(f"Jeu : {source} · {len(items)} élément(s). G1–G4 (résolution, recherche, pièges, bout en bout) : M2/M4.")
st.caption(
    "Le serveur limite à 30 appels/min par client (PLAN §8.8) et toute l'interface compte comme un seul client : "
    "G0 en utilise 20 ; un appel limité est rejoué une fois après le délai indiqué par le serveur."
)
st.dataframe(
    [
        {
            "id": i.id,
            "outil": i.tool,
            "description": i.description,
            "arguments": json.dumps(i.arguments, ensure_ascii=False),
        }
        for i in items
    ],
    hide_index=True,
    width="stretch",
)

if st.button("Lancer G0", key="eval_run", disabled=not items):

    async def go() -> list[evalg0.ItemResult]:
        async with ui.deps(st).open_session(cfg) as session:
            return await evalg0.run_g0(session, items)

    with st.spinner("G0 …"):
        try:
            st.session_state["eval_results"] = run_sync(go())
            st.session_state["eval_error"] = None
        except McpUnavailableError as exc:
            st.session_state["eval_results"], st.session_state["eval_error"] = None, str(exc)

if st.session_state.get("eval_error"):
    st.error(st.session_state["eval_error"])
results = st.session_state.get("eval_results")
if results:
    s = evalg0.summary(results)
    cols = st.columns(3)
    cols[0].metric("G0", f"{s['passed']}/{s['total']}")
    cols[1].metric("latence p50", f"{s['latency_p50_s'] * 1000:.0f} ms")
    cols[2].metric("latence max", f"{s['latency_max_s'] * 1000:.0f} ms")
    (st.success if s["gate_ok"] else st.warning)(f"porte {s['gate']} : {'ok' if s['gate_ok'] else 'échec'}")
    st.dataframe(
        [
            {
                "id": r.item.id,
                "ok": r.ok,
                "outil": r.item.tool,
                "isError": r.is_error,
                "latence (ms)": round(r.latency_s * 1000),
                "échecs": "; ".join(
                    f"{c.path} {c.op} {c.expected!r} (obtenu {c.actual!r})"[:200] for c in r.checks if not c.ok
                )
                or (r.error or ""),
            }
            for r in results
        ],
        hide_index=True,
        width="stretch",
    )
    payload = {"set": "G0", "source": source, "summary": s, "results": [r.to_dict() for r in results]}
    st.download_button(
        "Télécharger les résultats (JSON)",
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        file_name="g0-results.json",
        mime="application/json",
        key="eval_download",
    )
