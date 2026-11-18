"""R1: a dropped Postgres connection is reopened instead of failing forever.

Every thread kept one connection for the life of the process; nothing
validated or reopened it. After a Postgres restart or failover each
long-lived thread failed every query until the app was restarted, and the
liveness probe stayed green.
"""
from __future__ import annotations

import pytest
from peewee import OperationalError, PostgresqlDatabase

from db.db import ReconnectingPostgresqlDatabase


def test_postgres_urls_use_the_reconnecting_class():
    from playhouse.db_url import connect

    for url in ("postgres://u:p@db/emly", "postgresql://u:p@db/emly"):
        db = connect(url)
        assert isinstance(db, ReconnectingPostgresqlDatabase), url


@pytest.fixture
def fake_pg(monkeypatch):
    calls = {"exec": 0, "connect": 0, "close": 0}
    errors = []

    def execute_sql(self, sql, params=None):
        calls["exec"] += 1
        if errors:
            raise errors.pop(0)
        return "rows"

    monkeypatch.setattr(PostgresqlDatabase, "execute_sql", execute_sql)
    monkeypatch.setattr(ReconnectingPostgresqlDatabase, "connect",
                        lambda self, reuse_if_open=False: calls.__setitem__("connect", calls["connect"] + 1))
    monkeypatch.setattr(ReconnectingPostgresqlDatabase, "close",
                        lambda self: calls.__setitem__("close", calls["close"] + 1))
    monkeypatch.setattr(ReconnectingPostgresqlDatabase, "is_closed", lambda self: False)
    db = ReconnectingPostgresqlDatabase("emly")
    return db, calls, errors


@pytest.mark.parametrize("message", [
    "terminating connection due to administrator command",
    "server closed the connection unexpectedly",
    "SSL connection has been closed unexpectedly",
])
def test_dropped_connection_is_reopened_and_query_retried(fake_pg, message):
    db, calls, errors = fake_pg
    errors.append(OperationalError(message))
    assert db.execute_sql("SELECT 1") == "rows"
    assert calls["exec"] == 2 and calls["connect"] == 1


def test_other_errors_are_not_retried(fake_pg):
    db, calls, errors = fake_pg
    errors.append(OperationalError('relation "nope" does not exist'))
    with pytest.raises(OperationalError):
        db.execute_sql("SELECT * FROM nope")
    assert calls["exec"] == 1 and calls["connect"] == 0
