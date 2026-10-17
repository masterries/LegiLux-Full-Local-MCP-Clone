"""Generate G1 (citation resolution, PLAN §11.1) from the release database.

Expectations come from database facts only (ELIs, article numbers, alias table, Mémorial references, the number
of acts sharing a (type, date)), never from the resolver under test. Rules applied, from PLAN §8.3 step 1:

* an article citation of an identified act resolves to the one non-annex article of that number in the version
  serving at T (an extra ``article_alias`` key, e.g. « art. 18 » inside « Art. 17 à 20. », resolves to that unit);
  several articles of that number (code-recueil members) → ``ambiguous``; none → ``not_found``;
* « <type> du <date> » without a title → ``resolved`` iff exactly one act of that type carries the date in its
  work ELI, else ``ambiguous`` (members listed) — never a guess;
* with a title hint taken from the act's own title → ``resolved`` (groups whose titles are too close to tell apart
  are not sampled);
* Mémorial A references resolve iff exactly one act carries them; ELIs (work, /jo, version, article, legilux://,
  https display URL) resolve to their act (and article);
* out-of-scope aliases (RGPD) → ``out_of_scope``.

Deterministic for a database + seed (md5 ordering). Every item is ``draft_unvalidated`` (a lawyer reviews 10 %).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row

ELI = "http://data.legilux.public.lu/eli/"
LEG = ELI + "etat/leg/"
MONTHS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
OF_TYPE = {  # « article 5 de la loi … », « … du règlement grand-ducal … », « … de l'arrêté grand-ducal … »
    "loi": "de la loi",
    "rgd": "du règlement grand-ducal",
    "agd": "de l'arrêté grand-ducal",
    "rmin": "du règlement ministériel",
    "amin": "de l'arrêté ministériel",
}
TYPE_WORDS = {
    "loi": ["loi", "Loi"],
    "rgd": ["règlement grand-ducal", "RGD"],
    "agd": ["arrêté grand-ducal", "AGD"],
    "rmin": ["règlement ministériel"],
    "amin": ["arrêté ministériel"],
}
# codes and the Constitution: (work ELI suffix, display name, abbreviation or None)
CODES = [
    ("loi/1804/03/21/n1", "Code civil", "C. civ."),
    ("loi/2006/07/31/n2", "Code du travail", "C. trav."),
    ("loi/1879/06/18/n1", "Code pénal", "C. pén."),
    ("rgd/1998/08/03/n4", "Nouveau Code de procédure civile", "NCPC"),
    ("constitution/1868/10/17/n1", "Constitution", "Const."),
]
STOP = {
    "de",
    "du",
    "des",
    "la",
    "le",
    "les",
    "l",
    "d",
    "et",
    "à",
    "a",
    "au",
    "aux",
    "en",
    "sur",
    "pour",
    "portant",
    "relative",
    "relatif",
    "concernant",
    "modifiée",
    "modifié",
    "un",
    "une",
}


def fr_date(d: dt.date) -> str:
    day = "1er" if d.day == 1 else str(d.day)
    return f"{day} {MONTHS[d.month - 1]} {d.year}"


def _h(seed: int, *parts: object) -> str:
    return hashlib.md5(("|".join(map(str, (seed, *parts)))).encode()).hexdigest()


def _pick(rows: Iterable[dict[str, Any]], n: int, seed: int, key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: _h(seed, r[key]))[:n]


def raw_num(num_raw: str) -> str:
    """'Art. L. 121-1.' -> 'L. 121-1'; 'Art. 1er.' -> '1er'."""
    s = re.sub(r"^\s*(?:art(?:icle)?s?\.?)\s*", "", num_raw.strip(), flags=re.IGNORECASE)
    return s.rstrip(". ").strip()


def title_hint(title: str, words: int = 7) -> str | None:
    """The part of an act title after its date (« portant organisation de la Commission nationale … »)."""
    m = re.search(r"\b\d{4}\b[,\s-]*(.+)$", title or "")
    rest = (m.group(1) if m else "").strip(" .,-")
    toks = rest.split()
    if len([t for t in toks if t.lower() not in STOP]) < 3:
        return None
    return " ".join(toks[:words]).rstrip(" ,;:")


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (title or "").lower()) if w not in STOP and len(w) > 2}


def eli_date(eli_work: str) -> tuple[str, dt.date] | None:
    m = re.match(re.escape(LEG) + r"([a-z_]+)/(\d{4})/(\d{2})/(\d{2})/", eli_work + "/")
    if not m:
        return None
    try:
        return m.group(1), dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
    except ValueError:
        return None


class _Gen:
    def __init__(self, conn: psycopg.Connection[dict[str, Any]], seed: int) -> None:
        self.conn = conn
        self.seed = seed
        self.items: list[dict[str, Any]] = []

    def q(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return list(self.conn.execute(sql, params).fetchall())

    def add(self, text: str, style: str, expect: dict[str, Any], **extra: Any) -> None:
        if any(i["text"] == text for i in self.items):
            return
        self.items.append(
            {"id": f"G1-{len(self.items) + 1:03d}", "text": text, "style": style, "expect": expect, **extra}
        )

    # ------------------------------------------------------------------------------------------- codes
    def article_units(self, act_suffix: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        act = self.q("SELECT act_id, eli_work, basis_version_id FROM lgx.act WHERE eli_work = %s", [LEG + act_suffix])[
            0
        ]
        units = self.q(
            "SELECT u.unit_id, u.num_raw, u.num_norm, u.eli_unit, u.is_repealed,"
            " (u.akn_id LIKE 'annex%%' OR lower(coalesce(u.breadcrumb, '')) LIKE '%%annexe%%') AS in_annex"
            " FROM lgx.unit u WHERE u.version_id = %s AND u.unit_type = 'article' AND u.num_norm IS NOT NULL",
            [act["basis_version_id"]],
        )
        return act, units

    def codes(self, per_code: int = 15) -> None:
        styles = ["art_du", "article_du", "abbrev", "l_article", "backward", "compact", "paragraph"]
        for suffix, name, abbr in CODES:
            act, units = self.article_units(suffix)
            by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for u in units:
                if not u["in_annex"]:
                    by_key[u["num_norm"]].append(u)
            unique = [u[0] for k, u in by_key.items() if len(u) == 1 and u[0]["eli_unit"] and not u[0]["is_repealed"]]
            de = "de la" if name == "Constitution" else "du"
            for i, u in enumerate(_pick(unique, per_code, self.seed, "eli_unit")):
                num = raw_num(u["num_raw"])
                expect = {"status": "resolved", "act_eli": act["eli_work"], "unit_eli": u["eli_unit"]}
                for style in (styles[i % len(styles)], styles[(i + 3) % len(styles)]):
                    if style == "art_du":
                        text = f"art. {num} {de} {name}"
                    elif style == "article_du":
                        text = f"article {num} {de} {name}"
                    elif style == "abbrev" and abbr:
                        text = f"art. {num} {abbr}"
                    elif style == "l_article":
                        text = f"l'article {num} {de} {name}"
                    elif style == "backward":
                        text = f"{name}, art. {num}"
                    elif style == "compact" and re.match(r"^[LR]\. \d", num):
                        text = f"art. {num.replace('. ', '.', 1)} {de} {name}"
                    elif style == "paragraph":
                        text = f"article {num}, paragraphe 2, {de} {name}"
                    else:
                        text = f"Art. {num} {abbr or name}"
                        style = "capital_art"
                    self.add(text, f"code:{style}", expect)
            # article 1er, bis/ter, range members (article_alias), non-existing numbers
            first = by_key.get("1")
            if first and len(first) == 1 and first[0]["eli_unit"]:
                self.add(
                    f"article 1er {de} {name}",
                    "code:1er",
                    {"status": "resolved", "act_eli": act["eli_work"], "unit_eli": first[0]["eli_unit"]},
                )
            bis = [
                u[0]
                for k, u in by_key.items()
                if len(u) == 1 and re.search(r"\d(bis|ter|quater)$", k) and u[0]["eli_unit"]
            ]
            for u in _pick(bis, 4, self.seed, "eli_unit"):
                num = raw_num(u["num_raw"])
                spaced = re.sub(r"(\d)\s*(bis|ter|quater)\b", r"\1 \2", num)
                glued = re.sub(r"(\d)\s+(bis|ter|quater)\b", r"\1\2", num)
                exp = {"status": "resolved", "act_eli": act["eli_work"], "unit_eli": u["eli_unit"]}
                self.add(f"art. {spaced} {de} {name}", "code:bis_spaced", exp)
                self.add(f"art. {glued} {abbr or name}", "code:bis_glued", exp)
            aliases = self.q(
                "SELECT aa.num_norm AS key, u.eli_unit, u.num_raw FROM lgx.article_alias aa"
                " JOIN lgx.unit u ON u.unit_id = aa.unit_id WHERE u.version_id = %s AND u.eli_unit IS NOT NULL",
                [act["basis_version_id"]],
            )
            members = [a for a in aliases if a["key"] not in by_key and re.fullmatch(r"\d+", a["key"])]
            for a in _pick(members, 3, self.seed, "key"):
                self.add(
                    f"art. {a['key']} {de} {name}",
                    "code:range_member",
                    {"status": "resolved", "act_eli": act["eli_work"], "unit_eli": a["eli_unit"]},
                )
            self.add(f"art. 98765 {de} {name}", "not_found:article", {"status": "not_found"})
            if abbr:
                self.add(f"art. 54321 {abbr}", "not_found:article", {"status": "not_found"})

    def code_recueil(self) -> None:
        act, units = self.article_units("code/fonction_publique")
        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for u in units:
            if not u["in_annex"]:
                by_key[u["num_norm"]].append(u)
        many = sorted(k for k, v in by_key.items() if len(v) > 1 and re.fullmatch(r"\d+", k))
        for k in sorted(many, key=lambda k: _h(self.seed, k))[:6]:
            num = "1er" if k == "1" else k
            self.add(
                f"article {num} du Code de la fonction publique",
                "code_recueil:member_number",
                {"status": "ambiguous", "act_eli": act["eli_work"]},
            )

    # ------------------------------------------------------------------------------------------- ELIs
    def elis(self) -> None:
        rows = self.q(
            "SELECT a.eli_work, a.eli_jo, v.eli_version, u.eli_unit, u.num_norm FROM lgx.act a"
            " JOIN lgx.act_version v ON v.version_id = a.basis_version_id"
            " LEFT JOIN LATERAL (SELECT eli_unit, num_norm FROM lgx.unit u WHERE u.version_id = v.version_id"
            "   AND u.unit_type = 'article' AND u.eli_unit IS NOT NULL AND NOT u.is_repealed"
            "   AND NOT u.eli_unit_derived ORDER BY md5(u.eli_unit || %s) LIMIT 1) u ON true",
            [str(self.seed)],
        )
        for r in _pick(rows, 14, self.seed, "eli_work"):
            act = r["eli_work"]
            self.add(act, "eli:work", {"status": "resolved", "act_eli": act})
            self.add(r["eli_version"], "eli:version", {"status": "resolved", "act_eli": act})
            disp = r["eli_version"].replace("http://data.legilux.public.lu/", "https://legilux.public.lu/")
            self.add(disp, "eli:display_url", {"status": "resolved", "act_eli": act})
            if r["eli_jo"]:
                self.add(r["eli_jo"], "eli:jo", {"status": "resolved", "act_eli": act})
            if r["eli_unit"]:
                exp = {"status": "resolved", "act_eli": act, "unit_eli": r["eli_unit"]}
                self.add(r["eli_unit"], "eli:article", exp)
                self.add(act.replace(ELI, "legilux://eli/") + "/art/" + r["num_norm"], "eli:resource_uri", exp)
        self.add(LEG + "loi/2099/01/01/a1", "not_found:eli", {"status": "not_found"})
        self.add(LEG + "rgd/1950/02/30/n1", "not_found:eli", {"status": "not_found"})

    # ------------------------------------------------------------------------------------------- dated acts
    def dated_with_text(self) -> None:
        rows = self.q(
            "SELECT a.act_id, a.eli_work, a.title, a.basis_version_id FROM lgx.act a"
            " WHERE a.basis_version_id IS NOT NULL"
            " AND a.doc_type NOT IN ('CODE', 'CODE_RECUEIL', 'RECUEIL', 'Constitution') ORDER BY a.eli_work"
        )
        for r in rows:
            ed = eli_date(r["eli_work"])
            if not ed or ed[0] not in TYPE_WORDS:
                continue
            seg, d = ed
            group = self.q("SELECT eli_work, title FROM lgx.act WHERE eli_work LIKE %s", [f"{LEG}{seg}/{d:%Y/%m/%d}/%"])
            hint = title_hint(r["title"])
            word = TYPE_WORDS[seg][0]
            unit = self.q(
                "SELECT num_raw, eli_unit FROM lgx.unit WHERE version_id = %s AND unit_type = 'article'"
                " AND eli_unit IS NOT NULL AND NOT is_repealed ORDER BY md5(eli_unit || %s) LIMIT 1",
                [r["basis_version_id"], str(self.seed)],
            )
            distinct = all(
                len(_words(g["title"]) & _words(r["title"])) <= 0.5 * len(_words(r["title"]))
                for g in group
                if g["eli_work"] != r["eli_work"]
            )
            if hint and distinct:
                act_exp = {"status": "resolved", "act_eli": r["eli_work"]}
                self.add(f"{word} du {fr_date(d)} {hint}", "dated:title", act_exp)
                if seg == "loi":
                    self.add(f"loi modifiée du {fr_date(d)} {hint}", "dated:modifiee_title", act_exp)
                if unit:
                    num = raw_num(unit[0]["num_raw"])
                    uexp = {**act_exp, "unit_eli": unit[0]["eli_unit"]}
                    self.add(f"article {num} {OF_TYPE[seg]} du {fr_date(d)} {hint}", "dated:article_title", uexp)
                    if len(TYPE_WORDS[seg]) > 1:
                        self.add(f"{TYPE_WORDS[seg][1]} du {fr_date(d)} {hint}, art. {num}", "dated:backward", uexp)
            if len(group) == 1:
                self.add(
                    f"{word} du {fr_date(d)}", "dated:unique_date", {"status": "resolved", "act_eli": r["eli_work"]}
                )
            elif len(group) > 1:
                self.add(
                    f"{word} du {fr_date(d)}",
                    "ambiguous:dated_text",
                    {"status": "ambiguous", "members": sorted(g["eli_work"] for g in group)[:20]},
                )

    def dated_metadata(self, n_unique: int = 35, n_ambiguous: int = 55, n_title: int = 25) -> None:
        rows = self.q(
            "SELECT a.eli_work, a.title, a.doc_type FROM lgx.act a WHERE a.doc_type IN ('LOI', 'RGD', 'AGD')"
            " AND a.eli_work ~ '/(loi|rgd|agd)/[0-9]{4}/[0-9]{2}/[0-9]{2}/'"
        )
        groups: dict[tuple[str, dt.date], list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            ed = eli_date(r["eli_work"])
            if ed:
                groups[ed].append(r)
        keys = sorted(groups, key=lambda k: _h(self.seed, k[0], k[1]))
        uniq = [k for k in keys if len(groups[k]) == 1]
        amb = [k for k in keys if 2 <= len(groups[k]) <= 20]
        for seg, d in uniq[:n_unique]:
            self.add(
                f"{TYPE_WORDS[seg][0]} du {fr_date(d)}",
                "dated:unique_date",
                {"status": "resolved", "act_eli": groups[(seg, d)][0]["eli_work"]},
            )
        for seg, d in amb[:n_ambiguous]:
            self.add(
                f"{TYPE_WORDS[seg][0]} du {fr_date(d)}",
                "ambiguous:dated",
                {"status": "ambiguous", "members": sorted(g["eli_work"] for g in groups[(seg, d)])},
            )
        taken = 0
        for seg, d in amb[n_ambiguous:]:
            if taken >= n_title:
                break
            g = groups[(seg, d)]
            target = sorted(g, key=lambda r: _h(self.seed, r["eli_work"]))[0]
            hint = title_hint(target["title"])
            others = [o for o in g if o is not target]
            if not hint or any(
                len(_words(o["title"]) & _words(target["title"])) > 0.4 * len(_words(target["title"])) for o in others
            ):
                continue
            self.add(
                f"{TYPE_WORDS[seg][0]} du {fr_date(d)} {hint}",
                "dated:title_among_same_date",
                {"status": "resolved", "act_eli": target["eli_work"]},
            )
            taken += 1
        # dates without any act of that type
        empty = 0
        d = dt.date(2017, 1, 3)
        while empty < 4 and d.year < 2019:
            if ("loi", d) not in groups:
                self.add(f"loi du {fr_date(d)}", "not_found:date", {"status": "not_found"})
                empty += 1
            d += dt.timedelta(days=37)

    # ------------------------------------------------------------------------------------------- Mémorial
    def memorial(self, n: int = 30) -> None:
        rows = self.q(
            "SELECT memorial_ref, array_agg(eli_work ORDER BY eli_work) AS acts FROM lgx.act"
            " WHERE memorial_ref ~ '^Mémorial A n° [0-9]+ de [0-9]{4}$' GROUP BY memorial_ref"
        )
        for r in _pick(rows, n, self.seed, "memorial_ref"):
            m = re.match(r"Mémorial A n° (\d+) de (\d{4})", r["memorial_ref"])
            assert m
            num, year = m.group(1), m.group(2)
            # the resolver also matches the A-number ELIs of that year (…/a686): count them like it does
            extra = self.q(
                "SELECT eli_work FROM lgx.act WHERE eli_work ~ %s AND extract(year FROM publication_date) = %s",
                [f"/a{num}$", int(year)],
            )
            acts = sorted({*r["acts"], *(e["eli_work"] for e in extra)})
            if len(acts) == 1:
                exp: dict[str, Any] = {"status": "resolved", "act_eli": acts[0]}
            else:
                exp = {"status": "ambiguous", "members": acts[:20]}
            style = "memorial:short" if int(num) % 2 else "memorial:long"
            text = f"Mém. A n° {num} de {year}" if style == "memorial:short" else f"Mémorial A n° {num} de {year}"
            self.add(text, style, exp)
        self.add("Mém. A n° 9876 de 2018", "not_found:memorial", {"status": "not_found"})

    # ------------------------------------------------------------------------------------------- aliases
    def aliases(self) -> None:
        rows = self.q(
            "SELECT al.alias_norm, al.kind, a.eli_work, count(*) OVER (PARTITION BY al.alias_norm) AS n"
            " FROM lgx.act_alias al JOIN lgx.act a USING (act_id) ORDER BY al.alias_norm"
        )
        for r in rows:
            if r["n"] != 1:
                continue
            surface = r["alias_norm"].upper() if r["kind"] == "acronym" else r["alias_norm"]
            self.add(surface, f"alias:{r['kind']}", {"status": "resolved", "act_eli": r["eli_work"]})
        for text in (
            "RGPD",
            "règlement général sur la protection des données",
            "règlement (UE) 2016/679",
            "directive (UE) 2019/1152",
        ):
            self.add(text, "out_of_scope:alias", {"status": "out_of_scope"})


def generate(dsn: str, *, seed: int = 20261016) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("SET statement_timeout = '120s'")
        release = conn.execute("SELECT release_id FROM lgx_meta.release").fetchone()
        g = _Gen(conn, seed)
        g.codes()
        g.code_recueil()
        g.elis()
        g.dated_with_text()
        g.dated_metadata()
        g.memorial()
        g.aliases()
    meta = {
        "release": release["release_id"] if release else None,
        "status": "draft_unvalidated",
        "generated": {"by": "eval/harness/gen_g1.py", "seed": seed, "date": dt.date.today().isoformat()},
        "note": "Citation resolution (PLAN §11.1 G1), auto-generated from database facts (see gen_g1.py for the"
        " expectation rules); lawyer review of a 10 % sample pending.",
    }
    return g.items, meta
