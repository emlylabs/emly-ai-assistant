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
check afterwards. A microsecond DNS-rebinding window between resolution
and the actual TCP connect is accepted as out-of-scope for v1.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional, TypedDict
from urllib.parse import urljoin, urlparse

import requests

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


def validate_url(url: str) -> tuple[str, str]:
    """Return ``(scheme, host)``. Raises UnsafeUrlError for unsafe URLs."""
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
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Unable to resolve host {host}") from exc
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeUrlError(
                f"Refusing to fetch private/loopback/link-local address {ip_str}"
            )
    return parsed.scheme, host


def safe_get(url: str, *, headers: dict, stream: bool = False) -> requests.Response:
    """GET ``url``, following redirects by hand.

    Each URL, the first and every ``Location`` target, goes through
    ``validate_url`` before any request is sent to it. Raises
    ``UnsafeUrlError`` for a blocked hop and ``TooManyRedirects`` after
    ``CRAWL_FETCH_MAX_REDIRECTS``. The caller closes the returned response.
    """
    current = url
    for _ in range(CRAWL_FETCH_MAX_REDIRECTS + 1):
        validate_url(current)
        resp = requests.get(
            current,
            headers=headers,
            timeout=CRAWL_FETCH_TIMEOUT_S,
            allow_redirects=False,
            stream=stream,
        )
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
