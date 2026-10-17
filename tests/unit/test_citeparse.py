"""Citation parser (lgx_core.citeparse): real French / Luxembourg citation formats, offline."""

from __future__ import annotations

import datetime as dt

import pytest

from lgx_core.citeparse import CitationParser, exact_tokens, parse
from lgx_core.citeparse.fold import fold

P = CitationParser(["CNPD", "CSSF", "ADEM", "loi AIFM", "LSF", "loi sur le secteur financier", "RGPD"])


def one(text: str):
    cits = P.parse(text)
    assert cits, f"no citation in {text!r}"
    return cits[0]


# ------------------------------------------------------------------------------------- code articles
@pytest.mark.parametrize(
    ("text", "key", "alias"),
    [
        ("art. L. 121-1 C. trav.", "l121-1", "c. trav."),
        ("art. L.121-1 C.trav.", "l121-1", "c. trav."),
        ("Art. L. 124-10. du Code du travail", "l124-10", "code du travail"),
        ("article L. 121-1 du Code du travail", "l121-1", "code du travail"),
        ("L. 124-10 du Code du travail", "l124-10", "code du travail"),
        ("l'article 1134 du Code civil", "1134", "code civil"),
        ("article 6-1 du Code civil", "6-1", "code civil"),
        ("art. 1384 al. 1er C.civ.", "1384", "c. civ."),
        ("Code civil, art. 1134", "1134", "code civil"),
        ("art. 32-1 du Code pénal", "32-1", "code penal"),
        ("article 1007-29 NCPC", "1007-29", "ncpc"),
        ("art. 3 du Nouveau Code de procédure civile", "3", "nouveau code de procedure civile"),
        ("art. 1er du Code de la sécurité sociale", "1", "code de la securite sociale"),
        ("article 11(3) de la Constitution", "11", "constitution"),
        ("article premier de la Constitution", "1", "constitution"),
        ("Const., art. 32", "32", "const."),
        ("art. 12bis C. com.", "12bis", "c. com."),
        ("art. 120 ter du Code civil", "120ter", "code civil"),
    ],
)
def test_code_articles(text: str, key: str, alias: str) -> None:
    c = one(text)
    assert c.article is not None and c.article.key == key
    assert c.act is not None and c.act.kind == "alias" and c.act.alias_norm == alias


def test_article_subparts() -> None:
    c = one("Art. 5, paragraphe 2, alinéa 3, point 3° de la loi du 1er août 2018 portant organisation de la CNPD")
    assert c.article and (c.article.key, c.article.paragraph, c.article.alinea, c.article.point) == (
        "5",
        "2",
        "3",
        "3°",
    )
    c = one("art. 48 point 3° de la loi du 1er août 2018 relative à la protection des personnes physiques")
    assert c.article and c.article.point == "3°"
    c = one("article 11(3) de la Constitution")
    assert c.article and c.article.paragraph == "3"
    c = one("art. 5, § 1er, de la loi du 30 mai 2018")
    assert c.article and c.article.paragraph == "1"
    c = one("art. 2, sous a), de la loi du 30 mai 2018")
    assert c.article and c.article.point == "a)"


def test_ranges_and_lists() -> None:
    c = one("articles 17 à 20 du Code civil")
    assert c.article and (c.article.key, c.article.to_key, c.article.connector) == ("17", "20", "range")
    c = one("art. 9 et 10 de la loi du 4 juillet 2014")
    assert c.article and (c.article.key, c.article.to_key, c.article.connector) == ("9", "10", "list")
    c = one("art. 5 et art. 6 de la loi du 1er août 2018")
    assert c.article and c.article.to_key == "6" and c.act and c.act.date == dt.date(2018, 8, 1)
    # « al. » after the number is an alinea, not a range connector « a »
    c = one("art. 1384 al. 1er C. civ.")
    assert c.article and c.article.to_key is None and c.article.alinea == "1"


# ----------------------------------------------------------------------------------------- dated acts
@pytest.mark.parametrize(
    ("text", "doc_type", "date", "hint"),
    [
        (
            "Art. 5, paragraphe 2, de la loi du 1er août 2018 portant organisation de la Commission nationale pour"
            " la protection des données",
            "LOI",
            dt.date(2018, 8, 1),
            "portant organisation de la Commission nationale pour la protection des données",
        ),
        (
            "article 12bis de la loi modifiée du 4 décembre 1967 concernant l'impôt sur le revenu",
            "LOI",
            dt.date(1967, 12, 4),
            "concernant l'impôt sur le revenu",
        ),
        ("art. 3 du règlement grand-ducal du 14 novembre 2013", "RGD", dt.date(2013, 11, 14), None),
        ("art. 3 du règlement grand-ducal modifié du 14 novembre 2013", "RGD", dt.date(2013, 11, 14), None),
        ("art. 2 du RGD du 8 mars 2017", "RGD", dt.date(2017, 3, 8), None),
        ("art. 1er de l'arrêté grand-ducal du 14 février 1913 réglant le transport", "AGD", dt.date(1913, 2, 14), None),
        ("art. 4 de l'AGD du 14.02.1913", "AGD", dt.date(1913, 2, 14), None),
        ("art. 3 du règlement ministériel du 28 janvier 1985", "RMIN", dt.date(1985, 1, 28), None),
        ("art. 2 de l'arrêté ministériel du 15 juillet 2022", "AMIN", dt.date(2022, 7, 15), None),
        (
            "article 1er de la loi du 23 décembre 2016 concernant le Journal officiel",
            "LOI",
            dt.date(2016, 12, 23),
            None,
        ),
        ("art. 7 de la Loi du 1 août 2018", "LOI", dt.date(2018, 8, 1), None),
    ],
)
def test_dated_acts(text: str, doc_type: str, date: dt.date, hint: str | None) -> None:
    c = one(text)
    assert c.article is not None
    assert c.act is not None and c.act.kind == "dated"
    assert (c.act.doc_type, c.act.date) == (doc_type, date)
    if hint is not None:
        assert c.act.title_hint == hint


def test_modified_flag_and_hint_keeps_accents() -> None:
    c = one("article 12bis de la loi modifiée du 4 décembre 1967 concernant l'impôt sur le revenu")
    assert c.act and c.act.modified and c.act.title_hint and "impôt" in c.act.title_hint


@pytest.mark.parametrize(
    ("text", "doc_type", "key"),
    [
        ("loi du 1er août 2018, art. 5", "LOI", "5"),
        ("règlement grand-ducal du 14 novembre 2013, art. 3, alinéa 2", "RGD", "3"),
        ("RGD du 14 novembre 2013, art. 3", "RGD", "3"),
        ("Loi du 5 avril 1993 relative au secteur financier, article 2", "LOI", "2"),
    ],
)
def test_backward_attachment(text: str, doc_type: str, key: str) -> None:
    c = one(text)
    assert c.article and c.article.key == key
    assert c.act and c.act.kind == "dated" and c.act.doc_type == doc_type


def test_backward_alinea_and_title_cut() -> None:
    c = one("règlement grand-ducal du 14 novembre 2013, art. 3, alinéa 2")
    assert c.article and c.article.alinea == "2"
    c = one("Loi du 5 avril 1993 relative au secteur financier, article 2")
    assert c.act and c.act.title_hint == "relative au secteur financier"


def test_provenance_note() -> None:
    c = one("( L. 13 mai 2008 )")
    assert c.act and (c.act.doc_type, c.act.date, c.act.title_hint) == ("LOI", dt.date(2008, 5, 13), None)
    assert c.article is None


def test_alias_inside_title_hint() -> None:
    c = one("art. 5 de la loi du 1er août 2018 portant organisation de la CNPD")
    assert c.act and c.act.kind == "dated" and c.act.inner_aliases == ("cnpd",)


# -------------------------------------------------------------------------------- aliases and titles
@pytest.mark.parametrize(
    ("text", "alias", "key"),
    [
        ("art. 3 de la loi AIFM", "loi aifm", "3"),
        ("art. 2 LSF", "lsf", "2"),
        ("CNPD", "cnpd", None),
        ("la loi sur le secteur financier", "loi sur le secteur financier", None),
        ("Code de la consommation", "code de la consommation", None),
    ],
)
def test_aliases(text: str, alias: str, key: str | None) -> None:
    c = one(text)
    assert c.act and c.act.kind == "alias" and c.act.alias_norm == alias
    assert (c.article.key if c.article else None) == key


def test_alias_is_a_word() -> None:
    # « CSS » must not match inside « CSSF », and an unknown acronym is not an alias
    cits = P.parse("la CSSF")
    assert [c.act.alias_norm for c in cits if c.act] == ["cssf"]
    assert CitationParser().parse("la CSSF") == []


def test_typed_title_without_date() -> None:
    c = one("la loi relative au secteur des assurances")
    assert c.act and c.act.kind == "type_title" and c.act.doc_type == "LOI"
    assert c.act.title_hint == "relative au secteur des assurances"


# --------------------------------------------------------------------------------- ELIs and Mémorial
@pytest.mark.parametrize(
    ("text", "eli", "article"),
    [
        (
            "voir http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo",
            "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo",
            None,
        ),
        (
            "https://legilux.public.lu/eli/etat/leg/code/travail/20260726.",
            "http://data.legilux.public.lu/eli/etat/leg/code/travail/20260726",
            None,
        ),
        (
            "legilux://eli/etat/leg/loi/2018/08/01/a686/art/49",
            "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686",
            "49",
        ),
        (
            "https://legilux.public.lu/filestore/eli/etat/leg/loi/2018/08/01/a686/jo/fr/html/eli-etat-leg-loi-2018-08-01-a686-jo-fr-html.html",
            "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo",
            None,
        ),
        (
            "eli/etat/leg/code/travail/art_l_121-1",
            "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1",
            None,
        ),
    ],
)
def test_eli(text: str, eli: str, article: str | None) -> None:
    c = one(text)
    assert c.act and c.act.kind == "eli" and c.act.eli == eli
    assert (c.article.raw if c.article else None) == article


def test_adm_eli_is_out_of_scope() -> None:
    c = one("http://data.legilux.public.lu/eli/etat/adm/amin/2020/01/01/b1")
    assert c.act and c.act.out_of_scope
    assert [o.kind for o in P.out_of_scope("http://data.legilux.public.lu/eli/etat/adm/amin/2020/01/01/b1")] == [
        "adm_branch"
    ]


@pytest.mark.parametrize(
    ("text", "series", "number", "year"),
    [
        ("Mém. A n° 686 de 2018", "A", 686, 2018),
        ("Mémorial A n° 23 de 1868", "A", 23, 1868),
        ("Mémorial A 2018, n° 686", "A", 686, 2018),
        ("Mém. A n° 686/2018", "A", 686, 2018),
        ("Mém. A – 686 du 16 août 2018", "A", 686, 2018),
        ("Mém. A n°12", "A", 12, None),
        ("Mém. B n° 12 de 2010", "B", 12, 2010),
    ],
)
def test_memorial(text: str, series: str, number: int, year: int | None) -> None:
    c = one(text)
    assert c.act and c.act.kind == "memorial"
    assert (c.act.memorial_series, c.act.memorial_number, c.act.memorial_year) == (series, number, year)
    assert (c.act.out_of_scope is not None) == (series == "B")


# ------------------------------------------------------------------------------------- out of scope
def test_out_of_scope_detection() -> None:
    kinds = {o.kind for o in P.out_of_scope("règlement (UE) 2016/679 RGPD jurisprudence de la Cour de cassation")}
    assert kinds == {"eu_law", "case_law"}
    assert {o.kind for o in P.out_of_scope("le projet de loi n° 7000")} == {"bill"}
    assert {o.kind for o in P.out_of_scope("un règlement communal de la Ville")} == {"communal"}
    assert {o.kind for o in P.out_of_scope("Mém. B n° 1")} == {"memorial_b"}
    assert {o.kind for o in P.out_of_scope("directive 2014/65/UE")} == {"eu_law"}
    assert P.out_of_scope("contrat de travail à durée déterminée") == []


# ------------------------------------------------------------------------------------ misc helpers
def test_exact_tokens() -> None:
    toks = exact_tokens("article L.121-1 et règlement (UE) 2016/679, art. 12bis, L. 124-10, 2018-08-01 et 1007-29")
    assert toks == ["l. 121-1", "2016/679", "12bis", "l. 124-10", "1007-29"]
    assert exact_tokens("licenciement avec préavis") == []


def test_no_citation_in_plain_question() -> None:
    assert P.parse("Quelles sont les règles du licenciement avec préavis ?") == []


def test_several_citations_in_order() -> None:
    cits = P.parse("Voir l'art. L. 121-1 C. trav. et l'article 1134 du Code civil.")
    assert [(c.article.key if c.article else None, c.act.alias_norm if c.act else None) for c in cits] == [
        ("l121-1", "c. trav."),
        ("1134", "code civil"),
    ]


def test_parse_one_prefers_complete_citation() -> None:
    c = P.parse_one("CNPD : voir art. 5 de la loi du 1er août 2018")
    assert c and c.article and c.act and c.act.kind == "dated"


def test_parsed_block_shape() -> None:
    c = one("Art. 5, paragraphe 2, de la loi du 1er août 2018 portant organisation de la CNPD")
    parsed = c.parsed()
    assert parsed["type"] == "LOI" and parsed["date"] == "2018-08-01" and parsed["article"] == "5"
    assert parsed["paragraph"] == "2" and parsed["title_hint"] == "portant organisation de la CNPD"


def test_module_level_parse_and_extra_aliases() -> None:
    assert parse("art. L. 121-1 C. trav.")[0].act.alias_norm == "c. trav."  # type: ignore[union-attr]
    assert parse("art. 2 XYZQ", aliases=["XYZQ"])[0].act.alias_norm == "xyzq"  # type: ignore[union-attr]


def test_fold_keeps_index_map() -> None:
    f = fold("Arrêté – œuvre")
    assert f.text == "arrete - oeuvre"
    a, b = f.span(0, 6)
    assert f.original[a:b] == "Arrêté"
    assert f.cut(9, 15) == "œuvre"
