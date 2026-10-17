"""Offline unit tests of the M2 retrieval pieces: query preparation, reranker client, search SQL API, settings."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest

from lgx_mcp.retrieval.candidates import SQL_V1, SQL_V2, search_candidates
from lgx_mcp.retrieval.expansion import QueryExpander, fold, guess_lang, load_expander
from lgx_mcp.retrieval.rerank import Reranker, RerankUnavailableError, compact_header, rerank_text
from lgx_mcp.schemas import TOOLS_BY_NAME
from lgx_mcp.settings import McpSettings, SettingsError

TOKEN = "unit-test-token-0123456789"


# --------------------------------------------------------------------------------------- query preparation
def test_fold_and_language_guess() -> None:
    assert fold("Liberté d’Expression  ŒUVRE") == "liberte d'expression oeuvre"
    assert guess_lang("Quel est le délai de préavis ?") == "fr"
    assert guess_lang("Welche Kündigungsfrist gilt für Arbeitnehmer?") == "de"
    assert guess_lang("What is the notice period for an employee?") == "en"
    assert guess_lang("Kündigungsfrist") == "fr"  # no function word: French wins (the glossary stays off)


def test_shipped_expansion_file_is_well_formed() -> None:
    ex = load_expander()
    assert ex.status == "draft_unvalidated"
    assert len(ex.expansions) > 100
    assert all(isinstance(w, str) and w for words in ex.noise.values() for w in words)  # YAML 1.1: `on` is not True
    assert {"fr", "de", "en"} <= set(ex.noise)
    assert {e.lang for e in ex.expansions} == {"fr", "de", "en"}


def test_prepare_strips_citation_spans_and_noise_and_expands() -> None:
    ex = load_expander()
    q = "Quel est le délai de préavis en cas de licenciement selon le Code du travail ?"
    start = q.index("Code du travail")
    p = ex.prepare(q, strip_spans=[(start, start + len("Code du travail"))])
    assert "Code du travail" in p.removed and "Quel" in p.removed and "selon" in p.removed
    assert "Code" not in p.lexical and "préavis" in p.lexical
    assert "résiliation" in p.expansion  # « licenciement » → « résiliation du contrat de travail »
    assert p.lang == "fr" and p.as_dict()["lexical_query"] == p.lexical


def test_constitution_wording_for_freedom_of_expression() -> None:
    p = load_expander().prepare("Constitution liberté d'expression")
    assert {"manifester", "opinions"} <= set(p.expansion.split())


def test_glossary_only_for_the_question_language() -> None:
    ex = load_expander()
    de = ex.prepare("Was regelt das Gesetz zur Kündigungsfrist für Arbeitnehmer?")
    # German / English: the French glossary terms lead, the original words follow with the lower weight
    assert de.lang == "de" and "préavis" in de.lexical and "salarié" in de.lexical
    assert "Was" in de.removed and "Kündigungsfrist" in de.expansion
    en = ex.prepare("What is the notice period for dismissal of an employee?")
    assert en.lang == "en" and "préavis" in en.lexical and "licenciement" in en.lexical and "notice" in en.expansion
    assert ex.prepare("Wie geht es?").expansion == ""  # no glossary hit: nothing to swap
    # French « court » (short), « an » (year) are not English glossary / noise words in a French question
    fr = ex.prepare("Un délai plus court d'un an est-il possible ?")
    assert fr.lang == "fr" and fr.expansion == "" and "an" in fold(fr.lexical).split()


def test_prepare_without_expansion_and_overlapping_spans() -> None:
    ex = QueryExpander({"noise": {"fr": ["quel"]}, "expansions": [{"lang": "fr", "when": ["bail"], "add": "louage"}]})
    p = ex.prepare("Quel bail commercial", strip_spans=[(5, 9), (6, 8)], expand=False)
    assert p.lexical == "commercial" and p.expansion == "" and p.removed == ["bail", "Quel"]
    assert ex.prepare("le bail").expansion == "louage"


def test_a_query_that_is_only_a_citation_keeps_its_words() -> None:
    """G3 v2 (M2 PDF text): an article starting with its provenance « (Loi du 21 novembre 1997) « … » was searched
    with an empty lexical query (the citation span covered everything) and found nothing."""
    q = "(Loi du 21 novembre 1997) «(1) Les banques"
    p = load_expander().prepare(q, strip_spans=[(1, len(q))])
    assert "banques" in p.lexical and "1997" in p.lexical and p.removed == []
    kept = load_expander().prepare("Code du travail licenciement", strip_spans=[(0, len("Code du travail"))])
    assert "licenciement" in kept.lexical and "travail" not in kept.lexical  # a real remainder: spans still go


def test_operator_override_file(tmp_path: Any) -> None:
    (tmp_path / "query_expansion.yaml").write_text(
        "noise: {fr: [quid]}\nexpansions: [{lang: fr, when: [zorglub], add: 'contrat'}]\n", encoding="utf-8"
    )
    ex = load_expander(tmp_path)
    assert ex.prepare("quid zorglub").as_dict() == {
        "lexical_query": "zorglub",
        "expansion": "contrat",
        "expansions": [{"lang": "fr", "trigger": "zorglub", "add": "contrat"}],
        "removed": ["quid"],
        "language_guess": "fr",
    }


# --------------------------------------------------------------------------------------------- reranker
def test_compact_header_drops_version_labels_and_keeps_the_inner_levels() -> None:
    h = (
        "Code du travail — Version consolidée applicable au 26/07/2026 > LIVRE PREMIER. – Relations individuelles"
        " et collectives du travail > Titre II – Contrat de travail > Chapitre IV. – Résiliation du contrat de"
        " travail > Section 1. – Résiliation avec préavis > Art. L. 124-3. | Index : Ancienneté; Durée du préavis"
    )
    c = compact_header(h)
    assert "Version consolidée" not in c and c.startswith("Code du travail > ") and "Art. L. 124-3." in c
    assert compact_header("Loi du 2 janvier 1889 — texte original (Mém. A n° 1/1889) > Art. 3.") == (
        "Loi du 2 janvier 1889 > Art. 3."
    )
    inner = "Recueil > Version consolidée applicable au 02/08/2026 : Loi du 8 juin 2004. > Art. 24."
    assert compact_header(inner) == "Recueil > Loi du 8 juin 2004. > Art. 24."
    long = "Titre — Version consolidée applicable au 01/01/2020 > " + " > ".join(
        f"Niveau {i} " + "x" * 60 for i in range(9)
    )
    short = compact_header(long + " > Art. 5. | Index : " + "terme; " * 80)
    assert len(short) <= 400 and short.startswith("Titre > … > ") and "Art. 5." in short and "| Index" in short


def test_rerank_text_cuts_the_body() -> None:
    assert len(rerank_text("H", "b" * 5000)) == 2 + 1800
    assert rerank_text("H", "b" * 5000, 600) == "H\n" + "b" * 600
    rr = Reranker("http://rerank:8080", "auto", body_chars=300)
    assert rr.text("H — Version consolidée applicable au 01/01/2020 > Art. 1.", "x" * 900).endswith("\n" + "x" * 300)


async def test_reranker_records_batch_latency_and_skips_calls_after_a_failure() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        calls.append(len(texts))
        return httpx.Response(200, json=[{"index": i, "score": -float(i)} for i in range(len(texts))])

    rr = Reranker("http://rerank:8080", "auto", batch=16, transport=httpx.MockTransport(handler))
    scores = await rr.rerank("q", [f"t{i}" for i in range(40)])
    assert calls == [16, 16, 8] and len(scores) == 40
    assert rr.last.texts == 40 and len(rr.last.batch_ms) == 3 and rr.last.as_dict()["batches"] == 3

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(-1)
        raise httpx.ConnectError("refused")

    bad = Reranker("http://rerank:8080", "auto", transport=httpx.MockTransport(down))
    with pytest.raises(RerankUnavailableError):
        await bad.rerank("q", ["a"])
    n = len(calls)
    with pytest.raises(RerankUnavailableError, match="ConnectError"):  # degrades at once, no second request
        await bad.rerank("q", ["a"])
    assert len(calls) == n and bad.recently_failed()


# ------------------------------------------------------------------------------------- search SQL API
class FakeDb:
    def __init__(self) -> None:
        self.sql: str = ""
        self.params: dict[str, Any] = {}

    async def fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.sql, self.params = sql, params
        return [{
            "chunk_id": 7, "score": 0.5, "lex_rank": 1, "dense_rank": None, "exact_hit": False, "cited_hit": False,
            "unit_id": 3, "act_id": 2, "version_id": 1, "part_no": 1, "part_count": 1, "header": "H", "body": "B",
            "doc_type": "CODE", "valid_from": dt.date(2026, 1, 1), "valid_to": None, "coverage": 4,
            "lex_score": 12.5, "phrase_hits": 2, "act_boosted": True,
        }]  # fmt: skip


async def test_search_api_1_keeps_the_m1_call() -> None:
    db = FakeDb()
    hits = await search_candidates(db, "q", as_of=dt.date(2026, 10, 16), api=1, expansion="x", boost_act_ids=[2])  # type: ignore[arg-type]
    assert db.sql == SQL_V1 and "expansion" not in db.params and "boost" not in db.params
    assert hits[0].coverage == 4


async def test_search_api_2_passes_expansion_boosts_and_options() -> None:
    db = FakeDb()
    hits = await search_candidates(
        db,  # type: ignore[arg-type]
        "délai préavis",
        as_of=dt.date(2026, 10, 16),
        api=2,
        expansion="résiliation",
        boost_act_ids=[2, 5],
        opts={"ranker": "bm25", "length_b": 0.4},
    )
    assert db.sql == SQL_V2 and "%(expansion)s, %(boost)s::bigint[], %(opts)s::jsonb" in SQL_V2
    assert db.params["expansion"] == "résiliation" and db.params["boost"] == [2, 5]
    assert json.loads(db.params["opts"]) == {"ranker": "bm25", "length_b": 0.4}
    h = hits[0]
    assert (h.lex_score, h.phrase_hits, h.act_boosted, h.coverage) == (12.5, 2, True, 4)


# ------------------------------------------------------------------------------------------- settings
def test_ranker_settings() -> None:
    s = McpSettings.from_env({"LGX_MCP_TOKEN": TOKEN})
    assert (s.lexical_ranker, s.query_expansion, s.lexical_opts, s.rerank_body_chars) == ("bm25", True, {}, 1800)
    s = McpSettings.from_env({
        "LGX_MCP_TOKEN": TOKEN, "LGX_LEXICAL_RANKER": "rank_cd", "LGX_QUERY_EXPANSION": "0",
        "LGX_LEXICAL_OPTS": '{"length_b": 0.5, "act_boost": 2}', "LGX_RERANK_BODY_CHARS": "600",
    })  # fmt: skip
    assert (s.lexical_ranker, s.query_expansion, s.rerank_body_chars) == ("rank_cd", False, 600)
    assert s.lexical_opts == {"length_b": 0.5, "act_boost": 2.0}
    assert s.describe()["lexical_ranker"] == "rank_cd"
    for env, msg in (
        ({"LGX_LEXICAL_RANKER": "bm42"}, "LGX_LEXICAL_RANKER"),
        ({"LGX_LEXICAL_OPTS": "{nope"}, "not JSON"),
        ({"LGX_LEXICAL_OPTS": '{"k1": 1.2}'}, "keys must be"),
        ({"LGX_LEXICAL_OPTS": '{"length_b": "x"}'}, "numbers"),
        ({"LGX_RERANK_BODY_CHARS": "50"}, "LGX_RERANK_BODY_CHARS"),
    ):
        with pytest.raises(SettingsError, match=msg):
            McpSettings.from_env({"LGX_MCP_TOKEN": TOKEN, **env})


def test_search_description_tells_the_llm_to_search_in_french_legal_terms() -> None:
    tool = TOOLS_BY_NAME["search_legislation"]
    desc = tool["description"]
    assert "français juridique" in desc and "Kündigungsfrist" in desc and "notice period" in desc
    assert "délai de préavis" in desc and "liberté de manifester ses opinions" in desc
    # the inputSchema stays Appendix D verbatim (test_mcp_schemas); the guidance lives in the tool description
    assert "reformulate DE/EN" in tool["inputSchema"]["properties"]["query"]["description"]
