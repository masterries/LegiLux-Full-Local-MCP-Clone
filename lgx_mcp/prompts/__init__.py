"""The ``legal_answer`` prompt (PLAN §8.6, Appendix D.6) in FR (canonical), DE and EN."""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
LANGS = ("fr", "de", "en")
PROMPT_NAME = "legal_answer"
PROMPT_DESCRIPTION = (
    "Politique de réponse (prompt système) pour un assistant d'information juridique qui utilise ces outils :"
    " rechercher d'abord, citer chaque affirmation, citations verbatim en français, avertissements obligatoires,"
    " jamais de devinette sur une référence ambiguë. Argument lang = fr | de | en."
)


@cache
def legal_answer(lang: str = "fr") -> str:
    lang = (lang or "fr").strip().lower()[:2]
    if lang not in LANGS:
        lang = "fr"
    return (PROMPT_DIR / f"legal_answer.{lang}.md").read_text(encoding="utf-8")


@cache
def answer_skeleton() -> str:
    return (PROMPT_DIR / "answer_skeleton.md").read_text(encoding="utf-8")
