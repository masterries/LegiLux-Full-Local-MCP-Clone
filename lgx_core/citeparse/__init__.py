"""Deterministic citation parser and resolver (PLAN §8.3; MCP stage, M1).

Resolution order: ELI → Mémorial reference → ``act_alias`` → (type, date) candidates ranked by title
trigram similarity. Never guess: top-two within 0.05 → ``ambiguous``.
"""
