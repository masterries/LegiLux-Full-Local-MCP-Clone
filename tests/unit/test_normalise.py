from __future__ import annotations

import pytest

from lgx_core.normalise import (
    alias_norm,
    is_repeal_stub_text,
    key_from_eid,
    normalise_whitespace,
    num_aliases,
    num_norm,
    unaccent,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Art. L. 121-1.", "l121-1"),
        ("Art. 1er.", "1"),
        ("Art. 1 er.", "1"),  # pretty-printed <sup>er</sup>
        ("Art. 1erbis.", "1bis"),
        ("Art. 7bis.", "7bis"),
        ("Art. 5 bis.", "5bis"),
        ("Art. 120quinquies.", "120quinquies"),
        ("Art. 4-1.", "4-1"),
        ("Art. L. 513-5", "l513-5"),
        ("« Art. 99-1.", "99-1"),
        ("Article unique.", "unique"),
        ("Art. 9 et 10.", "9_et_10"),
        ("Art. 17 à 20.", "17_a_20"),
        ("Art. 5, 6 et 7.", "5_et_6_et_7"),
        ("Art. 181. à Art. 192-2.", "181_a_192-2"),
        ("Art. L. 542-4 à L. 542-6.", "l542-4_a_l542-6"),
        ("Art. A. 12.", "a12"),
        ("Art.", ""),
    ],
)
def test_num_norm(raw: str, expected: str) -> None:
    assert num_norm(raw) == expected


def test_num_norm_is_idempotent_on_keys() -> None:
    for raw in ("Art. L. 121-1.", "Art. 1er.", "Art. 7bis."):
        assert num_norm(num_norm(raw)) == num_norm(raw)


def test_aliases_for_lists_and_ranges_with_eli_forms() -> None:
    civil = "http://data.legilux.public.lu/eli/etat/leg/code/civil/"
    assert num_aliases("Art. 9 et 10.", akn_id="art_9_et_10", eli_unit=civil + "art_9_10") == ["9", "10", "9_10"]
    assert num_aliases("Art. 17 à 20.", akn_id="art_17_a_20", eli_unit=civil + "art_17--20") == [
        "17",
        "18",
        "19",
        "20",
        "17--20",
    ]
    assert num_aliases("Art. 1er.", akn_id="art_1er", eli_unit=civil + "art_1") == []
    assert num_aliases("Art. L. 542-4 à L. 542-6.")[:3] == ["l542-4", "l542-5", "l542-6"]
    assert "160bis" in num_aliases("Art. 158 à 160bis.")
    members = num_aliases("Art. 181. à Art. 192-2.")
    assert members[0] == "181" and "192" in members and "192-2" in members
    assert num_aliases(None) == []


def test_key_from_eid() -> None:
    assert key_from_eid("art_l_121-1") == "l121-1"
    assert key_from_eid("http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1") == "l121-1"
    assert key_from_eid("art_1er") == "1"
    assert key_from_eid("art_9_et_10") == "9_et_10"
    assert key_from_eid("art_181_à_art_192-2") == "181_a_192-2"
    assert key_from_eid("annexe_1_art_1er") == "1"


def test_whitespace_unaccent_alias_norm() -> None:
    assert normalise_whitespace("a  b ​ c\n\td ") == "a b c d"
    assert normalise_whitespace("Art. 1er") == "Art. 1er"
    assert unaccent("Élément œuvre déjà") == "Element oeuvre deja"
    assert alias_norm("  Loi  modifiée du 1er août – l’État ") == "loi modifiee du 1er aout – l'etat"


@pytest.mark.parametrize(
    "text",
    [
        "Abrogé ( L. 23 avril 1934 )",
        "Abrogés( L. 4 juillet 2014 )",
        "(...) (abrogé par la loi du 14 mars 2017 )",
        "( L. du 20 juillet 2018 ) Article abrogé.",
        "Implicitement abrogé ( L. 13 juin 1994 ).",
        "Abrogés implicitement (Const. art. 18)",
        "Abrogé ( L. 13 juin 1994 et L. 8 juillet 1996 )",
        "(Section 3. – Comité permanent de l’emploi abrogée par la loi du 21 décembre 2007 )",
    ],
)
def test_repeal_stub_texts(text: str) -> None:
    assert is_repeal_stub_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "L’article 104 de la loi modifiée du 10 août 1915 concernant les sociétés commerciales est abrogé.",
        "Les lois sont exécutoires dans tout le territoire luxembourgeois.",
        "",
        "Al. 3 implicitement abrogé par arr. gr.-d. 22 octobre 1842. " * 10,
    ],
)
def test_not_repeal_stubs(text: str) -> None:
    assert not is_repeal_stub_text(text)
