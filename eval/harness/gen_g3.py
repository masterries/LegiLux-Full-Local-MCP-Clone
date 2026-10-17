"""Generate G3 (mandatory warnings and status statements, PLAN §11.1 / §8.5) from the release database.

The expected codes come from the PLAN §8.5 trigger table applied to database facts — ``lgx.serving_at`` (the SQL
twin of the serving rules: status, text basis, flags, next version at ``as_of``), version kind / format, unit
flags (repealed stub, derived ELI) and ``lgx.data_quality_issue`` — never from the MCP annotator under test.
Forbidden codes (``absent``) catch the opposite error: a warning of another act (e2e 2026-10-17, bug 2), a repeal
claimed for an act in force, « non consolidé » on a consolidation.

Every code the release can trigger gets at least one item; the others are listed in ``not_triggerable`` with the
reason (e.g. W_PDF_EXTRACTED until the PDF codes are loaded). Gate: 100 % of the expected pairs, no forbidden
code, ≥ 98 % status statements.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

import psycopg
from psycopg.rows import dict_row

ENVELOPE = ["W_NOT_OFFICIAL", "W_SNAPSHOT"]
ALL_CODES = [
    "W_NOT_OFFICIAL",
    "W_SNAPSHOT",
    "W_DATA_STALE",
    "W_CLOCK_SUSPECT",
    "W_NOT_CONSOLIDATED",
    "W_STUB",
    "W_CONSO_LAGGING",
    "W_CONSO_DISCONTINUED",
    "W_REPEALED",
    "W_NOT_YET_IN_FORCE",
    "W_SUNSET",
    "W_FUTURE_VERSION",
    "W_ARTICLE_REPEALED",
    "W_BASE_LEGALE_ABROGEE",
    "W_TEMPORARY_ACT",
    "W_PDF_EXTRACTED",
    "W_SUPERSEDED_BY_CODE",
    "W_PARTIAL_REPEAL_UNKNOWN",
    "W_DERIVED_ELI",
    "W_DATA_QUALITY",
    "W_TRANSLATION",
    "W_OUT_OF_SCOPE",
]
WHY_NOT = {
    "W_TEMPORARY_ACT": "no stored text carries acte_temporaire (temporary RGDs are metadata-only, OD10)",
    "W_PDF_EXTRACTED": "no PDF-extracted text in this release (PDF codes are an M2 ingest deliverable)",
    "W_SUPERSEDED_BY_CODE": "no act carries superseded_by_code in this release (recueil routing, M2 ingest)",
    "W_TRANSLATION": "added by the answer policy / verifier for non-French answers, not by a tool",
}


def _h(*parts: object) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()


class _Gen:
    def __init__(self, conn: psycopg.Connection[dict[str, Any]]) -> None:
        self.conn = conn
        self.items: list[dict[str, Any]] = []
        rel = conn.execute("SELECT release_id, sync_date, horizon FROM lgx_meta.release").fetchone()
        assert rel is not None
        self.release = rel["release_id"]
        self.T: dt.date = rel["sync_date"]
        self.H: dt.date = rel["horizon"]

    def q(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return list(self.conn.execute(sql, params).fetchall())

    def add(self, trigger: str, tool: str, arguments: dict[str, Any], expect: dict[str, Any], **extra: Any) -> None:
        warnings = list(dict.fromkeys([*ENVELOPE, *expect.get("warnings", [])]))
        exp = {**expect, "warnings": warnings}
        if not exp.get("absent"):
            exp.pop("absent", None)
        self.items.append(
            {
                "id": f"G3-{len(self.items) + 1:03d}",
                "trigger": trigger,
                "tool": tool,
                "arguments": arguments,
                "expect": exp,
                **extra,
            }
        )

    # ------------------------------------------------------------------------------------------- facts
    def serving(self, act_id: int, as_of: dt.date) -> dict[str, Any]:
        rows = self.q("SELECT * FROM lgx.serving_at(%s, %s)", [act_id, as_of])
        return rows[0]

    def dq(self, elis: list[str | None]) -> bool:
        elis = [e for e in elis if e]
        return bool(elis) and bool(
            self.q("SELECT 1 FROM lgx.data_quality_issue WHERE entity_eli = ANY(%s) LIMIT 1", [elis])
        )

    def act_expectation(self, act: dict[str, Any], as_of: dt.date) -> tuple[list[str], list[str], dict[str, Any]]:
        s = self.serving(act["act_id"], as_of)
        flags = set(s["flags"] or [])
        vid = s["text_version_id"] or s["window_version_id"]
        v = self.q("SELECT eli_version, kind, source_format FROM lgx.act_version WHERE version_id = %s", [vid])
        v0 = v[0] if v else {}
        want: list[str] = []
        absent: list[str] = []
        tb = s["text_basis"]
        if tb == "original_possibly_outdated":
            want.append("W_NOT_CONSOLIDATED")
        elif tb == "consolidated":
            absent.append("W_NOT_CONSOLIDATED")
        if tb == "stub_fallback" or "stub_no_text" in flags:
            want.append("W_STUB")
        elif tb == "consolidated":
            absent.append("W_STUB")
        if "consolidation_lags" in flags and v0.get("kind") != "original":
            want.append("W_CONSO_LAGGING")
        if "consolidation_discontinued" in flags:
            want.append("W_CONSO_DISCONTINUED")
        if s["status"] == "no-longer-in-force":
            want.append("W_REPEALED")
        else:
            absent.append("W_REPEALED")
        if s["status"] == "not-yet-in-force":
            want.append("W_NOT_YET_IN_FORCE")
        else:
            absent.append("W_NOT_YET_IN_FORCE")
        if act["dnl"] and as_of < act["dnl"] <= as_of + dt.timedelta(days=90):
            want.append("W_SUNSET")
        if s["next_version_date"] and vid:
            want.append("W_FUTURE_VERSION")
        if "base_legale_abrogee" in flags:
            want.append("W_BASE_LEGALE_ABROGEE")
        if "acte_temporaire" in flags:
            want.append("W_TEMPORARY_ACT")
        if "pdf_extracted" in flags or v0.get("source_format") == "pdf":
            want.append("W_PDF_EXTRACTED")
        if "superseded_by_code" in flags:
            want.append("W_SUPERSEDED_BY_CODE")
        if v0.get("kind") == "original":
            want.append("W_PARTIAL_REPEAL_UNKNOWN")
        if self.dq([act["eli_work"], act["eli_jo"], v0.get("eli_version") if vid else None]):
            want.append("W_DATA_QUALITY")
        return want, absent, s

    # ------------------------------------------------------------------------------------------- cases
    def acts_with_text(self) -> None:
        acts = self.q(
            "SELECT act_id, eli_work, eli_jo, dnl, text_basis, basis_version_id FROM lgx.act"
            " WHERE basis_version_id IS NOT NULL ORDER BY eli_work"
        )
        for a in acts:
            want, absent, s = self.act_expectation(a, self.T)
            self.add(
                f"act:{a['text_basis']}",
                "get_act",
                {"eli": a["eli_work"]},
                {"warnings": want, "absent": absent, "status": s["status"]},
            )
            # one article of the served text (the first non-repealed, non-modifying one with a chunk)
            units = self.q(
                "SELECT u.unit_id, u.num_norm, u.num_raw, u.eli_unit, u.eli_unit_derived, u.is_repealed FROM lgx.unit u"
                " WHERE u.version_id = %s AND u.unit_type = 'article' AND u.num_norm IS NOT NULL AND NOT u.is_repealed"
                " AND length(u.num_norm) <= 20"  # a few recueil keys swallowed their heading (ingest data issue)
                " AND EXISTS (SELECT 1 FROM lgx.chunk c WHERE c.unit_id = u.unit_id)"
                " AND NOT EXISTS (SELECT 1 FROM lgx.unit o WHERE o.version_id = u.version_id"
                "   AND o.unit_type = 'article'"
                "   AND o.num_norm = u.num_norm AND o.unit_id <> u.unit_id)"  # one article of that number
                " ORDER BY md5(u.unit_id::text) LIMIT 1",
                [s["text_version_id"]],
            )
            if s["text_version_id"] is None or not units:
                continue
            u = units[0]
            uw = list(want)
            if u["eli_unit_derived"]:
                uw.append("W_DERIVED_ELI")
            if "W_DATA_QUALITY" not in uw and self.dq([u["eli_unit"]]):
                uw.append("W_DATA_QUALITY")
            ua = [*absent, *([] if u["eli_unit_derived"] else ["W_DERIVED_ELI"]), "W_ARTICLE_REPEALED"]
            self.add(
                f"article:{a['text_basis']}",
                "get_article",
                {"act_eli": a["eli_work"], "article": u["num_norm"]},
                {"warnings": uw, "absent": ua, "status": s["status"], "status_path": "citation.status_as_of.status"},
            )
            # the same act through search (act-level warnings on its hits)
            words = " ".join(((u["num_raw"] or "") + " ").split()[:1])
            text = self.q("SELECT left(body, 200) AS b FROM lgx.chunk WHERE unit_id = %s LIMIT 1", [u["unit_id"]])
            query = " ".join((text[0]["b"] if text else words).split()[:8])
            if len(query) >= 3:
                aw = [c for c in want if c not in ("W_DATA_QUALITY",)]
                self.add(
                    f"search:{a['text_basis']}",
                    "search_legislation",
                    {"query": query, "filters": {"act_eli": a["eli_work"]}, "top_k": 3},
                    {"warnings": aw, "absent": [c for c in absent if c != "W_NOT_YET_IN_FORCE"]},
                )

    def repealed_articles(self) -> None:
        rows = self.q(
            "SELECT a.eli_work, u.num_norm FROM lgx.unit u JOIN lgx.act a ON a.act_id = u.act_id"
            " WHERE u.version_id = a.basis_version_id AND u.unit_type = 'article' AND u.is_repealed"
            " AND u.num_norm IS NOT NULL ORDER BY md5(u.unit_id::text) LIMIT 6"
        )
        for r in rows:
            self.add(
                "article:repealed_stub",
                "get_article",
                {"act_eli": r["eli_work"], "article": r["num_norm"]},
                {"warnings": ["W_ARTICLE_REPEALED"]},
            )

    def derived_elis(self) -> None:
        rows = self.q(
            "SELECT a.eli_work, u.eli_unit FROM lgx.unit u JOIN lgx.act a ON a.act_id = u.act_id"
            " WHERE u.version_id = a.basis_version_id AND u.eli_unit_derived AND u.unit_type = 'article'"
            " AND NOT u.is_repealed ORDER BY md5(u.unit_id::text) LIMIT 4"
        )
        for r in rows:
            self.add(
                "article:derived_eli_citation",
                "get_article",
                {"citation": r["eli_unit"]},
                {"warnings": ["W_DERIVED_ELI", "W_PARTIAL_REPEAL_UNKNOWN"]},
            )

    def metadata_status(self) -> None:
        T = self.T
        cases = [
            (
                "status:repealed",
                "dnl <= %s AND (rdf_status IS NULL OR rdf_status <> 'no-longer-in-force-implicit')",
                [T],
            ),
            (
                "status:repealed_implicit",
                "rdf_status = 'no-longer-in-force-implicit' AND (dnl IS NULL OR dnl > %s)",
                [T],
            ),
            ("status:not_yet_in_force", "dei_min > %s AND dei_min <= %s", [T, self.H]),
            ("status:sunset", "dnl > %s AND dnl <= %s", [T, T + dt.timedelta(days=90)]),
            ("data_quality:act", "eli_work IN (SELECT entity_eli FROM lgx.data_quality_issue)", []),
        ]
        for trigger, where, params in cases:
            rows = self.q(
                f"SELECT act_id, eli_work, eli_jo, dnl, dei_min, text_basis FROM lgx.act WHERE {where}"
                " AND doc_type IN ('LOI', 'RGD', 'AGD', 'AMIN', 'RMIN') ORDER BY md5(eli_work) LIMIT 5",
                params,
            )
            for a in rows:
                want, absent, s = self.act_expectation(a, T)
                self.add(
                    trigger,
                    "get_act",
                    {"eli": a["eli_work"], "as_of": T.isoformat()},
                    {"warnings": want, "absent": absent, "status": s["status"]},
                )
                if trigger == "status:not_yet_in_force" and a["dei_min"] <= self.H:
                    later = a["dei_min"]
                    want2, absent2, s2 = self.act_expectation(a, later)
                    self.add(
                        "time_travel:in_force_from_dei",
                        "get_act",
                        {"eli": a["eli_work"], "as_of": later.isoformat()},
                        {"warnings": want2, "absent": absent2, "status": s2["status"]},
                    )

    def future_versions(self) -> None:
        rows = self.q(
            "SELECT a.act_id, a.eli_work FROM lgx.act a JOIN lgx.act_version v ON v.act_id = a.act_id"
            " WHERE v.has_text AND lower(v.text_valid) > %s GROUP BY a.act_id, a.eli_work ORDER BY a.eli_work",
            [self.T],
        )
        for r in rows:
            nxt = self.q(
                "SELECT min(lower(text_valid)) AS d FROM lgx.act_version WHERE act_id = %s AND has_text"
                " AND lower(text_valid) > %s",
                [r["act_id"], self.T],
            )[0]["d"]
            self.add(
                "future:stored_next_version",
                "search_legislation",
                {"query": "article", "filters": {"act_eli": r["eli_work"], "in_force_only": False}, "top_k": 20},
                {"warnings": ["W_FUTURE_VERSION"]},
            )
            if nxt and nxt <= self.H:
                self.add(
                    "future:before_switch",
                    "get_act",
                    {"eli": r["eli_work"], "as_of": (nxt - dt.timedelta(days=1)).isoformat()},
                    {"warnings": ["W_FUTURE_VERSION"]},
                )

    def envelope(self) -> None:
        T = self.T
        eli = self.q("SELECT eli_work FROM lgx.act WHERE text_basis = 'consolidated' ORDER BY eli_work LIMIT 1")[0][
            "eli_work"
        ]
        self.add("envelope:always", "about_corpus", {}, {"warnings": []})
        self.add(
            "envelope:stale_level1",
            "get_act",
            {"eli": eli, "as_of": (T + dt.timedelta(days=45)).isoformat()},
            {"warnings": ["W_DATA_STALE"]},
        )
        self.add(
            "envelope:stale_level2",
            "get_act",
            {"eli": eli, "as_of": (T + dt.timedelta(days=120)).isoformat()},
            {"warnings": ["W_DATA_STALE"]},
        )
        self.add(
            "envelope:fresh",
            "get_act",
            {"eli": eli, "as_of": (T + dt.timedelta(days=10)).isoformat()},
            {"warnings": [], "absent": ["W_DATA_STALE", "W_CLOCK_SUSPECT"]},
        )
        before = f"{(T - dt.timedelta(days=5)).isoformat()}T12:00:00+02:00"
        after = f"{(self.H + dt.timedelta(days=20)).isoformat()}T12:00:00+02:00"
        self.add(
            "envelope:clock_before_T",
            "get_act",
            {"eli": eli, "as_of": T.isoformat()},
            {"warnings": ["W_CLOCK_SUSPECT"]},
            clock=before,
        )
        self.add("envelope:clock_after_H", "about_corpus", {}, {"warnings": ["W_CLOCK_SUSPECT"]}, clock=after)
        self.add(
            "envelope:clock_no_as_of",
            "get_act",
            {"eli": eli},
            {"warnings": [], "error_code": "AS_OF_OUT_OF_RANGE"},
            clock=before,
        )
        for q in (
            "règlement (UE) 2016/679 protection des données",
            "jurisprudence de la Cour de cassation licenciement",
            "règlement communal de la Ville de Luxembourg stationnement",
        ):
            self.add(
                "search:out_of_scope_vocabulary",
                "search_legislation",
                {"query": q, "top_k": 3},
                {"warnings": ["W_OUT_OF_SCOPE"]},
            )
        self.add(
            "search:national_only",
            "search_legislation",
            {"query": "contrat de travail à durée déterminée", "top_k": 3},
            {"warnings": [], "absent": ["W_OUT_OF_SCOPE"]},
        )

    def citation_scoping(self) -> None:
        # bug 2 of e2e 2026-10-17: a resolved citation carries only its own act's status warnings
        self.add(
            "cite:resolved_warns_only_for_itself",
            "resolve_citation",
            {"text": "règlement grand-ducal du 24 juillet 2000 concernant le travail de candidature"},
            {"warnings": [], "absent": ["W_REPEALED"], "status": "resolved", "status_path": "status"},
        )
        rows = self.q(
            "SELECT a.eli_work FROM lgx.act a WHERE a.dnl <= %s AND a.doc_type = 'LOI'"
            " ORDER BY md5(a.eli_work) LIMIT 2",
            [self.T],
        )
        for r in rows:
            self.add(
                "cite:repealed_candidate",
                "resolve_citation",
                {"text": r["eli_work"]},
                {"warnings": ["W_REPEALED"], "status": "resolved", "status_path": "status"},
            )


def generate(dsn: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("SET statement_timeout = '120s'")
        g = _Gen(conn)
        g.envelope()
        g.acts_with_text()
        g.repealed_articles()
        g.derived_elis()
        g.metadata_status()
        g.future_versions()
        g.citation_scoping()
    covered = sorted({c for it in g.items for c in it["expect"]["warnings"]})
    missing = {c: WHY_NOT.get(c, "no case found in this release") for c in ALL_CODES if c not in covered}
    meta = {
        "release": g.release,
        "status": "draft_unvalidated",
        "generated": {"by": "eval/harness/gen_g3.py", "date": dt.date.today().isoformat(), "T": g.T.isoformat()},
        "covered_codes": covered,
        "not_triggerable": missing,
        "note": "Mandatory warnings and status statements (PLAN §11.1 G3, §8.5): expected codes from the §8.5"
        " triggers over database facts (lgx.serving_at, flags, units, data_quality_issue); `absent` = codes that"
        " must not appear. Gate: 100 % of the expected pairs, 0 forbidden, ≥ 98 % status statements.",
    }
    return g.items, meta
