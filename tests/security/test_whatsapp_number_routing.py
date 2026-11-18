"""S16: a WhatsApp channel handles only messages sent to its own number.

The signature check uses the deployment-wide META_APP_SECRET and the
channel comes from the webhook URL. ``metadata.phone_number_id`` was never
compared, so with two bots on two numbers under one Meta app, messages for
number B could be answered and stored by bot A. Only the first change of
the first entry was read.
"""
from __future__ import annotations

import asyncio

from channels.whatsapp_cloud import WhatsAppCloudAdapter, WhatsAppCloudSecrets
from tests.test_pr11_channel_end_user import _request

SECRETS = WhatsAppCloudSecrets.model_construct(phone_number_id="P-A")


def _change(number, text, msg_id):
    return {"value": {"metadata": {"phone_number_id": number},
                      "messages": [{"from": "15550100", "id": msg_id, "type": "text", "text": {"body": text}}]}}


def _parse(body):
    return asyncio.run(WhatsAppCloudAdapter().parse_inbound(_request(body), SECRETS))


def test_message_for_another_number_is_ignored():
    assert _parse({"entry": [{"changes": [_change("P-B", "for B", "m1")]}]}) is None


def test_own_message_is_found_past_other_numbers_and_entries():
    body = {"entry": [{"changes": [_change("P-B", "for B", "m1")]},
                      {"changes": [_change("P-A", "for A", "m2")]}]}
    inc = _parse(body)
    assert inc is not None and inc.text == "for A"


def test_missing_metadata_is_not_trusted():
    body = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "1", "id": "m1", "type": "text", "text": {"body": "x"}}]}}]}]}
    assert _parse(body) is None
