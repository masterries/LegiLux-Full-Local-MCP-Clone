"""Load and render the FR/DE/EN warning texts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from string import Formatter
from typing import Any

from lgx_core.config import DEFAULT_CONFIG_DIR, WarningsConfig, load_yaml
from lgx_core.models import Lang, RenderedWarning, Severity, WarningCode, WarningDef


class Catalogue:
    """Immutable view over the warning definitions."""

    def __init__(self, defs: Mapping[WarningCode, WarningDef]) -> None:
        self._defs = dict(defs)

    def __getitem__(self, code: WarningCode | str) -> WarningDef:
        return self._defs[WarningCode(code)]

    def __len__(self) -> int:
        return len(self._defs)

    def codes(self) -> list[WarningCode]:
        return list(self._defs)

    def template(self, code: WarningCode | str, lang: Lang | str = Lang.FR, variant: str | None = None) -> str:
        d = self[code]
        lang = Lang(lang)
        if variant is not None:
            try:
                return d.variants[variant][lang]
            except KeyError as exc:
                raise KeyError(f"{d.code}: no variant {variant!r} in {lang.value}") from exc
        return {Lang.FR: d.text_fr, Lang.DE: d.text_de, Lang.EN: d.text_en}[lang]

    def render(
        self,
        code: WarningCode | str,
        /,
        lang: Lang | str = Lang.FR,
        variant: str | None = None,
        **params: Any,
    ) -> str:
        """Fill a template; every placeholder must be supplied (missing values are a bug, not blank).

        ``code`` is positional-only: a template may have a ``{code}`` placeholder (W_SUPERSEDED_BY_CODE).
        """
        template = self.template(code, lang, variant)
        needed = {name for _, name, _, _ in Formatter().parse(template) if name}
        missing = needed - params.keys()
        if missing:
            raise ValueError(f"{code}: missing parameters {sorted(missing)}")
        return template.format(**params)

    def warning(
        self,
        code: WarningCode | str,
        /,
        lang: Lang | str = Lang.FR,
        variant: str | None = None,
        refs: list[str] | None = None,
        data: dict[str, Any] | None = None,
        severity: Severity | None = None,
        **params: Any,
    ) -> RenderedWarning:
        """Build the Appendix D.1 warning object."""
        d = self[code]
        return RenderedWarning(
            code=d.code,
            severity=severity or d.severity,
            message=self.render(code, lang, variant, **params),
            refs=refs or [],
            data=data or {},
        )


def load_catalogue(config_dir: Path = DEFAULT_CONFIG_DIR) -> Catalogue:
    cfg = WarningsConfig.model_validate(load_yaml(config_dir / "warnings.yaml"))
    return Catalogue(cfg.defs())
