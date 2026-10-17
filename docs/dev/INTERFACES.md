# Interfaces and ownership (M0 contract for M1 / M1b)

This document is the contract between the stages that are built in parallel after M0. It fixes **who owns
which files**, the **function signatures** the CLI calls, the **on-disk layout** between stages and the
**record types** (`lgx_core/models.py`). If code and this document disagree, fix one of them in the same
change and say so in your report.

- PLAN references: §2 (selection), §3.3 (politeness), §6 (pipeline), §7 + Appendix B (DB), §8 + Appendix D (MCP).
- Development uses the **sample** only (`config/sample.yaml`). Never run a full crawl. Full SPARQL metadata
  pulls are part of the sample path (PLAN §6.2); file GETs are not.
- Embeddings are **off** (`provider: none`). No stage computes vectors before M3.

## 0. Ownership

| Area | Files | Owner |
|---|---|---|
| Shared foundation (M0) | `lgx_core/{models,config,eli,io}.py`, `lgx_core/warnings/`, `lgx_core/embed/`, `lgx_ingest/{cli,context,sparql_templates}.py`, `config/*.yaml`, `sparql/`, `db/`, `scripts/`, `pyproject.toml`, `uv.lock`, `Makefile`, CI | orchestrator (M0) |
| **Agent A** — metadata + selection | `lgx_ingest/{robots,sparql,select}.py`, `lgx_core/rules/*`, tests under `tests/unit/test_{robots,sparql,select,rules}*.py`, `tests/golden/` | agent A |
| **Agent B** — fetch + parsers | `lgx_ingest/{fetch,structure,chunk}.py`, `lgx_ingest/parse/*`, `lgx_core/normalise/*`, tests under `tests/unit/test_{fetch,parse,akn,html,chunk,normalise}*.py`, small CC-BY fixtures in `tests/fixtures/legilux/` | agent B |
| Loader + gates (after A and B) | `lgx_ingest/{load,gates,report,package,cli_ingest}.py`, `db/schema/*` (since M1), tests `tests/{unit,db}/test_load*.py`, `tests/unit/load_fixture.py` | loader stage |
| MCP server | `lgx_mcp/`, `lgx_core/citeparse/`, `lgx_core/verify/` | MCP stage |
| Streamlit UI | `ui/` (package `lgx_ui`) | M1b |

Rules:
- Do not edit files you do not own. All dependencies are already in `pyproject.toml`/`uv.lock` (httpx, lxml,
  pyarrow, pdfplumber, psycopg, mcp 2.3.0, streamlit 1.65.0, openai 3.24.0, respx, pytest-asyncio …).
- If a shared file truly needs a change (a missing model field, a new `DataLayout` path), make the
  **smallest additive** change, never rename or remove, and list it under "shared-file changes" in your
  report. Prefer `--opt KEY=VALUE` (→ `ctx.options`) over new CLI flags.
- Offline unit tests must not touch the network: mock HTTP with `respx`. Real requests only in
  `@pytest.mark.live` tests (skipped unless `--run-live`), polite (≤ 1 req/s, project User-Agent).
- Typed Python, small modules; names from the plan (tables, labels, warning codes, tool names).

## 1. CLI → stage functions

`lgx` (`lgx_ingest/cli.py`) parses options, builds a `StageContext` and calls exactly these functions.
A stage that raises `NotImplementedError` makes the CLI exit with code **3** ("not implemented yet").

| Command | Function (module:function) | Signature |
|---|---|---|
| `lgx robots` | `lgx_ingest.robots:run_robots` | `(ctx: StageContext) -> StageResult` |
| `lgx robots-ack SHA` | `lgx_ingest.robots:acknowledge` | `(ctx, sha256: str) -> StageResult` |
| `lgx robots check` / `lgx robots ack SHA` | same functions (group added by `lgx_ingest/cli_meta.py`; `lgx robots` alone still runs S0) | — |
| `lgx meta pull [-q …] [--manif sample\|all\|none]` | `lgx_ingest.sparql:run_discover` | as `lgx discover` |
| `lgx meta select [--date D] [--snapshot DIR]` | `lgx_ingest.select:run_select` | as `lgx select`, selection at any date / on any snapshot |
| `lgx meta golden [--snapshot DIR]` | `lgx_ingest.select:golden_check` | PLAN §11.5 expected vs actual (also `scripts/check_golden.py`) |
| — (used by fetch) | `lgx_ingest.robots:load_policy` | `(ctx) -> UrlPolicy` |
| `lgx discover [-q q01 …]` | `lgx_ingest.sparql:run_discover` | `(ctx, queries: Sequence[str] \| None = None) -> StageResult` |
| `lgx select [--sample/--full]` | `lgx_ingest.select:run_select` | `(ctx) -> StageResult` |
| `lgx fetch [--limit N] [--include-m2] [--dry-run]` | `lgx_ingest.fetch:run_fetch` | `(ctx, *, limit: int \| None = None, include_m2: bool = False) -> StageResult` |
| `lgx parse` | `lgx_ingest.parse:run_parse` | `(ctx) -> StageResult` |
| — | `lgx_ingest.parse:parse_file` | `(path: Path, entry: ManifestEntry, raw: RawFile) -> ParseResult` |
| `lgx structure` | `lgx_ingest.structure:run_structure` | `(ctx) -> StageResult` (may be folded into parse in M1) |
| `lgx chunk` | `lgx_ingest.chunk:run_chunk` | `(ctx) -> StageResult` |
| — | `lgx_ingest.chunk:chunk_units` | `(units: Sequence[Unit], *, title_short: str, version_label: str) -> list[Chunk]` |
| `lgx load [--dsn] [--db-name] [--recreate]` | `lgx_ingest.load:run_load` | `(ctx, *, dsn: str \| None = None, db_name: str \| None = None, recreate: bool = True) -> StageResult` |
| `lgx gates` / `lgx report` | `lgx_ingest.gates:run_gates` / `lgx_ingest.report:run_report` | `(ctx) -> StageResult` (gates also take `*, dsn=, db_name=`; CLI: `--opt dsn=… --opt db_name=…`) |
| `lgx ingest [--sample/--full] [--refresh] [--offline] [--revalidate] [--load/--no-load] [--dsn] [--db-name] [--package [--container C] [--restore-to DB]] [--include-m2]` | `lgx_ingest.cli_ingest:run_ingest` | robots → discover → select → fetch → parse → chunk → load → gates → report [→ package] (§6) |
| `lgx package [--dsn] [--db-name] [--out-dir] [--container C] [--restore-to DB]` | `lgx_ingest.package:run_package` | `(ctx, *, dsn=None, db_name=None, out_dir=None, container=None, restore_to=None) -> StageResult` |
| `lgx embed` | built in: provider `none` → no-op | — |
| `lgx config-check`, `lgx paths`, `lgx version` | built in | — |

Global options: `--t/--sync-date YYYY-MM-DD|auto`, `--config-dir`, `--data-dir`, `-o/--opt KEY=VALUE`.

`StageContext` (`lgx_ingest/context.py`) gives every stage: `sync_date` (T), `settings` (`LGX_*` env),
`config` (`ProjectConfig`: scope, sample, embedding, warnings, legal_texts, aliases, denylist,
recueil_members), `layout` (`DataLayout`), `sample: bool`, `dry_run: bool`, `options`, `console`.
Stages never read `os.environ` or `config/` themselves. `StageResult` = `{stage, ok, counts, outputs,
messages, details}`; the CLI prints it as JSON and exits 1 when `ok` is false.

`UrlPolicy` (protocol in `context.py`): `allows(url) -> bool` (robots.txt of the URL's host) and
`is_denied_eli(eli) -> bool` (robots-derived + manual + GDPR denylist, work-prefix match).

## 2. Data layout (`lgx_core.config.DataLayout`, root `data/`, gitignored, never shipped)

`<T>` is the ISO sync date (`2026-10-16`). Writers write atomically (`lgx_core.io.atomic_write_text`,
`write_jsonl`); append-only logs use `append_jsonl`.

| Path | Format / record | Writer → readers |
|---|---|---|
| `robots/<T>/robots-legilux.txt`, `robots-data.txt`, `robots.json`, `denylist.json` | raw files; `{host: {sha256, fetched_at, disallow[]}}`; generated ELI denylist | robots → fetch, select |
| `robots/ack.json` | acknowledged robots sha256 per host | `robots-ack` → robots |
| `meta/<T>/<table>.parquet` (§3) | SPARQL snapshot tables, zstd parquet | discover → select |
| `meta/<T>/_snapshot.json` | per query: file, query sha256, rows, bytes, seconds, fetched_at; Q17 completeness | discover → select, gates |
| `meta/<T>/serving_plan.parquet` | one row per act of the metadata scope: label, rule, flags, served version + file + licence, text window, future version, amending list (`lgx_ingest.select.SERVING_PLAN_SCHEMA`) | select → report, review |
| `manifest/<T>/data_quality.jsonl` | `DataQualityIssue` (flag/date conflicts, URI-date mismatches, flag/window conflicts) | select → load |
| `manifest/<T>/gdpr_review.json` | naturalisation-titled LOIs **not** caught by the title rule, for lawyer review (never shipped) | select → lawyer |
| `manifest/<T>/manifest.jsonl` | `ManifestEntry` | select → fetch, parse, chunk, load |
| `manifest/<T>/acts.jsonl` | `Act` (whole metadata scope) | select → load |
| `manifest/<T>/versions.jsonl` | `ActVersion` (all versions; fetch provenance empty) | select → load |
| `manifest/<T>/relations.jsonl` | `Relation` (`source = sparql`) | select → load |
| `manifest/<T>/selection_report.json` | §4 | select → gates, report |
| `raw/<sha[:2]>/<sha256><ext>` | fetched bytes, content-addressed (`ext` = `.xml`/`.html`/`.pdf`) | fetch → parse |
| `raw/index.jsonl` | `RawFile`, append-only; the **last** line per `url` wins | fetch → parse, load |
| `logs/fetch_log.jsonl` | `FetchLogEntry`, every attempt incl. 304/errors (90-day retention) | fetch → gates |
| `parsed/<sha256>.<parser_version>.json` | `ParseResult` (cache keyed by file hash + parser version) | parse → structure |
| `units/<T>/<eli_slug(version)>.jsonl` | `Unit`, one line per unit, document order | parse/structure → chunk, load |
| `units/<T>/<eli_slug(version)>.relations.jsonl` | `Relation` from AKN (`akn-ref`, `akn-textualmod`, `akn-note`) | parse → load |
| `chunks/<T>/<eli_slug(version)>.jsonl` | `Chunk` | chunk → load |
| `reports/<T>/{load,gates,sizing}.json` | loader result, gate results, sizing report | load/gates/report |
| `bundles/data-<T>[-sample].{dump,release.json,sha256}` | `pg_dump -Fc` of the release + manifest + checksums | package → restore |

`eli_slug("http://data.legilux.public.lu/eli/etat/leg/code/travail/20260726") == "eli-etat-leg-code-travail-20260726"`.

## 3. Agent A — robots (S0) and discover (S1)

**robots** (PLAN §3.2): fetch `https://legilux.public.lu/robots.txt` and `https://data.legilux.public.lu/robots.txt`
every run; compare sha256 with `robots/ack.json`; if changed and unacknowledged → `ok=false` (fetch paused);
if `/filestore` becomes disallowed on the main host → hard stop. Specific URIs become an ELI denylist
(work prefixes). `load_policy(ctx)` returns the `UrlPolicy` used by fetch. Unit tests use the robots files in
`tests/fixtures/` (copy them from the research scratch, they are public).

**discover**: queries from `sparql/*.rq` rendered with `lgx_ingest.sparql_templates.render_query`
(`{{NAME}}` placeholders, typed values only; `sparql/README.md`). POST form-encoded, `Accept: text/csv`
(CONSTRUCT: `application/n-triples`), sequential, ≥ `max(settings.rate_limit_s, scope.fetch.min_spacing_s)`
apart, `settings.user_agent`. Run Q17 with every Q01/Q02 pull (completeness gate).

Snapshot tables (columns = SPARQL variable names; IRIs as strings; vocabulary IRIs reduced to their
last path segment where marked *code*; dates as `date32`, datetimes as UTC `timestamp`; values that do
not parse become null and the raw string is kept in `<col>_raw`). Rows are kept as returned (duplicates
included) — aggregation is selection's job.

| Table | Query | Columns |
|---|---|---|
| `acts` | q01 | `a` (…/jo), `cw`, `type` *code*, `st` *code*, `dei`, `dnl`, `pub` |
| `consolidations` | q02 | `c`, `cw`, `d`, `end`, `st` *code*, `type` *code*, `created` (timestamp), `pub` |
| `conso_formats` | q03 | `c`, `uf` *code*, `lic` (IRI), `lang` *code* (`FRA`) |
| `conso_base_acts` | q04 | `cw`, `act`, `ast` *code*, `atype` *code*, `dnl` |
| `rel_modifies_repeals` | q05 | `src`, `rel` (`modifies`/`repeals`), `tgt`, `dei` |
| `rel_other` | q06 | `src`, `rel` (`basedOn`/`cites`/`rectifies`/`transposes`), `tgt` |
| `titles` | q07 | `a`, `title` |
| `memorial_refs` | q08 | `a`, `h`, `mem` |
| `vocab` | q10 | `concept_uri`, `scheme`, `code`, `label_fr`, `label_de`, `label_en` (= `VocabConcept`) |
| `manifestations` | q_manif (≤ 500 IRIs per POST) | `w`, `uf` *code*, `lic`, `file`, `mod` (timestamp) |
| `act_formats` | q19 (M1, one flat request; IRIS = the 18 text types) | `a`, `uf` *code*, `lic` |

M1 additions (agent A): parquet rows are sorted by all columns (key first) so files and the snapshot digest
do not depend on server order; `_snapshot.json` also carries `act_formats_coverage` (`null` = every
`/etat/leg/` act of the text types; a `{doc_types, rdf_status}` filter for older snapshots, outside of which
the licence is *unknown*), `manifestations_scope`, `snapshot_digest` and per table `partitioned`/`errors`.
Partitioned fallbacks live in `sparql/partitions/` (year windows, predicates, types).

Q-MANIF must cover at least every version that gets a `file` in the manifest (in sample mode: the
sample works). Licence decisions for non-sampled JO acts use `act_formats` (Q19, which widens
`checks/q09_licence_crosstab` to every status and text type; the Q09 gate numbers are recomputed from it);
the source per act is in the serving plan (`licence_source`: `q_manif` | `conso_formats` | `act_formats` |
`unknown`) and summarised in the selection report (`licence_sources`).

Golden numbers (PLAN §11.5) are asserted on the **frozen 2026-10-16 snapshot**: the research pulls are at
`/tmp/claude-0/-home-user-LegiLux-Full-Local-MCP-Clone/dc1ab714-bae7-500d-838b-586da454eb94/scratchpad/research/gaps-and-reconciliation/out/`
(`a1`, `c1`, `c2`, `c3`, `m1`, `m2`, `b1`, `l4`, `histid`) plus `…/scratchpad/plan/verify/w/titles.csv`
and `relother.csv`. Convert them once into `tests/golden/snapshot-20261016/*.parquet` (zstd; parquet is
gitignored elsewhere but allowed there) using the §3 table schemas, so the golden tests run offline.

## 4. Agent A — select (S2) and rules

`lgx_core.rules` is pure (no I/O): `status.act_status(dei, dnl, rdf_status, as_of) -> ActStatus`
(twin of SQL `lgx.act_status`), `select.select(snapshot, T, config) -> Selection`, `serving.serving_at(act,
versions, relations, as_of)` (twin of `lgx.serving_at`). `lgx_ingest.select.run_select` only reads the
parquet tables, calls `select`, and writes §2 outputs.

Decisions fixed in M0 (tell the orchestrator if a rule forces a change):
- `rdf_status` is stored as the status **code** (`in-force`, `no-longer-in-force-implicit` …), which the
  SQL `lgx.act_status` compares against.
- `Act.doc_type` / `ManifestEntry.doc_type` / `Chunk.doc_type` (the search filter type): the consolidation
  `typeDocument` for works whose served text is a `CODE`, `CODE_RECUEIL`, `RECUEIL` or `Constitution`
  consolidation (fallback: base-act type when the version has no type), else the act's own type.
- `Act.eli_work` is the `jolux:isMemberOf` target (codes belong to their base act, e.g. `code/travail/*` →
  `loi/2006/07/31/n2`; RECUEIL/CODE_RECUEIL works without base act use their own work IRI, `eli_jo = null`).
- SPARQL relations keep the `/jo` IRIs on both ends; the loader resolves them via `act.eli_jo`.
- One `ManifestEntry` per stored or metadata-only version, plus one per metadata-only act without any
  version. `file` is set iff text must be fetched; then `text_valid` is the serving window (§2.2 g).
  `amending_acts` is mandatory for `original_possibly_outdated` and `stub_fallback`.
- Sample mode: `in_sample = True` and `sample_stage` from `config/sample.yaml` for entries of sample works
  (`SampleConfig.by_work()`); fetch uses them. Selection itself always covers the full metadata scope.
- `reason` is a stable rule id: `0:excluded`, `1:…`, `2a:repealed_base`, `2b:consolidated`, `2c:stub_fallback`,
  `2c:stub_no_text`, `2d:discontinued`, `2e:future`, `2f:metadata_version`, `3:original_never_amended`,
  `3:original_possibly_outdated`, `3:temporary`, `3:licence`, `4:metadata_only` (extend, don't rename).

`selection_report.json`: `{sync_date, config_digest, snapshot_digest, counts: {by_text_basis, by_flag,
by_role, by_reason}, golden: {name: value}, anomalies: [...], sample: {works, matched, mismatches: [{work,
expected, actual}]}}`. `config/sample.yaml` expectations (computed offline on the frozen snapshot) must
match on the frozen snapshot; mismatches are triaged, not silently fixed.

M1 implementation notes (agent A):
- `select(snapshot, T, config, *, denylist=(), works=None)`: `denylist` adds the robots-derived prefixes
  (`data/robots/<T>/denylist.json`, read by `run_select`); `works` restricts the output (Q-MANIF candidates,
  tests). The report also has `golden.research` (research definitions) and `golden.selection` (rule
  counts), `counts.by_act_reason`, `licence_sources`, `counts.excluded`.
- Sample mode: `text_valid` is set on every version whose text the rules store (the serving decision);
  `file` only where Q-MANIF ran (the sample works, or all with `--full` / `--opt manif=all`).
- `ActVersion` rows carry `source_format/source_url/licence` of the chosen file but `has_text = false`
  and `text_valid = null` (the model requires sha256 for text); the loader sets both from the manifest and
  `raw/index.jsonl`. JO originals get a version row only when their text is served.
- Extra rule ids: `2b:excluded_by_config`, `2d:discontinued_last_consolidation`, `2d:recueil_ended`,
  `2e:future_no_files`, `2e:future_only`, `2e:original_after_end` (JO original stored, role `future`, from
  the end date of a current version that has no successor), `2f:history_text`, `3:pdf_only`, `3:amending_metadata_only`
  (`lgx_core.rules.select.REASONS`).
- Runtime parity: `serving_at(T)` equals the build label for every act except not-yet-in-force acts with
  stored text (their window starts at `dei`, so the runtime says `metadata_only` + `not-yet-in-force` until
  then). Tested against the SQL twins in `tests/db/test_rules_parity.py`.

## 5. Agent B — fetch (S3), parse (S4/S5), normalise, chunk (S6)

Until `lgx select` exists, develop against `tests/fixtures/manifest/bootstrap_sample.jsonl` (12
`ManifestEntry` rows: codes, a686, AIFM, copyright law, a88, a936, n25, two CC-BY HTML JO acts …).

**fetch**: iterate manifest entries with `file`; in sample mode only `in_sample` entries, and only
`sample_stage == "m1"` unless `include_m2`. Before every request: `lgx_core.eli.assert_fetchable(url)`
(only `https://legilux.public.lu/filestore/eli/etat/leg/…`, no docx/adm/reg_ue/dir_ue), then
`policy.allows(url)` and `not policy.is_denied_eli(entry.eli_version)`. One connection; spacing
≥ `max(settings.rate_limit_s, scope.fetch.min_spacing_s)` (1.2 s); `User-Agent: settings.user_agent`;
conditional GET with `If-Modified-Since` from the last `RawFile` for that URL (304 → reuse); **never HEAD**;
retries/backoff/`Retry-After`, circuit breaker and hourly/daily caps from `scope.fetch`; refuse files above
`max_file_bytes` unless allow-listed. Store bytes at `raw/<sha[:2]>/<sha256><ext>`, append `RawFile` to
`raw/index.jsonl` and a `FetchLogEntry` for every attempt. Resumable (skip URLs already fetched this run).
`--dry-run` lists what would be fetched.

**parse**: `parse_file(path, entry, raw)` dispatches on `entry.file.source_format`: XML → `parse/akn.py`,
HTML → `parse/html.py` (CC-BY path for ~15.5k acts), PDF → `parse/pdf.py` (M2; PDF entries are `m2` in the
sample). Rules: PLAN §6.3 (no units from `embeddedStructure`; `<article style="del">`/`<del>`/« Abrogé »
→ `is_repealed` stubs with no deleted text in `text_plain`; base64 images → `[image: alt]`; tables → Markdown
in `text_md`; text nodes concatenated without inserted spaces, then whitespace-normalised; no metadata from
`<meta>`; article ELIs from `scl:JOLUXWork//scl:jolux[@scl:name='uriThis']` [1] work, [2] version;
`unit_version_date` from that article's `dateApplicability`, always shown as inferred; JO originals have no
article ELIs — `eli_unit` stays null, a derived `<work>/art_N` sets `eli_unit_derived = true`).

`Unit` contract (`lgx_core.models.Unit`): `ordinal` 1..n in document order, unique per version;
`parent_ordinal` < `ordinal`; `anchor` = AKN id / HTML anchor / `p.N-M`; generated recueil ids
(`art_N10064`) are never anchors; `breadcrumb` joins ancestor headings with `" > "`; provenance fields
(`doc_type`, `source_format`, `source_url`, `source_sha256`, `licence`, `text_basis`, `flags`) are copied
from the manifest entry / `RawFile`; `text_sha256` defaults to sha256(`text_plain`). Write
`ParseResult` to `parsed/…json` and units to `units/<T>/<slug>.jsonl` (+ `.relations.jsonl`).

**normalise** (`lgx_core/normalise`): `num_norm('Art. L. 121-1.') == 'l121-1'`, `num_norm('Art. 1er.') == '1'`,
`bis/ter/…` kept; ranges/lists (`art_9_10`, `art_9_et_10`, `art_17--20`) produce extra `article_alias` keys;
`normalise_whitespace`; `alias_norm` = lower + unaccent + collapsed spaces.

**chunk** (PLAN §6.4): one chunk per article; header `"{titre court} — {version label} > Livre … > Art. X."`
+ index terms; split > ~1,500 tokens at paragraph boundaries (header repeated, `part_no/part_count`); never
merge; no chunks for repealed stubs, preamble/visas, modifying articles, table annexes; `searchable=false` for
`superseded_by_code`; `valid_from/valid_to` = the version's `text_valid`; `content_sha256 = sha256(header + body)`.

M1 implementation notes (agent B):
- **Inputs.** `fetch`/`parse`/`chunk` read `manifest/<T>/manifest.jsonl`, or `--opt manifest=PATH` (`.jsonl`,
  a parquet export of manifest rows, or `meta/<T>/serving_plan.parquet`, converted by
  `lgx_ingest.fetch.entries_from_serving_plan`: served version + stored future version). `lgx content fetch
  --plan PATH` and `lgx content parse FILE` (`lgx_ingest/cli_content.py`) wrap the same code.
- **Fetch.** Fails closed without a robots policy (`robots.load_policy`). Progress for T is in
  `logs/fetch_progress_<T>.txt` (URLs done; `--opt refetch=1` ignores it). `RawFile.etag` was added
  (additive; the filestore sends no ETag). Cap = `scope.fetch.max_file_bytes` (checked on
  `X-Content-Size`/`Content-Length` and while streaming), allowlist matched on the path after `filestore/eli/`.
  `max_retries` = retries after the first attempt; transport errors are retried too.
- **Units.** Besides articles the parsers emit hierarchy units (`book…subsection`; anchor = AKN id, or
  `_<type>.<n>` when the source has none or a generated one), one `preamble` unit for JO acts (visas +
  formula), `paragraph` units for loose body text (acts without article markup, transitional alineas in
  recueils) and `annex` units (one per attachment, or per bold « ANNEXE … » heading). `text_plain` keeps
  one line per block (paragraph `(1) …`, alinea, list item with its rendered label `1°`/`a)`/`-`/`•`);
  `text_md` is set only when it differs (tables). Article `heading` fully in parentheses → `provenance`.
  Repeal stubs: `style="del"`, all-deleted bodies, and bodies (or headings) that are only a note such as
  « Abrogé ( L. 23 avril 1934 ) » (`lgx_core.normalise.is_repeal_stub_text`); `repeal_note` holds the note.
  `is_modifying` = the unit contains `embeddedStructure`/`quotedStructure` or a content `<mod>`
  (HTML: `table.richtext_embeddedStructurequote`).
- **Article ELIs.** Malformed in-XML URIs (55 articles of `code/travail/20260726`, e.g.
  « …/code/travail/art. l.143-1. ») are logged (`malformed_article_eli`) and replaced by a derived
  `<prefix>/<akn id>` with `eli_unit_derived = true`; articles without any ELI log `article_without_eli`.
  JO originals get `<work>/<akn id>` (derived). Recueil articles with a `wId` get `from_recueil_wid`
  (normalised) and `member_act_eli`, anchor = the relative wId.
- **Relations.** `akn-ref`: `src_eli` = act work, `src_version_eli` + `src_unit_ordinal` = the citing unit;
  `akn-textualmod`: `src_eli` = modifying act (`/jo`), `dst_eli` = this work, `dst_unit_hint` = destination
  anchor or the unit carrying the `mod-start` marker; `akn-note`: notes not tied to a textualMod.
- **Num keys.** `num_norm` keeps list/range connectors (`'Art. 9 et 10.' → '9_et_10'`, `'Art. 17 à 20.' →
  '17_a_20'`); `lgx_core.normalise.num_aliases(num_raw, akn_id=, eli_unit=)` gives the extra
  `article_alias` keys (members `9`, `10`; `17`…`20`; ELI forms `9_10`, `17--20`). Annex articles keep their
  plain number (the resolver must prefer non-annex units on a collision).
- **Chunks.** `chunk_units(..., text_valid=None, max_tokens=1500)`; body = `text_md` or `text_plain`;
  header `"{titre court} — {label} > {breadcrumb} > {num} {heading}"` + `" | Index : …"`; titre court =
  `Act.title_short` → curated `short_title` alias (`config/aliases.yaml`, « Code du travail ») →
  `citation_title` → title cut at 120 chars. Token estimate `lgx_core.textsize` (`ceil(chars/3.5)`).
  Deterministic identity `lgx_ingest.chunk.chunk_id` = `<slug>:<unit ordinal>:<part>`.
- **PDF** (`parse/pdf.py`) and recueil dedup (`parse/recueil.py`) are M2 stubs; `lgx parse` reports PDF
  entries as `skipped_pdf`.

## 6. Loader (S8), gates (S9), ingest, package (S10, minimal) — implemented (loader stage, M1)

**Inputs** (`lgx_ingest.load.read_inputs`): `manifest/<T>/{acts,versions,manifest,relations,data_quality}.jsonl`,
`selection_report.json`, `meta/<T>/_snapshot.json` + `vocab.parquet`, `raw/index.jsonl`, `parsed/<sha>.<parser>.json`
(parser version + parse issues), `units/<T>/<slug>.jsonl` (+ `.relations.jsonl`), `chunks/<T>/<slug>.jsonl`,
`robots/<T>/{robots,denylist}.json`, `logs/fetch_log.jsonl`, `config/{warnings,legal_texts,aliases,recueil_members}.yaml`.
A version gets **text** iff its manifest entry has a `file`, the file is in the raw index, a units file exists
whose `source_sha256` equals that raw file (else `LoadError`: stale units → re-run parse/chunk) and a chunks
file exists (else `LoadError`). Everything else is a metadata row (`has_text = false`, `text_valid = NULL`).

**Release naming.** `release_id = data-<YYYYMMDD>[-sample]`, database `legilux_build_<YYYYMMDD>[_sample]`
(`--db-name` / `--opt db_name=`), bundle `data/bundles/data-<T ISO>[-sample].dump`. The DSN is the build/owner
role (`--dsn`, `--opt dsn=`, `LGX_PG_DSN`); the loader connects to the DSN's database (or `postgres`) to
`DROP … WITH (FORCE)` + `CREATE` the release database. Idempotent per release name: surrogate ids are assigned
in ELI order (act, version, unit by version+ordinal, chunk by unit+part, relation by key), so a rebuild of the
same inputs gives the same rows and the same `lgx_meta.release.content_digest`.

**Steps** (`run_load`): `apply_schema` (all `db/schema/*.sql`; the psql subset `\set`, `\if :{?v}`, `:v`, `:'v'` is
rendered in Python, no `psql` needed; `embed_dim` = `config/embedding.yaml: dim`) → one transaction of COPYs
in FK order (vocab, legal_text, warning_def, act, act_version, unit, article_alias, act_alias, relation,
recueil_member, chunk, data_quality_issue, lgx_meta.release, build.fetch_log, build.sync_state; identity
sequences advanced) → `90_postload.sql` → `lgx_meta.invariant_violations` must be empty, no denylisted ELI in
any table, row counts == plan → `release.gates.loader`. Any constraint violation raises `LoadError` naming the
table, the constraint and the record (e.g. `load failed in lgx.act_version (record: …/consolide/20270101):
conflicting key value violates exclusion constraint "one_text_per_act_and_date"`). Report: `reports/<T>/load.json`.

**Transforms worth knowing (MCP stage):**
- **Relabel `not_loaded`.** An act whose selected text is not in this build (every act outside the sample in a
  sample release; a fetch/parse gap in a full build) is loaded as `text_basis = metadata_only`,
  `metadata_reason = not_loaded`, `basis_version_id = NULL`; its versions keep their selection `role`. The
  planned label is in `lgx_meta.release.counts.not_loaded_by_planned_text_basis` (sample at T0: 25,516 acts).
  Render it as « texte non chargé dans cette base (échantillon) ».
- `act.title` / `title_short` / `citation_title`: the literal two-character `\n` of 5,301 SPARQL titles is a space.
- Relations: SPARQL rows keep `/jo` IRIs; `src_act_id`/`dst_act_id` resolve exact work/`/jo`/version ELIs and
  else walk the path up (`…/art_5`, `…/code/travail/art_l_121-1` → the code's act via its version family).
  AKN rows: `src_unit_id` = citing unit (`akn-ref`), `dst_unit_id` = the unit whose anchor/akn_id matches
  `dst_unit_hint` in the same version (textualMod/note), or the stored unit with that `eli_unit` (article ELI
  targets; the version serving at T preferred). Missing `src_dei` of AKN rows = the source act's `dei_min`.
  Edges touching a denylisted ELI are dropped (`counts.relations.dropped_denylist`).
- `article_alias` holds only the **extra** keys of article units (`lgx_core.normalise.num_aliases`); the primary
  key is `unit.num_norm`.
- `act_alias`: `config/aliases.yaml` entries with a target (`alias_norm`, kind, reviewed) + `title_short` /
  `citation_title` of acts. Out-of-scope aliases (RGPD → EU law) are not in the database (open issue).
- `recueil_member`: derived from units' `member_act_eli` (method `wid`, `reviewed_by` NULL); entries of
  `config/recueil_members.yaml` override.
- `data_quality_issue`: selection issues + parse issues of loaded versions, deduplicated on (entity, code),
  details joined. `legal_text`: `{T}` filled (fr 16/10/2026, de 16.10.2026, en 2026-10-16);
  `attribution_vectors` replaces `attribution` only with vectors; `LICENSES/CC-BY-4.0.txt` (if present) is the en body.
- Embeddings off: **zero** `lgx.embedding_model` rows (its CHECK has no `none` provider), all `chunk.embedding`
  NULL; `lgx_meta.release.config` = `{release_kind, loader_version, config_digest, snapshot_digest, scope,
  embedding, vector_status: "disabled:provider_none", sample_works, …}`; `counts` = per table / text_basis / flag /
  role / unit type, relations, texts missing, selection counts. `inputs_digest` = sha256(snapshot digest,
  config digest, robots sha256s, loaded file sha256s, parser + rules versions).

**Gates** (`lgx_ingest.gates`, `reports/<T>/gates.json`, also stored in `lgx_meta.release.gates`): `pass` /
`fail` / `warn` / `skip` (no previous snapshot/selection/release to compare with). File gates: robots,
sparql_completeness (Q17), q01_rows / q02_rows (≤ 2 % drop vs previous snapshot), usable_consolidations (≤ 2 %),
text_basis_buckets (≤ 10 %), stubs (+20), flag_window_disagreements (≤ 5: `applicable` flag on an ended
window), fetch_errors (≤ 1 % of URLs, **final outcome per URL** — a transient error recovered by a retry is
reported, not counted), parse_failures (≤ 1 %, none on CODE/Constitution), sample_expectations. DB gates:
invariants, denylist_absent, release_row, acts/versions vs selection, text_basis_vs_selection (loaded +
not_loaded == planned per bucket), metadata_reason_consistent, unit/chunk/relation counts vs plan,
chunk_unit_consistency (chunk ↔ unit ↔ version, chunks inside the version's `text_valid`, no chunk of a
repealed/modifying unit, text versions have units), text_basis_consistent (labels vs stored texts vs
`lgx.serving_at(T)`; windows starting after T excepted), core_texts_present (sample: the m1 `core` /
`code_recueil` works; full: every CODE/Constitution with text), sample_texts_loaded (every m1 XML/HTML sample
file), core_article_counts (≤ 2 % vs previous gates report), licence_cc_by_only, embedding_off,
canned_lexical_query.

**Ingest** (`lgx_ingest.cli_ingest.run_ingest`): robots reused when `robots/<T>/` exists (fetched again with
`--refresh` or before any download); snapshot reused unless `--refresh`; select always; fetch **cache first**
(cached files are marked done for T in `logs/fetch_progress_<T>.txt` and never re-requested; only missing ones are
fetched; `--revalidate` = conditional GETs for all; `--offline` = no request at all, gaps are gated); parse, chunk;
load when a DSN is configured (`--load/--no-load`); gates; report; `--package` (only when the gates pass, or
`--opt package_on_fail=1`). `--full` requires `--confirm-full-crawl`. **Pass `--t 2026-10-16`**: T=auto is the
Luxembourg date, which is already 2026-10-17 at 22:00 UTC.

**Package** (`lgx_ingest.package`): `pg_dump -Fc -Z zstd:9 --exclude-schema=build --no-owner --no-privileges` →
`data/bundles/data-<T>[-sample].dump` + `.release.json` (release row, `requires {schema_version: 1, embedding:
null, postgres_major}`, dump size/sha256) + `.sha256`. `--restore-to NAME`: fresh `NAME_next`, `pg_restore -j 4
--exit-on-error --no-owner --no-privileges`, `95_roles.sql` (GRANTs), `ANALYZE`, `ALTER DATABASE … SET
default_transaction_read_only = on`, row counts == source, smoke (invariants, canned query, `lgx_ro` SELECT only),
then rename-swap to `NAME` (`NAME_prev` kept unless `--opt keep_prev=0`). Local `pg_dump`/`pg_restore` must be ≥ the
server major; otherwise pass `--container lgx-dev-db` (tools run inside the container). `--opt
ro_password_file=PATH` sets the `lgx_ro` password.

**Sizing report** (`lgx_ingest.report`, `reports/<T>/sizing.json`): per version and per stratum (sample group,
doc type, kind, format): units, articles, repealed, text bytes, raw bytes, chunks, tokens, split articles; an [E]
extrapolation (sample means × stored-text counts per kind; codes as measured) and the database size per table.

### 6.1 Persistent dev database (used by the MCP and UI stages)

| What | Value |
|---|---|
| Container | `lgx-dev-db` (image `lgx.local/lgx-db:dev` from `db/Dockerfile`), `--restart unless-stopped` |
| Volume | `lgx-dev-pgdata` → `/var/lib/postgresql` (PG18 layout) |
| Address | `127.0.0.1:55432` |
| Superuser (build/load/package) | `postgres` / `lgx-dev-only` → `LGX_PG_DSN=postgresql://postgres:lgx-dev-only@127.0.0.1:55432/postgres` |
| Runtime database (MCP) | `legilux` — restored from `data/bundles/data-2026-10-16-sample.dump`, read-only by default |
| Read-only role (MCP) | `lgx_ro` / `lgx-ro-dev-only` → `postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux` (compose names: `LGX_DB_HOST=127.0.0.1`, `LGX_DB_PORT=55432`, `LGX_DB_NAME=legilux`, `LGX_DB_USER=lgx_ro`, `LGX_DB_PASSWORD_FILE`) |
| Build database | `legilux_build_20261016_sample` (writable as `postgres`; `lgx gates` updates its release row) |

Re-create everything (offline, ≈ 90 s): `LGX_PG_DSN=postgresql://postgres:lgx-dev-only@127.0.0.1:55432/postgres
uv run lgx --t 2026-10-16 --opt keep_prev=0 ingest --sample --offline --package --container lgx-dev-db
--restore-to legilux`. The container must not be removed by `make db-down` (that target removes the throwaway
`lgx-db-dev` container, which also wants port 55432: do not run `make db-up` while `lgx-dev-db` runs).

Sample release at T = 2026-10-16: 58,390 acts, 29,133 versions (44 with text), 13,052 units, 10,220 chunks
(all searchable, no vectors), 174,396 relations (168,655 SPARQL + 5,741 AKN after de-duplication), 300 article
aliases, 30 act aliases, 3,785 vocab concepts, 623 data-quality rows; labels: consolidated 23, stub_fallback 1,
original_never_amended 11, original_possibly_outdated 7, metadata_only 58,348 (25,516 of them `not_loaded`).
Database 282 MB (build) / 261 MB (restored); dump 9.4 MB; load ≈ 40 s (COPY 22 s); restore ≈ 7 s + 4 s
ANALYZE/GRANTs.

## 7. MCP stage and UI

- The MCP server reads the database only as `lgx_ro`, computes status with `lgx.serving_at` (twin in
  `lgx_core.rules.serving`) and warnings with `lgx_core.warnings.Catalogue` over `lgx.warning_def`;
  embeddings come from `lgx_core.embed.get_embedder(config.embedding)` → `NoneEmbedder` →
  `vector_status = disabled:provider_none`, `mode_used = lexical`. Tool names and schemas: Appendix D.
- Dual-era contract tests need `mcp==1.27.2` as a client: run them in an isolated env
  (`uv run --isolated --with mcp==1.27.2 …`), never add it to the main lock (it conflicts with 2.3.0).
- The UI lives in `ui/` with the importable package `ui/lgx_ui` (already in the wheel config).

## 8. Plan deviations fixed in M0 (details in the M0 report)

- `config/embedding.yaml`: `retry.on` → `retry.on_status` (YAML 1.1 reads `on` as boolean true).
- `lgx.warning_def.variants jsonb` added; Appendix D's optional fragments are explicit variants in
  `config/warnings.yaml`.
- `lgx.search_candidates`: the exact-identifier branch honours `p_doc_types` and ranks by `ts_rank_cd`.
- `90_postload.sql` is idempotent and fails clearly when vectors exist without `lgx.embedding_model`; the
  invariant view is created with the schema.
- `DISTINCT` added to Q07-fallback, Q08, Q09 (A.0 rule).
- `loi/1978/07/14/n1/jo` XML is licenceSCL: under `cc_by_only` it is served (and tested) from HTML.
- `config/denylist_gdpr.yaml` is gitignored (personal-data ELIs); the loader treats it as optional.

## 9. Schema changes made by the loader stage (M1; `db/schema/*`)

- `lgx.act.metadata_reason` (+ `MetadataReason.NOT_LOADED`, shared-file change in `lgx_core/models.py`): `not_loaded`.
- `lgx.relation`: surrogate `relation_id` (identity PK), new `dst_unit_id` (FK unit, ON DELETE SET NULL), unique
  `relation_endpoints_key` (NULLS NOT DISTINCT) over `(src_eli, rel_type, dst_eli, source, src_unit_id,
  dst_unit_id, dst_unit_hint)`, indexes on `src_unit_id` / `dst_unit_id`. The Appendix B natural key collapsed AKN
  edges (one row per modifying act and target work: 6,452 sample edges → 1,618).
- `lgx.search_candidates(…, p_limit, p_act_ids bigint[] DEFAULT NULL, p_in_force_only boolean DEFAULT true)`:
  act filter and `in_force_only` on every branch (PLAN §8.3); the cited branch honours window, doc types and acts;
  deterministic tie-breaks (`chunk_id`). Helper `lgx.chunk_window_ok(from, to, as_of, in_force_only)`.
  Existing 6/7-argument calls are unchanged.
- `95_roles.sql`: the `REVOKE … ON SCHEMA build` is skipped when `build` does not exist (a restored dump has no
  `build` schema; the script failed on every restore).
