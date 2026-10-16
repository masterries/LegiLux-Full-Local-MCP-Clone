# LegiLux-Full-Local-MCP-Clone

An airgap-deployable, Docker-packaged local clone of the **current and relevant** part of
[Legilux](https://legilux.public.lu), the Luxembourg legal portal. A local LLM can query it
through an **MCP server** to answer legal questions in detail, with article-level citations.

- **Plan:** [`docs/PLAN.md`](docs/PLAN.md), with appendices in [`docs/plan/`](docs/plan/)
- **Short summary (German):** [`docs/PLAN_KURZFASSUNG.md`](docs/PLAN_KURZFASSUNG.md)
- **Research notes and tested SPARQL queries:** [`docs/research/`](docs/research/)
- **Developer quick start:** [`docs/dev/DEVELOPMENT.md`](docs/dev/DEVELOPMENT.md) ·
  stage contracts: [`docs/dev/INTERFACES.md`](docs/dev/INTERFACES.md)

```sh
uv sync --all-extras && uv run lgx config-check
make db-up db-schema && make test
```

Data source: Legilux, © Ministère d'État – Service central de législation, licensed under
CC BY 4.0. This is an unofficial copy: only the published Journal officiel is authoritative
(loi du 23 décembre 2016, art. 5).
