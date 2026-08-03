"""Pure validation for cited incident evidence shared by scanning and caching."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


_X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com"}


def canonical_citation_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _citation_host(value: object) -> str | None:
    canonical = canonical_citation_url(value)
    if canonical is None:
        return None
    host = urlsplit(canonical).hostname
    return host.casefold().removeprefix("www.") if host else None


def _is_x_host(host: str | None) -> bool:
    return bool(host and (host in _X_HOSTS or host.endswith(".x.com") or host.endswith(".twitter.com")))


def source_type_matches_url(source_type: object, source_url: object) -> bool:
    """Validate that a cited URL belongs to the search source it claims."""
    host = _citation_host(source_url)
    if host is None:
        return False
    if source_type == "x_search":
        return _is_x_host(host)
    if source_type == "web_search":
        return not _is_x_host(host)
    return False


def source_identity_from_url(source_url: object) -> str | None:
    """Derive a stable reporting identity from a citation, never model text."""
    canonical = canonical_citation_url(source_url)
    host = _citation_host(canonical)
    if canonical is None or host is None:
        return None
    if _is_x_host(host):
        segments = [segment.casefold() for segment in urlsplit(canonical).path.split("/") if segment]
        return f"x:{segments[0]}" if segments else None
    return f"web:{host}"
