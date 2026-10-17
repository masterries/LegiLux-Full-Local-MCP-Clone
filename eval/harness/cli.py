"""``python -m eval.harness …`` = ``lgx eval …`` — the evaluation CLI (PLAN §11).

python -m eval.harness run --set G2 --mode lexical            # in process, dev runtime DB, reranker off
python -m eval.harness run --set G2 --mode rerank --rerank-k 16 --rerank-body-chars 600
python -m eval.harness run --set G1 --url http://127.0.0.1:8000/mcp --token …   # a running server
python -m eval.harness compare eval/reports/G2-*.json          # side-by-side Markdown
python -m eval.harness gen-g1 | gen-g3 | freeze-g2              # (re)build the generated sets from the DB
python -m eval.harness gen-g1-pdf | mark-leaks                  # PDF G1 items (pdftotext); G2 lexical leakage
python -m eval.harness subset --set G2 --size 40                # fixed stratified subset (CPU-bound rerank runs)
python -m eval.harness run --set G2 --mode rerank --subset eval/gold/subsets/G2-rerank-40.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from eval.harness import gold, report
from eval.harness.backends import DEV_DSN, DEV_RERANK_URL, Options, open_backend
from eval.harness.runner import RUNNERS, stamp

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Evaluation harness (PLAN §11).")


def _progress(i: int, n: int, item_id: str) -> None:
    if i == n or i % 10 == 0:
        print(f"  {i}/{n} {item_id}", file=sys.stderr, flush=True)


@app.command("run")
def run(
    set_name: Annotated[str, typer.Option("--set", help="G0 | G1 | G2 | G3")],
    mode: Annotated[str, typer.Option(help="lexical | rerank")] = "lexical",
    dsn: Annotated[str, typer.Option(help="runtime database (lgx_ro)")] = DEV_DSN,
    url: Annotated[str | None, typer.Option(help="MCP endpoint instead of in-process")] = None,
    token: Annotated[str | None, typer.Option(help="bearer token for --url")] = None,
    rerank_url: Annotated[str, typer.Option()] = DEV_RERANK_URL,
    rerank_k: Annotated[int, typer.Option()] = 16,
    rerank_body_chars: Annotated[int, typer.Option()] = 600,
    ranker: Annotated[str, typer.Option(help="bm25 | rank_cd (M1 ordering)")] = "bm25",
    expansion: Annotated[bool, typer.Option(help="curated query expansion")] = True,
    opts: Annotated[str, typer.Option(help="ranker knobs as JSON, e.g. '{\"length_b\": 0.4}'")] = "",
    limit: Annotated[int | None, typer.Option(help="first N items only")] = None,
    subset: Annotated[
        str | None, typer.Option(help="only these item ids: a subset file (`subset` command) or a comma list")
    ] = None,
    out_dir: Annotated[
        Path, typer.Option(help="default data/reports/eval (not committed); eval/reports holds the baselines")
    ] = gold.RUNS_DIR,
    tag: Annotated[str | None, typer.Option(help="report name suffix")] = None,
) -> None:
    """Run one gold set and write ``<out-dir>/<set>-<label>.json|md`` (``--out-dir eval/reports`` for a baseline)."""
    if mode not in ("lexical", "rerank"):
        raise typer.BadParameter("--mode must be lexical or rerank")
    items, meta = gold.load_set(set_name)
    subset_info: dict[str, Any] | None = None
    if subset:
        try:
            ids = gold.subset_ids(subset, set_name)
            items = gold.select_ids(items, ids)
        except gold.GoldError as exc:
            raise typer.BadParameter(str(exc)) from exc
        subset_info = {"source": subset, "ids": ids}
    if limit:
        items = items[:limit]
        subset_info = {"source": f"first {limit}", "ids": [str(i["id"]) for i in items]}
    options = Options(
        mode=mode,
        dsn=dsn,
        rerank_url=rerank_url,
        rerank_k=rerank_k,
        rerank_body_chars=rerank_body_chars,
        ranker=ranker,
        expansion=expansion,
        lexical_opts=opts,
    )
    result = asyncio.run(_run(set_name, items, options, url, token))
    rep: dict[str, Any] = {
        "set": set_name,
        "started_at": result.pop("started_at"),
        "items_total": len(items),
        "gold_status": ", ".join(meta["status"]),
        "gold_files": meta["files"],
        "subset": subset_info,
        "config": result.pop("config"),
        **result,
    }
    name = tag or f"{set_name}-{report.run_label(rep).replace('/', '-').replace(' ', '-')}"
    js, md = report.write(rep, out_dir, name)
    print(json.dumps(rep["summary"], ensure_ascii=False, indent=1, default=str))
    print(f"wrote {js} and {md}", file=sys.stderr)


async def _run(set_name: str, items: list[dict[str, Any]], opts: Options, url: str | None, token: str | None) -> dict:
    started = stamp()
    async with open_backend(opts, url=url, token=token) as backend:
        out = await RUNNERS[set_name](backend, items, _progress)
        out["config"] = dict(backend.description)
    out["started_at"] = started
    return out


@app.command("subset")
def subset_cmd(
    set_name: Annotated[str, typer.Option("--set", help="G2 (strata: language x target act)")] = "G2",
    size: Annotated[int, typer.Option(help="items in the subset")] = 40,
    out: Annotated[Path | None, typer.Option(help="default eval/gold/subsets/<set>-rerank-<size>.json")] = None,
) -> None:
    """Write a fixed, stratified subset of a gold set (deterministic; outcomes are never looked at)."""
    items, _ = gold.load_set(set_name)
    doc = gold.stratified_subset(items, size, set_name)
    path = out or gold.GOLD_DIR / "subsets" / f"{set_name}-rerank-{size}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['ids'])} ids to {path}: {doc['strata']}", file=sys.stderr)


@app.command("compare")
def compare(
    reports: Annotated[list[Path], typer.Argument(help="report JSON files")],
    subset: Annotated[str | None, typer.Option(help="G2: score every report on these ids only")] = None,
) -> None:
    """Markdown tables of several runs (stdout)."""
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in reports]
    if subset:
        ids = gold.subset_ids(subset, "G2")
        loaded = [report.restrict(r, ids) if r["set"] == "G2" else r for r in loaded]
    print(report.compare_markdown(loaded))


@app.command("gen-g1")
def gen_g1(
    dsn: Annotated[str, typer.Option()] = DEV_DSN,
    out: Annotated[Path, typer.Option()] = gold.GOLD_DIR / "G1" / "citations.json",
    seed: Annotated[int, typer.Option()] = 20261016,
) -> None:
    """Generate G1 (citation resolution) from the database (deterministic for a release + seed)."""
    from eval.harness.gen_g1 import generate

    items, meta = generate(dsn, seed=seed)
    gold.write_set(out, "G1", items, **meta)
    print(f"wrote {len(items)} items to {out}", file=sys.stderr)


@app.command("gen-g1-pdf")
def gen_g1_pdf(
    out: Annotated[Path, typer.Option()] = gold.GOLD_DIR / "G1" / "pdf_codes.json",
) -> None:
    """G1 items for the PDF code-recueils from pdftotext alone (independent of the parser and the database)."""
    from eval.harness.gen_g1_pdf import generate

    items, meta = generate()
    gold.write_set(out, "G1", items, **meta)
    print(f"wrote {len(items)} items to {out}", file=sys.stderr)


@app.command("gen-g3")
def gen_g3(
    dsn: Annotated[str, typer.Option()] = DEV_DSN,
    out: Annotated[Path, typer.Option()] = gold.GOLD_DIR / "G3" / "warnings.json",
) -> None:
    """Generate G3 (mandatory warnings) from the database facts and the PLAN §8.5 triggers."""
    from eval.harness.gen_g3 import generate

    items, meta = generate(dsn)
    gold.write_set(out, "G3", items, **meta)
    print(f"wrote {len(items)} items to {out}", file=sys.stderr)


@app.command("freeze-g2")
def freeze_g2(
    dsn: Annotated[str, typer.Option()] = DEV_DSN,
    path: Annotated[Path, typer.Option()] = gold.GOLD_DIR / "G2" / "retrieval.json",
) -> None:
    """Check every G2 target against the database and record act, article and text sha256 (drift, PLAN §11.1)."""
    from eval.harness.gen_g2 import freeze

    n = freeze(dsn, path)
    print(f"froze {n} targets in {path}", file=sys.stderr)


@app.command("mark-leaks")
def mark_leaks(
    dsn: Annotated[str, typer.Option()] = DEV_DSN,
    path: Annotated[Path, typer.Option()] = gold.GOLD_DIR / "G2" / "retrieval.json",
) -> None:
    """Mark French G2 questions that reuse ≥ 5 tokens of their first target's wording (``leak``, ``leaky``)."""
    from eval.harness.leak import mark_file

    hist, leaky = mark_file(dsn, path)
    print(f"leak histogram (run length: items) {hist}; {leaky} leaky items in {path}", file=sys.stderr)


def main() -> None:  # pragma: no cover
    app()
