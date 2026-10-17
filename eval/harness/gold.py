"""Gold-set files (PLAN §11.1): ``eval/gold/<SET>/*.json``, each ``{"set", "release", "status", "note", "items"}``.

Every set built here is ``status: draft_unvalidated`` (lawyer review pending, PLAN §11.1 / §13 M2 "+ ≈ 4 ld").
Item shapes:

* **G0** smoke (shared with the Streamlit Eval tab, ``lgx_ui.evalg0``): ``{id, tool, arguments, expect_error?,
  checks: [{path, op, value}]}``.
* **G1** citation resolution: ``{id, text, style, as_of?, expect: {status, act_eli?, unit_eli?, members?}}``.
* **G2** retrieval: ``{id, lang, query, filters?, targets: [{eli, grade, act_eli, article, text_sha256}]}``.
* **G3** mandatory warnings: ``{id, trigger, tool, arguments, clock?, expect: {warnings, absent?, status?,
  status_path?, error_code?}}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GOLD_DIR = Path(__file__).resolve().parents[1] / "gold"
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"  # committed baselines (written only on purpose)
# default output of `run`: not committed (data/ is gitignored) — a plain `make eval` must never overwrite a baseline
RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports" / "eval"
SETS = ("G0", "G1", "G2", "G3")
REQUIRED = {
    "G0": ("id", "tool", "arguments", "checks"),
    "G1": ("id", "text", "style", "expect"),
    "G2": ("id", "lang", "query", "targets"),
    "G3": ("id", "trigger", "tool", "arguments", "expect"),
}


class GoldError(ValueError):
    """A malformed gold file."""


def set_files(name: str, gold_dir: Path = GOLD_DIR) -> list[Path]:
    d = gold_dir / name
    return sorted(d.glob("*.json")) if d.is_dir() else []


def load_set(name: str, gold_dir: Path = GOLD_DIR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(items, meta) of every file of the set, ids unique, required fields present."""
    if name not in SETS:
        raise GoldError(f"unknown gold set {name!r} (known: {', '.join(SETS)})")
    files = set_files(name, gold_dir)
    if not files:
        raise GoldError(f"no gold files under {gold_dir / name}")
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"files": [], "status": set()}
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
            raise GoldError(f"{f}: expected an object with an 'items' list")
        meta["files"].append(f.name)
        meta["status"].add(str(doc.get("status") or "draft_unvalidated"))
        meta.setdefault("release", doc.get("release"))
        for it in doc["items"]:
            missing = [k for k in REQUIRED[name] if k not in it]
            if missing:
                raise GoldError(f"{f}: item {it.get('id')!r} lacks {missing}")
            items.append(it)
    ids = [str(i["id"]) for i in items]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise GoldError(f"{name}: duplicate ids {dupes[:5]}")
    meta["status"] = sorted(meta["status"])
    return items, meta


def write_set(path: Path, name: str, items: list[dict[str, Any]], **meta: Any) -> None:
    doc = {"set": name, **meta, "items": items}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------ subsets
def subset_ids(spec: str, set_name: str) -> list[str]:
    """Item ids from a subset file (``{"set", "ids": [...]}``, see :func:`stratified_subset`) or ``"G2-001,G2-007"``."""
    path = Path(spec)
    if path.suffix == ".json" or path.exists():
        if not path.exists():
            raise GoldError(f"subset file not found: {path}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("set") not in (None, set_name):
            raise GoldError(f"{path} is a subset of {doc.get('set')}, not {set_name}")
        ids = [str(i) for i in doc.get("ids") or []]
    else:
        ids = [i.strip() for i in spec.split(",") if i.strip()]
    if not ids:
        raise GoldError(f"empty subset: {spec}")
    return ids


def select_ids(items: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(i["id"]): i for i in items}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise GoldError(f"subset ids not in the set: {unknown[:5]}")
    return [by_id[i] for i in ids]


def _stratum(it: dict[str, Any]) -> str:
    """G2: language × the act of the first target (codes and the Constitution are their own strata)."""
    lang = str(it.get("lang") or "?")
    targets = it.get("targets") or [{}]
    act = str(targets[0].get("act_eli") or targets[0].get("act") or "?").rsplit("/eli/etat/leg/", 1)[-1]
    return f"{lang}:{act}"


def stratified_subset(items: list[dict[str, Any]], size: int, set_name: str = "G2") -> dict[str, Any]:
    """A fixed subset: per language a share proportional to its items but at least 5 (DE/EN are few), and inside a
    language an even spread over the target acts (largest-remainder allocation, then every k-th item by id).
    Deterministic for a given gold file; it never looks at results, so it cannot favour a ranker."""
    import math
    from collections import defaultdict

    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in sorted(items, key=lambda i: str(i["id"])):
        by_lang[str(it.get("lang") or "?")].append(it)
    floor = {lang: min(5, len(v)) for lang, v in by_lang.items()}
    rest = size - sum(floor.values())
    big = {lang: len(v) - floor[lang] for lang, v in by_lang.items()}
    total = sum(big.values()) or 1
    quota = {lang: floor[lang] + math.floor(rest * big[lang] / total) for lang in by_lang}
    for lang in sorted(by_lang, key=lambda x: -(rest * big[x] / total) % 1)[: size - sum(quota.values())]:
        quota[lang] += 1
    chosen: list[str] = []
    strata: dict[str, int] = {}
    for lang, its in sorted(by_lang.items()):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for it in its:
            groups[_stratum(it)].append(it)
        n = min(quota[lang], len(its))
        share = {g: n * len(v) / len(its) for g, v in groups.items()}
        alloc = {g: math.floor(x) for g, x in share.items()}
        for g in sorted(groups, key=lambda g: (-(share[g] % 1), g))[: n - sum(alloc.values())]:
            alloc[g] += 1
        for g, v in sorted(groups.items()):
            k = alloc[g]
            if k:
                step = len(v) / k
                chosen += [str(v[int(i * step)]["id"]) for i in range(k)]
                strata[g] = k
    return {
        "set": set_name,
        "size": len(chosen),
        "method": "stratified: language (≥ 5 each) × first target act, every k-th item by id; results never used",
        "strata": dict(sorted(strata.items())),
        "ids": sorted(chosen),
    }
