# G2 — lexical/bm25

- release `data-20261016-sample`, run 20261017T122107Z, 218 items (draft_unvalidated), backend in-process

| subset | n | recall@1 | recall@5 | recall@10 | recall@20 | mrr@10 | ndcg@10 | act_recall@10 |
|---|---|---|---|---|---|---|---|---|
| all | 218 | 0.532 | 0.764 | 0.821 | 0.862 | 0.643 | 0.682 | 0.968 |
| fr | 198 | 0.566 | 0.800 | 0.854 | 0.894 | 0.679 | 0.717 | 0.980 |
| de | 10 | 0.100 | 0.500 | 0.600 | 0.600 | 0.247 | 0.331 | 0.800 |
| en | 10 | 0.300 | 0.300 | 0.400 | 0.500 | 0.317 | 0.336 | 0.900 |
| non_leaky | 187 | 0.503 | 0.730 | 0.797 | 0.845 | 0.616 | 0.655 | 0.963 |
| pdf_codes | 15 | 0.200 | 0.600 | 0.600 | 0.667 | 0.369 | 0.427 | 1.000 |

Latency: tool {'p50_ms': 229.5, 'p95_ms': 437.4, 'max_ms': 744.2}, SQL {'p50_ms': 186.1, 'p95_ms': 388.8, 'max_ms': 670.4}, rerank {}.

Targets not in the first 10 hits: 38 items.

- G2-043 (fr) « Un salarié à temps partiel est-il prioritaire pour un poste à temps plein qui se libère ? » → ranks [14]
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
- G2-119 (fr) « Peut-on être à la fois député et ministre ? » → ranks [11]
- G2-123 (fr) « Les audiences des tribunaux sont-elles publiques ? » → ranks [None]
- G2-130 (fr) « Comment la fonction de chef de l'État se transmet-elle au sein de la dynastie ? » → ranks [None]
- G2-136 (fr) « Comment le tiers saisi fait-il sa déclaration dans une saisie-arrêt ? » → ranks [16]
- G2-140 (fr) « Qui nomme les commissaires de la CNPD ? » → ranks [11]
- G2-143 (fr) « Quels pouvoirs d'investigation la CNPD possède-t-elle ? » → ranks [11]
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
- G2-206 (fr) « Un travailleur occupé moins d'un an au Grand-Duché et couvert par une caisse maladie étrangère peut-il échapper à l'affiliation ? » → ranks [None]
- G2-207 (fr) « L'époux et les enfants d'un assuré profitent-ils aussi de sa couverture maladie ? » → ranks [None]
- G2-208 (fr) « Comment calcule-t-on l'indemnité de maladie d'un salarié en arrêt de travail ? » → ranks [11]
- G2-209 (fr) « Le patient doit-il avancer les frais médicaux ou la CNS peut-elle payer directement le prestataire ? » → ranks [None]
- G2-210 (fr) « Une veuve remariée qui divorce récupère-t-elle sa pension de survie ? » → ranks [None]
- G2-214 (fr) « Dans quel délai l'embauche ou le départ d'un salarié doit-il être signalé au Centre commun ? » → ranks [None]
