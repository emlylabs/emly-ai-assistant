"""S19: a redirect can't take the crawler out of its scope.

Each hop was SSRF-checked, but the page a redirect landed on was stored
without checking the crawl's scope (same host, path prefix, patterns),
and with CRAWL_ALLOWED_PRIVATE_CIDRS set an external page could 302 the
crawler to an intranet URL and have it indexed into a public bot.
"""
from __future__ import annotations

import asyncio

import pytest

from services import crawl_fetch, crawl_worker
from tests.security.test_crawl_ssrf import DNS, INTRANET, FakeResponse, web  # noqa: F401  (fixture)


@pytest.fixture
def mixed(web, monkeypatch):  # noqa: F811
    DNS.update(INTRANET)
    monkeypatch.setenv(crawl_fetch.ALLOWED_PRIVATE_ENV, "10.20.0.0/16,192.168.0.0/16")
    yield web
    for k in INTRANET:
        DNS.pop(k, None)


def test_public_page_cannot_redirect_into_an_allowed_private_range(mixed):
    routes, requested = mixed
    routes["http://public.example/a"] = FakeResponse("http://public.example/a", 302,
                                                     location="http://wiki.corp/secret")
    result = crawl_fetch.fetch_html("http://public.example/a")
    assert result["skipped_reason"].startswith("unsafe url")
    assert requested == ["http://public.example/a"]


def test_intranet_redirects_inside_the_intranet_still_work(mixed):
    routes, requested = mixed
    routes["http://wiki.corp/a"] = FakeResponse("http://wiki.corp/a", 302, location="http://hr.corp/b")
    routes["http://hr.corp/b"] = FakeResponse("http://hr.corp/b", body=b"<html>hr</html>")
    assert crawl_fetch.fetch_html("http://wiki.corp/a")["html"] == "<html>hr</html>"


def test_public_to_public_redirect_still_works(mixed):
    routes, _ = mixed
    routes["http://public.example/a"] = FakeResponse("http://public.example/a", 301,
                                                     location="http://other.example/b")
    routes["http://other.example/b"] = FakeResponse("http://other.example/b", body=b"<html>b</html>")
    assert crawl_fetch.fetch_html("http://public.example/a")["html"] == "<html>b</html>"


# --------------------------------------------------------------- worker scope
class _Jobs:
    def __init__(self):
        self.states, self.added = [], []

    def update_page_state(self, page_id, state, **kw):
        self.states.append((state, kw.get("reason")))

    def bump_counters(self, *a, **k):
        pass

    def add_page_if_new(self, job_id, url, depth):
        self.added.append(url)
        return False


@pytest.mark.parametrize("final_url,options", [
    ("https://elsewhere.example/page", {"sameHostOnly": True}),
    ("https://docs.example/private/x", {"sameHostOnly": True, "pathPrefix": "/public"}),
    ("https://docs.example/public/admin", {"excludeRegex": "/admin"}),
])
def test_landing_page_outside_scope_is_skipped(monkeypatch, final_url, options):
    jobs = _Jobs()
    monkeypatch.setattr(crawl_worker, "Crawl_Jobs", jobs)
    monkeypatch.setattr(crawl_worker, "validate_url", lambda url: ("https", "docs.example"))
    monkeypatch.setattr(crawl_worker, "fetch_html",
                        lambda url: {"html": "<html><a href='/next'>n</a>" + "x" * 500 + "</html>",
                                     "final_url": final_url})
    page = {"id": "p1", "url": "https://docs.example/public/start", "depth": 0}
    opts = {"respectRobots": False, "politeDelayMs": 0, **options}
    asyncio.run(crawl_worker._process_page({"id": "j1", "bot_id": "b1"}, page, "docs.example", {}, {}, opts))
    assert jobs.states == [(crawl_worker.PAGE_SKIPPED, "redirected out of scope")]
    assert jobs.added == []  # nothing discovered from an out-of-scope page
