"""Shared HTTP-fetch helpers used by the crawl worker (and previously by
the admin browser-side proxy routes).

SSRF guard: every fetch resolves the hostname and rejects any URL whose
target IP is private/loopback/link-local/reserved. The threat model is an
authenticated admin asking the worker to fetch a URL — defense is against
casual mistakes (typing 169.254.169.254) and against an attacker who
already has admin credentials trying to escalate to internal networks.
A microsecond DNS-rebinding window between resolution and the actual
TCP connect is accepted as out-of-scope for v1.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional, TypedDict
from urllib.parse import urlparse

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


def fetch_html(url: str) -> CrawlFetchResult:
    """Fetch a URL and return its HTML body (or a skip reason).

    Caller MUST have already passed the URL through ``validate_url``;
    this routine still re-validates each redirect target.
    """
    headers = {"User-Agent": CRAWL_USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        with requests.get(
            url,
            headers=headers,
            timeout=CRAWL_FETCH_TIMEOUT_S,
            allow_redirects=True,
            stream=True,
        ) as resp:
            for hist in resp.history:
                validate_url(hist.url)
            validate_url(resp.url)

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
            validate_url(url)
        except UnsafeUrlError:
            return None
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": CRAWL_USER_AGENT},
                timeout=CRAWL_FETCH_TIMEOUT_S,
                allow_redirects=True,
            )
            if resp.status_code == 200 and len(resp.content) <= CRAWL_FETCH_MAX_BYTES:
                return resp.text
        except requests.exceptions.RequestException:
            continue
    return None
