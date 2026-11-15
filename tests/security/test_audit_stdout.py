"""Q-G5b: AUDIT_LOG_STDOUT writes every audit event as a JSON line on
stdout, so the customer's log pipeline keeps a copy the app can't edit."""
from __future__ import annotations

import json
import uuid


def _lines(out: str):
    return [json.loads(line) for line in out.splitlines() if line.startswith('{"type":"emly.audit"')]


def test_off_by_default(isolated_db, monkeypatch, capsys):
    from services.audit import audit

    monkeypatch.delenv("AUDIT_LOG_STDOUT", raising=False)
    audit("bot.created", admin_id="a", bot_id="b")
    assert _lines(capsys.readouterr().out) == []


def test_each_event_is_one_json_line_matching_the_row(isolated_db, monkeypatch, capsys):
    from models.admin_audit_log import AdminAuditLog
    from services.audit import audit

    monkeypatch.setenv("AUDIT_LOG_STDOUT", "true")
    target = f"t-{uuid.uuid4().hex[:6]}"
    audit("membership.granted", admin_id="adm-1", bot_id="bot-1", target_type="admin", target_id=target,
          payload={"role": "viewer", "who": "ann@example.com"}, ip="10.0.0.1", ua="UA", success=True)

    (event,) = _lines(capsys.readouterr().out)
    row = AdminAuditLog.get(AdminAuditLog.id == event["id"])
    assert event["stored"] is True
    assert (event["action"], event["admin_id"], event["bot_id"], event["target_id"]) == (
        "membership.granted", "adm-1", "bot-1", target)
    assert row.action == "membership.granted"
    # Not passed through log redaction: the SIEM gets what the table holds.
    assert event["payload"] == {"role": "viewer", "who": "ann@example.com"}
    assert event["ip"] == "10.0.0.1" and event["ts"].endswith("+00:00")


def test_event_still_reaches_stdout_when_the_db_write_fails(isolated_db, monkeypatch, capsys):
    from models.admin_audit_log import AdminAuditLogs
    from services.audit import audit

    monkeypatch.setenv("AUDIT_LOG_STDOUT", "true")
    monkeypatch.setenv("AUDIT_FAIL_CLOSED", "false")
    monkeypatch.setattr(AdminAuditLogs, "insert", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    audit("bot.deleted", admin_id="a", bot_id="b")
    (event,) = _lines(capsys.readouterr().out)
    assert event["stored"] is False and event["id"] is None and event["action"] == "bot.deleted"
