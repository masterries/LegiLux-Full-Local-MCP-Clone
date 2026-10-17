"""lgx_core.verify minimal v1 (PLAN §8.7): V1 existence, V2 support, V3 verbatim."""

from __future__ import annotations

from lgx_core.verify import TurnEvidence, cited_elis, cited_keys, quotes, verify

ART = "http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1"
VERSION = "http://data.legilux.public.lu/eli/etat/leg/code/travail/20260726"
TEXT = (
    "Sans préjudice des dispositions légales existantes, le contrat de louage de services et d’ouvrage visé par "
    "l’article 1779 1° du Code civil est régi, en ce qui concerne les salariés, par les dispositions du présent titre."
)
RESULTS = [
    {
        "citation": {"key": "C1", "act_eli": "http://data.legilux.public.lu/eli/etat/leg/loi/2006/07/31/n2",
                     "article_eli": ART, "version_eli": VERSION},
        "text": TEXT,
    },
    {"hits": [{"citation": {"key": "C2", "act_eli": "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686",
                            "article_eli": None, "version_eli": "x"}, "snippet": "La Commission nationale …"}]},
]  # fmt: skip


def test_good_answer_passes() -> None:
    answer = (
        "Le contrat de travail est régi par le Code du travail [C1] : « le contrat de louage de services et d'ouvrage "
        f"visé par l'article 1779 1° du Code civil est régi » — {ART}."
    )
    report = verify(answer, RESULTS)
    assert report.ok, report.as_dict()
    assert [c.check for c in report.checks] == ["V1_existence", "V2_support", "V3_verbatim"]


def test_fabricated_eli_key_and_quote_fail() -> None:
    answer = (
        "Voir http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_999-9 [C7] : « le salarié a toujours "
        "droit à une indemnité de six mois »."
    )
    report = verify(answer, RESULTS).as_dict()
    by = {c["check"]: c for c in report["checks"]}
    assert not report["ok"]
    assert not by["V2_support"]["ok"] and any("art_l_999-9" in d for d in by["V2_support"]["details"])
    assert any("[C7]" in d for d in by["V2_support"]["details"])
    assert not by["V3_verbatim"]["ok"]
    assert not by["V1_existence"]["ok"]  # no resolver: inferred from the turn


def test_resolver_drives_v1() -> None:
    class DB:
        def exists(self, eli: str) -> bool:
            return eli.endswith("art_l_999-9") is False

    answer = f"Voir {ART} et http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_999-9."
    v1 = verify(answer, RESULTS, DB()).checks[0]
    assert not v1.ok and v1.details == [
        "ELI does not resolve: http://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_999-9"
    ]


def test_extractors_and_evidence() -> None:
    a = f'[C1] [C2] [C1] « {TEXT[:40]} » "une citation assez longue" {ART}.'
    assert cited_keys(a) == ["C1", "C2"] and cited_elis(a) == [ART] and len(quotes(a)) == 2
    ev = TurnEvidence.from_results(RESULTS)
    assert ART in ev.elis and set(ev.keys) == {"C1", "C2"} and len(ev.texts) == 2
    # a work ELI is supported by an article ELI below it
    assert verify("http://data.legilux.public.lu/eli/etat/leg/code/travail", RESULTS).checks[1].ok
