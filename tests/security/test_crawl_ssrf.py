"""D5: the crawler checks every redirect hop before requesting it."""

from __future__ import annotations

import socket

import pytest

from services import crawl_fetch

DNS = {
    "public.example": "93.184.216.34",
    "other.example": "93.184.216.35",
    "sneaky.example": "10.0.0.7",  # public-looking name, private address
}


class FakeResponse:
    def __init__(self, url, status=200, location=None, body=b"<html>ok</html>"):
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        if location:
            self.headers["Location"] = location
        self.is_redirect = location is not None and status in (301, 302, 303, 307, 308)
        self.encoding = "utf-8"
        self.content = body
        self.text = body.decode()
        self._body = body

    def iter_content(self, chunk_size=1):
        yield self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def web(monkeypatch):
    """``routes`` maps URL -> FakeResponse; ``requested`` records every
    URL actually sent."""
    routes: dict = {}
    requested: list = []

    def fake_getaddrinfo(host, *_a, **_k):
        try:
            ip = DNS.get(host) or str(__import__("ipaddress").ip_address(host))
        except ValueError:
            raise socket.gaierror(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def fake_get(url, **kwargs):
        assert kwargs.get("allow_redirects") is False, "redirects must be followed by hand"
        requested.append(url)
        return routes.get(url) or FakeResponse(url, status=404, body=b"")

    monkeypatch.setattr(crawl_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(crawl_fetch.requests, "get", fake_get)
    return routes, requested


@pytest.mark.parametrize("target", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://sneaky.example/",
    "file:///etc/passwd",
])
def test_redirect_to_internal_address_is_blocked_before_request(web, target):
    routes, requested = web
    routes["https://public.example/start"] = FakeResponse("https://public.example/start", 302, location=target)

    result = crawl_fetch.fetch_html("https://public.example/start")

    assert result["html"] is None
    assert result["skipped_reason"].startswith("unsafe url")
    assert requested == ["https://public.example/start"]  # the internal hop was never sent


def test_blocked_on_a_later_hop(web):
    routes, requested = web
    routes["https://public.example/a"] = FakeResponse("https://public.example/a", 301, location="https://other.example/b")
    routes["https://other.example/b"] = FakeResponse("https://other.example/b", 307,
                                                     location="http://169.254.169.254/")
    result = crawl_fetch.fetch_html("https://public.example/a")
    assert result["skipped_reason"].startswith("unsafe url")
    assert requested == ["https://public.example/a", "https://other.example/b"]


def test_safe_redirects_are_followed_including_relative(web):
    routes, requested = web
    routes["https://public.example/old"] = FakeResponse("https://public.example/old", 301, location="/new")
    routes["https://public.example/new"] = FakeResponse("https://public.example/new", 302,
                                                        location="https://other.example/final")
    routes["https://other.example/final"] = FakeResponse("https://other.example/final", body=b"<html>hi</html>")

    result = crawl_fetch.fetch_html("https://public.example/old")

    assert result["html"] == "<html>hi</html>"
    assert result["final_url"] == "https://other.example/final"
    assert requested == ["https://public.example/old", "https://public.example/new", "https://other.example/final"]


def test_redirect_limit(web):
    routes, requested = web
    for i in range(10):
        routes[f"https://public.example/{i}"] = FakeResponse(f"https://public.example/{i}", 302,
                                                            location=f"/{i + 1}")
    result = crawl_fetch.fetch_html("https://public.example/0")
    assert result["skipped_reason"] == "too many redirects"
    assert len(requested) == crawl_fetch.CRAWL_FETCH_MAX_REDIRECTS + 1 == 6


def test_unsafe_seed_never_requested(web):
    _, requested = web
    result = crawl_fetch.fetch_html("http://127.0.0.1:6333/collections")
    assert result["skipped_reason"].startswith("unsafe url")
    assert requested == []


def test_robots_redirect_to_internal_is_blocked(web):
    routes, requested = web
    routes["https://public.example/robots.txt"] = FakeResponse(
        "https://public.example/robots.txt", 302, location="http://127.0.0.1/robots.txt")
    assert crawl_fetch.fetch_robots("public.example") is None
    assert "http://127.0.0.1/robots.txt" not in requested
