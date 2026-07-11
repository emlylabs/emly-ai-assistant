"""Shared httpx client for channel strategies.

Single timeout policy, single connection pool. Imported by all auth
strategies so adapter-level outbound calls are subject to the same
connect/read/write budgets.
"""
from __future__ import annotations

import httpx

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 15.0
WRITE_TIMEOUT = 15.0

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=CONNECT_TIMEOUT,
    read=READ_TIMEOUT,
    write=WRITE_TIMEOUT,
    pool=READ_TIMEOUT,
)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
