"""Offline tests of the evaluation harness (PLAN §11.2 metrics, gold loading, keys, reports, runners)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.harness import gold, metrics, report
from eval.harness.backends import Call
from eval.harness.gen_g2 import hit_key, unit_key
from eval.harness.runner import run_g1, run_g2, run_g3

T = metrics.Target
E = "http://data.legilux.public.lu/eli/etat/leg/"


# ----------------------------------------------------------------------------------------- retrieval
def test_ranks_recall_mrr_ndcg() -> None:
    targets = [T("a", 2, "A"), T("b", 1, "B")]
    hits = ["x", "a", "y", "a", "z", "q", "w", "e", "r", "b", "t"]
    ranks = metrics.ranks_of(targets, hits)
    assert ranks == [2, 10]
    assert (
        metrics.recall_at(ranks, 1) == 0.0 and metrics.recall_at(ranks, 5) == 0.5 and metrics.recall_at(ranks, 10) == 1
    )
    assert metrics.reciprocal_rank(ranks) == 0.5
    assert metrics.reciprocal_rank([None, 11]) == 0.0
    ideal = metrics.ndcg_at(targets, [1, 2])
    assert ideal == pytest.approx(1.0)
    assert 0 < metrics.ndcg_at(targets, ranks) < 1
    assert metrics.ndcg_at([T("a")], [None]) == 0.0
    assert metrics.act_recall_at(targets, ["X", "A", "Y"], 10) == 0.5
    item = metrics.retrieval_item(targets, hits, ["X", "A"] + ["Y"] * 9)
    assert item["recall@20"] == 1.0 and item["mrr@10"] == 0.5 and item["act_recall@10"] == 0.5


def test_summarise_retrieval_means() -> None:
    rows = [metrics.retrieval_item([T("a")], ["a"], ["A"]), metrics.retrieval_item([T("a")], ["x"], ["X"])]
    s = metrics.summarise_retrieval(rows)
    assert s["n"] == 2 and s["recall@1"] == 0.5 and s["mrr@10"] == 0.5


# ------------------------------------------------------------------------------------------- resolver
@pytest.mark.parametrize(
    ("expect", "got", "outcome"),
    [
        ({"status": "resolved", "act_eli": "A"}, {"status": "resolved", "candidates": [{"act_eli": "A"}]}, "correct"),
        ({"status": "resolved", "act_eli": "A", "unit_eli": "u"},
         {"status": "resolved", "candidates": [{"act_eli": "A", "unit_eli": "v"}]}, "wrong"),
        ({"status": "resolved", "act_eli": "A"}, {"status": "resolved", "candidates": [{"act_eli": "B"}]}, "wrong"),
        ({"status": "resolved", "act_eli": "A"},
         {"status": "ambiguous", "candidates": [{"act_eli": "B"}, {"act_eli": "A"}]}, "abstained"),
        ({"status": "resolved", "act_eli": "A"}, {"status": "ambiguous", "candidates": [{"act_eli": "B"}]}, "wrong"),
        ({"status": "resolved", "act_eli": "A"}, {"status": "not_found", "candidates": []}, "missed"),
        ({"status": "ambiguous", "members": ["A", "B", "C"]},
         {"status": "ambiguous", "candidates": [{"act_eli": "A"}, {"act_eli": "C"}]}, "correct"),
        ({"status": "ambiguous", "members": ["A", "B"]},
         {"status": "ambiguous", "candidates": [{"act_eli": "A"}, {"act_eli": "Z"}]}, "wrong"),
        ({"status": "ambiguous", "members": ["A", "B"]},
         {"status": "resolved", "candidates": [{"act_eli": "A"}]}, "wrong"),
        ({"status": "not_found"}, {"status": "not_found", "candidates": []}, "correct"),
        ({"status": "not_found"}, {"status": "resolved", "candidates": [{"act_eli": "A"}]}, "wrong"),
        ({"status": "out_of_scope"}, {"status": "out_of_scope", "candidates": []}, "correct"),
        ({"status": "out_of_scope"}, {"status": "not_found", "candidates": []}, "wrong"),
    ],
)  # fmt: skip
def test_resolver_outcome(expect: dict[str, Any], got: dict[str, Any], outcome: str) -> None:
    assert metrics.resolver_outcome(expect, got) == outcome


def test_summarise_resolver() -> None:
    rows = [
        {"outcome": "correct", "expect_status": "resolved", "got_status": "resolved", "style": "a"},
        {"outcome": "wrong", "expect_status": "resolved", "got_status": "resolved", "style": "a"},
        {"outcome": "correct", "expect_status": "ambiguous", "got_status": "ambiguous", "style": "b"},
        {"outcome": "abstained", "expect_status": "resolved", "got_status": "ambiguous", "style": "b"},
        {"outcome": "correct", "expect_status": "not_found", "got_status": "not_found", "style": "c"},
    ]
    s = metrics.summarise_resolver(rows)
    assert (
        s["exact_accuracy"] == 0.6
        and s["citation_precision"] == 0.5
        and s["resolved_recall"] == pytest.approx(1 / 3, abs=1e-4)
    )
    assert s["ambiguity_precision"] == 0.5 and s["ambiguity_recall"] == 1.0 and s["not_found_accuracy"] == 1.0
    assert s["by_style"]["a"] == {"n": 2, "correct": 1}


# ------------------------------------------------------------------------------------------- warnings
def test_warning_items_and_summary() -> None:
    a = metrics.warning_item(["W_NOT_OFFICIAL", "W_STUB"], ["W_REPEALED"], ["W_NOT_OFFICIAL", "W_STUB"])
    b = metrics.warning_item(["W_NOT_OFFICIAL", "W_STUB"], ["W_REPEALED"], ["W_NOT_OFFICIAL", "W_REPEALED"])
    assert a["ok"] and not b["ok"] and b["missing"] == ["W_STUB"] and b["forbidden_seen"] == ["W_REPEALED"]
    s = metrics.summarise_warnings([{**a, "status_ok": True}, {**b, "status_ok": False}])
    assert s["mandatory_warning_coverage"] == 0.75 and s["forbidden_seen"] == 1 and s["status_correct"] == 0.5
    assert s["per_code"]["W_STUB"] == {"expected": 2, "present": 1}


def test_latency_summary() -> None:
    assert metrics.latency_summary([]) == {}
    s = metrics.latency_summary([float(i) for i in range(1, 101)])
    assert s == {"p50_ms": 50.5, "p95_ms": 95.0, "max_ms": 100.0}


# ------------------------------------------------------------------------------------------- keys
def test_unit_and_hit_keys_agree() -> None:
    act = E + "recueil/presse_medias"
    k = unit_key(None, act, "12", E + "loi/2004/06/08/n4")
    assert k == f"legilux://eli/etat/leg/recueil/presse_medias/art/12|{E}loi/2004/06/08/n4"
    cit = {"article_eli": None, "resource_uri": "legilux://eli/etat/leg/recueil/presse_medias/art/12",
           "member_act": E + "loi/2004/06/08/n4"}  # fmt: skip
    assert hit_key(cit) == k
    assert unit_key(E + "code/travail/art_l_124-3", act, "x", None) == E + "code/travail/art_l_124-3"
    assert hit_key({"article_eli": E + "code/travail/art_l_124-3"}) == E + "code/travail/art_l_124-3"
    assert hit_key({"resource_uri": "legilux://eli/etat/leg/loi/2018/08/01/a686/consolide/20251226"}) is None


# ------------------------------------------------------------------------------------------- gold files
def _write(tmp: Path, name: str, items: list[dict[str, Any]]) -> None:
    gold.write_set(tmp / name / "x.json", name, items, status="draft_unvalidated")


def test_gold_loader_validates(tmp_path: Path) -> None:
    _write(tmp_path, "G2", [{"id": "a", "lang": "fr", "query": "q", "targets": []}])
    items, meta = gold.load_set("G2", tmp_path)
    assert len(items) == 1 and meta["status"] == ["draft_unvalidated"] and meta["files"] == ["x.json"]
    _write(tmp_path, "G1", [{"id": "a", "text": "t", "style": "s", "expect": {}}] * 2)
    with pytest.raises(gold.GoldError, match="duplicate"):
        gold.load_set("G1", tmp_path)
    _write(tmp_path, "G3", [{"id": "a", "tool": "about_corpus"}])
    with pytest.raises(gold.GoldError, match="lacks"):
        gold.load_set("G3", tmp_path)
    with pytest.raises(gold.GoldError, match="unknown"):
        gold.load_set("G9", tmp_path)
    with pytest.raises(gold.GoldError, match="no gold files"):
        gold.load_set("G0", tmp_path)


# ------------------------------------------------------------------------------------------- runners
class FakeBackend:
    def __init__(self, answers: dict[str, dict[str, Any]], error: bool = False) -> None:
        self.description: dict[str, Any] = {"backend": "fake"}
        self.answers = answers
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def call(self, tool: str, arguments: dict[str, Any], *, clock: str | None = None) -> Call:
        self.calls.append((tool, dict(arguments), clock))
        key = arguments.get("query") or arguments.get("text") or tool
        return Call(self.error, self.answers.get(key, {}), 12.0)


async def test_run_g2_scores_hits_by_key() -> None:
    hits = [{"citation": {"article_eli": "x", "act_eli": "A"}}, {"citation": {"article_eli": "t1", "act_eli": "A"}}]
    sc = {"hits": hits, "retrieval": {"reranked": False, "latency_ms": {"sql": 5.0}}}
    items = [{"id": "G2-1", "lang": "fr", "query": "q", "targets": [{"key": "t1", "grade": 2, "act_eli": "A"}]}]
    out = await run_g2(FakeBackend({"q": sc}), items)
    row = out["items"][0]
    assert row["ranks"] == [2] and row["mrr@10"] == 0.5 and out["summary"]["fr"]["recall@5"] == 1.0
    assert out["summary"]["latency"]["sql"]["p50_ms"] == 5.0


async def test_run_g1_maps_out_of_scope_errors() -> None:
    items = [
        {"id": "1", "text": "RGPD", "style": "oos", "expect": {"status": "out_of_scope"}},
        {"id": "2", "text": "loi", "style": "x", "expect": {"status": "resolved", "act_eli": "A"}},
    ]
    backend = FakeBackend({"RGPD": {"error": {"code": "OUT_OF_SCOPE"}}}, error=True)
    out = await run_g1(backend, items)
    assert [r["outcome"] for r in out["items"]] == ["correct", "missed"]
    assert out["summary"]["gates"]["exact_accuracy>=0.95"] is False


async def test_run_g3_checks_codes_status_errors_and_clock() -> None:
    sc = {"warnings": [{"code": "W_NOT_OFFICIAL"}, {"code": "W_SNAPSHOT"}, {"code": "W_STUB"}],
          "status_as_of": {"status": "in-force"}}  # fmt: skip
    items = [
        {"id": "1", "trigger": "t", "tool": "get_act", "arguments": {}, "clock": "2026-10-01T12:00:00+02:00",
         "expect": {"warnings": ["W_NOT_OFFICIAL", "W_STUB"], "absent": ["W_REPEALED"], "status": "in-force"}},
        {"id": "2", "trigger": "t", "tool": "get_act", "arguments": {},
         "expect": {"warnings": ["W_NOT_OFFICIAL"], "status": "no-longer-in-force"}},
    ]  # fmt: skip
    backend = FakeBackend({"get_act": sc})
    out = await run_g3(backend, items)
    assert [r["ok"] for r in out["items"]] == [True, False]
    assert backend.calls[0][2] == "2026-10-01T12:00:00+02:00"
    s = out["summary"]
    assert s["mandatory_warning_coverage"] == 1.0 and s["status_correct"] == 0.5
    assert s["gates"]["mandatory_warnings==100%"] is True and s["gates"]["status_statements>=98%"] is False


def test_markdown_reports_render(tmp_path: Path) -> None:
    cfg = {"mode": "lexical", "ranker": "bm25", "expansion": False, "lexical_opts": {"length_b": 0.5}}
    g2 = {"set": "G2", "config": cfg,
          "summary": {"all": {**metrics.summarise_retrieval([metrics.retrieval_item([T("a")], ["a"], ["A"])])},
                      "latency": {}}, "items": [{"id": "x", "lang": "fr", "query": "q", "ranks": [None]}]}  # fmt: skip
    js, md = report.write(g2, tmp_path, "G2-test")
    assert json.loads(js.read_text())["set"] == "G2"
    text = md.read_text()
    assert "lexical/bm25/no-exp/length_b=0.5" in text and "| all | 1 |" in text and "« q »" in text
    cmp = report.compare_markdown([g2, {**g2, "config": {"mode": "rerank", "ranker": "bm25", "rerank_k": 16}}])
    assert "### G2" in cmp and "rerank/bm25 k=16" in cmp
