"""Text and number normalisation shared by parsers, resolver and loader (owner: agent B, M1).

Contract (PLAN §6.3 "Normalisation", INTERFACES §5):

* ``num_norm('Art. L. 121-1.') == 'l121-1'``, ``num_norm('Art. 1er.') == '1'``, ``bis/ter/…`` kept;
* ``normalise_whitespace`` collapses runs of whitespace (incl. NBSP) to one space and strips;
* ``alias_norm`` = lower-case + unaccent + collapsed spaces (``lgx.act_alias.alias_norm``).
"""

from __future__ import annotations


def num_norm(num_raw: str) -> str:
    raise NotImplementedError("lgx_core.normalise.num_norm: M1 (agent B)")


def normalise_whitespace(text: str) -> str:
    raise NotImplementedError("lgx_core.normalise.normalise_whitespace: M1 (agent B)")


def alias_norm(text: str) -> str:
    raise NotImplementedError("lgx_core.normalise.alias_norm: M1 (agent B)")
