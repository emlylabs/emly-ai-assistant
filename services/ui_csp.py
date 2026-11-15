"""G13b: Content-Security-Policy for the admin UI and the sign-in pages.

Before this, every response carried only ``frame-ancestors 'none'``. A
script injected into an admin page (a stored XSS, a compromised
third-party URL) could run, load more script from anywhere and send
what it read anywhere.

**Admin UI** (the Next.js static export in ``ui/out``): scripts only from
this origin, plus the inline scripts the export itself contains, allowed
by their SHA-256 hashes, which are computed from each HTML file when it
is served (and cached until the file changes). No ``'unsafe-inline'`` or
``'unsafe-eval'`` for scripts. Styles allow ``'unsafe-inline'``: React
renders ``style`` attributes, which hashes can't cover. Images may come
from any https host, because admins preview icons and link images set
for their bots. Network requests (``connect-src``) go only to this origin.

**Sign-in form and auth error pages** (server-rendered, no scripts):
nothing may load except their inline styles.

``frame-ancestors 'none'`` is added by ``services.security_headers`` to
every non-widget response, these included.
"""
from __future__ import annotations

import base64
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Tuple

# <script> elements without a src attribute; their text is what the
# browser hashes.
_INLINE_SCRIPT = re.compile(rb"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.S | re.I)

UI_POLICY = (
    "default-src 'self'; "
    "script-src 'self'{hashes}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# Server-rendered sign-in and error pages: inline styles only.
STATIC_PAGE_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'"
)


def inline_script_hashes(html: bytes) -> Tuple[str, ...]:
    """CSP source expressions (``'sha256-…'``) for each inline script."""
    seen = []
    for body in _INLINE_SCRIPT.findall(html):
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        source = f"'sha256-{digest}'"
        if source not in seen:
            seen.append(source)
    return tuple(seen)


@lru_cache(maxsize=1024)
def _policy_for(path: str, mtime_ns: int, size: int) -> str:
    hashes = inline_script_hashes(Path(path).read_bytes())
    return UI_POLICY.format(hashes="".join(f" {h}" for h in hashes))


def ui_policy_for_file(path: Path) -> str:
    """The CSP for an exported UI HTML file."""
    st = path.stat()
    return _policy_for(str(path), st.st_mtime_ns, st.st_size)
