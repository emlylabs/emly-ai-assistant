"""A6: Telegram rejects updates that don't carry the configured secret."""

from __future__ import annotations

import asyncio

import pytest


# -------------------------------------------------------------- A6 Telegram


class _FakeRequest:
    def __init__(self, headers: dict):
        self.headers = headers


@pytest.mark.parametrize(
    "configured, provided, expected",
    [
        ("", "", False),  # no secret configured → rejected
        ("", "anything", False),
        ("right-secret", "", False),  # missing header
        ("right-secret", "wrong-secret", False),
        ("right-secret", "right-secret", True),
    ],
)
def test_telegram_verify_signature(configured, provided, expected):
    from channels.telegram import TelegramAdapter, TelegramSecrets

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    headers = {"x-telegram-bot-api-secret-token": provided} if provided else {}
    secrets = TelegramSecrets.model_construct(secret_token=configured)
    assert asyncio.run(adapter.verify_signature(_FakeRequest(headers), secrets)) is expected
