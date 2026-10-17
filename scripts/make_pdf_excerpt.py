#!/usr/bin/env python3
"""Cut a page excerpt of a cached CC BY Legilux PDF into tests/fixtures/legilux/pdf/ (pypdfium2 page import, no other
change) and print its catalogue entry for ``excerpts.json``.

    uv run python scripts/make_pdf_excerpt.py \
        data/fixtures-large/eli-etat-leg-code-securite_sociale-20260101-fr-pdf.pdf 72,76-77 \
        --name css2 --derived-from css_pdf

Only CC BY 4.0 sources may be committed (``test_excerpts_are_catalogued_small_and_cc_by``); the page numbers are
the 1-based PDF page indexes a viewer shows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pypdfium2 as pdfium

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "legilux" / "pdf"


def pages_of(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("pages", help="1-based pages, e.g. 72,76-77")
    ap.add_argument("--name", required=True)
    ap.add_argument("--derived-from", required=True)
    args = ap.parse_args()
    pages = pages_of(args.pages)
    src = pdfium.PdfDocument(args.source)
    dst = pdfium.PdfDocument.new()
    dst.import_pages(src, [p - 1 for p in pages])
    label = "_".join(f"{a}" if a == b else f"{a}-{b}" for a, b in _runs(pages))
    path = OUT / f"{args.source.stem}.p{label}.pdf"
    dst.save(path)
    data = path.read_bytes()
    print(
        json.dumps(
            {
                args.name: {
                    "path": f"pdf/{path.name}",
                    "pages": ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in _runs(pages)),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "derived_from": args.derived_from,
                }
            },
            indent=1,
        )
    )


def _runs(pages: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], p)
        else:
            runs.append((p, p))
    return runs


if __name__ == "__main__":
    main()
