# G2 — lexical/bm25

- release `data-20261016-sample`, run 20261017T090828Z, 205 items (draft_unvalidated), backend in-process

| subset | n | recall@1 | recall@5 | recall@10 | recall@20 | mrr@10 | ndcg@10 | act_recall@10 |
|---|---|---|---|---|---|---|---|---|
| all | 205 | 0.556 | 0.773 | 0.844 | 0.878 | 0.662 | 0.702 | 0.961 |
| fr | 185 | 0.595 | 0.814 | 0.881 | 0.913 | 0.703 | 0.741 | 0.973 |
| de | 10 | 0.100 | 0.500 | 0.600 | 0.600 | 0.247 | 0.331 | 0.800 |
| en | 10 | 0.300 | 0.300 | 0.400 | 0.500 | 0.317 | 0.336 | 0.900 |

Latency: tool {'p50_ms': 297.6, 'p95_ms': 585.1, 'max_ms': 910.5}, SQL {'p50_ms': 254.9, 'p95_ms': 544.7, 'max_ms': 863.7}, rerank {}.

Targets not in the first 10 hits: 31 items.

- G2-043 (fr) « Un salarié à temps partiel est-il prioritaire pour un poste à temps plein qui se libère ? » → ranks [18]
- G2-049 (fr) « Quels devoirs les époux ont-ils l'un envers l'autre ? » → ranks [None, 11]
- G2-051 (fr) « Quel est l'âge minimum pour se marier ? » → ranks [None]
- G2-056 (fr) « De quelles manières le mariage est-il dissous ? » → ranks [None]
- G2-059 (fr) « Un contrat valablement conclu a-t-il force de loi entre les parties ? » → ranks [None]
- G2-061 (fr) « Quelles sont les obligations principales du locataire ? » → ranks [None]
- G2-063 (fr) « À partir de quand l'acheteur devient-il propriétaire de la chose vendue ? » → ranks [None]
- G2-080 (fr) « Le personnel d'une maison de retraite peut-il acheter un bien appartenant à un résident ? » → ranks [None]
- G2-086 (fr) « Est-on punissable lorsqu'on blesse quelqu'un pour se défendre ? » → ranks [None]
- G2-095 (fr) « Quelle peine pour celui qui met le feu à un immeuble habité ? » → ranks [None]
- G2-109 (fr) « La police peut-elle entrer dans un domicile sans base légale ? » → ranks [None]
- G2-123 (fr) « Les audiences des tribunaux sont-elles publiques ? » → ranks [None]
- G2-130 (fr) « Comment la fonction de chef de l'État se transmet-elle au sein de la dynastie ? » → ranks [None]
- G2-136 (fr) « Comment le tiers saisi fait-il sa déclaration dans une saisie-arrêt ? » → ranks [16]
- G2-140 (fr) « Qui nomme les commissaires de la CNPD ? » → ranks [12]
- G2-143 (fr) « Quels pouvoirs d'investigation la CNPD possède-t-elle ? » → ranks [12]
- G2-153 (fr) « Combien de temps sont protégés les droits des artistes interprètes ? » → ranks [12]
- G2-162 (fr) « Un fonds d'investissement alternatif peut-il avoir plusieurs gestionnaires ? » → ranks [None]
- G2-167 (fr) « Quels actes sont publiés au Mémorial A ? » → ranks [None]
- G2-177 (fr) « Le Luxembourg extrade-t-il ses propres ressortissants ? » → ranks [None]
- G2-179 (fr) « Une personne peut-elle être extradée pour une infraction fiscale ou douanière ? » → ranks [None]
- G2-187 (de) « Darf einer schwangeren Arbeitnehmerin gekündigt werden? » → ranks [None]
- G2-188 (de) « Wie lange gilt das Urheberrecht nach dem Tod des Urhebers? » → ranks [None]
- G2-190 (de) « Ab welchem Alter darf man in Luxemburg heiraten? » → ranks [None]
- G2-195 (de) « Liefert Luxemburg eigene Staatsangehörige aus? » → ranks [None]
- G2-197 (en) « Can an employer dismiss an employee immediately for serious misconduct? » → ranks [None]
- G2-198 (en) « How long does copyright last after the death of the author? » → ranks [None]
- G2-199 (en) « Does the constitution protect the right to privacy? » → ranks [None]
- G2-200 (en) « On what grounds can a divorce be granted under the Civil Code? » → ranks [None]
- G2-201 (en) « How is sexual harassment at work defined? » → ranks [None]
- G2-205 (en) « When does a law published in the Official Journal become binding? » → ranks [11]
