"""Metrics of PLAN §11.2, as pure functions (unit-tested in ``tests/unit/test_eval_metrics.py``).

Retrieval (article level, one gold item = one question with 1–3 target articles):
  Recall@k = share of the item's targets among the first k hits; MRR@10 = 1 / rank of the first target within
  10 (0 otherwise); nDCG@10 with graded gains (2 = primary target, 1 = secondary) and log2 discounts; act-level
  Recall@10 = some hit of a target's act within 10 (the partial credit of §11.2, reported separately).
Resolver: exact accuracy, ambiguity precision / recall, citation precision (a resolved answer is right),
not-found accuracy. Warnings: share of expected (item, code) pairs present, items with every expected code,
forbidden codes seen.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, median
from typing import Any

KS = (1, 3, 5, 10, 20)


@dataclass(frozen=True)
class Target:
    key: str  # article ELI (eli_unit) — stable across consolidated versions
    grade: int = 2
    act: str | None = None


def ranks_of(targets: Sequence[Target], hit_keys: Sequence[str | None]) -> list[int | None]:
    """1-based rank of each target among the hits (first occurrence), ``None`` when absent."""
    pos: dict[str, int] = {}
    for i, k in enumerate(hit_keys, 1):
        if k and k not in pos:
            pos[k] = i
    return [pos.get(t.key) for t in targets]


def recall_at(ranks: Sequence[int | None], k: int) -> float:
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks) if ranks else 0.0


def reciprocal_rank(ranks: Sequence[int | None], k: int = 10) -> float:
    found = [r for r in ranks if r is not None and r <= k]
    return 1.0 / min(found) if found else 0.0


def ndcg_at(targets: Sequence[Target], ranks: Sequence[int | None], k: int = 10) -> float:
    dcg = sum((2**t.grade - 1) / math.log2(r + 1) for t, r in zip(targets, ranks, strict=True) if r and r <= k)
    ideal = sorted((t.grade for t in targets), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def act_recall_at(targets: Sequence[Target], hit_acts: Sequence[str | None], k: int = 10) -> float:
    acts = {a for a in hit_acts[:k] if a}
    wanted = [t.act for t in targets if t.act]
    return sum(1 for a in wanted if a in acts) / len(wanted) if wanted else 0.0


def retrieval_item(targets: Sequence[Target], hit_keys: Sequence[str | None], hit_acts: Sequence[str | None]) -> dict:
    ranks = ranks_of(targets, hit_keys)
    out: dict[str, Any] = {"ranks": ranks}
    for k in KS:
        out[f"recall@{k}"] = recall_at(ranks, k)
    out["mrr@10"] = reciprocal_rank(ranks, 10)
    out["ndcg@10"] = ndcg_at(targets, ranks, 10)
    out["act_recall@10"] = act_recall_at(targets, hit_acts, 10)
    return out


def summarise_retrieval(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    keys = [f"recall@{k}" for k in KS] + ["mrr@10", "ndcg@10", "act_recall@10"]
    out: dict[str, Any] = {"n": len(rows)}
    for key in keys:
        out[key] = round(mean(r[key] for r in rows), 4) if rows else 0.0
    return out


# ------------------------------------------------------------------------------------------- resolver (G1)
OUTCOMES = ("correct", "abstained", "wrong", "missed")


def resolver_outcome(expect: Mapping[str, Any], got: Mapping[str, Any]) -> str:
    """correct | abstained (ambiguous listing the expected act) | wrong (another act / article, or a guess) | missed.

    ``expect``: {status, act_eli?, unit_eli?, members?}; ``got``: {status, candidates: [{act_eli, unit_eli}]}.
    """
    status = got.get("status")
    cands = got.get("candidates") or []
    want = expect["status"]
    if want == "resolved":
        if status == "resolved" and cands:
            top = cands[0]
            if top.get("act_eli") != expect.get("act_eli"):
                return "wrong"
            if expect.get("unit_eli") and top.get("unit_eli") != expect["unit_eli"]:
                return "wrong"
            return "correct"
        if status == "ambiguous":
            return "abstained" if any(c.get("act_eli") == expect.get("act_eli") for c in cands) else "wrong"
        return "missed"
    if want == "ambiguous":
        if status == "ambiguous":
            members = set(expect.get("members") or [])
            listed = {c.get("act_eli") for c in cands}
            return "correct" if not members or len(members & listed) >= min(2, len(members)) else "wrong"
        return "wrong" if status == "resolved" else "missed"
    if want == "out_of_scope":
        return "correct" if status == "out_of_scope" else "wrong"
    # not_found
    return "correct" if status == "not_found" else "wrong"


def summarise_resolver(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    by = {o: sum(1 for r in rows if r["outcome"] == o) for o in OUTCOMES}
    exp_amb = [r for r in rows if r["expect_status"] == "ambiguous"]
    got_amb = [r for r in rows if r["got_status"] == "ambiguous"]
    tp_amb = [r for r in exp_amb if r["got_status"] == "ambiguous" and r["outcome"] == "correct"]
    resolved = [r for r in rows if r["got_status"] == "resolved"]
    resolved_ok = [r for r in resolved if r["expect_status"] == "resolved" and r["outcome"] == "correct"]
    exp_res = [r for r in rows if r["expect_status"] == "resolved"]
    exp_nf = [r for r in rows if r["expect_status"] == "not_found"]

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    return {
        "n": n,
        "outcomes": by,
        "exact_accuracy": ratio(by["correct"], n),
        "citation_precision": ratio(len(resolved_ok), len(resolved)),
        "resolved_recall": ratio(len(resolved_ok), len(exp_res)),
        "ambiguity_precision": ratio(len([r for r in got_amb if r["expect_status"] == "ambiguous"]), len(got_amb)),
        "ambiguity_recall": ratio(len(tp_amb), len(exp_amb)),
        "not_found_accuracy": ratio(sum(1 for r in exp_nf if r["outcome"] == "correct"), len(exp_nf)),
        "by_style": _by(rows, "style"),
    }


def _by(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get(field) or "-"), []).append(r)
    return {
        k: {"n": len(v), "correct": sum(1 for r in v if r["outcome"] == "correct")} for k, v in sorted(groups.items())
    }


# ------------------------------------------------------------------------------------------- warnings (G3)
def warning_item(expected: Sequence[str], forbidden: Sequence[str], got: Sequence[str]) -> dict[str, Any]:
    present = set(got)
    missing = [c for c in expected if c not in present]
    seen_forbidden = [c for c in forbidden if c in present]
    return {
        "expected": list(expected),
        "missing": missing,
        "forbidden_seen": seen_forbidden,
        "ok": not missing and not seen_forbidden,
    }


def summarise_warnings(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pairs = sum(len(r["expected"]) for r in rows)
    missing = sum(len(r["missing"]) for r in rows)
    status_rows = [r for r in rows if r.get("status_ok") is not None]
    per_code: dict[str, dict[str, int]] = {}
    for r in rows:
        for c in r["expected"]:
            d = per_code.setdefault(c, {"expected": 0, "present": 0})
            d["expected"] += 1
            d["present"] += c not in r["missing"]
    return {
        "n": len(rows),
        "items_ok": sum(1 for r in rows if r["ok"]),
        "mandatory_warning_coverage": round((pairs - missing) / pairs, 4) if pairs else None,
        "expected_pairs": pairs,
        "missing_pairs": missing,
        "forbidden_seen": sum(len(r["forbidden_seen"]) for r in rows),
        "status_correct": (
            round(sum(1 for r in status_rows if r["status_ok"]) / len(status_rows), 4) if status_rows else None
        ),
        "per_code": dict(sorted(per_code.items())),
    }


def latency_summary(values_ms: Sequence[float]) -> dict[str, float]:
    if not values_ms:
        return {}
    s = sorted(values_ms)
    p95 = s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]
    return {"p50_ms": round(median(s), 1), "p95_ms": round(p95, 1), "max_ms": round(s[-1], 1)}
