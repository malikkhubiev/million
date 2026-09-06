from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.services.payments import (
    create_checkout,
    get_payment_by_order,
    latest_succeeded_for_telegram,
    sync_payment_from_yookassa,
)
from app.services.telegram import TelegramClient
from app.services.yookassa import YooKassaError

logger = logging.getLogger(__name__)

WELCOME = (
    "Ты уже знаешь, зачем ты здесь.\n\n"
    "Не чтобы стать другой — чтобы снова почувствовать ту себя, "
    "которая живёт, а не справляется.\n\n"
    "50 000 ₽. Доступ в закрытый канал откроется сам, сразу после оплаты."
)

PAY_KB = {"inline_keyboard": [[{"text": "Вернуть себе себя — 50 000 ₽", "callback_data": "pay"}]]}


def _invite_kb(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть канал", "url": url}]]}


def _pay_url_kb(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "Оплатить 50 000 ₽", "url": url}]]}


async def handle_bot_update(settings: Settings, update: dict[str, Any]) -> None:
    if update.get("callback_query"):
        await _handle_callback(settings, update["callback_query"])
        return
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    if not chat_id:
        return
    if text.startswith("/start") or not text:
        payload = ""
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            payload = parts[1].strip() if len(parts) > 1 else ""
        await _handle_start(settings, from_user, chat_id, payload)
        return
    await _handle_start(settings, from_user, chat_id, "")


async def _handle_start(settings: Settings, from_user: dict, chat_id: int, payload: str) -> None:
    tg_id = from_user.get("id")
    username = from_user.get("username")
    name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x)

    async with SessionLocal() as session:
        if payload.startswith("paid_"):
            order_id = payload[5:]
            payment = await get_payment_by_order(session, order_id)
            if payment and payment.yookassa_payment_id:
                try:
                    payment = await sync_payment_from_yookassa(session, settings, payment)
                except Exception:
                    logger.exception("sync after return")
            if payment and payment.status == "succeeded" and payment.invites:
                await _send(
                    settings,
                    chat_id,
                    "Оплата прошла. Ты внутри.",
                    _invite_kb(payment.invites[0].invite_url),
                )
                return

        if tg_id:
            paid = await latest_succeeded_for_telegram(session, tg_id)
            if paid and paid.invites:
                await _send(
                    settings,
                    chat_id,
                    "Тебе уже открыт доступ.",
                    _invite_kb(paid.invites[0].invite_url),
                )
                return

    await _send(settings, chat_id, WELCOME, PAY_KB)
    # регистрируем клиента заранее
    if tg_id:
        async with SessionLocal() as session:
            from app.services.payments import get_or_create_client

            await get_or_create_client(
                session, name=name, telegram_user_id=tg_id, telegram_username=username
            )
            await session.commit()


async def _handle_callback(settings: Settings, cb: dict[str, Any]) -> None:
    data = cb.get("data") or ""
    from_user = cb.get("from") or {}
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id") or from_user.get("id")
    cb_id = cb.get("id")

    async with TelegramClient(settings) as tg:
        if cb_id:
            try:
                await tg.answer_callback(cb_id)
            except Exception:
                pass

    if data != "pay" or not chat_id:
        return

    tg_id = from_user.get("id")
    username = from_user.get("username")
    name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x)

    async with SessionLocal() as session:
        paid = await latest_succeeded_for_telegram(session, tg_id) if tg_id else None
        if paid and paid.invites:
            await _send(settings, chat_id, "Тебе уже открыт доступ.", _invite_kb(paid.invites[0].invite_url))
            return
        try:
            payment = await create_checkout(
                session,
                settings,
                name=name,
                telegram_user_id=tg_id,
                telegram_username=username,
                source="telegram",
            )
        except YooKassaError:
            logger.exception("checkout из бота")
            await _send(
                settings,
                chat_id,
                "Оплата сейчас недоступна. Напиши сюда ещё раз через минуту.",
            )
            return

    if not payment.confirmation_url:
        await _send(settings, chat_id, "Не удалось создать платёж. Попробуй ещё раз.")
        return

    await _send(
        settings,
        chat_id,
        "Один шаг. Оплата на стороне ЮKassa — карта на бота не вводится.\nПосле оплаты ссылка придёт сюда сама.",
        _pay_url_kb(payment.confirmation_url),
    )


async def _send(settings: Settings, chat_id: int, text: str, markup: dict | None = None) -> None:
    async with TelegramClient(settings) as tg:
        await tg.send_message(chat_id, text, reply_markup=markup)


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
                logger.exception("deleteWebhook не удался")
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
