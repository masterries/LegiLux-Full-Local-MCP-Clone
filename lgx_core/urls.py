"""Internal-service URL test shared by lgx-mcp (reranker / embedding URLs) and lgx-streamlit (LLM endpoints)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def is_internal_url(url: str) -> bool:
    """True for service names (no dot), localhost, private/loopback IPs and internal suffixes (PLAN §9.5)."""
    host = urlsplit(url).hostname or ""
    if not host:
        return False
    if host == "localhost" or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    return host.endswith((".internal", ".local", ".lan", ".intra", ".svc", ".cluster.local", ".home.arpa"))
