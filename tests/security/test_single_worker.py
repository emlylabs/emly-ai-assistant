"""R2: Emly refuses to start with more than one worker.

The old guard refused only embedded Qdrant or the built-in issuer, so the
usual enterprise setup (Postgres, Qdrant server, external IdP) started N
workers: sign-in state is per process (sign-in failed now and then), rate
limits multiplied by N, and every worker ran the schedulers, migrations,
crawl and enrichment jobs.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("value", ["2", "4", "0", "abc"])
def test_anything_but_one_worker_is_refused(monkeypatch, value):
    import main

    monkeypatch.setenv("WEB_CONCURRENCY", value)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "false")
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        main._assert_runtime_topology()


@pytest.mark.parametrize("value", [None, "1", " 1 "])
def test_one_worker_starts(monkeypatch, value):
    import main

    if value is None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("WEB_CONCURRENCY", value)
    monkeypatch.setenv("MODE", "dev")
    main._assert_runtime_topology()
