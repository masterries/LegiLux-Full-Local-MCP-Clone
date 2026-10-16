# Test fixtures

Policy (PLAN §11.4): only **CC BY 4.0** Legilux files are committed, and only small ones. Large files
(codes, the 1.9 MB `rgd/2018/10/02/a936` XML, PDFs) stay in the build-side cache (`data/raw/`) and are
used by `@pytest.mark.live` or cache-backed tests that skip when the file is absent.

- `manifest/bootstrap_sample.jsonl` — 12 hand-written `ManifestEntry` rows (parser fixtures of
  `config/sample.yaml`) so the fetch/parse stage can be developed before `lgx select` exists. File URLs
  follow the filestore naming rule (`<eli>/fr/<fmt>/eli-<eli dashed>-fr-<fmt>.<ext>`), checked for several
  of them during research and with one Q-MANIF request on 2026-10-16; `lgx select` output (from
  `jolux:isExemplifiedBy`) supersedes it. Text windows/amending lists come from the frozen 2026-10-16
  snapshot.
- `loi/1978/07/14/n1/jo`: its XML manifestation is **licenceSCL** (only the HTML is CC BY), so the XML
  must not be committed here even though PLAN §11.4 lists it as an XML fixture; use the HTML.
