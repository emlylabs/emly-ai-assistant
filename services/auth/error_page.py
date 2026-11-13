"""Human-readable error pages for the browser-facing sign-in flow.

``/api/admin/auth/login`` and ``/callback`` are reached by top-level
browser navigation, so a JSON error body is shown to the user verbatim.
Callers check ``wants_html`` and render ``error_page`` instead; API
clients (``fetch``, tests, curl) keep getting JSON.
"""

from __future__ import annotations

from html import escape
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse


def wants_html(request: Request) -> bool:
    if request.method not in ("GET", "HEAD"):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def error_page(
    title: str,
    message: str,
    *,
    status_code: int,
    retry_after: Optional[int] = None,
    retry_href: str = "/api/admin/auth/login",
) -> HTMLResponse:
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #f6f7f9; color: #1f2328; }}
  main {{ max-width: 420px; padding: 32px; background: #fff; border: 1px solid #e3e6ea; border-radius: 10px; }}
  h1 {{ font-size: 20px; margin: 0 0 12px; }}
  p {{ font-size: 14px; line-height: 1.5; margin: 0 0 20px; color: #4b5563; }}
  a {{ display: inline-block; padding: 8px 16px; border-radius: 6px; background: #1f2328; color: #fff;
       text-decoration: none; font-size: 14px; }}
</style>
</head>
<body>
<main>
  <h1>{escape(title)}</h1>
  <p>{escape(message)}</p>
  <a href="{escape(retry_href)}">Try again</a>
</main>
</body>
</html>
"""
    return HTMLResponse(body, status_code=status_code, headers=headers)
