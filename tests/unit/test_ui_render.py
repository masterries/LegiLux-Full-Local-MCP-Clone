"""lgx_ui.render (citation cards, warnings, envelope, latency/vector lines), lgx_ui.verify_light (V1–V3) and
lgx_ui.evalg0 (the G0 runner and the packaged dev gold set)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from lgx_ui import evalg0, render
from lgx_ui.mcp_client import ToolOutcome
from lgx_ui.prompts import ATTRIBUTION_FALLBACK_FR, LEGAL_ANSWER_FALLBACK, fallback_prompt
from lgx_ui.tools_bridge import ToolTrace, TurnResult
from lgx_ui.verify_light import verify_turn
from unit.test_ui_fakes import ART_TEXT, ATTRIBUTION, ELI, FakeSession, act_result, article_result, search_result


def outcome(name: str, structured: object, *, is_error: bool = False) -> ToolOutcome:
    return ToolOutcome(name, {}, is_error, structured, "", latency_s=0.25)


SEARCH = outcome("search_legislation", search_result({"retrieval_mode": "hybrid"}))
ARTICLE = outcome("get_article", article_result({}))
ACT = outcome("get_act", act_result({}))


def test_citation_cards_from_search_article_and_act() -> None:
    cards = render.build_citation_cards([SEARCH, ARTICLE, ACT])
    # the article result repeats C1 of the search (same act/article/version): de-duplicated
    assert [c.key for c in cards] == ["C1", "C2", ""]
    c1, c2, act = cards
    assert c1.label.startswith("Art. L. 121-1, Code du travail (version consolidée")
    assert c1.eli == f"{ELI}/code/travail/art_l_121-1"
    assert c1.basis_badge == ("consolidé", "green")
    assert c1.status == "in-force" and c1.article_wording_since == "2020-11-01"
    assert c1.tool == "search_legislation" and c1.call_index == 1
    assert c2.basis_badge[1] == "red" and "NON CONSOLIDÉ" in c2.basis_badge[0]
    assert c2.article_eli_derived and "W_NOT_CONSOLIDATED" in c2.warnings
    assert act.act_eli == f"{ELI}/loi/2018/05/30/a446" and act.text_basis == "stub_fallback"
    assert act.version_eli.endswith("/consolide/20240101"), "taken from current_version"
    # get_article's citation_string (top level, D.2.2) is used as the label
    only_article = render.build_citation_cards([ARTICLE])[0]
    assert only_article.label.startswith(
        "Art. L. 121-1, Code du travail (version consolidée applicable au 26/07/2026) —"
    )


def test_error_results_and_odd_shapes_give_no_cards() -> None:
    err = outcome("get_article", {"error": {"code": "BAD_INPUT", "message": "x"}}, is_error=True)
    odd = outcome(
        "resolve_citation", {"status": "ambiguous", "candidates": [{"act_eli": f"{ELI}/loi/2018/08/01/a686"}]}
    )
    assert render.build_citation_cards([err, odd, outcome("x", "not a dict")]) == []


def test_not_loaded_label() -> None:
    sc = {"act_eli": "x", "text_basis": "metadata_only", "metadata_reason": "not_loaded", "title": "Loi"}
    card = render.build_citation_cards([outcome("get_act", sc)])[0]
    assert card.basis_badge == (render.NOT_LOADED_LABEL, "gray")


def test_warnings_are_deduplicated_and_sorted() -> None:
    cards = render.build_citation_cards([SEARCH, ARTICLE, ACT])
    warnings = render.build_warnings([SEARCH, ARTICLE, ACT], cards)
    codes = [w.code for w in warnings]
    assert codes[:2] == ["W_NOT_CONSOLIDATED", "W_STUB"], "high severity first"
    assert codes.count("W_NOT_OFFICIAL") == 1 and codes.count("W_SNAPSHOT") == 1
    nc = warnings[0]
    assert nc.refs == ["C2"] and "modifié depuis par" in nc.message
    # W_DERIVED_ELI only exists as a code on C2: shown with its short French name
    derived = next(w for w in warnings if w.code == "W_DERIVED_ELI")
    assert derived.refs == ["C2"] and "dérivé" in derived.message


def test_envelope_and_fallbacks() -> None:
    env = render.build_envelope([SEARCH])
    assert env.from_server and env.attribution == ATTRIBUTION
    assert env.release_id == "data-20261016-sample" and env.sync_date == "2026-10-16"
    assert env.retrieval["mode_requested"] == "hybrid" and env.retrieval["mode_used"] == "lexical"
    fallback = render.build_envelope([outcome("x", {"no": "envelope"})])
    assert not fallback.from_server and fallback.attribution == ATTRIBUTION_FALLBACK_FR
    assert "CC BY 4.0" in fallback.attribution and "art. 5" in fallback.disclaimer


def test_vector_and_latency_lines() -> None:
    line = render.vector_status_line(SEARCH.structured["retrieval"])
    assert line == "mode lexical (vecteurs désactivés : provider=none, non reclassé (off))"
    assert render.vector_status_line(None, "disabled:parity_failed") == "vecteurs désactivés : parity_failed"
    assert render.vector_status_line({"mode_used": "hybrid", "vector_status": "ok", "reranked": True}) == (
        "mode hybrid (vecteurs : ok, reclassé)"
    )
    assert render.vector_status_line(None) == "mode de recherche inconnu"
    assert render.latency_line(38.2, 1.2, 39.6) == "LLM 38 s / MCP 1.2 s / total 40 s"
    assert render.latency_line(0.0123, 0.5, 0.6) == "LLM 12 ms / MCP 500 ms / total 600 ms"


def test_trace_rows_and_turn_summary() -> None:
    ok = ToolTrace(1, "a", "search_legislation", {"query": "x", "top_k": 12}, {"query": "x", "top_k": 6},
                   ["top_k 12→6"], outcome=SEARCH, latency_s=0.2)  # fmt: skip
    bad = ToolTrace(1, "b", "get_article", {"article": "5"}, validation_errors=["(racine): bad"])
    turn = TurnResult("Réponse", "tool_calling", "final", [ok, ok, bad], 38.0, 1.2, 39.5, llm_calls=2)
    rows = render.trace_rows(turn.traces)
    assert rows[0]["surcharges"] == "top_k 12→6" and rows[0]["isError"] is False
    assert rows[2]["isError"] is True and rows[2]["erreur"].startswith("BAD_INPUT")
    summary = render.turn_summary_line(turn)
    assert summary.startswith("appel d'outils · outils : search_legislation ×2, get_article ×1")
    assert "mode lexical (vecteurs désactivés : provider=none" in summary and summary.endswith("total 40 s")
    hits = render.hit_rows(SEARCH)
    assert [h["clé"] for h in hits] == ["C1", "C2"] and hits[1]["base du texte"] == "original_possibly_outdated"


# ------------------------------------------------------------------------------------------ verifier


def test_verifier_passes_on_supported_answer() -> None:
    answer = f"Selon l'Art. L. 121-1 [C1] — {ELI}/code/travail/art_l_121-1 : « {ART_TEXT[:60]} »."
    report = verify_turn(answer, [SEARCH])
    by = {c.code: c for c in report.checks}
    assert by["V1"].ok and by["V2"].ok and by["V3"].ok and report.ok


def test_verifier_flags_unsupported_eli_unknown_key_and_altered_quote() -> None:
    answer = (
        f"Voir {ELI}/loi/1999/01/01/n99/art_7 [C9] et « Le contrat de travail est un accord verbal sans conditions »."
    )
    by = {c.code: c for c in verify_turn(answer, [SEARCH]).checks}
    assert by["V2"].ok is False and "n99" in by["V2"].detail and "[C9]" in by["V2"].detail
    assert by["V3"].ok is False and by["V1"].ok is None


def test_verifier_without_citations_is_not_checked() -> None:
    by = {c.code: c for c in verify_turn("Les textes disponibles localement ne permettent pas de répondre.", []).checks}
    assert by["V2"].ok is None and by["V3"].ok is None


def test_quote_normalisation() -> None:
    sc = {"text": "L’employeur doit informer le salarié  par écrit."}
    answer = "« L'employeur doit informer le salarié par écrit. »"
    by = {c.code: c for c in verify_turn(answer, [outcome("get_article", sc)]).checks}
    assert by["V3"].ok is True


# ------------------------------------------------------------------------------------------ G0


def test_resolve_path_and_operators() -> None:
    data = {"hits": [{"c": {"e": "a/code/travail/1"}}, {"c": {"e": "b"}}], "x": {"y": [1, 2]}, "n": None}
    assert evalg0.resolve_path(data, "hits[*].c.e") == ["a/code/travail/1", "b"]
    assert evalg0.resolve_path(data, "hits[1].c.e") == "b"
    assert evalg0.resolve_path(data, "x.y[0]") == 1
    assert evalg0.resolve_path(data, "missing.k") is evalg0._MISSING
    assert evalg0.evaluate("any_contains", ["a/code/travail/1", "b"], "/code/travail/")
    assert evalg0.evaluate("all_equals", ["b", "b"], "b") and not evalg0.evaluate("all_equals", [], "b")
    assert evalg0.evaluate("contains", ["W_A", "W_B"], "W_B") and evalg0.evaluate("contains", "abc", "b")
    assert evalg0.evaluate("exists", None, True) and evalg0.evaluate("exists", evalg0._MISSING, False)
    assert not evalg0.evaluate("equals", evalg0._MISSING, None)
    assert evalg0.evaluate("nonempty", [1], None) and not evalg0.evaluate("nonempty", [], None)
    assert evalg0.evaluate("gte", 3, 2) and evalg0.evaluate("in", "a", ["a", "b"])


def test_packaged_dev_gold_set_is_well_formed() -> None:
    items, source = evalg0.load_gold(None)
    assert len(items) == 20 and "g0_dev_sample.json" in source
    assert {i.tool for i in items} == {
        "search_legislation",
        "get_article",
        "get_act",
        "resolve_citation",
        "about_corpus",
    }
    assert len({i.id for i in items}) == 20
    for item in items:
        assert item.checks, item.id
        for check in item.checks:
            evalg0.evaluate(check.get("op", "equals"), "probe", check.get("value"))  # no unknown operator
    raw = json.loads(resources.files("lgx_ui").joinpath("gold/g0_dev_sample.json").read_text(encoding="utf-8"))
    assert raw["set"] == "G0"


def test_gold_from_eval_dir(tmp_path: Path) -> None:
    (tmp_path / "G0").mkdir()
    (tmp_path / "G0" / "a.json").write_text(json.dumps([{"id": "X1", "tool": "about_corpus", "checks": []}]))
    items, source = evalg0.load_gold(tmp_path)
    assert [i.id for i in items] == ["X1"] and source.endswith("a.json")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert len(evalg0.load_gold(empty)[0]) == 20, "falls back to the packaged dev set"


async def test_run_g0_against_the_fake_session() -> None:
    items = evalg0.parse_items(
        [
            {"id": "a", "tool": "about_corpus", "checks": [
                {"path": "embedding.vector_status", "op": "equals", "value": "disabled:provider_none"}]},
            {"id": "b", "tool": "search_legislation", "arguments": {"query": "licenciement"}, "checks": [
                {"path": "hits[*].citation.version_eli", "op": "any_contains", "value": "/code/travail/"},
                {"path": "hits[*].citation.doc_type", "op": "all_equals", "value": "CODE"}]},
            {"id": "c", "tool": "get_article", "arguments": {"article": "5"}, "expect_error": True,
             "checks": [{"path": "error.code", "op": "equals", "value": "BAD_INPUT"}]},
            {"id": "d", "tool": "get_article", "arguments": {"article": "5"}, "checks": []},
        ]
    )  # fmt: skip
    results = await evalg0.run_g0(FakeSession(), items)
    assert [r.ok for r in results] == [True, False, True, False]
    failed = [c for c in results[1].checks if not c.ok]
    assert failed[0].path == "hits[*].citation.doc_type" and failed[0].actual == ["CODE", "RGD"]
    assert "isError=True" in (results[3].error or "")
    s = evalg0.summary(results)
    assert s["passed"] == 2 and s["total"] == 4 and not s["gate_ok"]
    assert json.dumps([r.to_dict() for r in results])


def test_prompt_fallbacks_are_verbatim_appendix_d() -> None:
    assert set(LEGAL_ANSWER_FALLBACK) == {"fr", "de", "en"}
    assert fallback_prompt("fr").startswith("Tu es un assistant d'information juridique")
    assert "10. Termine par l'avertissement (disclaimer)" in fallback_prompt("fr")
    assert fallback_prompt("xx") == fallback_prompt("fr")
    assert "Write search queries in French" in fallback_prompt("en")


async def test_g0_retries_once_after_the_servers_rate_limit_hint() -> None:
    limited = ToolOutcome(
        "about_corpus",
        {},
        True,
        {"error": {"code": "BACKEND_UNAVAILABLE", "message": "limite d'appels atteinte ; réessayez dans 25 s"}},
    )

    class RateLimitedOnce(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.limited = True

        async def call_tool(self, name: str, arguments: dict) -> ToolOutcome:  # type: ignore[override]
            if self.limited:
                self.limited = False
                self.calls.append((name, dict(arguments)))
                return limited
            return await super().call_tool(name, arguments)

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    items = evalg0.parse_items([{"id": "a", "tool": "about_corpus", "checks": [{"path": "counts", "op": "nonempty"}]}])
    session = RateLimitedOnce()
    results = await evalg0.run_g0(session, items, sleep=fake_sleep)
    assert results[0].ok and waits == [25.5] and len(session.calls) == 2
    assert evalg0.retry_after(limited) == 25.0
    down = ToolOutcome("x", {}, True, {"error": {"code": "BACKEND_UNAVAILABLE", "message": "database down"}})
    assert evalg0.retry_after(down) is None
