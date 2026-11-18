"""G13b: the admin UI and sign-in pages send a real Content-Security-Policy.

Only ``frame-ancestors 'none'`` was sent before, so an injected script
could run, pull in more code from anywhere and send data anywhere.
Checked by hand in Chromium against a full UI build (sign-in, bots list,
workspace settings, audit, a bot's config page with the live widget
preview): no violations, and an injected inline or third-party script is
blocked.
"""
from __future__ import annotations

import base64
import hashlib
import os

import pytest

from services.security_headers import security_headers_for
from services.ui_csp import STATIC_PAGE_POLICY, inline_script_hashes

PAGE = (b'<!DOCTYPE html><html><head><script src="/_next/static/chunks/a.js" async></script>'
        b'<script>self.__next_f=self.__next_f||[]</script></head><body>'
        b'<script>self.__next_f.push([1,"x"])</script>'
        b'<script type="application/json">{"a":1}</script></body></html>')


def _sha(text: bytes) -> str:
    return f"'sha256-{base64.b64encode(hashlib.sha256(text).digest()).decode()}'"


def _directive(policy: str, name: str) -> str:
    return next(d.strip() for d in policy.split(";") if d.strip().startswith(name + " "))


def test_inline_scripts_are_hashed_and_external_ones_are_not():
    hashes = inline_script_hashes(PAGE)
    assert hashes == (_sha(b"self.__next_f=self.__next_f||[]"), _sha(b'self.__next_f.push([1,"x"])'),
                      _sha(b'{"a":1}'))


@pytest.fixture
def ui_dir(tmp_path, monkeypatch):
    import main

    (tmp_path / "index.html").write_bytes(PAGE)
    (tmp_path / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(main, "_UI_DIR", tmp_path)
    return tmp_path


def test_ui_pages_get_a_strict_script_policy(ui_dir):
    import main

    resp = main._serve_ui_path(None, "")
    policy = resp.headers["content-security-policy"]
    script = _directive(policy, "script-src")
    assert script.startswith("script-src 'self' ")
    for h in inline_script_hashes(PAGE):
        assert h in script
    assert "unsafe-inline" not in script and "unsafe-eval" not in script
    for d in ("default-src 'self'", "connect-src 'self'", "object-src 'none'",
              "base-uri 'self'", "media-src 'self' blob:"):
        assert d in policy

    # The middleware keeps it and adds the anti-framing directive.
    headers = dict(security_headers_for("/", [(b"content-security-policy", policy.encode())], hsts=False))
    assert headers[b"content-security-policy"].endswith(b"frame-ancestors 'none'")


def test_non_html_files_have_no_page_policy(ui_dir):
    import main

    assert "content-security-policy" not in main._serve_ui_path(None, "app.js").headers


def test_policy_follows_a_rebuilt_page(ui_dir):
    import main

    first = main._serve_ui_path(None, "").headers["content-security-policy"]
    page = ui_dir / "index.html"
    page.write_bytes(PAGE.replace(b'[1,"x"]', b'[1,"changed"]'))
    st = page.stat()
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    second = main._serve_ui_path(None, "").headers["content-security-policy"]
    assert second != first and _sha(b'self.__next_f.push([1,"changed"])') in second


def test_sign_in_and_error_pages_allow_no_scripts():
    from routes.auth_issuer import _render_authorize_form
    from services.auth.error_page import error_page

    for resp in (error_page("t", "m", status_code=400), _render_authorize_form(params={})):
        policy = resp.headers["content-security-policy"]
        assert policy == STATIC_PAGE_POLICY
        assert "script-src" not in policy and policy.startswith("default-src 'none'")
