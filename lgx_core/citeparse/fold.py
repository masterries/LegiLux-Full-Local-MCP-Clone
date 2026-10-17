"""Case- and accent-folding with an index map back to the original text.

The citation patterns run on a folded copy of the input (lower-case, unaccented, typographic quotes and dashes
unified) so that « Arrêté grand-ducal », « arrete grand-ducal » and « ARRÊTÉ GRAND-DUCAL » are one pattern. Every
folded character remembers the index of the original character it came from, so spans (and title hints) are
cut from the original text with its accents intact.
"""

from __future__ import annotations

from dataclasses import dataclass

from lgx_core.normalise import unaccent

_SPECIAL = {
    "’": "'",
    "‘": "'",
    "`": "'",
    "´": "'",
    "–": "-",
    "—": "-",
    "‑": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "\t": " ",
    "\r": " ",
    "º": "°",
}


def _fold_char(ch: str) -> str:
    if ch in _SPECIAL:
        return _SPECIAL[ch]
    if ch.isascii():
        return ch.lower()
    folded = unaccent(ch).lower()
    return folded if folded else ch


@dataclass(frozen=True)
class Folded:
    """``text`` folded; ``index[i]`` = position in ``original`` of folded character ``i``."""

    original: str
    text: str
    index: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Original span of the folded span ``[start, end)``."""
        if start >= end:
            pos = self.index[start] if start < len(self.index) else len(self.original)
            return pos, pos
        return self.index[start], self.index[end - 1] + 1

    def cut(self, start: int, end: int) -> str:
        """Original text of the folded span ``[start, end)``."""
        a, b = self.span(start, end)
        return self.original[a:b]


def fold(text: str) -> Folded:
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        f = _fold_char(ch)
        out.append(f)
        index.extend([i] * len(f))
    return Folded(original=text, text="".join(out), index=tuple(index))
