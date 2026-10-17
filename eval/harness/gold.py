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
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
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
