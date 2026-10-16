"""Golden numbers of PLAN §11.5 on the frozen 2026-10-16 snapshot.

Two families, both pure functions of a snapshot:

* ``research_numbers`` re-implements the research definitions exactly (``gaps-and-reconciliation``
  ``bin/recon.py``, ``bin/acts.py``, ``bin/lag.py``, the b1/tt1/l4 analyses and plan check P8). They are
  computed over all branches where the research did, and use the raw RDF flags where the research did
  (e.g. "in-force LOI" = ``inForceStatus in-force``). They must match ``EXPECTED`` exactly on the frozen
  snapshot; on a live snapshot they drift with the data.
* ``selection_numbers`` counts what the selection rules (dates win, ``/eli/etat/leg/`` only) produce.
  ``scripts/check_golden.py`` prints both next to the expected values and explains the differences.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from lgx_core.eli import LEG_PREFIX
from lgx_core.models import CC_BY_4_0, ActFlag, ActStatus, TextBasis
from lgx_core.rules.snapshot import MetaSnapshot

GOLDEN_DATE = dt.date(2026, 10, 16)

# PLAN §11.5 / §2.2 / §2.4 and the research tracks (all [V] on 2026-10-16).
EXPECTED: dict[str, int] = {
    "window_current_works": 1213,
    "flag_applicable": 1181,
    "flag_minus_window": 2,
    "window_minus_flag": 34,
    "open_ended_versions": 1214,
    "window_xml": 1132,
    "window_pdf_only": 32,
    "window_stubs": 49,
    "stubs_with_fallback": 10,
    "stubs_without_fallback": 39,
    "repealed_base": 35,
    "usable": 1129,
    "usable_xml": 1097,
    "usable_pdf_only": 32,
    "usable_leg": 1118,
    "discontinued": 40,
    "lagging": 13,
    "future_versions": 34,
    "future_works": 18,
    "future_with_files": 26,
    "loi_never_modified": 6803,
    "loi_up_to_date": 467,
    "loi_never_consolidated": 721,
    "loi_only_ended": 16,
    "loi_stub": 18,
    "loi_lags": 8,
    "rgd_never_modified": 8445,
    "rgd_up_to_date": 486,
    "rgd_never_consolidated": 764,
    "rgd_only_ended": 17,
    "rgd_stub": 19,
    "rgd_lags": 3,
    "temporary_traffic_rgd": 663,
    "temporary_traffic_rgd_title_rule": 638,
    "c04_minus_window_not_repealed": 50,
    "discontinued_base_live": 38,
    "base_legale_abrogee_rgd": 1586,
    "rgd_with_national_base_in_force": 7651,
    "acts_distinct": 151250,
    "consolidations_distinct": 4719,
    "leg_acts": 80918,
    "leg_in_force": 64536,
    "leg_no_longer": 16320,
    "leg_implicit": 48,
    "leg_not_yet": 14,
    "leg_rc": 21656,
    "leg_loi_naturalisation_titles": 951,
    "leg_loi_naturalisation_rule": 918,
    "licence_cc_by_xml": 8115,
    "licence_cc_by_html_only": 15502,
    "licence_scl_text_only": 619,
    "licence_scl_pdf_only": 4442,
    "licence_cc_by_pdf_only": 25,
    "licence_leg_cc_by_xml": 7765,
    "licence_leg_cc_by_html_only": 15499,
    "licence_leg_scl_text_only": 608,
    "licence_leg_scl_pdf_only": 1459,
    "licence_leg_cc_by_pdf_only": 3,
}

_Q09_NOTE = "Q09 has no branch filter; a live Q19 snapshot is leg-only, so compare licence_leg_*"
RESEARCH_NOTES: dict[str, str] = dict.fromkeys(
    (
        "licence_cc_by_xml",
        "licence_cc_by_html_only",
        "licence_scl_text_only",
        "licence_scl_pdf_only",
        "licence_cc_by_pdf_only",
    ),
    _Q09_NOTE,
)

# Selection-derived numbers (leg branch only, dates win over flags) with their reconciliation to PLAN §11.5.
EXPECTED_SELECTION: dict[str, int] = {
    "window_current_works": 1198,
    "window_stubs": 48,
    "consolidated": 1118,
    "stub_fallback": 10,
    "stub_no_text": 38,
    "consolidation_discontinued": 40,
    "repealed_base": 31,
    "consolidation_lags": 12,
    "temporary_traffic": 638,
    "base_legale_abrogee_rgd": 1586,
}
SELECTION_NOTES: dict[str, str] = {
    "window_current_works": "1,213 minus the 15 current /etat/adm/ consolidations (robots-disallowed branch)",
    "window_stubs": "49 minus 1 in /etat/adm/",
    "consolidated": "= usable_leg 1,118 (PLAN: 1,129 usable of which 1,118 in /etat/leg/)",
    "stub_no_text": "39 window stubs without fallback minus 1 in /etat/adm/ (out of scope)",
    "repealed_base": (
        "35 minus 3 in /etat/adm/ minus loi/2010/05/27/n2 (flag no-longer-in-force but dnl 2026-10-20 > T: "
        "dates win, it stays consolidated until its dnl)"
    ),
    "consolidation_lags": "13 minus 1 in /etat/adm/ (agc/2023/10/06/b3399)",
    "temporary_traffic": (
        "663 used the looser research pattern temporaire.*circulation, which also matches 25 COVID-19 "
        "RGDs (libre circulation des personnes); the scope.yaml title rule gives 638"
    ),
}

_TEMPORARY = re.compile(r"r[ée]glementation\s+temporaire\s+de\s+la\s+circulation", re.I)  # scope.yaml rule
# The research count (663) used this looser pattern; it also matches 25 COVID-19 RGDs on « mesures
# temporaires … libre circulation des personnes et l'immigration », which are not traffic rules.
_TEMPORARY_RESEARCH = re.compile(r"temporaire.*circulation", re.I)
_NATURAL_RULE = re.compile(r"(confér|confer|accord)\w*\s+la\s+naturalisation", re.I)
_XML = {"xml", "xml-akomantoso"}


def _no_longer(st: str | None) -> bool:
    return bool(st) and str(st).startswith("no-longer")


def research_numbers(snapshot: MetaSnapshot, t: dt.date = GOLDEN_DATE) -> dict[str, int]:
    """The research definitions (flag-based where the research was), all branches unless noted."""
    out: dict[str, int] = {}
    cons: dict[str, Mapping[str, Any]] = {}
    for r in snapshot.rows("consolidations"):
        cons.setdefault(r["c"], r)
    fmts: dict[str, set[str]] = defaultdict(set)
    for r in snapshot.rows("conso_formats"):
        if r["c"] in cons and r["uf"]:
            fmts[r["c"]].add(r["uf"])
    base: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in snapshot.rows("conso_base_acts"):
        base[r["cw"]].append(r)
    works: dict[str, list[str]] = defaultdict(list)
    for c, r in cons.items():
        works[r["cw"]].append(c)

    def window(c: str) -> bool:
        r = cons[c]
        return r["d"] <= t and (r["end"] is None or r["end"] > t)

    def repealed_base(cw: str) -> bool:
        b = base.get(cw)
        return bool(b) and all(_no_longer(x["ast"]) for x in b)

    w_set = {c for c in cons if window(c)}
    a_set = {c for c, r in cons.items() if r["st"] == "applicable"}
    out["window_current_works"] = len({cons[c]["cw"] for c in w_set})
    out["flag_applicable"] = len(a_set)
    out["flag_minus_window"] = len(a_set - w_set)
    out["window_minus_flag"] = len(w_set - a_set)
    out["open_ended_versions"] = sum(1 for r in cons.values() if r["end"] is None)

    def kind(c: str) -> str:
        f = fmts.get(c, set())
        if f & _XML:
            return "xml"
        if f and f <= {"pdf", "pdfa"}:
            return "pdf-only"
        return "none" if not f else "other"

    wk = Counter(kind(c) for c in w_set)
    out["window_xml"], out["window_pdf_only"], out["window_stubs"] = wk["xml"], wk["pdf-only"], wk["none"]
    latest_with_files: dict[str, str] = {}
    for cw, cs in works.items():
        cand = [c for c in cs if cons[c]["d"] <= t and fmts.get(c)]
        if cand:
            latest_with_files[cw] = max(cand, key=lambda c: cons[c]["d"])
    stubs = [c for c in w_set if not fmts.get(c)]
    out["stubs_with_fallback"] = sum(1 for c in stubs if cons[c]["cw"] in latest_with_files)
    out["stubs_without_fallback"] = len(stubs) - out["stubs_with_fallback"]
    out["repealed_base"] = sum(1 for c in w_set if repealed_base(cons[c]["cw"]))
    usable = [c for c in w_set if fmts.get(c) and not repealed_base(cons[c]["cw"])]
    uk = Counter(kind(c) for c in usable)
    out["usable"], out["usable_xml"], out["usable_pdf_only"] = len(usable), uk["xml"], uk["pdf-only"]
    out["usable_leg"] = sum(1 for c in usable if c.startswith(LEG_PREFIX))
    # C04 rule ("latest version with files and dA <= T") minus W, base not repealed (no base counts):
    # 50 = 10 works whose window version is a newer stub + 40 discontinued (38 with a live base act,
    # 2 recueils without base act).
    window_works = {cons[c]["cw"] for c in w_set}
    c04_minus_w = [c for c in set(latest_with_files.values()) - w_set if not repealed_base(cons[c]["cw"])]
    out["c04_minus_window_not_repealed"] = len(c04_minus_w)
    ended = [c for c in c04_minus_w if cons[c]["cw"] not in window_works]
    out["discontinued"] = len(ended)
    out["discontinued_base_live"] = sum(1 for c in ended if base.get(cons[c]["cw"]))
    future = [c for c in cons if cons[c]["d"] > t]
    out["future_versions"] = len(future)
    out["future_works"] = len({cons[c]["cw"] for c in future})
    out["future_with_files"] = sum(1 for c in future if fmts.get(c))

    # acts (Q01 grouped)
    acts: dict[str, dict[str, Any]] = {}
    for r in snapshot.rows("acts"):
        a = acts.setdefault(r["a"], {"cw": r["cw"], "type": r["type"], "st": r["st"], "dnl": r["dnl"]})
        a["dnl"] = a["dnl"] or r["dnl"]
    out["acts_distinct"] = len(acts)
    out["consolidations_distinct"] = len(cons)
    edges_mod: dict[str, list[tuple[str, dt.date]]] = defaultdict(list)
    edges_all: dict[str, list[tuple[str, dt.date]]] = defaultdict(list)
    for r in snapshot.rows("rel_modifies_repeals"):
        if r["dei"]:
            edges_all[r["tgt"]].append((r["src"], r["dei"]))
            if r["rel"] == "modifies":
                edges_mod[r["tgt"]].append((r["src"], r["dei"]))
    current = {cons[c]["cw"]: c for c in w_set}
    lagging = 0
    for cw, c in current.items():
        if not fmts.get(c) or repealed_base(cw):
            continue
        d = cons[c]["d"]
        if any(d < dei <= t for src, dei in edges_all.get(cw + "/jo", ())):
            lagging += 1
    out["lagging"] = lagging
    classes = _loi_rgd_classes(acts, edges_mod, current, fmts, cons, works, t)
    for (typ, key), n in Counter((acts[a]["type"].lower(), k) for a, k in classes.items()).items():
        out[f"{typ}_{key}"] = n
    titles = {r["a"]: r["title"] for r in snapshot.rows("titles")}
    in_force_rgd = [a for a, v in acts.items() if v["type"] == "RGD" and v["st"] == "in-force" and not v["dnl"]]
    out["temporary_traffic_rgd"] = sum(1 for a in in_force_rgd if _TEMPORARY_RESEARCH.search(titles.get(a) or ""))
    out["temporary_traffic_rgd_title_rule"] = sum(1 for a in in_force_rgd if _TEMPORARY.search(titles.get(a) or ""))
    based: dict[str, set[str]] = defaultdict(set)
    for r in snapshot.rows("rel_other"):
        if r["rel"] == "basedOn":
            based[r["src"]].add(r["tgt"])
    abrogee = in_force_base = 0
    for a, v in acts.items():
        if v["type"] != "RGD" or v["st"] != "in-force" or a not in based:
            continue
        national = [acts[b] for b in based[a] if b in acts]
        if not national:
            continue
        if all(_no_longer(b["st"]) for b in national):
            abrogee += 1
        else:
            in_force_base += 1
    out["base_legale_abrogee_rgd"] = abrogee
    out["rgd_with_national_base_in_force"] = in_force_base
    leg = {a: v for a, v in acts.items() if a.startswith(LEG_PREFIX)}
    st = Counter(v["st"] for v in leg.values())
    out["leg_acts"] = len(leg)
    out["leg_in_force"] = st["in-force"]
    out["leg_no_longer"] = st["no-longer-in-force"]
    out["leg_implicit"] = st["no-longer-in-force-implicit"]
    out["leg_not_yet"] = st["not-yet-in-force"]
    out["leg_rc"] = sum(1 for v in leg.values() if v["type"] == "RC")
    natural = [a for a, v in leg.items() if v["type"] == "LOI" and "naturalisation" in (titles.get(a) or "").lower()]
    out["leg_loi_naturalisation_titles"] = len(natural)
    out["leg_loi_naturalisation_rule"] = sum(1 for a in natural if _NATURAL_RULE.search(titles.get(a) or ""))
    out.update(licence_crosstab(snapshot, acts))
    # Q09 has no branch filter (3,369 of its 28,703 acts are /etat/adm/); Q19 / selection are leg-only.
    out.update({k.replace("licence_", "licence_leg_"): v for k, v in licence_crosstab(snapshot, leg).items()})
    return out


def _loi_rgd_classes(
    acts: Mapping[str, Mapping[str, Any]],
    edges_mod: Mapping[str, list[tuple[str, dt.date]]],
    current: Mapping[str, str],
    fmts: Mapping[str, set[str]],
    cons: Mapping[str, Mapping[str, Any]],
    works: Mapping[str, list[str]],
    t: dt.date,
) -> dict[str, str]:
    """``bin/acts.py``: in-force (flag) LOI/RGD in /etat/leg/ by amendment/consolidation situation."""
    classes: dict[str, str] = {}
    for a, v in acts.items():
        if v["st"] != "in-force" or v["type"] not in ("LOI", "RGD") or "/etat/leg/" not in a:
            continue
        mods = [dei for _src, dei in edges_mod.get(a, ()) if dei <= t]
        c = current.get(v["cw"])
        if not mods:
            key = "never_modified"
        elif c and fmts.get(c):
            key = "lags" if any(dei > cons[c]["d"] for dei in mods) else "up_to_date"
        elif c:
            key = "stub"
        else:
            key = "only_ended" if works.get(v["cw"]) else "never_consolidated"
        classes[a] = key
    return classes


def reconcile(snapshot: MetaSnapshot, plan_rows: Iterable[Mapping[str, Any]], t: dt.date) -> dict[str, int]:
    """Research LOI/RGD classes → selection outcome (``type.class -> label/reason``), to explain drift."""
    cons: dict[str, Mapping[str, Any]] = {}
    for r in snapshot.rows("consolidations"):
        cons.setdefault(r["c"], r)
    fmts: dict[str, set[str]] = defaultdict(set)
    for r in snapshot.rows("conso_formats"):
        if r["uf"]:
            fmts[r["c"]].add(r["uf"])
    works: dict[str, list[str]] = defaultdict(list)
    for c, r in cons.items():
        works[r["cw"]].append(c)
    current = {r["cw"]: c for c, r in cons.items() if r["d"] <= t and (r["end"] is None or r["end"] > t)}
    acts: dict[str, Mapping[str, Any]] = {}
    for r in snapshot.rows("acts"):
        acts.setdefault(r["a"], r)
    edges_mod: dict[str, list[tuple[str, dt.date]]] = defaultdict(list)
    for r in snapshot.rows("rel_modifies_repeals"):
        if r["dei"] and r["rel"] == "modifies":
            edges_mod[r["tgt"]].append((r["src"], r["dei"]))
    plan = {r["eli_jo"]: r for r in plan_rows if r.get("eli_jo")}
    out: Counter[str] = Counter()
    for a, key in _loi_rgd_classes(acts, edges_mod, current, fmts, cons, works, t).items():
        p = plan.get(a)
        outcome = "excluded (naturalisation/denylist)" if p is None else f"{p['text_basis']} [{p['reason']}]"
        out[f"{acts[a]['type'].lower()}.{key} -> {outcome}"] += 1
    return dict(sorted(out.items()))


def licence_crosstab(snapshot: MetaSnapshot, acts: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Q09 classes for in-force (flag) LOI/RGD/AGD/RMIN, recomputed from ``act_formats``."""
    per: dict[str, dict[str, set[str | None]]] = defaultdict(lambda: defaultdict(set))
    for r in snapshot.rows("act_formats"):
        a = acts.get(r["a"])
        if a is None or a["st"] != "in-force" or a["type"] not in ("LOI", "RGD", "AGD", "RMIN") or not r["uf"]:
            continue
        per[r["a"]][r["uf"]].add(r["lic"])
    c: Counter[str] = Counter()
    for f in per.values():
        cc = {uf for uf, lics in f.items() if CC_BY_4_0 in lics}
        if cc & _XML:
            c["licence_cc_by_xml"] += 1
        elif "html" in cc:
            c["licence_cc_by_html_only"] += 1
        elif set(f) == {"pdf"}:
            c["licence_cc_by_pdf_only" if "pdf" in cc else "licence_scl_pdf_only"] += 1
        else:
            c["licence_scl_text_only"] += 1
    return dict(c)


def selection_numbers(plan_rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Counts produced by the selection rules (from serving-plan rows)."""
    c: Counter[str] = Counter()
    for r in plan_rows:
        basis, flags, typ = r["text_basis"], set(r["flags"]), r["act_type"]
        c[f"text_basis.{basis}"] += 1
        if typ in ("LOI", "RGD"):
            c[f"{typ.lower()}.{basis}"] += 1
            if r.get("text_basis_without_licence"):
                c[f"{typ.lower()}.{r['text_basis_without_licence']}_but_licence"] += 1
        for f in flags:
            c[f"flag.{f}"] += 1
        if basis == TextBasis.CONSOLIDATED.value:
            c["consolidated"] += 1
        if basis == TextBasis.STUB_FALLBACK.value:
            c["stub_fallback"] += 1
        if ActFlag.STUB_NO_TEXT.value in flags and r["window_version"]:
            c["stub_no_text"] += 1
        if ActFlag.CONSOLIDATION_DISCONTINUED.value in flags:
            c["consolidation_discontinued"] += 1
        if r["reason"] == "2a:repealed_base":
            c["repealed_base"] += 1
        if ActFlag.CONSOLIDATION_LAGS.value in flags:
            c["consolidation_lags"] += 1
        if ActFlag.ACTE_TEMPORAIRE.value in flags and r["metadata_reason"] == "temporary":
            c["temporary_traffic"] += 1
        if ActFlag.BASE_LEGALE_ABROGEE.value in flags and typ == "RGD" and r["status_at_t"] == ActStatus.IN_FORCE.value:
            c["base_legale_abrogee_rgd"] += 1
        if ActFlag.IS_AMENDING_ACT.value in flags and r["status_at_t"] == ActStatus.IN_FORCE.value:
            c["is_amending_act_in_force"] += 1
        if r["future_version"]:
            c["future_stored"] += 1
        if r["window_version"]:
            c["window_current_works"] += 1
            if r["window_is_stub"]:
                c["window_stubs"] += 1
    return dict(sorted(c.items()))


def compare(actual: Mapping[str, int], expected: Mapping[str, int]) -> list[tuple[str, int, int | None, bool]]:
    """``(name, expected, actual, ok)`` rows for every expected number."""
    return [(k, v, actual.get(k), actual.get(k) == v) for k, v in expected.items()]
