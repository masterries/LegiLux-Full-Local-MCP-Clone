"""Small I/O helpers for the JSONL contracts between stages (atomic writes, typed reads)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` via a temp file + rename, so readers never see partial files.

    The temp file of ``mkstemp`` is 0600; pass ``mode`` (e.g. 0o644) for files other users or containers read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if mode is not None:
            Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_jsonl[M: BaseModel](path: Path, records: Iterable[M]) -> int:
    """Atomically write one JSON object per line; returns the record count."""
    lines = [r.model_dump_json(exclude_none=False) for r in records]
    atomic_write_text(path, "".join(line + "\n" for line in lines))
    return len(lines)


def append_jsonl[M: BaseModel](path: Path, record: M) -> None:
    """Append one record (for append-only logs such as the fetch log and the raw index)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(record.model_dump_json() + "\n")


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> Iterator[M]:
    """Yield validated records; blank lines are skipped."""
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if line.strip():
                try:
                    yield model.model_validate_json(line)
                except ValueError as exc:
                    raise ValueError(f"{path}:{lineno}: {exc}") from exc
