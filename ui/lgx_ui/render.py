"""Rendering of answers, sources, warnings, tool traces and latencies (PLAN §9.3 "Next to every answer").

The ``build_*`` / ``*_line`` functions are pure (tested without Streamlit); the ``show_*`` functions take the
``streamlit`` module (or anything with the same API) and only lay the data out. Tool output is read
generically — any dict carrying ``act_eli`` plus ``version_eli`` or ``text_basis`` is a source — so new or
changed tool fields degrade to the raw JSON view instead of breaking the page.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lgx_ui.mcp_client import ToolOutcome
from lgx_ui.prompts import ATTRIBUTION_FALLBACK_FR, DISCLAIMER_FALLBACK_FR
from lgx_ui.tools_bridge import ToolTrace, TurnResult

# text_basis → (French label, Streamlit colour name)
TEXT_BASIS_BADGES: dict[str, tuple[str, str]] = {
    "consolidated": ("consolidé", "green"),
    "stub_fallback": ("consolidation annoncée sans texte — texte de repli", "orange"),
    "stub_no_text": ("consolidation annoncée sans texte", "orange"),
    "original_never_amended": ("texte original (jamais modifié)", "blue"),
    "original_possibly_outdated": ("TEXTE NON CONSOLIDÉ (texte original, peut être dépassé)", "red"),
    "metadata_only": ("métadonnées seulement", "gray"),
}
NOT_LOADED_LABEL = "texte non chargé dans cette base (échantillon)"

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}

# Short French names (PLAN §8.5) for warning codes that arrive without a message (e.g. on a citation).
WARNING_SHORT_FR: dict[str, str] = {
    "W_NOT_OFFICIAL": "information non officielle (loi du 23 décembre 2016, art. 5)",
    "W_SNAPSHOT": "données arrêtées à la date de synchronisation",
    "W_DATA_STALE": "données anciennes",
    "W_CLOCK_SUSPECT": "horloge du serveur incohérente",
    "W_NOT_CONSOLIDATED": "texte non consolidé : vérifier les actes modificatifs",
    "W_STUB": "consolidation annoncée sans texte",
    "W_CONSO_LAGGING": "consolidation en retard",
    "W_CONSO_DISCONTINUED": "consolidation non poursuivie",
    "W_REPEALED": "acte abrogé à la date demandée",
    "W_NOT_YET_IN_FORCE": "pas encore en vigueur",
    "W_SUNSET": "cessera d'être en vigueur",
    "W_FUTURE_VERSION": "nouvelle rédaction annoncée",
    "W_ARTICLE_REPEALED": "article abrogé",
    "W_BASE_LEGALE_ABROGEE": "bases légales abrogées",
    "W_TEMPORARY_ACT": "acte temporaire",
    "W_PDF_EXTRACTED": "texte extrait d'un PDF",
    "W_SUPERSEDED_BY_CODE": "acte intégré dans un code",
    "W_PARTIAL_REPEAL_UNKNOWN": "« en vigueur » = non connu comme abrogé",
    "W_DERIVED_ELI": "identifiant d'article dérivé, non officiel",
    "W_DATA_QUALITY": "incohérence des métadonnées source",
    "W_TRANSLATION": "traduction non officielle",
    "W_OUT_OF_SCOPE": "hors périmètre",
}

STOP_REASONS_FR = {
    "final": "réponse finale",
    "max_iterations": "arrêt : nombre maximal d'itérations atteint",
    "time_budget": "arrêt : budget de temps atteint",
    "llm_error": "erreur du LLM",
    "mcp_error": "erreur MCP",
    "no_search_tool": "outil de recherche absent",
}
MODES_FR = {"tool_calling": "appel d'outils", "retrieve_first": "retrieve-first (recherche injectée, sans outils)"}


# ------------------------------------------------------------------------------------------- data builders


@dataclass
class CitationCard:
    key: str
    tool: str
    call_index: int
    act_eli: str
    act_title: str = ""
    doc_type: str | None = None
    article: str | None = None
    article_eli: str | None = None
    article_eli_derived: bool = False
    version_eli: str | None = None
    version_label: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    article_wording_since: str | None = None
    article_wording_since_inferred: bool | None = None
    text_basis: str | None = None
    metadata_reason: str | None = None
    flags: list[str] = field(default_factory=list)
    status: str | None = None
    status_as_of: str | None = None
    warnings: list[str] = field(default_factory=list)
    display_url: str | None = None
    licence: str | None = None
    memorial: str | None = None
    citation_string: str | None = None

    @property
    def eli(self) -> str:
        return self.article_eli or self.version_eli or self.act_eli

    @property
    def label(self) -> str:
        if self.citation_string:
            return self.citation_string
        head = ", ".join(x for x in ((self.article or "").rstrip("."), self.act_title) if x)
        if self.version_label:
            head += f" ({self.version_label})"
        return head or self.act_eli

    @property
    def basis_badge(self) -> tuple[str, str]:
        if self.text_basis == "metadata_only" and self.metadata_reason == "not_loaded":
            return NOT_LOADED_LABEL, "gray"
        if self.text_basis is None:
            return "base du texte inconnue", "gray"
        return TEXT_BASIS_BADGES.get(self.text_basis, (self.text_basis, "gray"))


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _codes(value: Any) -> list[str]:
    out: list[str] = []
    for item in value or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping) and item.get("code"):
            out.append(str(item["code"]))
    return out


def _is_source(d: Mapping[str, Any]) -> bool:
    return bool(d.get("act_eli")) and ("version_eli" in d or "text_basis" in d)


def _card_from(d: Mapping[str, Any], parent: Mapping[str, Any] | None, tool: str, idx: int) -> CitationCard:
    status = d.get("status_as_of") if isinstance(d.get("status_as_of"), Mapping) else {}
    current = d.get("current_version") if isinstance(d.get("current_version"), Mapping) else {}
    citation_string = d.get("citation_string") or (parent or {}).get("citation_string")
    return CitationCard(
        key=str(d.get("key") or ""),
        tool=tool,
        call_index=idx,
        act_eli=str(d["act_eli"]),
        act_title=str(d.get("act_title") or d.get("title_short") or d.get("title") or ""),
        doc_type=_str(d.get("doc_type")),
        article=_str(d.get("article")),
        article_eli=_str(d.get("article_eli")),
        article_eli_derived=bool(d.get("article_eli_derived")),
        version_eli=_str(d.get("version_eli") or current.get("version_eli")),
        version_label=_str(d.get("version_label") or current.get("version_label") or current.get("label")),
        valid_from=_str(d.get("valid_from") or current.get("valid_from")),
        valid_to=_str(d.get("valid_to") or current.get("valid_to")),
        article_wording_since=_str(d.get("article_wording_since")),
        article_wording_since_inferred=d.get("article_wording_since_inferred"),
        text_basis=_str(d.get("text_basis")),
        metadata_reason=_str(d.get("metadata_reason")),
        flags=[str(f) for f in d.get("flags") or []],
        status=_str(status.get("status")),
        status_as_of=_str(status.get("as_of")),
        warnings=_codes(d.get("warnings")),
        display_url=_str(d.get("display_url")),
        licence=_str(d.get("licence")),
        memorial=_str(d.get("memorial")),
        citation_string=_str(citation_string),
    )


def _walk_sources(node: Any, parent: Mapping[str, Any] | None, found: list[tuple[Mapping[str, Any], Any]]) -> None:
    if isinstance(node, Mapping):
        if _is_source(node):
            found.append((node, parent))
            return  # a source's nested objects (current_version …) are part of it
        for value in node.values():
            _walk_sources(value, node, found)
    elif isinstance(node, list):
        for item in node:
            _walk_sources(item, parent, found)


def build_citation_cards(outcomes: Iterable[ToolOutcome]) -> list[CitationCard]:
    """Every source returned by successful tool calls, de-duplicated by (act, article, version)."""
    cards: list[CitationCard] = []
    seen: set[tuple[str, str, str]] = set()
    for idx, outcome in enumerate(outcomes, 1):
        if outcome.is_error or not isinstance(outcome.structured, Mapping):
            continue
        found: list[tuple[Mapping[str, Any], Any]] = []
        _walk_sources(outcome.structured, None, found)
        for d, parent in found:
            card = _card_from(d, parent, outcome.name, idx)
            ident = (card.act_eli, card.article_eli or card.article or "", card.version_eli or "")
            if ident in seen:
                continue
            seen.add(ident)
            cards.append(card)
    return cards


@dataclass
class WarningItem:
    code: str
    severity: str
    message: str
    refs: list[str] = field(default_factory=list)


def build_warnings(outcomes: Iterable[ToolOutcome], cards: Sequence[CitationCard] = ()) -> list[WarningItem]:
    """Envelope warnings (with messages) of every result, plus codes carried by sources without a message;
    de-duplicated by (code, message), refs merged, sorted high → medium → info."""
    items: dict[tuple[str, str], WarningItem] = {}
    for outcome in outcomes:
        sc = outcome.structured if isinstance(outcome.structured, Mapping) else {}
        for w in sc.get("warnings") or []:
            if not isinstance(w, Mapping) or not w.get("code"):
                continue
            code = str(w["code"])
            message = str(w.get("message") or WARNING_SHORT_FR.get(code, ""))
            item = items.setdefault((code, message), WarningItem(code, str(w.get("severity") or "info"), message))
            for ref in w.get("refs") or []:
                if str(ref) not in item.refs:
                    item.refs.append(str(ref))
    known_codes = {code for code, _ in items}
    for card in cards:
        for code in card.warnings:
            if code in known_codes:
                continue
            key = (code, WARNING_SHORT_FR.get(code, ""))
            item = items.setdefault(key, WarningItem(code, "info", key[1]))
            if card.key and card.key not in item.refs:
                item.refs.append(card.key)
    return sorted(items.values(), key=lambda w: (SEVERITY_ORDER.get(w.severity, 3), w.code))


@dataclass
class Envelope:
    attribution: str
    disclaimer: str
    release_id: str | None = None
    sync_date: str | None = None
    as_of: str | None = None
    staleness_level: int | None = None
    clock_ok: bool | None = None
    retrieval: dict[str, Any] | None = None
    from_server: bool = False


def build_envelope(outcomes: Iterable[ToolOutcome]) -> Envelope:
    env = Envelope(ATTRIBUTION_FALLBACK_FR, DISCLAIMER_FALLBACK_FR)
    for outcome in outcomes:
        sc = outcome.structured if isinstance(outcome.structured, Mapping) else {}
        if sc.get("attribution") and not env.from_server:
            env.attribution = str(sc["attribution"])
            env.disclaimer = str(sc.get("disclaimer") or env.disclaimer)
            env.from_server = True
        snap = sc.get("snapshot") if isinstance(sc.get("snapshot"), Mapping) else None
        if snap and env.release_id is None:
            env.release_id = _str(snap.get("release_id"))
            env.sync_date = _str(snap.get("sync_date"))
            env.staleness_level = snap.get("staleness_level")
            env.clock_ok = snap.get("clock_ok")
        if sc.get("as_of") and env.as_of is None:
            env.as_of = str(sc["as_of"])
        if isinstance(sc.get("retrieval"), Mapping) and env.retrieval is None:
            env.retrieval = dict(sc["retrieval"])
    return env


def vector_status_line(retrieval: Mapping[str, Any] | None, vector_status: str | None = None) -> str:
    """« mode lexical (vecteurs désactivés : provider=none) » — from a search result or the health body."""
    retrieval = retrieval or {}
    mode = retrieval.get("mode_used")
    status = retrieval.get("vector_status") or vector_status
    parts: list[str] = []
    if mode:
        parts.append(f"mode {'lexical' if mode == 'lexical' else mode}")
    if status:
        if status == "ok":
            parts.append("vecteurs : ok")
        else:
            reason = str(status).split(":", 1)[-1]
            detail = "provider=none" if reason == "provider_none" else reason
            parts.append(f"vecteurs désactivés : {detail}")
    if retrieval.get("reranked") is False:
        parts.append("non reclassé" + (f" ({retrieval['rerank_status']})" if retrieval.get("rerank_status") else ""))
    elif retrieval.get("reranked") is True:
        parts.append("reclassé")
    if not parts:
        return "mode de recherche inconnu"
    head, *rest = parts
    return head + (f" ({', '.join(rest)})" if rest else "")


def _seconds(value: float) -> str:
    if value >= 10:
        return f"{value:.0f} s"
    if value >= 1:
        return f"{value:.1f} s"
    return f"{value * 1000:.0f} ms"


def latency_line(llm_s: float, mcp_s: float, total_s: float) -> str:
    return f"LLM {_seconds(llm_s)} / MCP {_seconds(mcp_s)} / total {_seconds(total_s)}"


def tool_counts_line(traces: Sequence[ToolTrace]) -> str:
    counts: dict[str, int] = {}
    for t in traces:
        counts[t.name] = counts.get(t.name, 0) + 1
    if not counts:
        return "aucun outil appelé"
    return "outils : " + ", ".join(f"{name} ×{n}" for name, n in counts.items())


def turn_summary_line(turn: TurnResult, vector_status: str | None = None) -> str:
    env = build_envelope(turn.outcomes)
    bits = [
        MODES_FR.get(turn.mode, turn.mode),
        tool_counts_line(turn.traces),
        vector_status_line(env.retrieval, vector_status),
        latency_line(turn.llm_latency_s, turn.mcp_latency_s, turn.total_s),
    ]
    return " · ".join(bits)


def trace_rows(traces: Sequence[ToolTrace]) -> list[dict[str, Any]]:
    rows = []
    for t in traces:
        err = t.error_payload()
        rows.append(
            {
                "itération": t.iteration,
                "outil": t.name,
                "arguments du modèle": _short_json(t.model_arguments),
                "arguments effectifs": _short_json(t.effective_arguments),
                "surcharges": "; ".join(t.overrides_applied),
                "latence": _seconds(t.latency_s),
                "isError": t.is_error,
                "erreur": f"{err.get('code')}: {err.get('message')}" if err else "",
            }
        )
    return rows


def _short_json(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def hit_rows(outcome: ToolOutcome) -> list[dict[str, Any]]:
    """Table rows for a ``search_legislation`` result."""
    sc = outcome.structured if isinstance(outcome.structured, Mapping) else {}
    rows = []
    for hit in sc.get("hits") or []:
        if not isinstance(hit, Mapping):
            continue
        c = hit.get("citation") if isinstance(hit.get("citation"), Mapping) else {}
        rows.append(
            {
                "rang": hit.get("rank"),
                "clé": c.get("key"),
                "article": c.get("article"),
                "acte": c.get("act_title"),
                "type": c.get("doc_type"),
                "base du texte": c.get("text_basis"),
                "statut": (c.get("status_as_of") or {}).get("status")
                if isinstance(c.get("status_as_of"), Mapping)
                else None,
                "version": c.get("version_label"),
                "score": hit.get("score"),
                "extrait": _short_json(hit.get("snippet"), 200),
            }
        )
    return rows


# ------------------------------------------------------------------------------------------- Streamlit views


def _badge(st: Any, label: str, color: str) -> str:
    return f":{color}-background[{label}]"


def show_citation_card(st: Any, card: CitationCard) -> None:
    label, color = card.basis_badge
    key = f"[{card.key}] " if card.key else ""
    st.markdown(f"**{key}{card.label}**  \n{_badge(st, label, color)}")
    meta: list[str] = []
    if card.version_label or card.valid_from:
        window = ""
        if card.valid_from:
            window = f"(du {card.valid_from}" + (f" au {card.valid_to})" if card.valid_to else ")")
        meta.append(" ".join(x for x in (card.version_label or "version", window) if x))
    if card.article_wording_since:
        inferred = " (inféré)" if card.article_wording_since_inferred else ""
        meta.append(f"rédaction de l'article applicable depuis le {card.article_wording_since}{inferred}")
    if card.status:
        meta.append(f"statut au {card.status_as_of or '?'} : {card.status}")
    if card.flags:
        meta.append("indicateurs : " + ", ".join(card.flags))
    if card.memorial:
        meta.append(card.memorial)
    if meta:
        st.caption(" · ".join(meta))
    st.code(card.eli, language=None)
    if card.article_eli_derived:
        st.caption("ELI d'article dérivé localement, non officiel (W_DERIVED_ELI)")
    if card.warnings:
        st.caption("avertissements : " + ", ".join(card.warnings))
    tail = []
    if card.display_url:
        tail.append(f"affichage : {card.display_url} (non accessible hors ligne)")
    if card.licence:
        tail.append(f"licence : {card.licence}")
    tail.append(f"source : {card.tool} (appel {card.call_index})")
    st.caption(" · ".join(tail))


def show_warnings(st: Any, warnings: Sequence[WarningItem]) -> None:
    if not warnings:
        return
    lines = []
    for w in warnings:
        refs = f" [{', '.join(w.refs)}]" if w.refs else ""
        lines.append(f"- **[!] {w.code}**{refs} ({w.severity}) — {w.message}")
    st.markdown("\n".join(lines))


def show_envelope_footer(st: Any, env: Envelope) -> None:
    st.caption(f"{env.attribution} · {env.disclaimer}")


def show_traces(st: Any, traces: Sequence[ToolTrace]) -> None:
    if not traces:
        st.caption("aucun appel d'outil")
        return
    st.dataframe(trace_rows(traces), hide_index=True, width="stretch")
    for i, t in enumerate(traces, 1):
        with st.expander(f"{i}. {t.name} — {'erreur' if t.is_error else 'ok'} — {_seconds(t.latency_s)}"):
            cols = st.columns(2)
            cols[0].caption("arguments du modèle")
            cols[0].json(t.model_arguments if t.model_arguments is not None else {})
            cols[1].caption("arguments effectifs (après surcharges)")
            cols[1].json(t.effective_arguments if t.effective_arguments is not None else {})
            if t.overrides_applied:
                st.caption("surcharges de la barre latérale : " + "; ".join(t.overrides_applied))
            err = t.error_payload()
            if err:
                st.error(f"{err.get('code')}: {err.get('message')}")
                if err.get("details"):
                    st.json(err["details"])
            if t.outcome is not None and t.outcome.structured is not None:
                st.caption("structuredContent")
                st.json(t.outcome.structured, expanded=False)


def show_outcome(st: Any, outcome: ToolOutcome, *, vector_status: str | None = None) -> None:
    """Direct search: rendered view of one tool result + the raw JSON and the server's text rendering."""
    st.caption(f"{outcome.name} · {_seconds(outcome.latency_s)} · isError={outcome.is_error}")
    if outcome.is_error:
        err = outcome.error or {}
        st.error(f"{err.get('code')}: {err.get('message')}")
        if err.get("hint"):
            st.caption(f"indice : {err['hint']}")
        if err.get("candidates"):
            st.json(err["candidates"])
    else:
        env = build_envelope([outcome])
        if env.retrieval is not None:
            st.caption(vector_status_line(env.retrieval, vector_status))
        rows = hit_rows(outcome)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        cards = build_citation_cards([outcome])
        if cards:
            st.markdown("**Sources**")
            for card in cards:
                show_citation_card(st, card)
        sc = outcome.structured if isinstance(outcome.structured, Mapping) else {}
        if isinstance(sc.get("text"), str) and sc["text"]:
            st.markdown("**Texte (verbatim)**")
            st.text(sc["text"])
        warnings = build_warnings([outcome], cards)
        if warnings:
            st.markdown("**Avertissements**")
            show_warnings(st, warnings)
        show_envelope_footer(st, env)
    with st.expander("Rendu texte du serveur (TextContent)"):
        st.text(outcome.text or "(vide)")
    with st.expander("JSON brut (structuredContent)", expanded=False):
        st.json(outcome.structured if outcome.structured is not None else {})


def show_turn(st: Any, turn: TurnResult, *, vector_status: str | None = None, verifier: Any = None) -> None:
    """Chat: answer, then sources, warnings, verifier, tool calls, latency, attribution."""
    if turn.error:
        st.error(f"{STOP_REASONS_FR.get(turn.stop_reason, turn.stop_reason)} : {turn.error}")
    st.markdown(turn.answer or "_(pas de réponse)_")
    st.caption(turn_summary_line(turn, vector_status))
    if turn.stop_reason not in ("final", "llm_error", "mcp_error"):
        st.warning(STOP_REASONS_FR.get(turn.stop_reason, turn.stop_reason))
    for note in turn.notes:
        st.caption(note)
    if turn.prompt_source != "server":
        st.caption("prompt système : copie locale (le serveur n'a pas fourni legal_answer)")
    cards = build_citation_cards(turn.outcomes)
    warnings = build_warnings(turn.outcomes, cards)
    with st.expander(f"Base légale — {len(cards)} source(s)", expanded=bool(cards)):
        if not cards:
            st.caption("aucune source renvoyée par les outils")
        for card in cards:
            show_citation_card(st, card)
    if warnings:
        with st.expander(f"Avertissements — {len(warnings)}", expanded=True):
            show_warnings(st, warnings)
    if verifier is not None:
        with st.expander("Vérification (V1–V3)", expanded=False):
            for check in verifier.checks:
                status = "ok" if check.ok else ("non vérifié" if check.ok is None else "ÉCHEC")
                st.markdown(f"- **{check.code}** {status} — {check.detail}")
    with st.expander(f"Appels d'outils — {len(turn.traces)}", expanded=False):
        show_traces(st, turn.traces)
    show_envelope_footer(st, build_envelope(turn.outcomes))
