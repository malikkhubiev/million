from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings

TELEGRAM_API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, message: str, payload: Any = None):
        super().__init__(message)
        self.payload = payload


class TelegramClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> TelegramClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("TelegramClient не инициализирован")
        return self._client

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API}/bot{self.settings.telegram_bot_token}/{method}"

    async def call(self, method: str, **payload: Any) -> dict[str, Any]:
        if not self.settings.telegram_bot_token:
            raise TelegramError("TELEGRAM_BOT_TOKEN не задан")
        response = await self.client.post(self._url(method), json=payload)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API: {data.get('description', data)}", payload=data)
        return data["result"]

    async def get_me(self) -> dict[str, Any]:
        return await self.call("getMe")

    async def create_invite_link(
        self,
        *,
        name: str | None = None,
        member_limit: int | None = None,
        expire_days: int | None = None,
    ) -> dict[str, Any]:
        if not self.settings.telegram_channel_id:
            raise TelegramError("TELEGRAM_CHANNEL_ID не задан")

        body: dict[str, Any] = {"chat_id": self.settings.telegram_channel_id}
        if name:
            body["name"] = name[:32]
        limit = member_limit if member_limit is not None else self.settings.telegram_invite_member_limit
        if limit:
            body["member_limit"] = limit
        days = expire_days if expire_days is not None else self.settings.telegram_invite_expire_days
        expire_at: datetime | None = None
        if days and days > 0:
            expire_at = datetime.now(timezone.utc) + timedelta(days=days)
            body["expire_date"] = int(expire_at.timestamp())

        result = await self.call("createChatInviteLink", **body)
        result["_expire_at"] = expire_at.isoformat() if expire_at else None
        return result

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.call("sendMessage", **payload)

    async def edit_message(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.call("editMessageText", **payload)

    async def answer_callback(self, callback_id: str, text: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            body["text"] = text
        return await self.call("answerCallbackQuery", **body)

    async def set_webhook(self, url: str, secret_token: str) -> dict[str, Any]:
        return await self.call(
            "setWebhook",
            url=url,
            secret_token=secret_token,
            drop_pending_updates=False,
            allowed_updates=["message", "callback_query", "my_chat_member"],
        )

    async def delete_webhook(self) -> dict[str, Any]:
        return await self.call("deleteWebhook", drop_pending_updates=False)

    async def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query", "my_chat_member"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = await self.client.get(self._url("getUpdates"), params=payload, timeout=timeout + 10)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"getUpdates: {data}", payload=data)
        return data["result"]
