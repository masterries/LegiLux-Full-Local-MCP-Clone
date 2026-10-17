# G2 — lexical/bm25/no-exp

- release `data-20261016-sample`, run 20261017T064857Z, 205 items (draft_unvalidated), backend in-process

| subset | n | recall@1 | recall@5 | recall@10 | recall@20 | mrr@10 | ndcg@10 | act_recall@10 |
|---|---|---|---|---|---|---|---|---|
| all | 205 | 0.527 | 0.763 | 0.820 | 0.846 | 0.639 | 0.678 | 0.907 |
| fr | 185 | 0.573 | 0.830 | 0.886 | 0.916 | 0.695 | 0.736 | 0.973 |
| de | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| en | 10 | 0.200 | 0.300 | 0.400 | 0.400 | 0.260 | 0.292 | 0.600 |

Latency: tool {'p50_ms': 182.0, 'p95_ms': 363.5, 'max_ms': 688.5}, SQL {'p50_ms': 144.2, 'p95_ms': 324.4, 'max_ms': 656.1}, rerank {}.

Targets not in the first 10 hits: 36 items.

- G2-043 (fr) « Un salarié à temps partiel est-il prioritaire pour un poste à temps plein qui se libère ? » → ranks [14]
- G2-051 (fr) « Quel est l'âge minimum pour se marier ? » → ranks [None]
- G2-056 (fr) « De quelles manières le mariage est-il dissous ? » → ranks [None]
- G2-059 (fr) « Un contrat valablement conclu a-t-il force de loi entre les parties ? » → ranks [None]
- G2-061 (fr) « Quelles sont les obligations principales du locataire ? » → ranks [None]
- G2-063 (fr) « À partir de quand l'acheteur devient-il propriétaire de la chose vendue ? » → ranks [None]
- G2-080 (fr) « Le personnel d'une maison de retraite peut-il acheter un bien appartenant à un résident ? » → ranks [None]
- G2-086 (fr) « Est-on punissable lorsqu'on blesse quelqu'un pour se défendre ? » → ranks [None]
- G2-095 (fr) « Quelle peine pour celui qui met le feu à un immeuble habité ? » → ranks [None]
- G2-106 (fr) « Constitution liberté d'expression » → ranks [None]
- G2-109 (fr) « La police peut-elle entrer dans un domicile sans base légale ? » → ranks [None]
- G2-119 (fr) « Peut-on être à la fois député et ministre ? » → ranks [16]
- G2-123 (fr) « Les audiences des tribunaux sont-elles publiques ? » → ranks [None]
- G2-130 (fr) « Comment la fonction de chef de l'État se transmet-elle au sein de la dynastie ? » → ranks [None]
- G2-136 (fr) « Comment le tiers saisi fait-il sa déclaration dans une saisie-arrêt ? » → ranks [16]
- G2-153 (fr) « Combien de temps sont protégés les droits des artistes interprètes ? » → ranks [12]
- G2-162 (fr) « Un fonds d'investissement alternatif peut-il avoir plusieurs gestionnaires ? » → ranks [None]
- G2-167 (fr) « Quels actes sont publiés au Mémorial A ? » → ranks [None]
- G2-177 (fr) « Le Luxembourg extrade-t-il ses propres ressortissants ? » → ranks [17]
- G2-179 (fr) « Une personne peut-elle être extradée pour une infraction fiscale ou douanière ? » → ranks [None]
- G2-186 (de) « Welche Kündigungsfrist gilt, wenn der Arbeitgeber einem Arbeitnehmer kündigt? » → ranks [None]
- G2-187 (de) « Darf einer schwangeren Arbeitnehmerin gekündigt werden? » → ranks [None]
- G2-188 (de) « Wie lange gilt das Urheberrecht nach dem Tod des Urhebers? » → ranks [None]
- G2-189 (de) « Garantiert die Verfassung die Meinungsfreiheit und die Pressefreiheit? » → ranks [None]
- G2-190 (de) « Ab welchem Alter darf man in Luxemburg heiraten? » → ranks [None]
- G2-191 (de) « Wie definiert das Strafgesetzbuch den Diebstahl? » → ranks [None]
- G2-192 (de) « Ab welchem Alter darf man eine Drohne in der offenen Kategorie fliegen? » → ranks [None]
- G2-193 (de) « Wie viele Mitglieder hat die nationale Datenschutzkommission? » → ranks [None]
- G2-194 (de) « Haften Eltern für Schäden, die ihre minderjährigen Kinder verursachen? » → ranks [None]
- G2-195 (de) « Liefert Luxemburg eigene Staatsangehörige aus? » → ranks [None]
- G2-196 (en) « What notice period must an employer give when dismissing an employee? » → ranks [None]
- G2-197 (en) « Can an employer dismiss an employee immediately for serious misconduct? » → ranks [None]
- G2-198 (en) « How long does copyright last after the death of the author? » → ranks [None]
- G2-199 (en) « Does the constitution protect the right to privacy? » → ranks [None]
- G2-201 (en) « How is sexual harassment at work defined? » → ranks [None]
- G2-202 (en) « What is the minimum age to get married? » → ranks [None]
