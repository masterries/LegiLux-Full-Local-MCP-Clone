"""S0 robots: parse both robots.txt files every run, build the ELI denylist, pause on change.

Owner: agent A (M1). Contract: docs/dev/INTERFACES.md §3 (PLAN §3.2).

* Both files are fetched on every run (one polite request each) and stored under ``data/robots/<T>/``.
* A file whose sha256 is neither the reviewed baseline (:data:`REVIEWED_SHA256`) nor acknowledged in
  ``data/robots/ack.json`` pauses fetching (``ok = false``) until ``lgx robots-ack <sha256>``.
* If the main host ever disallows ``/filestore`` (any representative act file), fetching stops for good
  (hard stop, cannot be acknowledged); metadata syncs continue.
* Specific URIs (``Disallow: /eli/etat/leg/loi/2007/01/15/n2/jo`` …) act as a personal-data takedown
  list: they become ELI work prefixes in ``data/robots/<T>/denylist.json``, applied to SPARQL rows,
  relation targets and filestore paths (selection and :class:`RobotsPolicy`).

Matching follows RFC 9309: the group of the most specific matching user-agent (else ``*``), ``*``
wildcards, ``$`` end anchor, longest match wins, ``Allow`` wins a tie.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lgx_core.eli import ELI_BASE
from lgx_core.io import atomic_write_text
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.sparql import PoliteHttp, SparqlError, http_from_context

MAIN_HOST = "legilux.public.lu"
DATA_HOST = "data.legilux.public.lu"
ROBOTS = {
    MAIN_HOST: ("https://legilux.public.lu/robots.txt", "robots-legilux.txt"),
    DATA_HOST: ("https://data.legilux.public.lu/robots.txt", "robots-data.txt"),
}
# Reviewed with the plan on 2026-10-16 (PLAN §3.2; research robots_main.txt / robots_data.txt).
REVIEWED_SHA256 = {
    MAIN_HOST: "3c09eae06582b62bca4d06360b0f64eacbd6154aa81e830a2d264937445a1f5c",
    DATA_HOST: "768d80e1c6a3796663fe40bc7ac094b673daa39020d695df45c79ddeaf223447",
}
UA_TOKEN = "legilux-local-clone"
# Representative fetch targets; if any is disallowed on the main host the fetch stage stops for good.
FILESTORE_PROBES = (
    "/filestore/eli/etat/leg/",
    "/filestore/eli/etat/leg/loi/2016/12/23/n25/jo/fr/xml/eli-etat-leg-loi-2016-12-23-n25-jo-fr-xml.xml",
    "/filestore/eli/etat/leg/loi/2001/06/20/n5/jo/fr/html/eli-etat-leg-loi-2001-06-20-n5-jo-fr-html.html",
    "/filestore/eli/etat/leg/code/route/20260612/fr/pdf/eli-etat-leg-code-route-20260612-fr-pdf.pdf",
)
_VERSION_CUT = {"jo", "consolide", "fr", "de", "en", "lb", "graph"}


# ------------------------------------------------------------------------------------- parsing
@dataclass(frozen=True)
class Rule:
    allow: bool
    path: str

    @property
    def pattern(self) -> re.Pattern[str]:
        anchored = self.path.endswith("$")
        body = self.path[:-1] if anchored else self.path
        regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
        return re.compile("^" + regex + ("$" if anchored else ""))

    def matches(self, path: str) -> bool:
        return bool(self.path) and bool(self.pattern.match(path))

    @property
    def specificity(self) -> int:
        return len(self.path.rstrip("$"))


@dataclass
class Group:
    agents: list[str]
    rules: list[Rule] = field(default_factory=list)


@dataclass
class RobotsRules:
    groups: list[Group]
    sitemaps: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> RobotsRules:
        groups: list[Group] = []
        current: Group | None = None
        in_agents = False
        sitemaps: list[str] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not in_agents:
                    current = Group([])
                    groups.append(current)
                current.agents.append(value.lower())
                in_agents = True
            elif key in ("allow", "disallow"):
                in_agents = False
                if current is not None:
                    current.rules.append(Rule(key == "allow", value))
            elif key == "sitemap":
                sitemaps.append(value)
            else:
                in_agents = False
        return cls(groups, sitemaps)

    def rules_for(self, user_agent: str = UA_TOKEN) -> list[Rule]:
        """Rules of the group(s) naming our product token (case-insensitive); ``*`` otherwise (RFC 9309)."""
        ua = user_agent.lower()
        named = [g for g in self.groups if ua in g.agents]
        chosen = named or [g for g in self.groups if "*" in g.agents]
        return [r for g in chosen for r in g.rules]

    def allows(self, path: str, user_agent: str = UA_TOKEN) -> bool:
        best: Rule | None = None
        for rule in self.rules_for(user_agent):
            if rule.matches(path) and (
                best is None
                or rule.specificity > best.specificity
                or (rule.specificity == best.specificity and rule.allow)
            ):
                best = rule
        return best is None or best.allow

    def disallowed(self, user_agent: str = UA_TOKEN) -> list[str]:
        return [r.path for r in self.rules_for(user_agent) if not r.allow and r.path]


def eli_prefix(robots_path: str) -> str | None:
    """A specific ``/eli/etat/<branch>/…`` robots URI → ELI work prefix; ``None`` for generic rules.

    ``/eli/etat/leg/loi/2007/01/15/n2/jo/fr/html`` → ``…/eli/etat/leg/loi/2007/01/15/n2``;
    ``/eli/etat/leg/memorial/2007/8/fr/pdf`` → ``…/eli/etat/leg/memorial/2007/8``; ``/eli/etat/adm/`` and
    wildcard rules are branch-level (handled by robots matching), not takedowns.
    """
    path = robots_path.rstrip("$")
    if "*" in path or not path.startswith("/eli/etat/"):
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) < 5:  # eli / etat / branch / type / … : at least a type and one more segment
        return None
    kept: list[str] = []
    for i, seg in enumerate(parts[1:], start=1):
        if i >= 4 and seg in _VERSION_CUT:
            break
        kept.append(seg)
    return ELI_BASE + "/".join(kept)


def denylist_entries(rules: Mapping[str, RobotsRules], day: dt.date) -> list[dict[str, Any]]:
    """Robots-derived takedown entries (work prefixes) of both hosts, de-duplicated."""
    found: dict[str, dict[str, Any]] = {}
    for host, parsed in rules.items():
        for path in parsed.disallowed():
            prefix = eli_prefix(path)
            if prefix is None:
                continue
            entry = found.setdefault(
                prefix,
                {
                    "eli": prefix,
                    "reason": "robots.txt takedown",
                    "added": day.isoformat(),
                    "source": "robots",
                    "paths": [],
                },
            )
            entry["paths"].append(f"{host}{path}")
    return [found[k] for k in sorted(found)]


def filestore_allowed(rules: RobotsRules) -> bool:
    return all(rules.allows(p) for p in FILESTORE_PROBES)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------- ack + state
def ack_path(ctx: StageContext) -> Path:
    return ctx.layout.root / "robots" / "ack.json"


def read_acks(ctx: StageContext) -> dict[str, list[str]]:
    path = ack_path(ctx)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {host: list(v) for host, v in data.items()}


def acknowledged(ctx: StageContext, host: str, sha: str) -> bool:
    return sha == REVIEWED_SHA256.get(host) or sha in read_acks(ctx).get(host, [])


def evaluate(
    texts: Mapping[str, str], acks: Mapping[str, Iterable[str]], day: dt.date
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pure part of S0: robots.json state + denylist entries from the fetched texts."""
    rules = {host: RobotsRules.parse(text) for host, text in texts.items()}
    state: dict[str, Any] = {"hosts": {}}
    for host, text in texts.items():
        sha = sha256_text(text)
        ok = sha == REVIEWED_SHA256.get(host) or sha in set(acks.get(host, ()))
        state["hosts"][host] = {
            "sha256": sha,
            "bytes": len(text.encode("utf-8")),
            "acknowledged": ok,
            "reviewed_baseline": sha == REVIEWED_SHA256.get(host),
            "disallow": rules[host].disallowed(),
        }
    main = rules.get(MAIN_HOST)
    state["filestore_allowed"] = bool(main) and filestore_allowed(main)  # type: ignore[arg-type]
    state["hard_stop"] = not state["filestore_allowed"]
    state["paused"] = state["hard_stop"] or not all(h["acknowledged"] for h in state["hosts"].values())
    return state, denylist_entries(rules, day)


def fetch_robots(http: PoliteHttp) -> dict[str, str]:
    texts: dict[str, str] = {}
    for host, (url, _name) in ROBOTS.items():
        texts[host] = http.get(url).text
    return texts


def run_robots(ctx: StageContext, *, http: PoliteHttp | None = None) -> StageResult:
    """Fetch + parse robots.txt of both hosts, write data/robots/<T>/, fail if changed and unacknowledged."""
    result = StageResult(stage="robots")
    out = ctx.layout.robots_dir
    try:
        with http or http_from_context(ctx) as conn:
            texts = fetch_robots(conn)
    except SparqlError as exc:
        result.ok = False
        result.messages.append(f"robots.txt could not be fetched ({exc}): fetching stays paused")
        return result
    state, entries = evaluate(texts, read_acks(ctx), ctx.sync_date)
    fetched_at = dt.datetime.now(dt.UTC).isoformat()
    for host, (url, name) in ROBOTS.items():
        atomic_write_text(out / name, texts[host])
        state["hosts"][host] |= {"url": url, "file": name, "fetched_at": fetched_at}
        result.outputs.append(str(out / name))
    state["sync_date"] = ctx.sync_date.isoformat()
    atomic_write_text(out / "robots.json", json.dumps(state, indent=2) + "\n")
    denylist = {"sync_date": ctx.sync_date.isoformat(), "generated_at": fetched_at, "entries": entries}
    atomic_write_text(out / "denylist.json", json.dumps(denylist, indent=2) + "\n")
    result.outputs += [str(out / "robots.json"), str(out / "denylist.json")]
    result.counts = {"denylist_entries": len(entries), "hosts": len(texts)}
    result.details = {"state": {k: v for k, v in state.items() if k != "hosts"}} | {
        "hosts": {h: {k: v[k] for k in ("sha256", "acknowledged")} for h, v in state["hosts"].items()}
    }
    if state["hard_stop"]:
        result.ok = False
        result.messages.append("HARD STOP: legilux.public.lu robots.txt disallows /filestore — fetching stops for good")
    for host, h in state["hosts"].items():
        if not h["acknowledged"]:
            result.ok = False
            result.messages.append(
                f"robots.txt of {host} changed (sha256 {h['sha256']}): fetching paused until "
                f"`lgx robots-ack {h['sha256']}` after review"
            )
    return result


def acknowledge(ctx: StageContext, sha256: str) -> StageResult:
    """Operator acknowledgement of a changed robots.txt (``lgx robots-ack <sha256>``)."""
    result = StageResult(stage="robots-ack")
    state_path = ctx.layout.robots_dir / "robots.json"
    if not state_path.exists():
        result.ok = False
        result.messages.append(f"no robots state for {ctx.sync_date}: run `lgx robots` first")
        return result
    state = json.loads(state_path.read_text(encoding="utf-8"))
    hosts = [h for h, v in state["hosts"].items() if v["sha256"] == sha256.lower()]
    if not hosts:
        result.ok = False
        result.messages.append(f"{sha256} is not the sha256 of a robots.txt fetched for {ctx.sync_date}")
        return result
    if MAIN_HOST in hosts and state.get("hard_stop"):
        result.ok = False
        result.messages.append("this robots.txt disallows /filestore: it cannot be acknowledged (hard stop)")
        return result
    acks = read_acks(ctx)
    for host in hosts:
        acks.setdefault(host, [])
        if sha256.lower() not in acks[host]:
            acks[host].append(sha256.lower())
        state["hosts"][host]["acknowledged"] = True
    state["paused"] = state.get("hard_stop", False) or not all(v["acknowledged"] for v in state["hosts"].values())
    atomic_write_text(ack_path(ctx), json.dumps(acks, indent=2) + "\n")
    atomic_write_text(state_path, json.dumps(state, indent=2) + "\n")
    result.outputs = [str(ack_path(ctx)), str(state_path)]
    result.messages.append(f"acknowledged {sha256} for {', '.join(hosts)}")
    return result


# -------------------------------------------------------------------------------------- policy
def url_to_eli(url: str) -> str | None:
    """Filestore / SPA / identifier URL → ELI (``…/filestore/eli/etat/…`` → ``http://…/eli/etat/…``)."""
    if url.startswith(ELI_BASE):
        return url
    parts = urlsplit(url)
    path = parts.path
    for marker in ("/filestore/eli/", "/eli/"):
        if marker in path:
            return ELI_BASE + path.split(marker, 1)[1]
    return None


@dataclass
class RobotsPolicy:
    """The :class:`~lgx_ingest.context.UrlPolicy` of one run (robots rules + all denylists)."""

    rules: Mapping[str, RobotsRules]
    deny_prefixes: Sequence[str]
    paused: bool = False
    hard_stop: bool = False
    user_agent: str = UA_TOKEN

    def allows(self, url: str) -> bool:
        if self.paused or self.hard_stop:
            return False
        parts = urlsplit(url)
        rules = self.rules.get(parts.hostname or "")
        if rules is None:
            return False  # never fetch from a host whose robots.txt we did not read
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        return rules.allows(path, self.user_agent)

    def is_denied_eli(self, eli: str) -> bool:
        target = url_to_eli(eli) if not eli.startswith(ELI_BASE) else eli
        if target is None:
            return False
        target = target.rstrip("/")
        return any(target == p or target.startswith(p + "/") for p in self.deny_prefixes)


def load_policy(ctx: StageContext) -> RobotsPolicy:
    """The policy for this run (from data/robots/<T>/ and the denylist config); used by fetch."""
    out = ctx.layout.robots_dir
    state_path = out / "robots.json"
    if not state_path.exists():
        raise FileNotFoundError(f"no robots state for {ctx.sync_date} in {out}: run `lgx robots` first")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    rules = {host: RobotsRules.parse((out / name).read_text(encoding="utf-8")) for host, (_u, name) in ROBOTS.items()}
    deny_path = out / "denylist.json"
    generated = json.loads(deny_path.read_text(encoding="utf-8"))["entries"] if deny_path.exists() else []
    prefixes = {e["eli"].rstrip("/") for e in generated}
    for entry in ctx.config.denylist.entries:
        prefixes.add((entry.eli if entry.eli.startswith("http") else ELI_BASE + entry.eli.strip("/")).rstrip("/"))
    paused = bool(state.get("paused")) or not all(
        acknowledged(ctx, host, v["sha256"]) for host, v in state.get("hosts", {}).items()
    )
    return RobotsPolicy(rules, sorted(prefixes), paused=paused, hard_stop=bool(state.get("hard_stop")))
