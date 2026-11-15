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

    def fake_get(url, ip, **kwargs):
        # The connection goes to the address that was checked (G13i).
        assert ip == fake_getaddrinfo(__import__("urllib.parse").parse.urlparse(url).hostname)[0][4][0]
        requested.append(url)
        return routes.get(url) or FakeResponse(url, status=404, body=b"")

    monkeypatch.setattr(crawl_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(crawl_fetch, "_pinned_get", fake_get)
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


# ------------------------------------------------- G10a: intranet ranges
INTRANET = {
    "wiki.corp": "10.20.1.5",
    "hr.corp": "192.168.1.9",
}


@pytest.fixture
def intranet(web, monkeypatch):
    routes, requested = web
    DNS.update(INTRANET)
    yield routes, requested
    for k in INTRANET:
        DNS.pop(k, None)


def _allow(monkeypatch, value):
    monkeypatch.setenv(crawl_fetch.ALLOWED_PRIVATE_ENV, value)


def test_private_sites_are_refused_by_default(intranet, monkeypatch):
    monkeypatch.delenv(crawl_fetch.ALLOWED_PRIVATE_ENV, raising=False)
    _, requested = intranet
    result = crawl_fetch.fetch_html("http://wiki.corp/home")
    assert result["skipped_reason"].startswith("unsafe url")
    assert crawl_fetch.ALLOWED_PRIVATE_ENV in result["skipped_reason"]  # tells the admin how
    assert requested == []


def test_a_listed_range_can_be_crawled(intranet, monkeypatch):
    _allow(monkeypatch, "10.20.0.0/16")
    routes, requested = intranet
    routes["http://wiki.corp/home"] = FakeResponse("http://wiki.corp/home", body=b"<html>wiki</html>")
    assert crawl_fetch.fetch_html("http://wiki.corp/home")["html"] == "<html>wiki</html>"
    assert crawl_fetch.validate_url("http://wiki.corp/x") == ("http", "wiki.corp")


def test_redirect_into_an_unlisted_private_range_is_blocked(intranet, monkeypatch):
    _allow(monkeypatch, "10.20.0.0/16")
    routes, requested = intranet
    routes["http://wiki.corp/a"] = FakeResponse("http://wiki.corp/a", 302, location="http://hr.corp/payroll")
    result = crawl_fetch.fetch_html("http://wiki.corp/a")
    assert result["skipped_reason"].startswith("unsafe url")
    assert requested == ["http://wiki.corp/a"]


@pytest.mark.parametrize("listed,target", [
    ("169.254.0.0/16", "http://169.254.169.254/latest/meta-data/"),
    ("127.0.0.0/8", "http://127.0.0.1:6333/collections"),
    ("100.64.0.0/10", "http://100.100.100.200/latest/meta-data/"),
    ("fd00::/8", "http://[fd00:ec2::254]/latest/meta-data/"),
    ("::/1", "http://[::1]/"),
    ("10.0.0.0/8", "http://[::ffff:127.0.0.1]/"),
])
def test_loopback_link_local_and_metadata_stay_blocked_even_when_listed(web, monkeypatch, listed, target):
    _allow(monkeypatch, f"10.0.0.0/8,{listed}")
    _, requested = web
    result = crawl_fetch.fetch_html(target)
    assert result["skipped_reason"].startswith("unsafe url")
    assert crawl_fetch.ALLOWED_PRIVATE_ENV not in result["skipped_reason"]  # no hint: never allowed
    assert requested == []


def test_ipv4_mapped_addresses_follow_the_ipv4_rules(monkeypatch):
    import ipaddress

    monkeypatch.delenv(crawl_fetch.ALLOWED_PRIVATE_ENV, raising=False)
    assert crawl_fetch._is_blocked(ipaddress.ip_address("::ffff:10.20.1.5"))
    _allow(monkeypatch, "10.20.0.0/16")
    assert not crawl_fetch._is_blocked(ipaddress.ip_address("::ffff:10.20.1.5"))
    assert crawl_fetch._is_blocked(ipaddress.ip_address("::ffff:169.254.169.254"))


def test_shared_address_space_is_blocked_unless_listed(monkeypatch):
    """100.64.0.0/10 (carrier-grade NAT, Tailscale) isn't "private" to
    Python, so it used to pass. It is internal: blocked unless listed."""
    import ipaddress

    monkeypatch.delenv(crawl_fetch.ALLOWED_PRIVATE_ENV, raising=False)
    ip = ipaddress.ip_address("100.64.3.4")
    assert crawl_fetch._is_blocked(ip)
    _allow(monkeypatch, "100.64.0.0/10")
    assert not crawl_fetch._is_blocked(ip)
    assert not crawl_fetch._is_blocked(ipaddress.ip_address("93.184.216.34"))  # public is unaffected


@pytest.mark.parametrize("value", ["10.20.0.0/16, intranet", "0.0.0.0/0", "::/0", "10.0.0.0/33"])
def test_a_malformed_list_stops_start_up(monkeypatch, value):
    _allow(monkeypatch, value)
    with pytest.raises(crawl_fetch.CrawlSettingsError):
        crawl_fetch.log_crawl_settings()


def test_boot_logs_the_ranges_and_overlaps(monkeypatch, caplog):
    import logging

    _allow(monkeypatch, "10.20.0.0/16; 169.254.0.0/16")
    with caplog.at_level(logging.INFO, logger="services.crawl_fetch"):
        crawl_fetch.log_crawl_settings()
    assert "admins may crawl 10.20.0.0/16, 169.254.0.0/16" in caplog.text
    assert "169.254.0.0/16 overlaps" in caplog.text and "stay blocked" in caplog.text
