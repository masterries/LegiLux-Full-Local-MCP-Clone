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
    # a timeout far below one CPU forward pass: the real container is reached but cannot answer in time
    srv = start_server(make_settings(mcp_dsn, rerank_url=tei, rerank="auto", rerank_k=16, rerank_timeout_s=0.001))
    try:
        sc = await _search(srv, {"query": "délai de préavis en cas de licenciement", "top_k": 5})
    finally:
        srv.stop()
    ret = sc["retrieval"]
    assert sc["hits"] and ret["reranked"] is False and ret["rerank_status"] == "unavailable"
    assert "Timeout" in (ret["rerank_reason"] or "") and "non reclassés" in ret["rerank_reason"]
