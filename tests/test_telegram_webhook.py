"""Telegram webhook: секрет и принятие update."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_telegram_webhook_rejects_bad_secret(client):
    r = await client.post(
        "/api/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_telegram_webhook_ok(client):
    with patch("app.main.handle_bot_update", new_callable=AsyncMock) as handler:
        r = await client.post(
            "/api/telegram/webhook",
            json={"update_id": 2, "message": {"text": "/start"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-tg-secret"},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    handler.assert_called()


@pytest.mark.asyncio
async def test_telegram_english_webhook_ok(client):
    with patch("app.main.handle_bot_update", new_callable=AsyncMock) as handler:
        r = await client.post(
            "/api/telegram/english/webhook",
            json={"update_id": 3, "message": {"text": "/start"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "change-me-english"},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    handler.assert_called()
    kwargs = handler.await_args.kwargs
    assert kwargs["bot"].key == "english"
