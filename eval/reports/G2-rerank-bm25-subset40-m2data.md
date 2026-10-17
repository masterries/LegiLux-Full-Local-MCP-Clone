# G2 — rerank/bm25 k=16 600c

- release `data-20261016-sample`, run 20261017T085648Z, 40 items (draft_unvalidated), backend in-process

| subset | n | recall@1 | recall@5 | recall@10 | recall@20 | mrr@10 | ndcg@10 | act_recall@10 |
|---|---|---|---|---|---|---|---|---|
| all | 40 | 0.713 | 0.750 | 0.825 | 0.825 | 0.745 | 0.763 | 0.950 |
| fr | 29 | 0.845 | 0.897 | 0.966 | 0.966 | 0.885 | 0.903 | 1.000 |
| de | 6 | 0.333 | 0.333 | 0.500 | 0.500 | 0.357 | 0.389 | 0.667 |
| en | 5 | 0.400 | 0.400 | 0.400 | 0.400 | 0.400 | 0.400 | 1.000 |

Latency: tool {'p50_ms': 17076.4, 'p95_ms': 22609.1, 'max_ms': 25280.3}, SQL {'p50_ms': 257.6, 'p95_ms': 676.2, 'max_ms': 1050.3}, rerank {'p50_ms': 16736.4, 'p95_ms': 22405.6, 'max_ms': 25061.6}.

Targets not in the first 10 hits: 7 items.

- G2-177 (fr) « Le Luxembourg extrade-t-il ses propres ressortissants ? » → ranks [None]
- G2-188 (de) « Wie lange gilt das Urheberrecht nach dem Tod des Urhebers? » → ranks [None]
- G2-190 (de) « Ab welchem Alter darf man in Luxemburg heiraten? » → ranks [None]
- G2-195 (de) « Liefert Luxemburg eigene Staatsangehörige aus? » → ranks [None]
- G2-198 (en) « How long does copyright last after the death of the author? » → ranks [None]
- G2-199 (en) « Does the constitution protect the right to privacy? » → ranks [None]
- G2-200 (en) « On what grounds can a divorce be granted under the Civil Code? » → ranks [None]
