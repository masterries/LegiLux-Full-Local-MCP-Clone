# MCP contract tests (M1 MCP stage, Appendix D.3)

Every tool is called through both client eras — `mcp==2.3.0` `Client` (main environment) and
`mcp==1.27.2` `ClientSession` (as used by Open WebUI 0.12.3) — and results must validate against the
output schemas. The 1.x client conflicts with the locked 2.3.0, so run that half in an isolated
environment: `uv run --isolated --with mcp==1.27.2 python -m pytest tests/contract -m legacy_client`.
