# G1 — lexical/bm25

- release `data-20261016-sample`, run 20261017T085306Z, 566 items (draft_unvalidated), backend in-process

| metric | value |
|---|---|
| n | 566 |
| exact_accuracy | 0.995 |
| citation_precision | 0.995 |
| resolved_recall | 0.998 |
| ambiguity_precision | 1.000 |
| ambiguity_recall | 0.991 |
| not_found_accuracy | 1.000 |

Outcomes: {'correct': 563, 'abstained': 0, 'wrong': 3, 'missed': 0}; gates {'exact_accuracy>=0.95': True, 'ambiguity_recall>=0.90': True}

| style | n | correct |
|---|---|---|
| alias:acronym | 6 | 6 |
| alias:code_abbrev | 9 | 9 |
| alias:curated | 3 | 3 |
| alias:short_title | 12 | 12 |
| ambiguous:dated | 55 | 55 |
| ambiguous:dated_text | 26 | 26 |
| code:1er | 4 | 4 |
| code:abbrev | 20 | 20 |
| code:art_du | 25 | 25 |
| code:article_du | 20 | 20 |
| code:backward | 20 | 20 |
| code:bis_glued | 14 | 14 |
| code:bis_spaced | 14 | 14 |
| code:capital_art | 16 | 16 |
| code:compact | 4 | 4 |
| code:l_article | 25 | 25 |
| code:paragraph | 20 | 20 |
| code:range_member | 9 | 9 |
| code_recueil:member_number | 6 | 5 |
| dated:article_title | 21 | 21 |
| dated:backward | 18 | 17 |
| dated:modifiee_title | 12 | 12 |
| dated:title | 22 | 22 |
| dated:title_among_same_date | 25 | 25 |
| dated:unique_date | 43 | 43 |
| eli:article | 8 | 8 |
| eli:display_url | 14 | 14 |
| eli:jo | 8 | 8 |
| eli:resource_uri | 8 | 8 |
| eli:version | 14 | 14 |
| eli:work | 14 | 14 |
| memorial:long | 12 | 12 |
| memorial:short | 18 | 18 |
| not_found:article | 10 | 10 |
| not_found:date | 4 | 4 |
| not_found:eli | 2 | 2 |
| not_found:memorial | 1 | 1 |
| out_of_scope:alias | 4 | 3 |

Not correct: 3

- G1-204 [code_recueil:member_number] « article 106 du Code de la fonction publique »: expected ambiguous http://data.legilux.public.lu/eli/etat/leg/code/fonction_publique — got resolved http://data.legilux.public.lu/eli/etat/leg/code/fonction_publique (wrong)
- G1-288 [dated:backward] « Loi du 5 mars 1922 portant approbation de la Convention d'Union économique, art. unique »: expected resolved http://data.legilux.public.lu/eli/etat/leg/loi/1922/03/05/n1/article_unique — got resolved http://data.legilux.public.lu/eli/etat/leg/loi/1922/03/05/n1 (wrong)
- G1-564 [out_of_scope:alias] « règlement général sur la protection des données »: expected out_of_scope  — got not_found  (wrong)
