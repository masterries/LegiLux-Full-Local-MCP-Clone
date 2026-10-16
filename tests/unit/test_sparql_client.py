"""SPARQL client, literal parsing, snapshot I/O and the discover stage (offline: respx mocks)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from lgx_core.rules.snapshot import TABLES, ColKind, convert_row, iri_code, parse_xsd_date, parse_xsd_datetime
from lgx_ingest.context import StageContext
from lgx_ingest.sparql import (
    PoliteHttp,
    Puller,
    RetryPolicy,
    SparqlClient,
    SparqlError,
    Throttle,
    load_snapshot,
    parse_csv,
    parse_ntriples,
    read_table,
    run_discover,
    snapshot_digest,
    vocab_rows,
    write_table,
    year_windows,
)

ENDPOINT = "https://data.legilux.public.lu/sparqlendpoint"
E = "http://data.legilux.public.lu/eli/etat/leg/"
RT = "http://data.legilux.public.lu/resource/authority/resource-type/"
ST = "http://data.legilux.public.lu/resource/authority/application-status/"


# ------------------------------------------------------------------------------- literal parsing
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-16", dt.date(2026, 10, 16)),
        ("2026-10-16Z", dt.date(2026, 10, 16)),
        ("2026-10-16+02:00", dt.date(2026, 10, 16)),
        ("1617-01-01", dt.date(1617, 1, 1)),
        ("2026-02-30", None),
        ("20142209", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_xsd_date(raw: str | None, expected: dt.date | None) -> None:
    assert parse_xsd_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-08T13:58:50.834+02:00", dt.datetime(2026, 10, 8, 11, 58, 50, 834000, tzinfo=dt.UTC)),
        ("2014-09-03T22:00:00+02:00", dt.datetime(2014, 9, 3, 20, 0, tzinfo=dt.UTC)),
        ("2026-10-15T16:59:40Z", dt.datetime(2026, 10, 15, 16, 59, 40, tzinfo=dt.UTC)),
        # naive publisher timestamps are Luxembourg local time (CEST = UTC+2 in April)
        ("2023-04-26T11:43:35.514141", dt.datetime(2023, 4, 26, 9, 43, 35, 514141, tzinfo=dt.UTC)),
        ("2026-04-14", dt.datetime(2026, 4, 13, 22, 0, tzinfo=dt.UTC)),
        # the malformed dct:modified literals seen in Q11 (Appendix A.0)
        ("2026-09-01T10:00:00.472Z+02:00", None),
        ("2026-13-01T00:00:00Z", None),
        ("not a date", None),
    ],
)
def test_parse_xsd_datetime(raw: str, expected: dt.datetime | None) -> None:
    assert parse_xsd_datetime(raw) == expected


def test_convert_row_keeps_raw_of_unparsable_values() -> None:
    row = convert_row(
        TABLES["manifestations"],
        {"w": E + "loi/2023/04/19/a203/jo", "uf": "http://x/user-format/xml", "lic": "", "file": "f", "mod": "bad"},
    )
    assert row == {
        "w": E + "loi/2023/04/19/a203/jo",
        "uf": "xml",
        "lic": None,
        "file": "f",
        "mod": None,
        "mod_raw": "bad",
    }
    assert iri_code("http://data.legilux.public.lu/resource/ontology/jolux#modifies") == "modifies"
    assert iri_code(RT + "LOI") == "LOI"


def test_parse_csv_unbound_and_quotes() -> None:
    text = '"a","title","dei"\n"http://x/a","Loi du 1er « août », ""citée""",2026-01-01\n"http://x/b",,\n'
    assert parse_csv(text) == [
        {"a": "http://x/a", "title": 'Loi du 1er « août », "citée"', "dei": "2026-01-01"},
        {"a": "http://x/b", "title": "", "dei": ""},
    ]


def test_ntriples_and_vocab_rows() -> None:
    nt = (
        "<http://v/resource-type/LOI>\t<http://www.w3.org/2004/02/skos/core#inScheme>\t<http://v/resource-type> .\n"
        '<http://v/resource-type/LOI>\t<http://www.w3.org/2004/02/skos/core#prefLabel>\t"loi \\u00E9t\\"e"@fr .\n'
        '<http://v/resource-type/LOI> <http://www.w3.org/2004/02/skos/core#prefLabel> "Gesetz"@de .\n'
        '<http://v/resource-type/LOI> <http://www.w3.org/2004/02/skos/core#notation> "LOI" .\n'
        "garbage line\n"
    )
    triples = parse_ntriples(nt)
    assert len(triples) == 4
    assert vocab_rows(triples) == [
        {
            "concept_uri": "http://v/resource-type/LOI",
            "scheme": "http://v/resource-type",
            "code": "LOI",
            "label_fr": 'loi ét"e',
            "label_de": "Gesetz",
            "label_en": None,
        }
    ]


def test_parquet_round_trip_is_sorted_and_typed(tmp_path: Path) -> None:
    spec = TABLES["acts"]
    rows = [
        convert_row(spec, {"a": E + "b/jo", "cw": E + "b", "type": RT + "LOI", "st": ST + "in-force", "dei": "x"}),
        convert_row(spec, {"a": E + "a/jo", "cw": E + "a", "type": RT + "RGD", "dei": "2020-01-01"}),
    ]
    sha = write_table(tmp_path / "acts.parquet", spec, rows)
    back = read_table(tmp_path / "acts.parquet")
    assert [r["a"] for r in back] == [E + "a/jo", E + "b/jo"]
    assert back[0]["dei"] == dt.date(2020, 1, 1) and back[1]["dei"] is None and back[1]["dei_raw"] == "x"
    assert len(sha) == 64
    assert set(back[0]) == set(spec.columns) | set(spec.raw_columns)
    assert spec.columns["dei"] is ColKind.DATE


# -------------------------------------------------------------------------------------- HTTP
class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def test_throttle_spacing() -> None:
    fc = FakeClock()
    th = Throttle(1.2, clock=fc.clock, sleep=fc.sleep)
    th.wait()
    fc.now += 0.5
    th.wait()
    th.wait()
    assert fc.sleeps == [pytest.approx(0.7), pytest.approx(1.2)]
    assert th.requests == 3


def _http(sleeps: list[float] | None = None) -> PoliteHttp:
    sink = sleeps if sleeps is not None else []
    return PoliteHttp(
        user_agent="LegiLux-Local-Clone/0.1 (+test)",
        spacing_s=0.0,
        retry=RetryPolicy(max_retries=3, backoff_s=(1, 2, 4)),
        sleep=sink.append,
    )


@respx.mock
def test_post_form_accept_and_user_agent() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, text='"a"\n"x"\n'))
    with _http() as http:
        resp = SparqlClient(http, ENDPOINT).query("SELECT 1")
    req = route.calls.last.request
    assert parse_qs(req.content.decode())["query"] == ["SELECT 1"]
    assert req.headers["Accept"] == "text/csv"
    assert req.headers["User-Agent"].startswith("LegiLux-Local-Clone/")
    assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert resp.status == 200 and resp.attempts == 1


@respx.mock
def test_retries_on_503_and_429_with_retry_after() -> None:
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(429, headers={"Retry-After": "7"}, text="slow down"),
            httpx.Response(200, text='"a"\n'),
        ]
    )
    sleeps: list[float] = []
    with _http(sleeps) as http:
        resp = SparqlClient(http, ENDPOINT).query("SELECT 1")
    assert resp.attempts == 3
    assert sleeps == [1, 7.0]


@respx.mock
def test_sorted_cap_is_not_retried_and_exhausted_retries_raise() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(500, text="Virtuoso 22023 Error SR353: Sorted TOP clause specifies more")
    )
    with _http() as http, pytest.raises(SparqlError) as exc:
        SparqlClient(http, ENDPOINT).query("SELECT 1")
    assert exc.value.sorted_cap and respx.calls.call_count == 1
    respx.post(ENDPOINT).mock(return_value=httpx.Response(502))
    with _http() as http, pytest.raises(SparqlError):
        SparqlClient(http, ENDPOINT).query("SELECT 1")


@respx.mock
def test_timeouts_are_retried() -> None:
    respx.post(ENDPOINT).mock(side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, text='"a"\n')])
    with _http() as http:
        assert SparqlClient(http, ENDPOINT).query("SELECT 1").attempts == 2


def test_head_is_refused() -> None:
    with _http() as http, pytest.raises(ValueError):
        http.request("HEAD", "https://legilux.public.lu/filestore/eli/etat/leg/x")


# ----------------------------------------------------------------------------------- partitions
def test_year_windows_cover_all_years() -> None:
    windows = year_windows(2026)
    years = [y for lo, hi in windows for y in range(lo, hi + 1)]
    assert len(years) == len(set(years))
    assert {1617, 1899, 1900, 1999, 2000, 2026, 2027} <= set(years)


@respx.mock
def test_partitioned_fallback_merges_and_dedupes() -> None:
    header = '"src","rel","tgt","dei"\n'
    row_m = f'"{E}x/jo","http://data.legilux.public.lu/resource/ontology/jolux#modifies","{E}y/jo",2020-01-01\n'
    row_r = f'"{E}x/jo","http://data.legilux.public.lu/resource/ontology/jolux#repeals","{E}z/jo",2020-01-01\n'

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode())["query"][0]
        if "VALUES ?rel {\n    <" in query and "#modifies>" in query and "#repeals>" not in query:
            return httpx.Response(200, text=header + row_m + row_m)
        if "#repeals>" in query and "#modifies>" not in query:
            return httpx.Response(200, text=header + row_r)
        return httpx.Response(504, text="gateway timeout")

    respx.post(ENDPOINT).mock(side_effect=handler)
    with _http() as http:
        result = Puller(SparqlClient(http, ENDPOINT), last_year=2026).pull("rel_modifies_repeals")
    assert result.partitioned
    assert sorted((r["rel"], r["tgt"]) for r in result.rows) == [("modifies", E + "y/jo"), ("repeals", E + "z/jo")]
    assert result.errors


# --------------------------------------------------------------------------------- run_discover
def _ctx(tmp_path: Path, **opts: str) -> StageContext:
    return StageContext.create(sync_date=dt.date(2026, 10, 16), data_dir=tmp_path, options=opts)


def _fake_endpoint(complete: bool = True):  # type: ignore[no-untyped-def]
    acts = (
        '"a","cw","type","st","dei","dnl","pub"\n'
        f'"{E}loi/2016/12/23/n25/jo","{E}loi/2016/12/23/n25","{RT}LOI","{ST}in-force",2017-01-01,,2016-12-28\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.content.decode())["query"][0]
        if "COUNT(DISTINCT ?s)" in q:
            n = 1 if complete else 2
            return httpx.Response(
                200,
                text=(
                    '"c","rows","distinct"\n'
                    f'"http://data.legilux.public.lu/resource/ontology/jolux#Act",{n},{n}\n'
                    '"http://data.legilux.public.lu/resource/ontology/jolux#Consolidation",0,0\n'
                ),
            )
        if "CONSTRUCT" in q:
            return httpx.Response(200, text="")
        if "?a ?cw ?type ?st" in q:
            return httpx.Response(200, text=acts)
        return httpx.Response(200, text='"x"\n')

    return handler


@respx.mock
def test_run_discover_writes_tables_and_snapshot_info(tmp_path: Path) -> None:
    respx.post(ENDPOINT).mock(side_effect=_fake_endpoint())
    ctx = _ctx(tmp_path, manif="none")
    result = run_discover(ctx, http=_http())
    assert result.ok, result.messages
    info = json.loads(ctx.layout.snapshot_info.read_text())
    assert set(info["tables"]) == set(TABLES)
    assert info["tables"]["acts"]["rows"] == 1 and info["completeness"]["checks"]["acts"]["ok"]
    assert info["snapshot_digest"] == snapshot_digest(info)
    snap = load_snapshot(ctx.layout.meta_dir)
    assert snap.rows("acts")[0]["type"] == "LOI" and snap.snapshot_date == dt.date(2026, 10, 16)
    # 1 completeness + 10 table pulls (Q-MANIF skipped with manif=none), all sequential POSTs
    assert result.counts["requests"] == 11 == respx.calls.call_count


@respx.mock
def test_run_discover_incomplete_pull_switches_to_partitions_and_fails(tmp_path: Path) -> None:
    respx.post(ENDPOINT).mock(side_effect=_fake_endpoint(complete=False))
    ctx = _ctx(tmp_path, manif="none")
    result = run_discover(ctx, ["q01"], http=_http())
    assert not result.ok
    info = json.loads(ctx.layout.snapshot_info.read_text())
    assert info["tables"]["acts"]["partitioned"] is True
    assert info["completeness"]["checks"]["acts"] == {
        "class": "Act",
        "distinct_endpoint": 2,
        "distinct_pulled": 1,
        "ok": False,
    }


def test_resolve_tables_by_query_id(tmp_path: Path) -> None:
    result = run_discover(StageContext.create(data_dir=tmp_path, dry_run=True), ["q05", "q_manif", "titles"])
    assert result.details["would_pull"] == ["rel_modifies_repeals", "titles", "manifestations"]
    with pytest.raises(ValueError):
        run_discover(StageContext.create(data_dir=tmp_path, dry_run=True), ["q99"])


@pytest.mark.live
def test_live_q17_completeness_through_the_client() -> None:
    """One polite POST (project User-Agent, ≥ 1.2 s spacing) — only with --run-live."""
    from lgx_ingest.sparql import http_from_context

    ctx = StageContext.create()
    with http_from_context(ctx) as http:
        counts = Puller(SparqlClient(http, ctx.settings.sparql_url), last_year=ctx.sync_date.year).completeness()
    assert counts["Act"]["distinct"] > 150_000
    assert counts["Consolidation"]["distinct"] > 4_000
