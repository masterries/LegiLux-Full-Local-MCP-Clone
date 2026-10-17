"""Deterministic parser for French / Luxembourg legal citations (PLAN §8.3, port of the research prototype).

Recognised (on a case- and accent-folded copy of the text, :mod:`lgx_core.citeparse.fold`):

* article references: « art. L. 121-1 », « l'article 1134 », « articles 17 à 20 », « art. 5, paragraphe 2,
  alinéa 3, point 3° », « article 11(3) », « Art. 1er », « article premier », « article unique »;
* acts: ELIs (``http://data.legilux.public.lu/eli/…``, ``https://legilux.public.lu/eli/…``, filestore URLs,
  ``legilux://eli/…/art/{n}``, relative ``eli/etat/leg/…``), Mémorial references (« Mém. A n° 686 de 2018 »,
  « Mémorial A 2018, n° 686 », « Mém. A n° 686/2018 »), aliases (« C. trav. », « Code civil », « NCPC », CNPD …:
  the caller passes the alias list, default = the codes and :data:`DEFAULT_ALIASES`), dated acts (« loi
  (modifiée) du 1er août 2018 portant … », « RGD du 14 novembre 2013 », « L. 13 mai 2008 », « AGD du 14.02.1913 »)
  and typed titles without a date (« la loi relative au secteur financier »);
* attachment forward (« art. 5 de la loi du … », « art. L. 121-1 C. trav. ») and backward (« loi du 1er août
  2018, art. 5 », « RGD du 14 novembre 2013, art. 3, alinéa 2 », « Code civil, art. 1134 »).

The parser never resolves anything: it returns :class:`~lgx_core.citeparse.model.Citation` records; the MCP
resolver turns them into ELIs and reports ambiguity instead of guessing.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable

from lgx_core.citeparse.fold import Folded, fold
from lgx_core.citeparse.model import ActMention, ArticleMention, Citation, OutOfScopeMention, QueryAnalysis
from lgx_core.eli import ELI_BASE
from lgx_core.normalise import LATIN_SUFFIXES, alias_norm, num_norm

MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

# folded surface -> (resource-type code, ELI path segment). Longest surfaces first in the regex.
TYPE_SURFACES: dict[str, tuple[str, str]] = {
    "loi": ("LOI", "loi"),
    "l.": ("LOI", "loi"),  # provenance notes « ( L. 13 mai 2008 ) »; only matched directly before a date
    "reglement grand-ducal": ("RGD", "rgd"),
    "reglement grand ducal": ("RGD", "rgd"),
    "rgd": ("RGD", "rgd"),
    "r.g.d.": ("RGD", "rgd"),
    "r.g.-d.": ("RGD", "rgd"),
    "arrete grand-ducal": ("AGD", "agd"),
    "arrete grand ducal": ("AGD", "agd"),
    "agd": ("AGD", "agd"),
    "a.g.d.": ("AGD", "agd"),
    "a.g.-d.": ("AGD", "agd"),
    "arrete ministeriel": ("AMIN", "amin"),
    "a.m.": ("AMIN", "amin"),
    "amin": ("AMIN", "amin"),
    "reglement ministeriel": ("RMIN", "rmin"),
    "r.m.": ("RMIN", "rmin"),
    "rmin": ("RMIN", "rmin"),
    "arrete royal grand-ducal": ("ARGD", "argd"),
    "arrete royal grand ducal": ("ARGD", "argd"),
    "arrete royal": ("AR", "ar"),
    "reglement du gouvernement en conseil": ("RGC", "rgc"),
    "arrete du gouvernement en conseil": ("AGC", "agc"),
    "arrete": ("A", "a"),
    "decret": ("D", "d"),
    "ordonnance": ("ORD", "ord"),
    "convention": ("CONV", "conv"),
    "constitution": ("Constitution", "constitution"),
}
# Types that may introduce a typed title without a date (« la loi relative au … »).
_TITLE_TYPES = ("loi", "reglement grand-ducal", "reglement grand ducal", "arrete grand-ducal", "rgd", "agd")

# Codes and the Constitution: always recognised (surface forms as written; folded at compile time).
DEFAULT_ALIASES: tuple[str, ...] = (
    "C. trav.",
    "Code du travail",
    "C. civ.",
    "Code civil",
    "C. pén.",
    "Code pénal",
    "C. com.",
    "Code de commerce",
    "C. proc. pén.",
    "Code de procédure pénale",
    "Code d'instruction criminelle",
    "CPP",
    "NCPC",
    "N.C.P.C.",
    "Nouveau Code de procédure civile",
    "Code de procédure civile",
    "C. consom.",
    "Code de la consommation",
    "CSS",
    "C.S.S.",
    "Code de la sécurité sociale",
    "C. route",
    "Code de la route",
    "Code de la fonction publique",
    "Code de la santé",
    "Code de l'environnement",
    "Code communal",
    "Code de l'éducation nationale",
    "Constitution",
    "Const.",
)

_SUFFIX = r"(?:\s?(?:" + "|".join(sorted(LATIN_SUFFIXES, key=len, reverse=True)) + r")\b)"
# Article number (folded text): optional L./R./D./A. prefix, digits, 1er, -n / .n parts, latin suffix.
_NUM = r"(?:(?:[lrda])\s?\.?\s?)?\d+(?:\s?(?:er|ere|re)\b)?(?:[-.]\d+)*" + _SUFFIX + r"?(?:-\d+)*" + r"|premier|unique"
_ART_WORD = r"(?:\bl'\s?)?\b(?:articles?|arts?\.?)"
_SUBPART = (
    r"(?:\s*,?\s*(?:"
    r"(?:paragraphes?|par\.|§)\s*(?P<par>\d+(?:\s?er)?|premier)"
    r"|(?:alineas?|al\.)\s*(?P<al>\d+(?:\s?er)?|premier|dernier)"
    r"|(?:points?|pt\.?|lettre|sous)\s*(?P<pt>\d+\s?°|[a-z]\)|\d+\)|[a-z]\b)"
    r"|\((?P<parn>\d+)\)"
    r"))"
)
_ART_RE = re.compile(
    _ART_WORD + r"\s*(?P<num>" + _NUM + r")"
    r"(?:\s*(?P<conn>(?:a|au|et)\b|,)\s*(?:l'\s?)?(?:articles?\s+|arts?\.?\s*)?(?P<num2>" + _NUM + r"))?"
)
# Code-style numbers without the « art. » word: « L. 121-1 du Code du travail ».
_BARE_CODE_ART_RE = re.compile(r"(?<![\w.])(?P<num>[lr]\.?\s?\d+-\d+(?:-\d+)*" + _SUFFIX + r"?)(?![\w-])")
_SUB_RE = re.compile(_SUBPART)

_DAY = r"(?P<d>1er|1re|premier|\d{1,2})"
_MONTH = r"(?P<m>" + "|".join(MONTHS) + r")"
_DATE_WORDS = _DAY + r"\s+" + _MONTH + r"\s+(?P<y>\d{4})"
_DATE_NUM = r"(?P<nd>\d{1,2})[./](?P<nm>\d{1,2})[./](?P<ny>\d{4})"
_TYPE_ALT = "|".join(re.escape(s) for s in sorted(TYPE_SURFACES, key=len, reverse=True))
_MODIFIED = r"(?:\s+(?P<mod>modifiee?s?|modifie|coordonnee?|amendee?)(?:\s+et\s+completee?)?)?"
_DATED_RE = re.compile(
    r"(?<![\w.])(?P<type>"
    + _TYPE_ALT
    + r")"
    + _MODIFIED
    + r"\s+(?:du\s+|des\s+)?(?:"
    + _DATE_WORDS
    + r"|"
    + _DATE_NUM
    + r")\b"
)
_TITLE_START = (
    r"(?:relative?s?|portant|concernant|sur|instituant|organisant|ayant pour objet|fixant|determinant|"
    r"modifiant|transposant|precisant|reglant|creant|etablissant|approuvant|autorisant)"
)
_TYPE_TITLE_RE = re.compile(
    r"(?<![\w.])(?P<type>"
    + "|".join(re.escape(s) for s in _TITLE_TYPES)
    + r")"
    + _MODIFIED
    + r"\s+(?P<hint>"
    + _TITLE_START
    + r"\b[^;\n]{4,160})"
)
_HINT_STOP = re.compile(
    r"(?:,?\s*\b(?:l'\s?)?(?:articles?|arts?)\b\.?\s*\d)|[;\n]|\s+\(|,\s*(?:et|ou)\s|\?|!|\.(?=\s+[a-z]{3})"
)
_MEMORIAL_RE = re.compile(
    r"\bmem(?:orial)?\.?\s*(?P<s>[ab])\b\s*[-,]?\s*"
    r"(?:(?P<y1>\d{4})\s*,?\s*)?"
    r"(?:(?:n\s?°|no\.?|numero|nr\.?)\s*)?(?P<num>\d{1,4})\b"
    r"(?:\s*(?:/\s*(?P<y2>\d{4})|(?:du|de|en)\s+(?:(?:1er|\d{1,2})\s+(?:" + "|".join(MONTHS) + r")\s+)?(?P<y3>\d{4})))?"
)
_ELI_RE = re.compile(
    r"(?:https?://(?:data\.)?legilux\.public\.lu/(?:filestore/)?|legilux://|(?<![\w/]))eli/etat/(?P<branch>leg|adm)/"
    r"(?P<path>[A-Za-z0-9_\-./]+)"
)
_EU_ELI_RE = re.compile(r"(?:https?://)?(?:data\.europa\.eu/eli|eur-lex\.europa\.eu)/\S+", re.IGNORECASE)

_OOS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "eu_law",
        re.compile(
            r"\b(?:reglement|directive|decision)s?\s*(?:d'execution\s*|delegue\s*)?(?:\((?:ue|ce|cee|euratom)\)|ue|ce|cee)"
            r"\s*(?:n\s?°\s*)?\d{2,4}/\d+"
            r"|\b(?:rgpd|gdpr|dsgvo|tfue|traite sur le fonctionnement de l'union europeenne)\b"
            r"|\bdirective\s+(?:n\s?°\s*)?\d{2,4}/\d+"
        ),
    ),
    (
        "case_law",
        re.compile(
            r"\b(?:jurisprudences?|arrets? de la cour|cour de cassation|cour d'appel|cour constitutionnelle|"
            r"tribunal administratif|cour administrative|tribunal d'arrondissement|cjue|cour de justice de l'union)\b"
        ),
    ),
    ("communal", re.compile(r"\breglements? (?:communa(?:l|ux)|de police communal)|\breglement-taxe\b")),
    ("memorial_b", re.compile(r"\bmem(?:orial)?\.?\s*b\b")),
    ("bill", re.compile(r"\b(?:projets? de loi|propositions? de loi|doc(?:ument)?s?\.? parl(?:ementaires?)?\.?)\b")),
)

_EXACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![\w/])\d{1,4}/\d{1,4}(?:/[a-z]{2,4})?(?![\w/])"),  # 2016/679, 2014/65/ue
    re.compile(r"(?<![\w.])[lr]\.?\s?\d+-\d+(?:-\d+)*(?![\w-])"),  # L. 121-1
    re.compile(r"(?<![\w.-])\d+(?:-\d+)+(?![\w-])"),  # 124-10, 1007-29
    re.compile(r"(?<![\w.])\d+" + _SUFFIX + r"(?![\w])"),  # 12bis
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GAP_FORWARD = re.compile(
    r"^(?:[\s.,;:()\-«»\"']|l'|d'|\b(?:du|de|des|la|le|les|dans|en|au|aux|precitee?s?|susvisee?s?|suivant)\b)*$"
)
_GAP_BACKWARD = re.compile(r"^[\s,;:()\-]*$")
_GAP_LIST = re.compile(r"^\s*(?:,|et|ou|ainsi que)?\s*$")


def _day(d: str) -> int:
    return 1 if d in ("1er", "1re", "premier") else int(d)


def _alias_pattern(surface_norm: str) -> str:
    """Regex for one folded alias surface: dots may be followed by optional spaces; a final dot is optional."""
    out: list[str] = []
    prev = ""
    for ch in surface_norm:
        if ch == ".":
            out.append(r"\.\s*")
        elif ch == " ":
            if prev not in (".", "'"):  # « c. civ. » also matches « C.civ. »
                out.append(r"\s+")
        elif ch == "'":
            out.append(r"'\s*")
        else:
            out.append(re.escape(ch))
        prev = ch
    pattern = "".join(out)
    if pattern.endswith(r"\.\s*"):
        pattern = pattern[: -len(r"\.\s*")] + r"\.?"
    return pattern


def _clean_raw_num(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw.rstrip(".")


class CitationParser:
    """Compiled parser. ``aliases`` = surface forms (any case/accents) recognised as act references."""

    def __init__(self, aliases: Iterable[str] = ()) -> None:
        norms = {alias_norm(a).strip() for a in (*DEFAULT_ALIASES, *aliases) if a and a.strip()}
        norms = {n for n in norms if n}
        self.alias_norms: frozenset[str] = frozenset(norms)
        # canonical key: alias_norm without trailing dots/spaces variations (« c. trav » == « c. trav. »)
        self._canon = {self._canon_key(n): n for n in sorted(norms)}
        alternatives = sorted(norms, key=len, reverse=True)
        self._alias_re = (
            re.compile(r"(?<![\w.])(?:" + "|".join(_alias_pattern(a) for a in alternatives) + r")(?![\w])")
            if alternatives
            else None
        )

    @staticmethod
    def _canon_key(s: str) -> str:
        return re.sub(r"[\s.]+", "", s)

    # ------------------------------------------------------------------------------------------ mentions
    def _articles(self, f: Folded) -> list[ArticleMention]:
        out: list[ArticleMention] = []
        taken: list[tuple[int, int]] = []
        for m in _ART_RE.finditer(f.text):
            end = m.end()
            par = al = pt = None
            pos = end
            while True:
                sm = _SUB_RE.match(f.text, pos)
                if not sm or sm.end() == pos:
                    break
                par = par or sm.group("par") or sm.group("parn")
                al = al or sm.group("al")
                pt = pt or sm.group("pt")
                pos = sm.end()
            raw = _clean_raw_num(f.cut(m.start("num"), m.end("num")))
            raw2 = _clean_raw_num(f.cut(m.start("num2"), m.end("num2"))) if m.group("num2") else None
            conn = m.group("conn")
            connector = None
            if raw2:
                connector = "range" if conn in ("a", "au") else "list"
            out.append(
                ArticleMention(
                    span=f.span(m.start(), pos),
                    raw=raw,
                    key=self._key(raw),
                    to_raw=raw2,
                    to_key=self._key(raw2) if raw2 else None,
                    connector=connector,
                    paragraph=self._ordinal(par),
                    alinea=self._ordinal(al),
                    point=pt.replace(" ", "") if pt else None,
                )
            )
            taken.append((m.start(), pos))
        for m in _BARE_CODE_ART_RE.finditer(f.text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            raw = _clean_raw_num(f.cut(m.start("num"), m.end("num")))
            out.append(ArticleMention(span=f.span(m.start(), m.end()), raw=raw, key=self._key(raw)))
        out.sort(key=lambda a: a.span)
        return out

    @staticmethod
    def _key(raw: str) -> str:
        r = raw.strip().lower()
        if r in ("premier", "1er", "1re", "1ere"):
            return "1"
        return num_norm(raw)

    @staticmethod
    def _ordinal(v: str | None) -> str | None:
        if v is None:
            return None
        v = v.replace(" ", "")
        return "1" if v in ("1er", "premier") else v

    def _acts(self, f: Folded) -> tuple[list[ActMention], list[OutOfScopeMention]]:
        acts: list[ActMention] = []
        oos: list[OutOfScopeMention] = []
        occupied: list[tuple[int, int]] = []  # folded spans already used by a more specific mention

        def free(a: int, b: int) -> bool:
            return not any(a < y and x < b for x, y in occupied)

        # ELIs (on the original text: identifiers are case-sensitive)
        for m in _ELI_RE.finditer(f.original):
            path = m.group("path").rstrip(".:/")
            branch = m.group("branch")
            act = self._eli_mention(branch, path, (m.start(), m.start() + len(m.group(0).rstrip(".:"))))
            if act.out_of_scope:
                oos.append(OutOfScopeMention(kind="adm_branch", span=act.span, surface=act.surface))
            acts.append(act)
            occupied.append(self._to_folded(f, act.span))
        for m in _EU_ELI_RE.finditer(f.original):
            oos.append(OutOfScopeMention(kind="eu_law", span=(m.start(), m.end()), surface=m.group(0)))
            occupied.append(self._to_folded(f, (m.start(), m.end())))

        # Mémorial references
        for m in _MEMORIAL_RE.finditer(f.text):
            if not free(m.start(), m.end()):
                continue
            series = m.group("s").upper()
            year_s = m.group("y1") or m.group("y2") or m.group("y3")
            span = f.span(m.start(), m.end())
            act = ActMention(
                kind="memorial",
                span=span,
                surface=f.cut(m.start(), m.end()),
                memorial_series=series,
                memorial_number=int(m.group("num")),
                memorial_year=int(year_s) if year_s else None,
                out_of_scope="Mémorial B (not in scope)" if series == "B" else None,
            )
            acts.append(act)
            occupied.append((m.start(), m.end()))

        # dated acts
        for m in _DATED_RE.finditer(f.text):
            if not free(m.start(), m.end()):
                continue
            t = m.group("type")
            doc_type, segment = TYPE_SURFACES[t]
            try:
                if m.group("y"):
                    date = dt.date(int(m.group("y")), MONTHS[m.group("m")], _day(m.group("d")))
                else:
                    date = dt.date(int(m.group("ny")), int(m.group("nm")), int(m.group("nd")))
            except ValueError:
                continue
            hint_start = m.end()
            hint_end = self._hint_end(f.text, hint_start)
            hint = f.cut(hint_start, hint_end).strip(" ,.:-()") if hint_end > hint_start else ""
            hint = re.sub(r"\s+", " ", hint)
            span_end = hint_end if hint else m.end()
            inner = tuple(self._aliases_in(f.text[hint_start:hint_end]))
            acts.append(
                ActMention(
                    kind="dated",
                    span=f.span(m.start(), span_end),
                    surface=f.cut(m.start(), m.end()),
                    doc_type=doc_type,
                    eli_segment=segment,
                    date=date,
                    title_hint=hint or None,
                    modified=bool(m.group("mod")),
                    inner_aliases=inner,
                )
            )
            occupied.append((m.start(), span_end))

        # aliases (codes, acronyms, curated short titles)
        if self._alias_re is not None:
            for m in self._alias_re.finditer(f.text):
                if not free(m.start(), m.end()):
                    continue
                norm = self._norm_of(m.group(0))
                acts.append(
                    ActMention(
                        kind="alias",
                        span=f.span(m.start(), m.end()),
                        surface=f.cut(m.start(), m.end()),
                        alias_norm=norm,
                    )
                )
                occupied.append((m.start(), m.end()))

        # typed titles without a date (« la loi relative au secteur financier »)
        for m in _TYPE_TITLE_RE.finditer(f.text):
            if not free(m.start(), m.end()):
                continue
            hint_start = m.start("hint")
            hint_end = self._hint_end(f.text, hint_start)
            hint = re.sub(r"\s+", " ", f.cut(hint_start, hint_end)).strip(" ,.:-()")
            if len(hint) < 8:
                continue
            doc_type, segment = TYPE_SURFACES[m.group("type")]
            acts.append(
                ActMention(
                    kind="type_title",
                    span=f.span(m.start(), hint_end),
                    surface=f.cut(m.start(), hint_end),
                    doc_type=doc_type,
                    eli_segment=segment,
                    title_hint=hint,
                    modified=bool(m.group("mod")),
                    inner_aliases=tuple(self._aliases_in(f.text[hint_start:hint_end])),
                )
            )
            occupied.append((m.start(), hint_end))

        # out-of-scope vocabulary (EU law, case law, communal, Mémorial B, bills)
        for kind, pattern in _OOS_PATTERNS:
            for m in pattern.finditer(f.text):
                span = f.span(m.start(), m.end())
                if any(o.span == span for o in oos):
                    continue
                oos.append(OutOfScopeMention(kind=kind, span=span, surface=f.cut(m.start(), m.end())))  # type: ignore[arg-type]
        acts.sort(key=lambda a: a.span)
        oos.sort(key=lambda o: o.span)
        return acts, oos

    @staticmethod
    def _to_folded(f: Folded, span: tuple[int, int]) -> tuple[int, int]:
        a, b = span
        fa = next((i for i, o in enumerate(f.index) if o >= a), len(f.index))
        fb = next((i for i, o in enumerate(f.index) if o >= b), len(f.index))
        return fa, fb

    @staticmethod
    def _hint_end(text: str, start: int) -> int:
        limit = min(len(text), start + 200)
        stop = _HINT_STOP.search(text, start, limit)
        end = stop.start() if stop else limit
        # do not cut inside a word at the 200-char limit
        if not stop and end < len(text):
            sp = text.rfind(" ", start, end)
            end = sp if sp > start else end
        return end

    def _aliases_in(self, text: str) -> list[str]:
        if self._alias_re is None:
            return []
        return [self._norm_of(m.group(0)) for m in self._alias_re.finditer(text)]

    def _norm_of(self, surface: str) -> str:
        return self._canon.get(self._canon_key(surface), alias_norm(surface))

    @staticmethod
    def _eli_mention(branch: str, path: str, span: tuple[int, int]) -> ActMention:
        article = None
        m = re.search(r"/art/(?P<n>[^/]+)$", path)
        if m:
            article = m.group("n")
            path = path[: m.start()]
        # filestore manifestation tails: …/jo/fr/html/eli-….html, …/consolide/20251226/fr/xml/…
        path = re.split(r"/(?:fr|de|en)/(?:html|xml|pdf|docx?)\b", path)[0].rstrip("/")
        eli = f"{ELI_BASE}etat/{branch}/{path}"
        return ActMention(
            kind="eli",
            span=span,
            surface=eli,
            eli=eli,
            eli_article=article,
            out_of_scope="administrative branch (etat/adm) is not in scope" if branch == "adm" else None,
        )

    # -------------------------------------------------------------------------------------------- public
    def parse(self, text: str) -> list[Citation]:
        """All citations in ``text``, in text order: articles with their act, then free-standing acts."""
        f = fold(text)
        articles = self._articles(f)
        acts, _ = self._acts(f)
        # an article number inside an act mention (« … art. 5 » inside a title hint is cut, ELI tails) is dropped
        articles = [a for a in articles if not any(x.span[0] <= a.span[0] and a.span[1] <= x.span[1] for x in acts)]
        attached: dict[int, ActMention] = {}
        used: set[int] = set()
        mention_spans = sorted([a.span for a in articles] + [x.span for x in acts])

        def gap_clean(a: int, b: int) -> bool:
            return not any(a <= s < b or a < e <= b for s, e in mention_spans if (s, e) != (a, b))

        for i, art in enumerate(articles):
            best: tuple[int, ActMention] | None = None
            for j, act in enumerate(acts):
                if act.span[0] >= art.span[1]:
                    gap = text[art.span[1] : act.span[0]]
                    if len(gap) <= 90 and _GAP_FORWARD.match(fold(gap).text) and gap_clean(art.span[1], act.span[0]):
                        best = (j, act)
                    break
            if best is None:
                prev = [(j, act) for j, act in enumerate(acts) if act.span[1] <= art.span[0]]
                if prev:
                    j, act = prev[-1]
                    gap = text[act.span[1] : art.span[0]]
                    if len(gap) <= 40 and _GAP_BACKWARD.match(gap) and gap_clean(act.span[1], art.span[0]):
                        best = (j, act)
            if best is not None:
                attached[i] = best[1]
                used.add(best[0])
        # « art. 5 et art. 6 de la loi … »: an unattached article inherits the act of the next one
        for i in range(len(articles) - 2, -1, -1):
            if i not in attached and (i + 1) in attached:
                gap = text[articles[i].span[1] : articles[i + 1].span[0]]
                if _GAP_LIST.match(fold(gap).text):
                    attached[i] = attached[i + 1]
        out: list[Citation] = []
        for i, art in enumerate(articles):
            act = attached.get(i)
            start = min(art.span[0], act.span[0]) if act else art.span[0]
            end = max(art.span[1], act.span[1]) if act else art.span[1]
            out.append(Citation(span=(start, end), text=text[start:end], article=art, act=act))
        for j, act in enumerate(acts):
            if j not in used:
                if act.kind == "eli" and act.eli_article:
                    art = ArticleMention(span=act.span, raw=act.eli_article, key=self._key(act.eli_article))
                    out.append(Citation(span=act.span, text=text[act.span[0] : act.span[1]], article=art, act=act))
                else:
                    out.append(Citation(span=act.span, text=text[act.span[0] : act.span[1]], act=act))
        out.sort(key=lambda c: c.span)
        return out

    def parse_one(self, text: str) -> Citation | None:
        """The best single citation: the first with both an article and an act, else the first act, else any."""
        cits = self.parse(text)
        for c in cits:
            if c.article and c.act:
                return c
        for c in cits:
            if c.act:
                return c
        return cits[0] if cits else None

    def out_of_scope(self, text: str) -> list[OutOfScopeMention]:
        return self._acts(fold(text))[1]

    def analyse(self, text: str) -> QueryAnalysis:
        """Citations, identifier tokens for the exact branch and out-of-scope mentions of a search query."""
        return QueryAnalysis(
            citations=self.parse(text), exact_tokens=exact_tokens(text), out_of_scope=self.out_of_scope(text)
        )

    def with_aliases(self, aliases: Iterable[str]) -> CitationParser:
        return CitationParser((*self.alias_norms, *aliases))


def exact_tokens(text: str, limit: int = 8) -> list[str]:
    """Identifier-like tokens (« 2016/679 », « L. 121-1 », « 124-10 », « 12bis ») for the ``tsv_exact`` branch."""
    f = fold(text)
    spans: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    for pattern in _EXACT_PATTERNS:
        for m in pattern.finditer(f.text):
            if any(a < m.end() and m.start() < b for a, b in spans):
                continue
            tok = m.group(0)
            if _ISO_DATE.match(tok):
                continue
            # « L.121-1 » / « l 121-1 » -> « l. 121-1 » (tokenised by simple_ua as 'l' '121' '-1')
            tok = re.sub(r"^([lr])\.?\s?(\d)", r"\1. \2", tok)
            spans.append((m.start(), m.end()))
            found.append((m.start(), tok))
    found.sort()
    out: list[str] = []
    for _, tok in found:
        if tok not in out:
            out.append(tok)
    return out[:limit]


_DEFAULT_PARSER: CitationParser | None = None


def default_parser() -> CitationParser:
    global _DEFAULT_PARSER
    if _DEFAULT_PARSER is None:
        _DEFAULT_PARSER = CitationParser()
    return _DEFAULT_PARSER


def parse(text: str, aliases: Iterable[str] = ()) -> list[Citation]:
    """Module-level convenience: :meth:`CitationParser.parse` with the default aliases (+ ``aliases``)."""
    aliases = tuple(aliases)
    parser = CitationParser(aliases) if aliases else default_parser()
    return parser.parse(text)
