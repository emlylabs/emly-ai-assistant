"""Shared HTTP-fetch helpers used by the crawl worker (and previously by
the admin browser-side proxy routes).

SSRF guard: every fetch resolves the hostname and rejects any URL whose
target IP is private/loopback/link-local/reserved. The threat model is an
authenticated admin asking the worker to fetch a URL — defense is against
casual mistakes (typing 169.254.169.254) and against an attacker who
already has admin credentials trying to escalate to internal networks.
Redirects are followed by hand (at most ``CRAWL_FETCH_MAX_REDIRECTS``)
and every hop is checked *before* it is requested; letting ``requests``
follow them would send the request to an internal address first and
check afterwards.

DNS rebinding (G13i): the name is resolved once, the addresses are
checked, and the connection goes to a checked address. ``requests`` is
never handed the hostname to resolve again, so a DNS server answering
with a short TTL can't return a public address for the check and an
internal one for the connection. The hostname is still used for the
``Host`` header, TLS SNI and certificate verification.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import List, Optional, Tuple, TypedDict
from urllib.parse import ParseResult, unquote, urljoin, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

CRAWL_FETCH_TIMEOUT_S = 10
CRAWL_FETCH_MAX_BYTES = 5 * 1024 * 1024
CRAWL_FETCH_MAX_REDIRECTS = 5
CRAWL_USER_AGENT = "EmlyAdminCrawler/1.0 (+https://emly.health)"


class CrawlFetchResult(TypedDict, total=False):
    status: int
    final_url: str
    content_type: Optional[str]
    html: Optional[str]
    fetched_bytes: int
    skipped_reason: Optional[str]


class UnsafeUrlError(ValueError):
    """Raised when an SSRF-guard check rejects a URL."""


def _is_blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_checked(url: str) -> Tuple[ParseResult, List[str]]:
    """Parse ``url``, resolve its host once and check every address.
    Returns the parsed URL and the checked addresses, in resolver order.
    Raises UnsafeUrlError for unsafe URLs."""
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise UnsafeUrlError("Malformed URL") from exc
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"Only http(s) URLs are allowed (got {parsed.scheme!r})")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL is missing a host")
    try:
        parsed.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise UnsafeUrlError("Malformed port") from exc
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Unable to resolve host {host}") from exc
    addresses: List[str] = []
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked(ip):
            raise UnsafeUrlError(
                f"Refusing to fetch private/loopback/link-local address {ip_str}"
            )
        if ip_str not in addresses:
            addresses.append(ip_str)
    if not addresses:
        raise UnsafeUrlError(f"No usable address for host {host}")
    return parsed, addresses


def validate_url(url: str) -> tuple[str, str]:
    """Return ``(scheme, host)``. Raises UnsafeUrlError for unsafe URLs."""
    parsed, _ = _resolve_checked(url)
    return parsed.scheme, parsed.hostname or ""


class _HostnameTLSAdapter(HTTPAdapter):
    """For a URL that names an IP: send ``hostname`` as TLS SNI and verify
    the certificate against it, as a normal request to the name would."""

    def __init__(self, hostname: str) -> None:
        self._hostname = hostname
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["server_hostname"] = self._hostname
        pool_kwargs["assert_hostname"] = self._hostname
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def _pinned_get(url: str, ip: str, *, headers: dict, stream: bool = False,
                verify: bool | str = True) -> requests.Response:
    """GET ``url`` by connecting to ``ip`` (already checked) without
    resolving the hostname again. ``resp.url`` is ``url``."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").encode("idna").decode("ascii")
    ip_literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{ip_literal}:{parsed.port}" if parsed.port else ip_literal
    target = urlunparse(parsed._replace(netloc=netloc))
    host_header = hostname if ":" not in hostname else f"[{hostname}]"
    if parsed.port:
        host_header = f"{host_header}:{parsed.port}"
    auth = (unquote(parsed.username), unquote(parsed.password or "")) if parsed.username else None

    session = requests.Session()
    adapter = _HostnameTLSAdapter(hostname)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    try:
        resp = session.get(
            target,
            headers={**headers, "Host": host_header},
            timeout=CRAWL_FETCH_TIMEOUT_S,
            allow_redirects=False,
            stream=stream,
            verify=verify,
            auth=auth,
        )
    except BaseException:
        session.close()
        raise
    resp.url = url
    close_response = resp.close

    def close() -> None:
        close_response()
        session.close()

    resp.close = close  # type: ignore[method-assign]
    return resp


def safe_get(url: str, *, headers: dict, stream: bool = False) -> requests.Response:
    """GET ``url``, following redirects by hand.

    Each URL, the first and every ``Location`` target, is resolved and
    checked before any request is sent to it, and the request goes to a
    checked address (``_pinned_get``). Raises ``UnsafeUrlError`` for a
    blocked hop and ``TooManyRedirects`` after
    ``CRAWL_FETCH_MAX_REDIRECTS``. The caller closes the returned response.
    """
    current = url
    for _ in range(CRAWL_FETCH_MAX_REDIRECTS + 1):
        _, addresses = _resolve_checked(current)
        resp = _pinned_get(current, addresses[0], headers=headers, stream=stream)
        location = resp.headers.get("Location") if resp.is_redirect else None
        if not location:
            return resp
        resp.close()
        current = urljoin(current, location)
    raise requests.exceptions.TooManyRedirects(f"more than {CRAWL_FETCH_MAX_REDIRECTS} redirects")


def fetch_html(url: str) -> CrawlFetchResult:
    """Fetch a URL and return its HTML body (or a skip reason).

    The URL and every redirect target are checked by ``validate_url``
    before they are requested; a blocked hop is returned as a skip.
    """
    headers = {"User-Agent": CRAWL_USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        with safe_get(url, headers=headers, stream=True) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            is_html = content_type.startswith("text/html") or content_type.startswith(
                "application/xhtml+xml"
            )
            if not is_html:
                return {
                    "status": resp.status_code,
                    "final_url": resp.url,
                    "content_type": content_type or None,
                    "html": None,
                    "fetched_bytes": 0,
                    "skipped_reason": "non-html",
                }

            body = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > CRAWL_FETCH_MAX_BYTES:
                    return {
                        "status": resp.status_code,
                        "final_url": resp.url,
                        "content_type": content_type or None,
                        "html": None,
                        "fetched_bytes": len(body),
                        "skipped_reason": f"body exceeds {CRAWL_FETCH_MAX_BYTES // (1024 * 1024)} MB cap",
                    }

            encoding = resp.encoding or "utf-8"
            try:
                html = body.decode(encoding, errors="replace")
            except LookupError:
                html = body.decode("utf-8", errors="replace")
            return {
                "status": resp.status_code,
                "final_url": resp.url,
                "content_type": content_type or None,
                "html": html,
                "fetched_bytes": len(body),
                "skipped_reason": None,
            }
    except UnsafeUrlError as exc:
        return {
            "status": 0,
            "final_url": url,
            "content_type": None,
            "html": None,
            "fetched_bytes": 0,
            "skipped_reason": f"unsafe url: {exc}",
        }
    except requests.exceptions.TooManyRedirects:
        return {
            "status": 0,
            "final_url": url,
            "content_type": None,
            "html": None,
            "fetched_bytes": 0,
            "skipped_reason": "too many redirects",
        }
    except requests.exceptions.Timeout:
        return {
            "status": 0,
            "final_url": url,
            "content_type": None,
            "html": None,
            "fetched_bytes": 0,
            "skipped_reason": "timeout",
        }
    except requests.exceptions.RequestException as exc:
        return {
            "status": 0,
            "final_url": url,
            "content_type": None,
            "html": None,
            "fetched_bytes": 0,
            "skipped_reason": f"fetch error: {exc}",
        }


def fetch_robots(host: str) -> Optional[str]:
    """Best-effort fetch of /robots.txt — None if missing or unreachable."""
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/robots.txt"
        try:
            with safe_get(url, headers={"User-Agent": CRAWL_USER_AGENT}) as resp:
                if resp.status_code == 200 and len(resp.content) <= CRAWL_FETCH_MAX_BYTES:
                    return resp.text
        except UnsafeUrlError:
            return None
        except requests.exceptions.RequestException:
            continue
    return None
