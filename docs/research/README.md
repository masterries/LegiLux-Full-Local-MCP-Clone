# Research notes (2026-10-16)

Raw findings from the live research that informed [`../PLAN.md`](../PLAN.md). Each track probed
`https://data.legilux.public.lu/sparqlendpoint` and `https://legilux.public.lu` directly (at most
1 request per second, with a descriptive User-Agent, respecting robots.txt).

| File | Track |
|---|---|
| `sparql-jolux-model.md` | JOLux/ELI data model, endpoint limits, tested queries |
| `content-formats.md` | ELI URI patterns, Akoma Ntoso CSD13 XML, HTML and PDF manifestations, parsing strategy |
| `scope-sizing.md` | What counts as "current and relevant", corpus counts, storage estimates, licensing |
| `db-retrieval-design.md` | Database comparison, schema, hybrid retrieval, evaluation |
| `airgap-llm-mcp-stack.md` | MCP SDK, inference servers, models, offline packaging |
| `prior-art.md` | Existing Legilux, JOLux and Fedlex tooling we can reuse |
| `gaps-and-reconciliation.md` | Cross-check of all the tracks above: resolves contradictions with fresh live checks. **Authoritative where tracks disagree.** |
| `sparql-catalog/` | The tested SPARQL queries (C01–C16) and a small runner script |

These are working notes. Where they conflict with `docs/PLAN.md`, the plan wins. Items marked
**UNVERIFIED** were not confirmed live. Paths such as `<research-scratch>/…` refer to sample
files from the research sandbox, which are not part of this repository.
