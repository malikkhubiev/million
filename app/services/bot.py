from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import Client
from app.services.payments import find_paid_invite_for_email, find_paid_invite_for_order
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Привет! Я бот программы «Верни себе себя».\n\n"
    "После оплаты пришлите номер заказа (например VS-XXXXXXXXXX) "
    "или email, указанный при оплате — и я пришлю ссылку в закрытый канал.\n\n"
    "/start — это сообщение\n"
    "/status — проверка бота"
)


async def handle_bot_update(settings: Settings, update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    if not chat_id or not text:
        return

    async with SessionLocal() as session:
        # Привязка telegram_user_id к клиенту по email, если пришлют email
        reply = await _reply_for_text(session, settings, text, from_user)
        if from_user.get("id") and "@" in text:
            email = text.lower().strip()
            result = await session.execute(select(Client).where(Client.email == email))
            client = result.scalar_one_or_none()
            if client:
                client.telegram_user_id = from_user["id"]
                client.telegram_username = from_user.get("username")
                await session.commit()

    async with TelegramClient(settings) as tg:
        await tg.send_message(chat_id, reply)


async def _reply_for_text(
    session: AsyncSession,
    settings: Settings,
    text: str,
    from_user: dict[str, Any],
) -> str:
    lower = text.lower().strip()
    if lower in {"/start", "start", "помощь", "/help"}:
        return HELP_TEXT
    if lower in {"/status", "status"}:
        ok_channel = "да" if settings.telegram_channel_id else "нет (нужен TELEGRAM_CHANNEL_ID)"
        return f"Бот работает.\nКанал настроен: {ok_channel}\nРежим: {settings.telegram_mode}"

    invite = None
    if text.upper().startswith("VS-"):
        invite = await find_paid_invite_for_order(session, text.upper())
    elif "@" in text:
        invite = await find_paid_invite_for_email(session, text)

    if invite:
        return (
            "Оплата найдена. Ваша пригласительная ссылка в закрытый канал:\n"
            f"{invite.invite_url}\n\n"
            "Ссылка рассчитана на одного человека. Сохраните её."
        )

    if text.upper().startswith("VS-") or "@" in text:
        return (
            "Оплаченный заказ не найден.\n"
            "Проверьте номер/email или подождите минуту после оплаты и напишите снова."
        )

    return HELP_TEXT


class PollingRunner:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="telegram-polling")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        if not self.settings.telegram_bot_token:
            logger.warning("Polling не запущен: нет TELEGRAM_BOT_TOKEN")
            return
        offset: int | None = None
        logger.info("Telegram polling запущен")
        async with TelegramClient(self.settings) as tg:
            try:
                await tg.delete_webhook()
            except Exception:
                logger.exception("deleteWebhook не удался (можно игнорировать локально)")
            while not self._stopped.is_set():
                try:
                    updates = await tg.get_updates(offset=offset, timeout=25)
                    for upd in updates:
                        offset = upd["update_id"] + 1
                        try:
                            await handle_bot_update(self.settings, upd)
                        except Exception:
                            logger.exception("Ошибка обработки update %s", upd.get("update_id"))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Ошибка polling, повтор через 3с")
                    await asyncio.sleep(3)
        logger.info("Telegram polling остановлен")
