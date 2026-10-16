from __future__ import annotations

import datetime as dt
import re

import pytest

from lgx_ingest import sparql_templates as st

E = "http://data.legilux.public.lu/eli/etat/leg/"


def test_all_appendix_a_queries_present() -> None:
    names = set(st.list_queries())
    expected = {
        "q01_acts",
        "q02_consolidations",
        "q03_conso_formats",
        "q04_conso_base_acts",
        "q05_rel_modifies_repeals",
        "q06_rel_other",
        "q07_titles",
        "q07_titles_by_year",
        "q08_memorial_refs",
        "q10_vocabularies.construct",
        "q11_graph_watermark",
        "q12_boundary_sweep",
        "q13_new_relations",
        "q16_construct_graphs.construct",
        "q_manif",
        "checks/q09_licence_crosstab",
        "checks/q14_stubs",
        "checks/q15_codes",
        "checks/q17_completeness",
        "checks/q18_impact",
    }
    assert expected <= names


def test_every_placeholder_is_documented() -> None:
    readme = (st.SPARQL_DIR / "README.md").read_text(encoding="utf-8")
    for name in st.list_queries():
        for ph in st.placeholders(st.query_path(name).read_text(encoding="utf-8")):
            assert f"{{{{{ph}}}}}" in readme, f"{name}: {{{{{ph}}}}} undocumented"


def test_flat_pulls_are_distinct_and_unsorted() -> None:
    for name in (
        "q01_acts",
        "q02_consolidations",
        "q03_conso_formats",
        "q05_rel_modifies_repeals",
        "q06_rel_other",
        "q07_titles",
        "q08_memorial_refs",
        "q_manif",
    ):
        body = st.query_path(name).read_text(encoding="utf-8")
        assert "SELECT DISTINCT" in body and "ORDER BY" not in body and "LIMIT" not in body


def test_render_prepends_prefixes_and_fills_values() -> None:
    q = st.render_query("q_manif", WORKS=[E + "loi/2018/08/01/a686/jo", E + "code/route/20260612"])
    assert q.startswith("PREFIX jolux:") and "{{" not in q
    assert f"<{E}code/route/20260612>" in q
    q = st.render_query(
        "q11_graph_watermark", SINCE=dt.datetime(2026, 9, 1, 2, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    )
    assert '"2026-09-01T00:00:00Z"^^xsd:dateTime' in q
    q = st.render_query("q12_boundary_sweep", FROM=dt.date(2026, 10, 9), TO=dt.date(2026, 12, 15))
    assert re.search(r'"2026-10-09"\^\^xsd:date && \?d <= "2026-12-15"', q)
    assert "1990" in st.render_query("q07_titles_by_year", Y0=1990, Y1=1999)


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("q_manif", {"WORKS": ["not an iri"]}),
        ("q_manif", {"WORKS": [E + "x> } ; DROP <y"]}),
        ("q_manif", {"WORKS": [E + f"x{i}" for i in range(501)]}),
        ("q_manif", {"WORKS": E + "single-string"}),
        ("q11_graph_watermark", {"SINCE": dt.datetime(2026, 9, 1)}),  # naive datetime
        ("q12_boundary_sweep", {"FROM": "2026-10-09", "TO": dt.date(2026, 12, 15)}),
        ("q07_titles_by_year", {"Y0": "1990", "Y1": 1999}),
        ("q01_acts", {"EXTRA": 1}),
        ("q12_boundary_sweep", {"FROM": dt.date(2026, 10, 9)}),
        ("../pyproject", {}),
    ],
)
def test_render_rejects_bad_values(name: str, params: dict[str, object]) -> None:
    with pytest.raises(st.TemplateError):
        st.render_query(name, **params)  # type: ignore[arg-type]
