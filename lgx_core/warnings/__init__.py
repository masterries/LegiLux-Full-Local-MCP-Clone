"""Warning catalogue (``config/warnings.yaml`` → ``lgx.warning_def``) and rendering.

Which warnings apply to a citation is decided by code from ``lgx.serving_at`` (MCP stage, PLAN §8.5);
this package only holds the texts and renders them. Warnings are never chosen by the model.
"""

from lgx_core.warnings.catalogue import Catalogue, load_catalogue

__all__ = ["Catalogue", "load_catalogue"]
