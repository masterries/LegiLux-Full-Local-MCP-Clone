"""The committed gold sets (eval/gold/G0..G3) are well formed and sized as PLAN §11.1 / the M2 task ask."""

from __future__ import annotations

from collections import Counter

from eval.harness import gold

ALLOWED_STATUS = {"draft_unvalidated"}


def test_g0_is_the_merged_smoke_set_in_the_eval_tab_format() -> None:
    from lgx_ui.evalg0 import evaluate, parse_items

    items, meta = gold.load_set("G0")
    assert len(items) == 20 and set(meta["status"]) <= ALLOWED_STATUS
    parsed = parse_items({"items": items})
    assert [p.id for p in parsed] == [f"G0-{i:02d}" for i in range(1, 21)]
    for p in parsed:
        for c in p.checks:  # every operator is one the Streamlit Eval tab knows
            evaluate(c.get("op", "equals"), None, c.get("value"))


def test_g1_covers_the_citation_styles_and_abstention() -> None:
    items, meta = gold.load_set("G1")
    assert 300 <= len(items) <= 650 and set(meta["status"]) <= ALLOWED_STATUS
    status = Counter(i["expect"]["status"] for i in items)
    assert status["ambiguous"] >= 50 and status["not_found"] >= 10 and status["out_of_scope"] >= 1
    styles = {i["style"].split(":")[0] for i in items}
    assert {"code", "eli", "dated", "memorial", "alias", "ambiguous", "not_found"} <= styles
    for i in items:
        if i["expect"]["status"] == "resolved":
            assert i["expect"]["act_eli"].startswith("http://data.legilux.public.lu/eli/etat/leg/")


def test_g2_questions_are_grounded_and_frozen() -> None:
    items, meta = gold.load_set("G2")
    assert set(meta["status"]) <= ALLOWED_STATUS
    langs = Counter(i["lang"] for i in items)
    assert langs["fr"] >= 150 and langs["de"] + langs["en"] >= 20
    queries = [i["query"] for i in items]
    assert len(set(queries)) == len(queries)
    for i in items:
        assert 1 <= len(i["targets"]) <= 3, i["id"]
        for t in i["targets"]:
            assert t["key"] and t["act_eli"] and t["article"] and len(t["text_sha256"]) == 64, i["id"]
            assert t["grade"] in (1, 2)
        assert any(t["grade"] == 2 for t in i["targets"]), i["id"]


def test_g3_covers_every_triggerable_warning() -> None:
    import json

    items, meta = gold.load_set("G3")
    assert set(meta["status"]) <= ALLOWED_STATUS
    doc = json.loads(gold.set_files("G3")[0].read_text(encoding="utf-8"))
    covered = {c for i in items for c in i["expect"]["warnings"]}
    assert covered == set(doc["covered_codes"])
    assert set(doc["covered_codes"]) | set(doc["not_triggerable"]) >= {
        "W_NOT_OFFICIAL", "W_SNAPSHOT", "W_DATA_STALE", "W_CLOCK_SUSPECT", "W_NOT_CONSOLIDATED", "W_STUB",
        "W_CONSO_LAGGING", "W_CONSO_DISCONTINUED", "W_REPEALED", "W_NOT_YET_IN_FORCE", "W_SUNSET", "W_FUTURE_VERSION",
        "W_ARTICLE_REPEALED", "W_BASE_LEGALE_ABROGEE", "W_TEMPORARY_ACT", "W_PDF_EXTRACTED", "W_SUPERSEDED_BY_CODE",
        "W_PARTIAL_REPEAL_UNKNOWN", "W_DERIVED_ELI", "W_DATA_QUALITY", "W_TRANSLATION", "W_OUT_OF_SCOPE",
    }  # fmt: skip
    assert len(covered) >= 18
    assert any(i.get("expect", {}).get("absent") for i in items)  # forbidden codes are tested too
