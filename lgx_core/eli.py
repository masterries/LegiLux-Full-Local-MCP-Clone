"""ELI and URL helpers shared by selection, fetch, loader and MCP server.

Rules from PLAN §3.1-§3.2 and Appendix A.0:

* identifiers are ``http://data.legilux.public.lu/eli/…``; files are identified as
  ``http://data.legilux.public.lu/filestore/…`` and fetched as ``https://legilux.public.lu/filestore/…``;
* only ``https://legilux.public.lu/filestore/eli/etat/leg/…`` may ever be fetched (no ``/etat/adm/``,
  no ``*.docx``, nothing from ``data.legilux.public.lu``) — :func:`assert_fetchable` enforces that as a
  last line of defence; the robots.txt check (``lgx_ingest.robots``) runs in addition;
* dates are never parsed from URIs.
"""

from __future__ import annotations

import re

ELI_BASE = "http://data.legilux.public.lu/eli/"
LEG_PREFIX = ELI_BASE + "etat/leg/"
ADM_PREFIX = ELI_BASE + "etat/adm/"
FILESTORE_ID_PREFIXES = (
    "http://data.legilux.public.lu/filestore/",
    "https://data.legilux.public.lu/filestore/",
)
FILESTORE_FETCH_PREFIX = "https://legilux.public.lu/filestore/"
FETCH_ALLOWED_PREFIX = FILESTORE_FETCH_PREFIX + "eli/etat/leg/"
DISPLAY_BASE = "https://legilux.public.lu/eli/"
RESOURCE_SCHEME = "legilux://eli/"

_FORBIDDEN_FETCH = re.compile(r"(\.docx?$)|(/reg_ue/)|(/dir_ue/)|(/eli/etat/adm/)", re.IGNORECASE)


class FetchNotAllowedError(ValueError):
    """Raised for any URL outside the robots-permitted filestore branch."""


def expand(eli: str) -> str:
    """``etat/leg/loi/…`` (relative) → ``http://data.legilux.public.lu/eli/etat/leg/loi/…``."""
    if eli.startswith(ELI_BASE):
        return eli
    if eli.startswith(("http://", "https://")):
        raise ValueError(f"not a Legilux ELI: {eli}")
    return ELI_BASE + eli.lstrip("/")


def relative(eli: str) -> str:
    """Inverse of :func:`expand`."""
    if not eli.startswith(ELI_BASE):
        raise ValueError(f"not a Legilux ELI: {eli}")
    return eli[len(ELI_BASE) :]


def is_leg(eli: str) -> bool:
    """True for the ``/eli/etat/leg/`` branch (Mémorial A), the only branch in scope."""
    return eli.startswith(LEG_PREFIX)


def eli_slug(eli: str) -> str:
    """File-system safe slug, mirroring filestore names: ``eli-etat-leg-loi-2018-08-01-a686-jo``."""
    return "eli-" + relative(eli).strip("/").replace("/", "-")


def jo_of(eli_work: str) -> str:
    """JO original version ELI of a work (``…/a686`` → ``…/a686/jo``)."""
    return eli_work.rstrip("/") + "/jo"


def work_of_jo(eli_jo: str) -> str:
    """Work ELI of a ``/jo`` act URI."""
    if not eli_jo.endswith("/jo"):
        raise ValueError(f"not a /jo act URI: {eli_jo}")
    return eli_jo[: -len("/jo")]


def display_url(eli: str) -> str:
    """Human display URL (SPA page; not resolvable offline, never fetched)."""
    return DISPLAY_BASE + relative(eli)


def resource_uri(eli: str) -> str:
    """MCP resource URI (``legilux://eli/…``, Appendix D.0)."""
    return RESOURCE_SCHEME + relative(eli)


def filestore_fetch_url(file_uri: str) -> str:
    """Rewrite a ``jolux:isExemplifiedBy`` identifier to the fetchable main-host URL."""
    for prefix in FILESTORE_ID_PREFIXES:
        if file_uri.startswith(prefix):
            return FILESTORE_FETCH_PREFIX + file_uri[len(prefix) :]
    if file_uri.startswith(FILESTORE_FETCH_PREFIX):
        return file_uri
    raise ValueError(f"not a filestore identifier: {file_uri}")


def assert_fetchable(url: str) -> str:
    """Return ``url`` if it is inside the permitted filestore branch, else raise."""
    if not url.startswith(FETCH_ALLOWED_PREFIX) or _FORBIDDEN_FETCH.search(url) or "/../" in url:
        raise FetchNotAllowedError(f"fetch not allowed: {url}")
    return url
