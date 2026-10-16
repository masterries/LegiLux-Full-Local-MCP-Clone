# Legilux parser fixtures

Real Legilux manifestations used by the parser, chunker and HTML-twin tests (PLAN §11.4). The catalogue
`fixtures.json` lists every file with its source URL, ELI identity, licence, sha256, size and
`Last-Modified` (as fetched on 2026-10-16).

**Licence and attribution.** Every file is a manifestation whose `jolux:license` is
[CC BY 4.0](http://creativecommons.org/licenses/by/4.0/) — © Service central de législation (SCL),
Grand-Duché de Luxembourg, source <https://legilux.public.lu>. Only CC-BY manifestations may be added
here (`tests/unit/test_parse_fixtures.py` enforces it). licenceSCL files are never committed: e.g. the XML
of `loi/1978/07/14/n1/jo` is licenceSCL, so its CC-BY **HTML** is the fixture.

**Changes.** `xml/eli-etat-leg-rgd-2018-10-02-a936-jo-fr-xml.trimmed.xml` is the a936 XML with its
base64 PNG payload replaced by a 1×1 PNG (1,900,411 → 18,615 bytes); everything else is byte-identical
to the source. `scripts/fetch_fixtures.sh --trim` rebuilds it from the full file and checks its pin.

**Size budget.** ≤ ~6 MB committed (currently 4.3 MB). Larger files — the codes (civil 5.4 MB, travail
4.1 MB, pénal, procédure civile), the code-recueil `code/fonction_publique/20261011` (6.5 MB), the full
a936 and two code HTML twins — are listed under `large` in `fixtures.json`, pinned by sha256, and
materialised in `data/fixtures-large/` (gitignored) by:

```sh
scripts/fetch_fixtures.sh            # copy from data/raw (after `lgx fetch --sample`) or GET politely
scripts/fetch_fixtures.sh --offline  # cache only, no network
uv run pytest -q -m large            # their tests skip when a file is absent or differs from its pin
```

| Name | File | Exercises |
|---|---|---|
| `a686_conso` | `xml/…a686-consolide-20251226…` | Art. 56 `style="del"` repealed stub (no chunk), textualMod source `loi/2025/12/19/a619/jo` |
| `aifm_conso` / `aifm_conso_html` | `…loi-2013-07-12-n1-consolide-20260416…` | 229 article units, 59 id-less `embeddedStructure` articles, 140 textualMod, 85 modifying articles; HTML twin with hidden widgets and `mod_del` rows |
| `constitution` / `constitution_html` | `…constitution-1868-10-17-n1-consolide-20260829…` | 132 articles with article ELIs; HTML twin |
| `a88_jo` / `a88_html` | `…loi-2026-02-25-a88-jo…` | 11 articles, preamble, annexes without articles split on « ANNEXE I/II », stale `<meta>` |
| `n25_jo` | `…loi-2016-12-23-n25-jo…` | Art. 5 « fait seul foi » (disclaimer source) |
| `copyright_conso` | `…loi-2001-04-18-n2-consolide-20220409…` | Art. 10, 8° list labels; content `<mod>`; `scl:comment` dropped |
| `a203_jo` | `…loi-2023-04-19-a203-jo…` | pure amending act: modifying articles stored, not chunked |
| `l1922_jo` | `…loi-1922-03-05-n1-jo…` | short JO act with an annex |
| `a936_jo_trimmed` | `xml/…a936-jo-fr-xml.trimmed.xml` | base64 images → `[image: alt]` |
| `l1978_jo_html`, `n5_jo_html`, `n3_rgd_jo_html`, `agd1939_jo_html`, `rgd1989_jo_html` | `html/…` | CC-BY HTML path for acts whose XML is licenceSCL (derived article ELIs, quoted amendments) |
