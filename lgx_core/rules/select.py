"""The pure selection function ``select(snapshot, T, config) -> Selection`` (PLAN §2.2; agent A, M1).

Input: the frozen SPARQL snapshot (:class:`~lgx_core.rules.snapshot.MetaSnapshot`), the sync date T and
the project configuration. Output: the metadata rows of the whole scope (``Act``, ``ActVersion``,
``Relation``), the manifest (``ManifestEntry``: which file serves which version on which dates), the
serving-plan rows and the selection report (INTERFACES §4). No I/O, no network.

Rule ids (``ManifestEntry.reason``) are stable; see ``REASONS``.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lgx_core.config import ProjectConfig
from lgx_core.eli import LEG_PREFIX, filestore_fetch_url, is_leg, relative
from lgx_core.models import (
    Act,
    ActFlag,
    ActStatus,
    ActVersion,
    DataQualityIssue,
    DateWindow,
    FileRef,
    ManifestEntry,
    Memorial,
    MetadataReason,
    Relation,
    RelationSource,
    RelType,
    SourceFormat,
    TextBasis,
    UserFormat,
    VersionKind,
    VersionRole,
)
from lgx_core.rules.licence import Pick, pick_format
from lgx_core.rules.snapshot import MetaSnapshot
from lgx_core.rules.status import act_status, status_flag_conflict

RULES_VERSION = "m1.1"

REASONS = {
    "0:excluded": "no row: adm branch, RC, naturalisation, denylist (counted in the report only)",
    "1:repealed": "act no longer in force at T (step 1)",
    "2a:repealed_base": "window-current consolidation whose base act is no longer in force",
    "2b:consolidated": "window-current consolidation with files",
    "2b:excluded_by_config": "consolidation text disabled by a scope knob (environnement_annexes)",
    "2c:stub_fallback": "window version is a stub; latest older version with files serves",
    "2c:stub_no_text": "window version is a stub without any older files; JO original serves",
    "2d:discontinued": "no window version, past versions exist, base live; JO original serves",
    "2d:discontinued_last_consolidation": "discontinued_policy last_consolidation: last version serves",
    "2d:recueil_ended": "recueil/code-recueil without base act and without a window version",
    "2e:future": "next version with files, stored, serves from its dateApplicability",
    "2e:future_no_files": "next version announced without files (metadata row)",
    "2e:future_only": "only future versions exist; act metadata-only until then",
    "2e:original_after_end": "JO original stored from the end date of a current version without successor",
    "2f:metadata_version": "every other consolidation version (window dates only)",
    "2f:history_text": "older version stored as point-in-time history (history.mode)",
    "3:original_never_amended": "JO original, no amending/repealing act in force",
    "3:original_possibly_outdated": "JO original, amended, no usable consolidation",
    "3:temporary": "temporary traffic RGD (title rule, no dnl)",
    "3:licence": "no CC-BY XML/HTML manifestation",
    "3:pdf_only": "only PDF manifestations (JO PDFs are not ingested in v1)",
    "3:amending_metadata_only": "pure amending act, amending_acts_text: metadata_only",
    "4:metadata_only": "every other act in the metadata scope (type not in text_types …)",
}

CODE_TYPES = frozenset({"CODE", "CODE_RECUEIL", "RECUEIL", "Constitution"})
KIND_OF_TYPE = {"CODE": VersionKind.CODE, "CODE_RECUEIL": VersionKind.CODE_RECUEIL, "RECUEIL": VersionKind.RECUEIL}
ENV_ANNEXES_WORK = LEG_PREFIX + "code/environnement_annexes"
AMENDING_TITLE = re.compile(
    r"^(\S+\s){0,9}?(portant (sur la |)modification|modifiant|complétant|portant abrogation|abrogeant)", re.I
)
REL_TYPES = {
    "modifies": RelType.MODIFIES,
    "repeals": RelType.REPEALS,
    "basedOn": RelType.BASED_ON,
    "cites": RelType.CITES,
    "rectifies": RelType.RECTIFIES,
    "transposes": RelType.TRANSPOSES,
}
URI_DATE = re.compile(r"/(\d{8})$")


# ------------------------------------------------------------------------------------- records
@dataclass
class ActRec:
    """One ``jolux:Act`` (Q01 rows grouped) with title and Mémorial reference."""

    eli_jo: str
    eli_work: str
    doc_type: str | None
    rdf_status: str | None
    dei: set[dt.date] = field(default_factory=set)
    dnl: dt.date | None = None
    pub: dt.date | None = None
    title: str | None = None
    historical_legal_id: str | None = None
    memorial_uri: str | None = None
    statuses: set[str] = field(default_factory=set)

    @property
    def dei_min(self) -> dt.date | None:
        return min(self.dei) if self.dei else None


@dataclass
class VersionRec:
    """One consolidation version (Q02) with its formats (Q03)."""

    eli: str
    cw: str
    d: dt.date
    end: dt.date | None
    st: str | None
    type: str | None
    created: dt.datetime | None
    formats: dict[str, set[str | None]] = field(default_factory=dict)

    @property
    def has_files(self) -> bool:
        return bool(self.formats)


@dataclass
class Edge:
    src: str
    rel: str
    dei: dt.date | None


@dataclass
class Served:
    """A version whose text is stored, with its serving window and chosen file."""

    eli: str
    role: VersionRole
    kind: VersionKind
    window: DateWindow
    pick: Pick
    reason: str
    file: FileRef | None = None


@dataclass
class Outcome:
    """Selection result of one work (one ``Act`` row)."""

    eli_work: str
    act: ActRec | None
    status: ActStatus | None
    text_basis: TextBasis = TextBasis.METADATA_ONLY
    metadata_reason: MetadataReason | None = None
    reason: str = "4:metadata_only"
    flags: set[ActFlag] = field(default_factory=set)
    doc_type: str = ""
    served: list[Served] = field(default_factory=list)
    roles: dict[str, tuple[VersionRole, str]] = field(default_factory=dict)  # version eli -> (role, reason)
    amending: list[Edge] = field(default_factory=list)
    window_version: VersionRec | None = None
    superseded_by: str | None = None
    licence_source: str | None = None
    licence_unknown: bool = False
    basis_without_licence: TextBasis | None = None  # label the act would have if its text were licensed

    def metadata(self, reason: MetadataReason, rule: str) -> Outcome:
        self.text_basis, self.metadata_reason, self.reason = TextBasis.METADATA_ONLY, reason, rule
        return self

    @property
    def current(self) -> Served | None:
        return next((s for s in self.served if s.role in (VersionRole.CURRENT, VersionRole.FALLBACK)), None)


@dataclass
class Selection:
    """Everything ``lgx select`` writes (INTERFACES §2/§4)."""

    sync_date: dt.date
    acts: list[Act]
    versions: list[ActVersion]
    relations: list[Relation]
    manifest: list[ManifestEntry]
    plan: list[dict[str, Any]]
    issues: list[DataQualityIssue]
    report: dict[str, Any]
    gdpr_review: list[dict[str, Any]]


# ------------------------------------------------------------------------------------- indexes
class SnapshotIndex:
    """Client-side joins over the snapshot tables (the endpoint is only ever asked for flat pulls)."""

    def __init__(self, snapshot: MetaSnapshot) -> None:
        self.snapshot = snapshot
        self.acts: dict[str, ActRec] = {}
        for r in snapshot.rows("acts"):
            a = r["a"]
            rec = self.acts.get(a)
            if rec is None:
                rec = self.acts[a] = ActRec(a, r["cw"], r["type"], r["st"])
            if r["st"]:
                rec.statuses.add(r["st"])
            if r["dei"]:
                rec.dei.add(r["dei"])
            if r["dnl"] and (rec.dnl is None or r["dnl"] < rec.dnl):
                rec.dnl = r["dnl"]
            if r.get("pub") and (rec.pub is None or r["pub"] < rec.pub):
                rec.pub = r["pub"]
        self.act_of_work = {rec.eli_work: rec for rec in self.acts.values()}
        for r in snapshot.rows("titles"):
            rec = self.acts.get(r["a"])
            if rec is not None and r["title"] and (rec.title is None or r["title"] < rec.title):
                rec.title = r["title"]
        for r in snapshot.rows("memorial_refs"):
            rec = self.acts.get(r["a"])
            if rec is not None:
                rec.historical_legal_id = rec.historical_legal_id or r["h"]
                rec.memorial_uri = rec.memorial_uri or r["mem"]
        self.versions: dict[str, VersionRec] = {}
        for r in snapshot.rows("consolidations"):
            if r["c"] not in self.versions and r["d"] is not None:
                self.versions[r["c"]] = VersionRec(
                    r["c"], r["cw"], r["d"], r.get("end"), r.get("st"), r.get("type"), r.get("created")
                )
        for r in snapshot.rows("conso_formats"):
            v = self.versions.get(r["c"])
            if v is not None and r["uf"]:
                v.formats.setdefault(r["uf"], set()).add(r["lic"])
        self.works: dict[str, list[VersionRec]] = defaultdict(list)
        for v in self.versions.values():
            self.works[v.cw].append(v)
        for vs in self.works.values():
            vs.sort(key=lambda v: (v.d, v.eli))
        edges: dict[str, dict[tuple[str, str], dt.date | None]] = defaultdict(dict)
        self.modifies_out: set[str] = set()
        for r in snapshot.rows("rel_modifies_repeals"):
            key = (r["src"], r["rel"])
            cur = edges[r["tgt"]].get(key)
            if key not in edges[r["tgt"]] or (r["dei"] and (cur is None or r["dei"] < cur)):
                edges[r["tgt"]][key] = r["dei"]
            if r["rel"] == "modifies":
                self.modifies_out.add(r["src"])
        self.edges_in: dict[str, list[Edge]] = {
            tgt: sorted((Edge(s, rel, d) for (s, rel), d in m.items()), key=_edge_key) for tgt, m in edges.items()
        }
        self.based_on: dict[str, set[str]] = defaultdict(set)
        for r in snapshot.rows("rel_other"):
            if r["rel"] == "basedOn":
                self.based_on[r["src"]].add(r["tgt"])
        self.act_formats: dict[str, dict[str, set[str | None]]] = defaultdict(dict)
        for r in snapshot.rows("act_formats"):
            if r["uf"]:
                self.act_formats[r["a"]].setdefault(r["uf"], set()).add(r["lic"])
        self.manif: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in snapshot.rows("manifestations"):
            self.manif[r["w"]].append(r)
        coverage = snapshot.info.get("act_formats_coverage")
        self.act_formats_coverage: Mapping[str, Any] | None = coverage if isinstance(coverage, Mapping) else None

    def act_formats_known(self, act: ActRec) -> bool:
        if not self.snapshot.has("act_formats"):
            return False
        cov = self.act_formats_coverage
        if cov is None:
            return True
        types, rdf = cov.get("doc_types"), cov.get("rdf_status")
        return (not types or act.doc_type in types) and (not rdf or act.rdf_status == rdf)


def _edge_key(e: Edge) -> tuple[dt.date, str, str]:
    return (e.dei or dt.date.max, e.src, e.rel)


# ----------------------------------------------------------------------------------- selection
class _Selector:
    def __init__(self, snapshot: MetaSnapshot, sync_date: dt.date, config: ProjectConfig, deny: Iterable[str]):
        self.ix = SnapshotIndex(snapshot)
        self.T = sync_date
        self.H = sync_date + dt.timedelta(days=config.scope.horizon_days)
        self.config = config
        self.scope = config.scope
        self.deny = sorted(
            {_abs(p).rstrip("/") for p in deny} | {_abs(e.eli).rstrip("/") for e in config.denylist.entries}
        )
        self._deny_exact = frozenset(self.deny)
        self._deny_tree = tuple(p + "/" for p in self.deny)
        self.natural = re.compile(self.scope.exclusions.naturalisation.title_regex)
        self.temporary = re.compile(self.scope.temporary_traffic.title_regex)
        self.members = {
            _abs(m.member): _abs(m.recueil)
            for m in sorted(config.recueil_members.members, key=lambda m: (m.member, m.recueil), reverse=True)
            if m.route
        }  # only routed members (route: false = excerpt, own consolidation, or recueil without text)
        self.excluded: Counter[str] = Counter()
        self.privacy_excluded: set[str] = set()  # work ELIs whose relations must also disappear
        self.anomalies: dict[str, list[str]] = defaultdict(list)
        self.gdpr_review: list[dict[str, Any]] = []
        self.restricted = False

    # --- scope (step 0) ----------------------------------------------------------------------
    def denied(self, eli: str) -> bool:
        return eli in self._deny_exact or eli.startswith(self._deny_tree)

    def exclusion(self, act: ActRec) -> str | None:
        if not is_leg(act.eli_work):
            return "branch"
        if self.denied(act.eli_work):
            self.privacy_excluded.add(act.eli_work)
            return "denylist"
        rule = self.scope.exclusions.naturalisation
        if act.doc_type in rule.doc_types and act.title and self.natural.search(act.title):
            self.privacy_excluded.add(act.eli_work)
            return "naturalisation"
        if act.title and act.doc_type == "LOI" and "naturalisation" in act.title.lower():
            self.gdpr_review.append({"eli_work": act.eli_work, "rdf_status": act.rdf_status, "rule": "review"})
        if self.scope.metadata_scope == "memorial_a_core" and act.doc_type in self.scope.exclusions.doc_types:
            return f"doc_type:{act.doc_type}"
        return None

    def in_scope_works(self) -> list[str]:
        works = []
        for act in self.ix.acts.values():
            reason = self.exclusion(act)
            if reason:
                self.excluded[reason] += 1
            else:
                works.append(act.eli_work)
        for cw in self.ix.works:
            if cw in self.ix.act_of_work:
                continue
            if not is_leg(cw):
                self.excluded["branch"] += 1
            elif self.denied(cw):
                self.excluded["denylist"] += 1
                self.privacy_excluded.add(cw)
            elif not any(self.consolidation_type_ok(v, None) for v in self.ix.works[cw]):
                self.excluded["consolidation_type"] += 1
            else:
                works.append(cw)
        return sorted(works)

    # --- helpers -------------------------------------------------------------------------------
    def consolidation_type_ok(self, v: VersionRec, act: ActRec | None) -> bool:
        """``scope.consolidation_types`` (``'*'`` = every type; an untyped version takes its base act's)."""
        types = self.scope.consolidation_types
        return "*" in types or (v.type or (act.doc_type if act else None)) in types

    def status(self, act: ActRec | None, day: dt.date | None = None) -> ActStatus | None:
        if act is None:
            return None
        return act_status(act.dei_min, act.dnl, act.rdf_status, day or self.T)

    def amendments(self, act: ActRec | None, after: dt.date | None, until: dt.date) -> list[Edge]:
        """Modifying/repealing acts in force in ``(after, until]`` (any source status: a later repeal of
        the amending act does not undo its amendments)."""
        if act is None:
            return []
        return [
            e
            for e in self.ix.edges_in.get(act.eli_jo, ())
            if e.dei and (after is None or e.dei > after) and e.dei <= until
        ]

    def pick_version(self, v: VersionRec) -> Pick:
        formats: Mapping[str, Iterable[str | None]] = v.formats
        rows = self.ix.manif.get(v.eli)
        if rows:
            formats = _formats_from_manif(rows)
        return pick_format(formats, policy=self.scope.licence_policy, allow_pdf=True)

    def file_for(self, eli: str, pick: Pick) -> FileRef | None:
        """The fetchable file of the chosen manifestation (Q-MANIF).

        Files under the version's own filestore path win. A consolidation whose only file is a whole
        Mémorial issue (old « textes coordonnés », e.g. ``loi/1993/04/05/n1/consolide/20031001`` →
        ``memorial/2003/a164``) keeps it, flagged as an anomaly; a JO act never does (an issue holds many
        acts, Appendix A Q-MANIF). Files under a denylisted path are never chosen.
        """
        if not pick.ok:
            return None
        own = "http://data.legilux.public.lu/filestore/eli/" + relative(eli).rstrip("/") + "/"
        issue = "http://data.legilux.public.lu/filestore/eli/etat/leg/memorial/"
        rows = [
            r
            for r in self.ix.manif.get(eli, ())
            if r["uf"] in pick.user_formats
            and r["lic"] == pick.licence
            and not self.denied("http://data.legilux.public.lu/eli/" + str(r["file"]).split("/filestore/eli/", 1)[-1])
        ]
        mine = [r for r in rows if str(r["file"]).startswith(own)]
        if not mine and not eli.endswith("/jo"):
            mine = [r for r in rows if str(r["file"]).startswith(issue)]
            if mine:
                self.anomalies["consolidation_file_is_memorial_issue"].append(eli)
        if not mine:
            if self.ix.manif.get(eli):
                self.anomalies["manif_without_chosen_file"].append(eli)
            return None
        best = min(mine, key=lambda r: (r["uf"], r["file"]))
        assert pick.source_format is not None
        return FileRef(
            url=filestore_fetch_url(best["file"]),
            file_uri=best["file"],
            user_format=UserFormat(best["uf"]),
            source_format=pick.source_format,
            licence=best["lic"],
            manif_modified=best.get("mod"),
        )

    def serve(
        self, out: Outcome, eli: str, role: VersionRole, kind: VersionKind, lower: dt.date | None, pick: Pick, rule: str
    ) -> Served:
        s = Served(eli, role, kind, DateWindow(lower=lower), pick, rule, self.file_for(eli, pick))
        out.served.append(s)
        out.roles[eli] = (role, rule)
        return s

    # --- step 2 + 3 per work -------------------------------------------------------------------
    def work(self, w: str) -> Outcome:
        act = self.ix.act_of_work.get(w)
        out = Outcome(w, act, self.status(act))
        out.doc_type = (act.doc_type if act else None) or ""
        if act is not None:
            out.flags |= self.act_flags(act, out.status)
        versions = [v for v in self.ix.works.get(w, []) if is_leg(v.eli) and self.consolidation_type_ok(v, act)]
        if not out.doc_type:
            out.doc_type = next((v.type for v in reversed(versions) if v.type), None) or "RECUEIL"
        for v in versions:
            out.roles[v.eli] = (VersionRole.METADATA, "2f:metadata_version")
        if versions:
            self.consolidated_work(out, versions)
        else:
            self.original(out, versions, forced=None)
        self.close_windows(out, versions)
        self.route_member(out)
        return out

    def route_member(self, out: Outcome) -> None:
        """Code-recueil members without a usable consolidation of their own are routed to the code text
        (PLAN §2.6 item 4): served JO originals in step 3, and members without stored text (PDF-only,
        licence, type) too — e.g. ``agd/1955/11/23/n1``, whose only consolidated text is the Code de la route.
        Repealed members and members with their own consolidation are never routed."""
        member_of = self.members.get(out.eli_work)
        if not member_of or out.superseded_by or out.text_basis in (TextBasis.CONSOLIDATED, TextBasis.STUB_FALLBACK):
            return
        if out.text_basis is TextBasis.METADATA_ONLY and out.metadata_reason in (
            MetadataReason.REPEALED,
            MetadataReason.REPEALED_BASE,
            MetadataReason.TEMPORARY,
            MetadataReason.EXCLUDED_BY_CONFIG,
        ):
            return
        out.flags.add(ActFlag.SUPERSEDED_BY_CODE)
        out.superseded_by = member_of

    def consolidated_work(self, out: Outcome, versions: list[VersionRec]) -> None:
        T, act = self.T, out.act
        window = [v for v in versions if v.d <= T and (v.end is None or v.end > T)]
        if len(window) > 1:
            self.anomalies["multiple_window_versions"].append(out.eli_work)
        vt = window[-1] if window else None
        out.window_version = vt
        past = [v for v in versions if v.d <= T]
        if act is not None and out.status is ActStatus.NO_LONGER_IN_FORCE:
            if vt is not None:
                out.metadata(MetadataReason.REPEALED_BASE, "2a:repealed_base")
            else:
                out.metadata(MetadataReason.REPEALED, "1:repealed")
            return
        if out.eli_work == ENV_ANNEXES_WORK and not self.scope.include_env_annexes_text:
            out.metadata(MetadataReason.EXCLUDED_BY_CONFIG, "2b:excluded_by_config")
            return
        if vt is not None:
            pick = self.pick_version(vt)
            if vt.has_files and not pick.ok:
                self.anomalies["window_version_without_usable_licence"].append(vt.eli)
            if pick.ok:
                self.serve(out, vt.eli, VersionRole.CURRENT, _kind(vt), vt.d, pick, "2b:consolidated")
                out.text_basis, out.reason = TextBasis.CONSOLIDATED, "2b:consolidated"
                lag = self.amendments(act, vt.d, T)
                if lag:
                    out.flags.add(ActFlag.CONSOLIDATION_LAGS)
                    out.amending = lag
                self.after_consolidation(out, vt, pick, versions)
                self.original_after_end(out, vt, versions)
                return
            older = [v for v in versions if v.d < vt.d and self.pick_version(v).ok]
            if older:
                fb = older[-1]
                fpick = self.pick_version(fb)
                self.serve(out, fb.eli, VersionRole.FALLBACK, _kind(fb), fb.d, fpick, "2c:stub_fallback")
                out.text_basis, out.reason = TextBasis.STUB_FALLBACK, "2c:stub_fallback"
                out.amending = self.amendments(act, fb.d, T)
                self.after_consolidation(out, fb, fpick, versions)
                return
            out.flags.add(ActFlag.STUB_NO_TEXT)
            if act is None:
                out.metadata(MetadataReason.NOT_YET_TEXT, "2c:stub_no_text")
                self.future(out, versions)
                return
            self.original(out, versions, forced="2c:stub_no_text")
            return
        if past:
            out.flags.add(ActFlag.CONSOLIDATION_DISCONTINUED)
            if self.scope.discontinued_policy == "last_consolidation":
                usable = [v for v in past if self.pick_version(v).ok]
                if usable:
                    last = usable[-1]
                    pick = self.pick_version(last)
                    rule = "2d:discontinued_last_consolidation"
                    self.serve(out, last.eli, VersionRole.FALLBACK, _kind(last), last.d, pick, rule)
                    out.text_basis, out.reason = TextBasis.STUB_FALLBACK, rule
                    out.amending = self.amendments(act, last.d, T)
                    self.after_consolidation(out, last, pick, versions)
                    return
            if act is None:
                out.metadata(MetadataReason.REPEALED, "2d:recueil_ended")
                return
            self.original(out, versions, forced="2d:discontinued")
            return
        # only future versions
        if act is None or out.status is ActStatus.NOT_YET_IN_FORCE:
            out.metadata(MetadataReason.NOT_YET_TEXT, "2e:future_only")
            self.future(out, versions)
            return
        self.original(out, versions, forced=None)

    def original_after_end(self, out: Outcome, vt: VersionRec, versions: list[VersionRec]) -> None:
        """A current version with a scheduled end and no successor becomes discontinued on its end date
        (step 2d): store the JO original from then on so an ageing database switches by itself."""
        act = out.act
        if (
            vt.end is None
            or any(v.d > vt.d for v in versions)
            or act is None
            or self.scope.discontinued_policy != "original"
            or act.doc_type not in self.scope.text_types
            or (act.dnl is not None and act.dnl <= vt.end)
        ):
            return
        pick, _source = self.pick_original(act)
        if not pick.ok:
            return
        rule = "2e:original_after_end"
        s = Served(act.eli_jo, VersionRole.FUTURE, VersionKind.ORIGINAL, DateWindow(lower=vt.end), pick, rule)
        s.file = self.file_for(act.eli_jo, pick)
        out.served.append(s)

    def after_consolidation(self, out: Outcome, served: VersionRec, pick: Pick, versions: list[VersionRec]) -> None:
        if pick.source_format is SourceFormat.PDF:
            out.flags.add(ActFlag.PDF_EXTRACTED)
        if pick.scl:
            out.flags.add(ActFlag.LICENCE_SCL_SOURCE)
        vtype = served.type or (out.act.doc_type if out.act else None)
        if vtype in CODE_TYPES:
            out.doc_type = vtype
        out.licence_source = "q_manif" if self.ix.manif.get(served.eli) else "conso_formats"
        self.history(out, served, versions)
        self.future(out, versions)

    def history(self, out: Outcome, served: VersionRec, versions: list[VersionRec]) -> None:
        hist = self.scope.history
        if hist.mode == "none" or (hist.restrict_to and out.doc_type not in hist.restrict_to):
            return
        older = [v for v in versions if v.d < served.d and self.pick_version(v).ok]
        if hist.mode == "full_text_last_n":
            older = older[-hist.keep_versions :] if hist.keep_versions > 0 else []
        elif hist.since is not None:
            older = [v for v in older if v.d >= hist.since]
        for v in older:
            self.serve(out, v.eli, VersionRole.HISTORY_TEXT, _kind(v), v.d, self.pick_version(v), "2f:history_text")

    def future(self, out: Outcome, versions: list[VersionRec]) -> None:
        policy = self.scope.future_versions
        upcoming = [v for v in versions if v.d > self.T]
        if policy == "none" or not upcoming:
            return
        for v in upcoming if policy == "all" else upcoming[:1]:
            if v.d > self.H:
                out.roles[v.eli] = (VersionRole.METADATA, "2e:future_no_files")
                continue
            pick = self.pick_version(v)
            if pick.ok:
                self.serve(out, v.eli, VersionRole.FUTURE, _kind(v), v.d, pick, "2e:future")
            else:
                out.roles[v.eli] = (VersionRole.METADATA, "2e:future_no_files")

    def original(self, out: Outcome, versions: list[VersionRec], forced: str | None) -> None:
        """Step 3: the JO original (or a metadata-only row) for an act not served in step 2."""
        act, T = out.act, self.T
        if act is None:  # recueil without base act and without versions: cannot happen in practice
            out.metadata(MetadataReason.NOT_YET_TEXT, "4:metadata_only")
            return
        if out.status is ActStatus.NO_LONGER_IN_FORCE:
            out.metadata(MetadataReason.REPEALED, "1:repealed")
            return
        if act.doc_type not in self.scope.text_types:
            out.metadata(MetadataReason.TYPE, "4:metadata_only")
            self.future(out, versions)  # a consolidation is served whatever the base act's type
            return
        rule = self.scope.temporary_traffic
        if (
            act.doc_type in rule.doc_types
            and act.title
            and self.temporary.search(act.title)
            and (act.dnl is None or not rule.requires_no_dnl)
        ):
            out.flags.add(ActFlag.ACTE_TEMPORAIRE)
            if not self.scope.include_temporary_traffic:
                out.metadata(MetadataReason.TEMPORARY, "3:temporary")
                return
        if self.scope.amending_acts_text == "metadata_only" and ActFlag.IS_AMENDING_ACT in out.flags:
            out.metadata(MetadataReason.EXCLUDED_BY_CONFIG, "3:amending_metadata_only")
            return
        pick, source = self.pick_original(act)
        out.licence_source = source
        if source == "unknown":
            out.licence_unknown = True
            self.anomalies["licence_unknown"].append(act.eli_jo)
        amended = self.amendments(act, None, T)
        outdated = bool(forced or amended or versions)
        if source != "unknown" and not pick.ok:
            assert pick.reason is not None
            out.basis_without_licence = (
                TextBasis.ORIGINAL_POSSIBLY_OUTDATED if outdated else TextBasis.ORIGINAL_NEVER_AMENDED
            )
            out.amending = amended
            out.metadata(
                pick.reason, f"3:{pick.reason.value}" if pick.reason is MetadataReason.PDF_ONLY else "3:licence"
            )
            self.future(out, versions)  # the next (CC BY) consolidation still switches on by itself
            return
        if pick.scl:
            out.flags.add(ActFlag.LICENCE_SCL_SOURCE)
        if outdated:
            out.text_basis = TextBasis.ORIGINAL_POSSIBLY_OUTDATED
            out.reason = forced or "3:original_possibly_outdated"
        else:
            out.text_basis, out.reason = TextBasis.ORIGINAL_NEVER_AMENDED, "3:original_never_amended"
        out.amending = amended
        member_of = self.members.get(out.eli_work)
        if member_of:
            out.flags.add(ActFlag.SUPERSEDED_BY_CODE)
            out.superseded_by = member_of
        lower = act.dei_min or act.pub
        served = Served(
            act.eli_jo, VersionRole.CURRENT, VersionKind.ORIGINAL, DateWindow(lower=lower), pick, out.reason
        )
        served.file = self.file_for(act.eli_jo, pick) if pick.ok else None
        out.served.append(served)
        self.future(out, versions)

    def pick_original(self, act: ActRec) -> tuple[Pick, str]:
        rows = self.ix.manif.get(act.eli_jo)
        if rows:
            return pick_format(_formats_from_manif(rows), policy=self.scope.licence_policy), "q_manif"
        if self.ix.act_formats_known(act):
            formats = self.ix.act_formats.get(act.eli_jo, {})
            return pick_format(formats, policy=self.scope.licence_policy), "act_formats"
        return Pick(None), "unknown"

    def close_windows(self, out: Outcome, versions: list[VersionRec]) -> None:
        """Step 2g: window of each stored text = [its start, least(next stored start, base dnl))."""
        dnl = out.act.dnl if out.act else None
        chain = sorted(out.served, key=lambda s: (s.window.lower or dt.date.min, s.role is VersionRole.FUTURE))
        kept: list[Served] = []
        for i, s in enumerate(chain):
            nxt = chain[i + 1].window.lower if i + 1 < len(chain) else None
            upper = min((d for d in (nxt, dnl) if d is not None), default=None)
            lower = s.window.lower
            if lower is not None and upper is not None and upper <= lower:
                out.roles[s.eli] = (VersionRole.METADATA, "2f:metadata_version")
                self.anomalies["empty_text_window"].append(s.eli)
                continue
            s.window = DateWindow(lower=lower, upper=upper)
            kept.append(s)
        out.served = kept
        if out.text_basis is not TextBasis.METADATA_ONLY and out.current is None:
            # the only text had an empty window (e.g. dnl before dei): nothing serves at T
            self.anomalies["text_basis_without_text"].append(out.eli_work)
            out.metadata(MetadataReason.REPEALED, "1:repealed")

    # --- act flags -----------------------------------------------------------------------------
    def act_flags(self, act: ActRec, status: ActStatus | None) -> set[ActFlag]:
        flags: set[ActFlag] = set()
        if len(act.dei) > 1:
            flags.add(ActFlag.ENTRY_DATES_MULTIPLE)
        if status_flag_conflict(act.dei_min, act.dnl, act.rdf_status, self.T):
            flags.add(ActFlag.STATUS_FLAG_CONFLICT)
        ref = act.pub or act.dei_min
        if ref is not None and ref.year < self.scope.old_act_before_year:
            flags.add(ActFlag.OLD_ACT)
        if act.eli_jo in self.ix.modifies_out and act.title and AMENDING_TITLE.search(act.title):
            flags.add(ActFlag.IS_AMENDING_ACT)
        if status is not ActStatus.NO_LONGER_IN_FORCE and self.base_legale_abrogee(act):
            flags.add(ActFlag.BASE_LEGALE_ABROGEE)
        return flags

    def base_legale_abrogee(self, act: ActRec) -> bool:
        """Every national ``jolux:basedOn`` target is no longer in force at T (EU targets ignored)."""
        targets = [self.ix.acts[t] for t in self.ix.based_on.get(act.eli_jo, ()) if t in self.ix.acts]
        return bool(targets) and all(self.status(t) is ActStatus.NO_LONGER_IN_FORCE for t in targets)


def _abs(eli: str) -> str:
    return eli if eli.startswith("http") else "http://data.legilux.public.lu/eli/" + eli.strip("/")


def _kind(v: VersionRec) -> VersionKind:
    return KIND_OF_TYPE.get(v.type or "", VersionKind.CONSOLIDATED)


def _formats_from_manif(rows: Iterable[Mapping[str, Any]]) -> dict[str, set[str | None]]:
    out: dict[str, set[str | None]] = {}
    for r in rows:
        if r["uf"]:
            out.setdefault(r["uf"], set()).add(r["lic"])
    return out


# ------------------------------------------------------------------------------------- output
def _label(v: VersionRec) -> str:
    return f"Version consolidée applicable au {v.d:%d/%m/%Y}"


class _Writer:
    """Turns outcomes into the typed records of INTERFACES §2."""

    def __init__(self, sel: _Selector) -> None:
        self.sel = sel
        self.sample = sel.config.sample.by_work()

    def act(self, out: Outcome) -> Act:
        a = out.act
        current = out.current
        memorial = None
        if a is not None and a.memorial_uri:
            try:
                memorial = Memorial.from_uri(a.memorial_uri).label
            except ValueError:
                memorial = None
        return Act(
            eli_work=out.eli_work,
            eli_jo=a.eli_jo if a else None,
            doc_type=out.doc_type,
            publication_date=a.pub if a else None,
            dei_min=a.dei_min if a else None,
            dei_count=len(a.dei) if a else 0,
            dnl=a.dnl if a else None,
            rdf_status=a.rdf_status if a else None,
            memorial_ref=memorial,
            historical_legal_id=a.historical_legal_id if a else None,
            title=a.title if a else None,
            text_basis=out.text_basis,
            metadata_reason=out.metadata_reason,
            flags=sorted(out.flags),
            basis_version_eli=current.eli if current and out.text_basis is not TextBasis.METADATA_ONLY else None,
            superseded_by_eli=out.superseded_by,
            rank_prior=0.8 if ActFlag.OLD_ACT in out.flags else 1.0,
        )

    def versions(self, out: Outcome) -> list[ActVersion]:
        served = {s.eli: s for s in out.served}
        rows = []
        for v in self.sel.ix.works.get(out.eli_work, []):
            if not is_leg(v.eli):
                continue
            role, _ = out.roles.get(v.eli, (VersionRole.METADATA, ""))
            s = served.get(v.eli)
            rows.append(
                ActVersion(
                    eli_work=out.eli_work,
                    eli_version=v.eli,
                    kind=_kind(v),
                    role=role,
                    date_applicability=v.d,
                    date_end_applicability=v.end,
                    rdf_status=v.st,
                    is_stub=not v.has_files,
                    has_files=v.has_files,
                    label=_label(v),
                    source_format=s.pick.source_format if s else None,
                    source_url=s.file.url if s and s.file else None,
                    licence=s.pick.licence if s else None,
                    manif_modified=s.file.manif_modified if s and s.file else None,
                    created=v.created,
                )
            )
        jo = next((s for s in out.served if s.kind is VersionKind.ORIGINAL), None)
        if jo is not None and out.act is not None:
            rows.append(
                ActVersion(
                    eli_work=out.eli_work,
                    eli_version=jo.eli,
                    kind=VersionKind.ORIGINAL,
                    role=jo.role,
                    date_applicability=out.act.dei_min,
                    date_end_applicability=out.act.dnl,
                    rdf_status=out.act.rdf_status,
                    has_files=jo.pick.ok,
                    label="Texte original",
                    source_format=jo.pick.source_format,
                    source_url=jo.file.url if jo.file else None,
                    licence=jo.pick.licence,
                    manif_modified=jo.file.manif_modified if jo.file else None,
                )
            )
        return rows

    def manifest(self, out: Outcome) -> list[ManifestEntry]:
        sample = self.sample.get(out.eli_work)
        common: dict[str, Any] = {
            "eli_work": out.eli_work,
            "doc_type": out.doc_type,
            "text_basis": out.text_basis,
            "flags": sorted(out.flags),
            "metadata_reason": out.metadata_reason,
            "amending_acts": [e.src for e in out.amending],
            "in_sample": sample is not None,
            "sample_stage": sample.stage if sample else None,
        }
        served = {s.eli: s for s in out.served}
        entries = []
        for v in self.sel.ix.works.get(out.eli_work, []):
            if not is_leg(v.eli):
                continue
            role, rule = out.roles.get(v.eli, (VersionRole.METADATA, "2f:metadata_version"))
            s = served.get(v.eli)
            if out.text_basis is TextBasis.METADATA_ONLY and s is None and rule == "2f:metadata_version":
                rule = out.reason if v is out.window_version else rule
            entries.append(
                ManifestEntry(
                    **common,
                    eli_version=v.eli,
                    kind=_kind(v),
                    role=role,
                    file=s.file if s else None,
                    text_valid=s.window if s else None,
                    date_applicability=v.d,
                    date_end_applicability=v.end,
                    reason=rule,
                )
            )
        for s in out.served:
            if s.kind is VersionKind.ORIGINAL:
                entries.append(
                    ManifestEntry(
                        **common,
                        eli_version=s.eli,
                        kind=VersionKind.ORIGINAL,
                        role=s.role,
                        file=s.file,
                        text_valid=s.window,
                        date_applicability=out.act.dei_min if out.act else None,
                        date_end_applicability=out.act.dnl if out.act else None,
                        reason=s.reason,
                    )
                )
        if not entries:
            entries.append(ManifestEntry(**common, role=VersionRole.METADATA, reason=out.reason))
        return entries

    def plan_row(self, out: Outcome) -> dict[str, Any]:
        cur = out.current
        fut = next((s for s in out.served if s.role is VersionRole.FUTURE), None)
        sample = self.sample.get(out.eli_work)
        text = out.text_basis is not TextBasis.METADATA_ONLY
        wv = out.window_version
        return {
            "eli_work": out.eli_work,
            "eli_jo": out.act.eli_jo if out.act else None,
            "doc_type": out.doc_type,
            "act_type": out.act.doc_type if out.act else None,
            "status_at_t": out.status.value if out.status else None,
            "text_basis": out.text_basis.value,
            "metadata_reason": out.metadata_reason.value if out.metadata_reason else None,
            "reason": out.reason,
            "flags": [f.value for f in sorted(out.flags)],
            "served_version": cur.eli if cur and text else None,
            "served_kind": cur.kind.value if cur and text else None,
            "served_role": cur.role.value if cur and text else None,
            "format": cur.pick.source_format.value if cur and text and cur.pick.source_format else None,
            "licence": cur.pick.licence if cur and text else None,
            "licence_source": out.licence_source,
            "file_url": cur.file.url if cur and text and cur.file else None,
            "file_uri": cur.file.file_uri if cur and text and cur.file else None,
            "text_valid_from": cur.window.lower if cur and text else None,
            "text_valid_to": cur.window.upper if cur and text else None,
            "window_version": wv.eli if wv else None,
            "window_is_stub": (not wv.has_files) if wv else None,
            "future_version": fut.eli if fut else None,
            "future_valid_from": fut.window.lower if fut else None,
            "future_file_url": fut.file.url if fut and fut.file else None,
            "amending_acts": [e.src for e in out.amending],
            "n_amending": len(out.amending),
            "superseded_by": out.superseded_by,
            "text_basis_without_licence": out.basis_without_licence.value if out.basis_without_licence else None,
            "in_sample": sample is not None,
            "sample_stage": sample.stage if sample else None,
            "title": out.act.title if out.act else None,
        }


# ----------------------------------------------------------------------------------- relations
def _relations(sel: _Selector, scope_jo: set[str]) -> list[Relation]:
    """SPARQL edges kept for the database: at least one end in scope, no privacy-excluded end."""

    def private(eli: str) -> bool:
        work = eli[:-3] if eli.endswith("/jo") else eli
        return work in sel.privacy_excluded or sel.denied(work)

    out: dict[tuple[str, RelType, str], Relation] = {}
    for r in sel.ix.snapshot.rows("rel_modifies_repeals"):
        rel = REL_TYPES.get(r["rel"] or "")
        if rel is None or not (r["tgt"] in scope_jo or r["src"] in scope_jo) or private(r["src"]) or private(r["tgt"]):
            continue
        key = (r["src"], rel, r["tgt"])
        prev = out.get(key)
        if prev is None or (r["dei"] and (prev.src_dei is None or r["dei"] < prev.src_dei)):
            out[key] = Relation(
                src_eli=r["src"], rel_type=rel, dst_eli=r["tgt"], source=RelationSource.SPARQL, src_dei=r["dei"]
            )
    for r in sel.ix.snapshot.rows("rel_other"):
        rel = REL_TYPES.get(r["rel"] or "")
        if rel is None or r["src"] not in scope_jo or private(r["tgt"]):
            continue
        key = (r["src"], rel, r["tgt"])
        if key not in out:
            out[key] = Relation(src_eli=r["src"], rel_type=rel, dst_eli=r["tgt"], source=RelationSource.SPARQL)
    return [out[k] for k in sorted(out, key=lambda k: (k[0], k[1].value, k[2]))]


# ------------------------------------------------------------------------------------ the entry
def select(
    snapshot: MetaSnapshot,
    sync_date: dt.date,
    config: ProjectConfig,
    *,
    denylist: Iterable[str] = (),
    works: Iterable[str] | None = None,
) -> Selection:
    """Return acts, versions, relations, manifest entries and the selection report for ``sync_date``.

    ``denylist`` adds robots-derived ELI prefixes to ``config.denylist``. ``works`` restricts the output
    to those work ELIs (tests and Q-MANIF candidates); the report then covers only them.
    """
    from lgx_core.rules.report import build_report

    sel = _Selector(snapshot, sync_date, config, denylist)
    scope_works = sel.in_scope_works()
    wanted = set(works) if works is not None else None
    sel.restricted = wanted is not None
    writer = _Writer(sel)
    outcomes = [sel.work(w) for w in scope_works if wanted is None or w in wanted]
    acts = [writer.act(o) for o in outcomes]
    versions = [v for o in outcomes for v in writer.versions(o)]
    manifest = [e for o in outcomes for e in writer.manifest(o)]
    plan = [writer.plan_row(o) for o in outcomes]
    scope_jo = {a.eli_jo for a in acts if a.eli_jo}
    relations = _relations(sel, scope_jo)
    issues = _issues(sel, outcomes)
    report = build_report(sel, outcomes, acts, manifest, issues, plan)
    return Selection(sync_date, acts, versions, relations, manifest, plan, issues, report, sel.gdpr_review)


def _issues(sel: _Selector, outcomes: Sequence[Outcome]) -> list[DataQualityIssue]:
    issues: dict[tuple[str, str], DataQualityIssue] = {}

    def add(eli: str, code: str, detail: str) -> None:
        issues.setdefault((eli, code), DataQualityIssue(entity_eli=eli, code=code, detail=detail, first_seen=sel.T))

    for o in outcomes:
        a = o.act
        if a is not None and ActFlag.STATUS_FLAG_CONFLICT in o.flags:
            add(
                o.eli_work,
                "flag_date_conflict",
                f"rdf {a.rdf_status}, dei {a.dei_min}, dnl {a.dnl}, computed {o.status}",
            )
        if a is not None and len(a.statuses) > 1:
            add(o.eli_work, "multiple_status_values", ", ".join(sorted(a.statuses)))
        for v in sel.ix.works.get(o.eli_work, []):
            m = URI_DATE.search(v.eli)
            if m and m.group(1) != v.d.strftime("%Y%m%d"):
                add(v.eli, "uri_date_mismatch", f"URI date {m.group(1)} vs dateApplicability {v.d}")
            in_window = v.d <= sel.T and (v.end is None or v.end > sel.T)
            if (v.st == "applicable") != in_window:
                add(v.eli, "consolidation_flag_window_conflict", f"flag {v.st}, window {v.d}..{v.end}")
        if o.eli_work in sel.anomalies.get("multiple_window_versions", ()):
            add(o.eli_work, "multiple_window_versions", "more than one consolidation window contains T")
    return [issues[k] for k in sorted(issues)]


def manifestation_candidates(
    snapshot: MetaSnapshot, sync_date: dt.date, config: ProjectConfig, *, works: Iterable[str] | None = None
) -> list[str]:
    """Version / act IRIs that will carry a file in the manifest (the Q-MANIF ``VALUES`` list)."""
    tables = {k: v for k, v in snapshot.tables.items() if k != "manifestations"}
    sel = select(MetaSnapshot(tables=tables, info=snapshot.info), sync_date, config, works=works)
    return sorted({e.eli_version for e in sel.manifest if e.text_valid is not None and e.eli_version})
