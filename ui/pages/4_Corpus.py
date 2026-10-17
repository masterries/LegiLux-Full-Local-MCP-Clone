"""Corpus: about_corpus (T, age, counts per label, gaps, embedding status) and the /readyz body."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from lgx_ui import app_support as ui
from lgx_ui import render

st.header("Corpus")
if st.button("Actualiser", key="corpus_refresh"):
    ui.corpus(st, force=True)
    ui.health(st, force=True)
outcome = ui.corpus(st)
status = ui.health(st)

if outcome.is_error:
    err = outcome.error or {}
    st.error(f"about_corpus : {err.get('code')}: {err.get('message')}")
else:
    sc = outcome.structured if isinstance(outcome.structured, Mapping) else {}
    snap = sc.get("snapshot") if isinstance(sc.get("snapshot"), Mapping) else {}
    emb = sc.get("embedding") if isinstance(sc.get("embedding"), Mapping) else {}
    cols = st.columns(4)
    cols[0].metric("Données au (T)", str(snap.get("sync_date", "?")))
    cols[1].metric("Âge (jours)", str(snap.get("age_days", "?")))
    cols[2].metric("Horloge", "ok" if snap.get("clock_ok") else "à vérifier" if "clock_ok" in snap else "?")
    vstatus = str(emb.get("vector_status", "?"))  # "disabled:provider_none" is cut off in a metric; reason below
    cols[3].metric("Vecteurs", "désactivés" if vstatus.startswith("disabled") else vstatus)
    st.caption(
        f"release {snap.get('release_id', '?')} · horizon {snap.get('horizon', '?')} · "
        f"niveau d'obsolescence {snap.get('staleness_level', '?')} · "
        + render.vector_status_line(None, emb.get("vector_status"))
    )
    counts = sc.get("counts") if isinstance(sc.get("counts"), Mapping) else {}
    by_basis = counts.get("by_text_basis") if isinstance(counts.get("by_text_basis"), Mapping) else {}
    if by_basis:
        st.subheader("Textes par base (text_basis)")
        st.dataframe(
            [
                {"text_basis": k, "libellé": render.TEXT_BASIS_BADGES.get(k, (k, ""))[0], "actes": v}
                for k, v in by_basis.items()
            ],
            hide_index=True,
            width="stretch",
        )
    other = {k: v for k, v in counts.items() if k != "by_text_basis"}
    if other:
        st.json(other, expanded=False)
    for section, title in (
        ("known_gaps", "Lacunes connues"),
        ("scope", "Périmètre et exclusions"),
        ("embedding", "Recherche vectorielle"),
        ("retrieval_defaults", "Recherche : réglages par défaut"),
        ("licences", "Licences"),
        ("versions", "Versions"),
    ):
        if section in sc:
            with st.expander(title, expanded=section in ("known_gaps", "embedding")):
                st.json(sc[section])
    render.show_envelope_footer(st, render.build_envelope([outcome]))
    with st.expander("JSON brut (about_corpus)"):
        st.json(sc, expanded=False)

st.subheader("État du serveur (/readyz)")
if status.reachable:
    st.caption(f"HTTP {status.status_code} · {status.latency_s * 1000:.0f} ms")
    st.json(status.body)
else:
    st.error(f"/readyz injoignable : {status.error}")
