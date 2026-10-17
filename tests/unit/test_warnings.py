from __future__ import annotations

import pytest

from lgx_core.models import Lang, Severity, WarningCode
from lgx_core.warnings import load_catalogue


@pytest.fixture(scope="module")
def cat():  # type: ignore[no-untyped-def]
    return load_catalogue()


def test_every_code_in_three_languages(cat) -> None:  # type: ignore[no-untyped-def]
    assert set(cat.codes()) == set(WarningCode)
    for code in WarningCode:
        d = cat[code]
        assert d.text_fr and d.text_de and d.text_en


def test_render_and_variants(cat) -> None:  # type: ignore[no-untyped-def]
    msg = cat.render("W_SNAPSHOT", "fr", T="16/10/2026", as_of="16/10/2026")
    assert msg.startswith("Données Legilux arrêtées au 16/10/2026")
    old = cat.render(WarningCode.W_STUB, Lang.EN, "old_fallback", d="2026-07-11", shown="consolidation of 2003-10-01")
    assert "Do not rely" in old
    assert cat.render("W_ARTICLE_REPEALED", "fr") == "Article abrogé."
    assert cat.render("W_ARTICLE_REPEALED", "fr", "by_act", act="la loi du 19 décembre 2025") == (
        "Article abrogé par la loi du 19 décembre 2025."
    )


def test_a_code_placeholder_is_a_template_parameter(cat) -> None:  # type: ignore[no-untyped-def]
    """W_SUPERSEDED_BY_CODE has a ``{code}`` placeholder: it used to collide with the ``code`` argument (M2 crash
    of get_act on loi/1955/02/14/n3, routed to the Code de la route)."""
    from lgx_mcp.context import WarningBag

    assert cat.render("W_SUPERSEDED_BY_CODE", "fr", code="Code de la route").startswith("Acte intégré au Code de la")
    w = cat.warning(WarningCode.W_SUPERSEDED_BY_CODE, "en", "redirected", code="Code de la route")
    assert "shown: the article as it stands in the code" in w.message
    bag = WarningBag(cat)
    bag.add(WarningCode.W_SUPERSEDED_BY_CODE, ref="C1", variant="no_text", code="Code de la route")
    assert bag.codes_for("C1") == ["W_SUPERSEDED_BY_CODE"]
    assert "aucun texte propre" in bag.as_list()[0]["message"]


def test_missing_parameter_is_an_error(cat) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="missing"):
        cat.render("W_NOT_CONSOLIDATED", "fr", memorial="Mém. A n° 12/2015")
    with pytest.raises(KeyError):
        cat.render("W_STUB", "fr", "nope", d="x", shown="y")


def test_rendered_warning_object(cat) -> None:  # type: ignore[no-untyped-def]
    w = cat.warning("W_DATA_STALE", "de", refs=["C1"], severity=Severity.HIGH, n=95, T="2026-10-16")
    assert w.severity is Severity.HIGH and w.refs == ["C1"] and "95" in w.message
    assert cat["W_NOT_OFFICIAL"].text_fr.endswith("(loi du 23 décembre 2016, art. 5).")
