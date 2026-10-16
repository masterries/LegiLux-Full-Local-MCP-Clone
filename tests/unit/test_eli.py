from __future__ import annotations

import pytest

from lgx_core import eli

W = "http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686"


def test_expand_relative_roundtrip() -> None:
    assert eli.expand("etat/leg/loi/2018/08/01/a686") == W
    assert eli.expand(W) == W
    assert eli.relative(W) == "etat/leg/loi/2018/08/01/a686"
    with pytest.raises(ValueError):
        eli.expand("https://example.org/x")


def test_slug_jo_display_resource() -> None:
    assert eli.eli_slug(W + "/consolide/20251226") == "eli-etat-leg-loi-2018-08-01-a686-consolide-20251226"
    assert eli.jo_of(W) == W + "/jo" and eli.work_of_jo(W + "/jo") == W
    assert eli.display_url(W) == "https://legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686"
    assert eli.resource_uri(W) == "legilux://eli/etat/leg/loi/2018/08/01/a686"
    assert eli.is_leg(W) and not eli.is_leg(eli.ADM_PREFIX + "pa/2022/06/08/b2117")


def test_filestore_rewrite_and_guard() -> None:
    ident = "http://data.legilux.public.lu/filestore/eli/etat/leg/loi/2026/02/25/a88/jo/fr/xml/eli-etat-leg-loi-2026-02-25-a88-jo-fr-xml.xml"
    url = eli.filestore_fetch_url(ident)
    assert url.startswith("https://legilux.public.lu/filestore/eli/etat/leg/")
    assert eli.assert_fetchable(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://data.legilux.public.lu/filestore/eli/etat/leg/loi/x.xml",  # data host: robots-disallowed
        "https://legilux.public.lu/filestore/eli/etat/adm/pa/2022/x.xml",  # Mémorial B
        "https://legilux.public.lu/filestore/eli/etat/leg/loi/x/fr/docx/x.docx",  # docx
        "https://legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686",  # SPA page, not content
        "https://legilux.public.lu/filestore/eli/etat/leg/../adm/x.xml",
        "https://legilux.public.lu/reg_ue/x",
        "http://legilux.public.lu/filestore/eli/etat/leg/loi/x.xml",  # not https
    ],
)
def test_forbidden_urls(url: str) -> None:
    with pytest.raises(eli.FetchNotAllowedError):
        eli.assert_fetchable(url)
