# G2 — lexical/rank_cd

- release `data-20261016-sample`, run 20261017T064807Z, 205 items (draft_unvalidated), backend in-process

| subset | n | recall@1 | recall@5 | recall@10 | recall@20 | mrr@10 | ndcg@10 | act_recall@10 |
|---|---|---|---|---|---|---|---|---|
| all | 205 | 0.229 | 0.388 | 0.434 | 0.471 | 0.302 | 0.333 | 0.722 |
| fr | 185 | 0.254 | 0.430 | 0.481 | 0.516 | 0.334 | 0.369 | 0.751 |
| de | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.300 |
| en | 10 | 0.000 | 0.000 | 0.000 | 0.100 | 0.000 | 0.000 | 0.600 |

Latency: tool {'p50_ms': 204.7, 'p95_ms': 501.4, 'max_ms': 630.7}, SQL {'p50_ms': 165.8, 'p95_ms': 442.5, 'max_ms': 585.5}, rerank {}.

Targets not in the first 10 hits: 116 items.

- G2-003 (fr) « Combien de temps de préavis un salarié doit-il respecter s'il démissionne de son CDI ? » → ranks [None]
- G2-008 (fr) « Le salarié licencié peut-il exiger que l'employeur lui communique les motifs du licenciement ? » → ranks [None]
- G2-015 (fr) « Comment la loi définit-elle le harcèlement sexuel sur le lieu de travail ? » → ranks [None, 15]
- G2-017 (fr) « Définition du harcèlement moral dans les relations de travail » → ranks [None]
- G2-019 (fr) « Comment une salariée enceinte demande-t-elle à être dispensée du travail de nuit ? » → ranks [None]
- G2-025 (fr) « Le congé culturel peut-il être fractionné en plusieurs parties ? » → ranks [None]
- G2-027 (fr) « Un salarié qui a plusieurs emplois doit-il les déclarer à l'Inspection du travail ? » → ranks [None]
- G2-029 (fr) « Le médecin du travail peut-il vérifier si un congé de maladie est justifié ? » → ranks [None]
- G2-030 (fr) « Sanctions pénales contre l'employeur qui n'organise pas les examens médicaux du médecin du travail » → ranks [None]
- G2-034 (fr) « Quelles conditions météorologiques ouvrent droit à l'indemnisation du chômage dû aux intempéries ? » → ranks [None]
- G2-035 (fr) « Dans quels cas l'indemnité de préretraite prend-elle fin ? » → ranks [19]
- G2-036 (fr) « Un salarié qui démissionne doit-il rembourser les frais de formation continue payés par l'employeur ? » → ranks [None]
- G2-037 (fr) « Les délégués du personnel sont-ils payés pour leur mandat et qui prend en charge leurs frais ? » → ranks [None]
- G2-038 (fr) « Quel salaire minimum pour un étudiant qui travaille pendant les vacances scolaires ? » → ranks [None]
- G2-042 (fr) « L'apprenti reçoit-il une indemnité pendant son apprentissage ? » → ranks [None]
- G2-043 (fr) « Un salarié à temps partiel est-il prioritaire pour un poste à temps plein qui se libère ? » → ranks [None]
- G2-046 (fr) « Un salarié peut-il être sanctionné pour avoir porté plainte afin de faire respecter ses droits ? » → ranks [None]
- G2-049 (fr) « Quels devoirs les époux ont-ils l'un envers l'autre ? » → ranks [None, None]
- G2-051 (fr) « Quel est l'âge minimum pour se marier ? » → ranks [None]
- G2-053 (fr) « Pour quelles causes le divorce peut-il être prononcé ? » → ranks [None]
- G2-056 (fr) « De quelles manières le mariage est-il dissous ? » → ranks [None]
- G2-057 (fr) « Après le divorce, le parent qui garde les enfants peut-il obtenir le logement familial ? » → ranks [None]
- G2-059 (fr) « Un contrat valablement conclu a-t-il force de loi entre les parties ? » → ranks [None]
- G2-060 (fr) « Le vendeur répond-il des vices cachés de la chose vendue même s'il les ignorait ? » → ranks [None]
- G2-061 (fr) « Quelles sont les obligations principales du locataire ? » → ranks [None]
- G2-062 (fr) « Quelles sont les obligations principales du vendeur ? » → ranks [None]
- G2-063 (fr) « À partir de quand l'acheteur devient-il propriétaire de la chose vendue ? » → ranks [None]
- G2-067 (fr) « Selon quelles règles les successions sont-elles dévolues aux héritiers ? » → ranks [None, None]
- G2-068 (fr) « Les enfants héritent-ils à parts égales de leurs parents ? » → ranks [None]
- G2-069 (fr) « Les frères et sœurs du défunt héritent-ils si ses parents sont déjà décédés ? » → ranks [None]
- G2-070 (fr) « Où un enfant mineur est-il domicilié lorsque ses parents ont des domiciles différents ? » → ranks [14]
- G2-073 (fr) « Quelle protection pour un adulte dont les facultés mentales sont altérées par la maladie ou l'âge ? » → ranks [None]
- G2-074 (fr) « Les donations faites entre époux pendant le mariage peuvent-elles être révoquées ? » → ranks [None]
- G2-076 (fr) « Qui supporte les frais d'inscription d'une hypothèque ? » → ranks [None]
- G2-078 (fr) « Les époux peuvent-ils organiser librement leur régime matrimonial par contrat ? » → ranks [None]
- G2-080 (fr) « Le personnel d'une maison de retraite peut-il acheter un bien appartenant à un résident ? » → ranks [None]
- G2-081 (fr) « Le locataire troublé par une action concernant la propriété du fonds a-t-il droit à une diminution du loyer ? » → ranks [None]
- G2-083 (fr) « Quelle peine encourt l'auteur d'un meurtre ? » → ranks [None]
- G2-084 (fr) « Quelle est la différence entre meurtre et assassinat ? » → ranks [None, None]
- G2-086 (fr) « Est-on punissable lorsqu'on blesse quelqu'un pour se défendre ? » → ranks [None]
