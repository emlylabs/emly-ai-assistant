"""Unknown /api/ and /widget/ paths answer 404, not the UI page with 200.

The UI catch-all served index.html for any unmatched GET, so a mistyped
or retired endpoint (``/api/admin/me``) returned 200 with HTML, which a
script reads as success. Found in the G8b restore drill.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import main

    return TestClient(main.app)


@pytest.mark.parametrize("path", ["/api/admin/does-not-exist", "/api/nope", "/api", "/widget/x/nope",
                                  "/api/admin/bots/x/unknown-tab"])
def test_unknown_api_and_widget_paths_are_404_json(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_real_routes_still_answer(client):
    assert client.get("/api/livez").status_code == 200
