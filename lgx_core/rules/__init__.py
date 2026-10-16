"""Selection and status rules: the single source of legal logic (owner: agent A, M1).

Pure functions only (no I/O, no network). The SQL twins are ``lgx.act_status`` / ``lgx.serving_at``
(``db/schema/30_functions.sql``); a parity test compares both. Contract: docs/dev/INTERFACES.md §4.
"""
