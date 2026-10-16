"""Tokenizer-free size estimate shared by the chunker and the runtime (PLAN §6.4): ``ceil(chars / 3.5)``."""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """Estimated token count of ``text`` (French legal prose; no tokenizer library is used)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def max_chars(tokens: int) -> int:
    """Largest character count whose estimate stays within ``tokens``."""
    return int(tokens * CHARS_PER_TOKEN)
