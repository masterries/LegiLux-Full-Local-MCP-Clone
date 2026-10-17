"""M2 serving-side contract tests: IDF-weighted lexical ranking, query preparation, OOS citations, real TEI reranker.

* Ranking tests need a release built with the M2 ``db/schema/40_search.sql`` (``retrieval.search_api == 2``); on
  an older release they skip (it is served with the M1 ordering). Point ``LGX_TEST_MCP_PG_DSN`` at such a
  database (e.g. ``…/legilux_eval_m2``, see eval/README.md) until the dev runtime is rebuilt.
* Reranker tests run against the real TEI container (``LGX_TEST_RERANK_URL``, default ``http://127.0.0.1:8090`` =
  ``docker compose -f deploy/compose.dev.yml --profile rerank up -d rerank``) and skip when it does not answer.
  CPU cost: 40 short pairs ≈ 8–15 s, a 16-candidate search with 300-character bodies ≈ 5–15 s on 4 vCPU.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import pytest

from contract.conftest import LiveServer, make_settings, mcp_client, start_server

E = "http://data.legilux.public.lu/eli/etat/leg/"
TEI_URL = os.environ.get("LGX_TEST_RERANK_URL", "http://127.0.0.1:8090")


async def _search(srv: LiveServer, args: dict[str, Any]) -> dict[str, Any]:
    async with mcp_client(srv) as c:
        r = await c.call_tool("search_legislation", args)
    assert not r.is_error, r.content[0].text[:400]
    return dict(r.structured_content or {})


def _need_api2(sc: dict[str, Any]) -> None:
    if (sc.get("retrieval") or {}).get("search_api", 1) < 2:
        pytest.skip("release built before the M2 search API (40_search.sql); served with the M1 ordering")


def _articles(sc: dict[str, Any]) -> list[str | None]:
    return [h["citation"].get("article_eli") for h in sc["hits"]]


# ------------------------------------------------------------------------------------------ lexical (M2)
async def test_notice_period_question_finds_l_124_3(live: LiveServer) -> None:
    sc = await _search(live, {"query": "Quel est le délai de préavis en cas de licenciement selon le Code du travail ?",
                              "top_k": 5})  # fmt: skip
    _need_api2(sc)
    assert E + "code/travail/art_l_124-3" in _articles(sc)[:5]
    ret, qi = sc["retrieval"], sc["query_interpretation"]
    assert ret["lexical_ranker"] == "bm25" and ret["lexical_order"] == "bm25"
    assert "Code du travail" in qi["lexical"]["removed"] and "Quel" in qi["lexical"]["removed"]
    assert qi["boosted_acts"] == 1 and all(h["match"]["act_boosted"] for h in sc["hits"])


async def test_constitution_freedom_of_expression_ranks_article_23_first(live: LiveServer) -> None:
    sc = await _search(live, {"query": "Constitution liberté d'expression", "top_k": 3})
    _need_api2(sc)
    assert _articles(sc)[0] == E + "constitution/1868/10/17/n1/art_23"
    assert "manifester" in sc["query_interpretation"]["lexical"]["expansion"]


async def test_german_question_without_reformulation_reaches_the_notice_articles(live: LiveServer) -> None:
    sc = await _search(live, {"query": "Welche Kündigungsfrist gilt, wenn der Arbeitgeber einem Arbeitnehmer kündigt?",
                              "top_k": 10})  # fmt: skip
    _need_api2(sc)
    assert sc["query_interpretation"]["lexical"]["language_guess"] == "de"
    assert E + "code/travail/art_l_124-3" in _articles(sc)


async def test_ranker_knob_rank_cd_reproduces_the_m1_ordering(mcp_dsn: str) -> None:
    srv = start_server(make_settings(mcp_dsn, lexical_ranker="rank_cd"))
    try:
        sc = await _search(srv, {"query": "licenciement avec préavis", "filters": {"doc_types": ["CODE"]}, "top_k": 5})
    finally:
        srv.stop()
    _need_api2(sc)
    assert sc["retrieval"]["lexical_ranker"] == "rank_cd" and sc["retrieval"]["lexical_order"] == "coverage"
    assert sc["query_interpretation"]["lexical"] is None
    cov = [h["match"]["coverage"] for h in sc["hits"]]
    assert cov == sorted(cov, reverse=True)


async def test_eu_regulation_citation_is_out_of_scope(live: LiveServer) -> None:
    async with mcp_client(live) as c:
        r = await c.call_tool("resolve_citation", {"text": "règlement (UE) 2016/679"})
    assert r.is_error and r.structured_content["error"]["code"] == "OUT_OF_SCOPE"
    assert "W_OUT_OF_SCOPE" in [w["code"] for w in r.structured_content["warnings"]]


# --------------------------------------------------------------- PDF codes + code-recueil routing (M2 ingest)
AGD_1955 = E + "agd/1955/11/23/n1"  # superseded_by_code → code/route (PLAN §2.6 (4)), no version of its own
LOI_1955 = E + "loi/1955/02/14/n3"


async def _call(srv: LiveServer, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    async with mcp_client(srv) as c:
        r = await c.call_tool(tool, args)
    return bool(r.is_error), dict(r.structured_content or {})


async def _need_pdf_codes(srv: LiveServer) -> None:
    err, sc = await _call(srv, "get_act", {"eli": E + "code/route"})
    if err or sc.get("text_basis") != "consolidated":
        pytest.skip("release without the M2 PDF codes (lgx ingest --sample --include-m2)")


def _codes(sc: dict[str, Any]) -> list[str]:
    return [w["code"] for w in sc.get("warnings", [])]


async def test_superseded_member_article_is_served_from_the_code_route(live: LiveServer) -> None:
    await _need_pdf_codes(live)
    for args in (
        {"act_eli": AGD_1955, "article": "12"},
        {"citation": "art. 12 de l'arrêté grand-ducal modifié du 23 novembre 1955 portant règlement de la circulation"
         " sur toutes les voies publiques"},
    ):  # fmt: skip
        err, sc = await _call(live, "get_article", args)
        assert not err, sc.get("error")
        cit = sc["citation"]
        assert cit["act_eli"] == E + "code/route" and cit["member_act"] == AGD_1955
        assert cit["version_eli"] == E + "code/route/20260612" and isinstance(cit["page"], int)
        assert {"W_PDF_EXTRACTED", "W_SUPERSEDED_BY_CODE"} <= set(cit["warnings"])
        assert cit["citation_string"].startswith("Art. 12 de l'arrêté grand-ducal du 23 novembre 1955")
        assert "(Code de la route, version consolidée applicable au 12/06/2026)" in cit["citation_string"]
        assert sc["resolution"]["redirected_from"]["act_eli"] == AGD_1955
        assert "masse totale" in sc["text"]


async def test_superseded_member_with_its_own_original_keeps_it_and_points_to_the_code(live: LiveServer) -> None:
    """rgd/2000/07/24/n3 has a stored JO original: it is shown « à titre d'information » + a pointer to the code."""
    await _need_pdf_codes(live)
    err, sc = await _call(live, "get_article", {"act_eli": E + "rgd/2000/07/24/n3", "article": "5"})
    assert not err, sc.get("error")
    assert sc["citation"]["act_eli"] == E + "rgd/2000/07/24/n3" and "W_SUPERSEDED_BY_CODE" in sc["citation"]["warnings"]
    ptr = sc["resolution"]["code_article"]
    assert ptr["act_eli"] == E + "code/education_nationale" and isinstance(ptr["page"], int)
    assert "redirected_from" not in sc["resolution"]


async def test_code_article_shared_by_members_is_ambiguous_not_guessed(live: LiveServer) -> None:
    await _need_pdf_codes(live)
    err, sc = await _call(live, "get_article", {"act_eli": E + "code/route", "article": "12"})
    assert err and sc["error"]["code"] == "AMBIGUOUS" and len(sc["error"]["candidates"]) > 2


async def test_social_security_code_article_is_the_codes_own_with_its_pages(live: LiveServer) -> None:
    """« art. 24 du Code de la sécurité sociale » = the code's art. 24 (p. 81), not that of a member printed later."""
    await _need_pdf_codes(live)
    err, sc = await _call(live, "get_article", {"citation": "art. 24 du Code de la sécurité sociale"})
    assert not err, sc.get("error")
    cit = sc["citation"]
    assert cit["act_eli"] == E + "loi/1925/12/17/n1" and cit["member_act"] is None
    assert cit["version_eli"] == E + "code/securite_sociale/20260101" and cit["page"] == 81
    assert "W_PDF_EXTRACTED" in cit["warnings"] and "soins de santé" in sc["text"]
    msg = next(w["message"] for w in sc["warnings"] if w["code"] == "W_PDF_EXTRACTED")
    assert "p. 81" in msg


async def test_member_act_card_points_to_the_code(live: LiveServer) -> None:
    await _need_pdf_codes(live)
    for eli in (AGD_1955, LOI_1955):
        err, sc = await _call(live, "get_act", {"eli": eli})
        assert not err, sc.get("error")
        sup = sc["superseded_by_code"]
        assert sup["code_eli"] == E + "code/route" and sup["version_eli"] == E + "code/route/20260612"
        assert "1955" in sup["member_context"] and "W_SUPERSEDED_BY_CODE" in _codes(sc)


async def test_search_filtered_to_a_superseded_member_runs_in_the_code_text(live: LiveServer) -> None:
    """The original of rgd/2000/07/24/n3 is not searchable (PLAN §6.4): its articles come from the code."""
    await _need_pdf_codes(live)
    sc = await _search(live, {"query": "Pour la rédaction de leur travail de candidature",
                              "filters": {"act_eli": E + "rgd/2000/07/24/n3"}, "top_k": 3})  # fmt: skip
    assert sc["hits"], sc["warnings"]
    for h in sc["hits"]:
        cit = h["citation"]
        assert cit["act_eli"] == E + "code/education_nationale" and cit["member_act"] == E + "rgd/2000/07/24/n3"
        assert "W_SUPERSEDED_BY_CODE" in cit["warnings"]
    assert sc["hits"][0]["citation"]["article"] == "Art. 5."


async def test_search_hits_in_pdf_codes_carry_member_and_page(live: LiveServer) -> None:
    await _need_pdf_codes(live)
    sc = await _search(live, {"query": "masse totale d'un véhicule ou d'un ensemble de véhicules couplés",
                              "filters": {"act_eli": E + "code/route"}, "top_k": 5})  # fmt: skip
    top = sc["hits"][0]["citation"]
    assert top["member_act"] == AGD_1955 and top["article"] == "Art. 12." and isinstance(top["page"], int)
    assert "W_PDF_EXTRACTED" in top["warnings"] and top["citation_string"].startswith("Art. 12 de l'arrêté")


# ------------------------------------------------------------------------------------- real TEI reranker
@pytest.fixture(scope="module")
def tei() -> str:
    try:
        ok = httpx.get(TEI_URL + "/health", timeout=3, trust_env=False).status_code == 200
    except httpx.HTTPError:
        ok = False
    if not ok:
        pytest.skip(f"no TEI reranker at {TEI_URL} (docker compose -f deploy/compose.dev.yml --profile rerank up -d)")
    return TEI_URL


async def test_real_tei_reranks_40_candidates_in_batches(tei: str) -> None:
    from lgx_mcp.retrieval.rerank import Reranker

    rr = Reranker(tei, "on", batch=32, timeout_s=180)
    assert (await rr.probe(force=True)).status == "ok"
    texts = [f"Le règlement fixe les heures d'ouverture des musées numéro {i}." for i in range(39)]
    texts.insert(17, "En cas de licenciement, le salarié a droit à un délai de préavis selon son ancienneté.")
    t0 = time.perf_counter()
    scores = await rr.rerank("durée du préavis de licenciement du salarié", texts)
    elapsed = time.perf_counter() - t0
    assert len(scores) == 40 and max(range(40), key=scores.__getitem__) == 17
    assert [len(b) for b in (texts[:32], texts[32:])] == [32, 8] and len(rr.last.batch_ms) == 2
    assert rr.state.status == "ok" and elapsed < 180


async def test_real_tei_search_is_reranked_and_timed(mcp_dsn: str, tei: str) -> None:
    settings = make_settings(mcp_dsn, rerank_url=tei, rerank="on", rerank_k=16, rerank_batch=16,
                             rerank_body_chars=300, rerank_timeout_s=180.0, allow_external_urls=False)  # fmt: skip
    srv = start_server(settings)
    try:
        sc = await _search(srv, {"query": "délai de préavis en cas de licenciement", "top_k": 5})
    finally:
        srv.stop()
    ret = sc["retrieval"]
    assert ret["reranked"] is True and ret["rerank_status"] == "ok" and ret["rerank_k"] == 16
    assert ret["latency_ms"]["rerank"] > 0 and len(ret["latency_ms"]["rerank_batches_ms"]) == 1
    assert all(h["match"]["rerank_score"] is not None for h in sc["hits"])
    assert [h["score"] for h in sc["hits"]] == sorted((h["score"] for h in sc["hits"]), reverse=True)


async def test_real_tei_keeps_the_explicitly_cited_article_first(mcp_dsn: str, tei: str) -> None:
    settings = make_settings(mcp_dsn, rerank_url=tei, rerank="on", rerank_k=8, rerank_batch=8,
                             rerank_body_chars=300, rerank_timeout_s=180.0, allow_external_urls=False)  # fmt: skip
    srv = start_server(settings)
    try:
        sc = await _search(srv, {"query": "art. L. 121-1 C. trav. salarié", "top_k": 3})
    finally:
        srv.stop()
    assert sc["retrieval"]["reranked"] is True
    first = sc["hits"][0]
    assert first["citation"]["article_eli"] == E + "code/travail/art_l_121-1" and first["match"]["cited_hit"] is True
    assert first["match"]["rerank_score"] is not None


async def test_real_tei_failure_falls_back_to_lexical_with_a_reason(mcp_dsn: str, tei: str) -> None:
    # a budget far below one CPU forward pass: the real container is reached but cannot answer in time. Since the
    # M2 review the probe times a search-sized payload (k texts of the body length), so it reports the reranker as
    # too slow for the budget before any search waits on it
    srv = start_server(make_settings(mcp_dsn, rerank_url=tei, rerank="auto", rerank_k=16, rerank_timeout_s=0.001))
    try:
        sc = await _search(srv, {"query": "délai de préavis en cas de licenciement", "top_k": 5})
    finally:
        srv.stop()
    ret = sc["retrieval"]
    assert sc["hits"] and ret["reranked"] is False and ret["rerank_status"] == "unavailable"
    reason = ret["rerank_reason"] or ""
    assert "reranker too slow" in reason and "0.001 s budget" in reason and "non reclassés" in reason
