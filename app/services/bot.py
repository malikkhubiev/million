from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.config import ROOT_DIR, Settings, get_settings
from app.db import SessionLocal
from app.models import Client
from app.services.behavior import mark_bot_started
from app.services.metrika import metrika_cid_for, track_goal, track_pageview
from app.services.payments import (
    create_checkout,
    get_or_create_client,
    latest_succeeded_for_telegram,
    pay_url_for,
    sync_open_payments,
)
from app.services.telegram import close_shared_tg, shared_tg
from app.services.tracking import apply_tracking_to_client, consume_intent

logger = logging.getLogger(__name__)

WELCOME = (
    "Ты готова к трансформации.\n\n"
    "Нажми кнопку «Показать номер» для регистрации тебя как участницы на 14 дней Трансформации."
)

PHONE_KB = {
    "keyboard": [[{"text": "Показать номер", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}

REMOVE_KB = {"remove_keyboard": True}

AFTER_PHONE = (
    "👏 Ты зарегистрирована на 14 дней)\n\n"
    "Я приготовил для тебя 2 вида Трансформации.\nВыбирай, что подходит Твоему Типу Личности:\n\n"
    "[1] Групповая Трансформация — 50 000 ₽\n\n"
    "Это доступ к готовым Материалам в закрытом канале "
    "(лицензия на цифровой контент). Всего 20 мест в наборе.\n\n"
    "Сразу после оплаты — ссылка в канал. "
    "Публикация Материалов стартует через 10 дней после закрытия набора "
    "и идёт 14 дней.\n\n"
    "Каждый день в канале:\n\n"
    "   1. Аудио — пошаговый материал\n\n"
    "   2. Ментальная тренировка\n\n"
    "   3. Духовная практика\n\n"
    "   4. Задание для самостоятельной работы\n\n"
    "Индивидуальные созвоны и кураторство не входят. "
    "На вопросы в канале могу отвечать по желанию — это не обязательство.\n\n"
    "🌱 Формат: Если Ты готова работать с Материалами самостоятельно "
    "в общем потоке набора.\n\n"
    "[2] Персональная Трансформация — 200 000 ₽\n\n"
    "   1. Первый шаг — диагностический созвон на 60 минут\n"
    "[ 10 000 ₽ ].\n\n"
    "   2. В течение 24 часов после оплаты Я свяжусь с тобой лично "
    "и назначу время созвона.\n\n"
    "   3. На диагностике Я подробно разберу Твоё текущее состояние, "
    "что именно мешает Тебе жить так, как Ты хочешь, "
    "и к каким изменениям Тебе необходимо прийти.\n\n"
    "   4. После диагностики Я определю векторы Твоего развития. "
    "Если Я увижу, что личный формат Тебе подходит, "
    "и Ты будешь готова продолжить, Мы перейдём "
    "к персональной Трансформации.\n\n"
    "🌱 Формат: Если Ты - Девушка - Интроверт, которая заряжается энергией "
    "в уединении, размышлениях и индивидуальной работе "
    "над задачами лично с Наставником.\n\n"
    "🤍 Выбирай путь, который подходит Тебе 🤍"
)

_chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_action_locks: dict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)
_last_text: dict[int, tuple[str, float]] = {}
_ENV_CHANNEL_RE = re.compile(r"(?m)^TELEGRAM_CHANNEL_ID=.*$")
_DEDUP_SEC = 45.0


def _invite_kb(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть портал", "url": url}]]}


def _pay_url_kb(url: str, title: str = "Оплатить") -> dict:
    return {"inline_keyboard": [[{"text": title, "url": url}]]}


def _paths_kb(personal_url: str, general_url: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Персональная Трансформация", "url": personal_url}],
            [{"text": "Групповая Трансформация", "url": general_url}],
        ]
    }


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def _remember_channel_id(settings: Settings, chat_id: int | str) -> None:
    cid = str(chat_id).strip()
    if not cid or settings.telegram_channel_id == cid:
        return
    settings.telegram_channel_id = cid
    get_settings.cache_clear()
    env_path = ROOT_DIR / ".env"
    try:
        text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        line = f"TELEGRAM_CHANNEL_ID={cid}"
        if _ENV_CHANNEL_RE.search(text):
            text = _ENV_CHANNEL_RE.sub(line, text)
        else:
            text = text.rstrip() + ("\n" if text and not text.endswith("\n") else "") + line + "\n"
        env_path.write_text(text, encoding="utf-8")
        logger.info("TELEGRAM_CHANNEL_ID сохранён: %s", cid)
    except Exception:
        logger.exception("Не удалось записать TELEGRAM_CHANNEL_ID в .env")


def _extract_channel_id(update: dict[str, Any]) -> int | None:
    for key in ("my_chat_member", "channel_post", "edited_channel_post"):
        block = update.get(key) or {}
        chat = block.get("chat") or {}
        if chat.get("type") == "channel" and chat.get("id") is not None:
            return int(chat["id"])
    message = update.get("message") or {}
    fwd = message.get("forward_from_chat") or {}
    if fwd.get("type") == "channel" and fwd.get("id") is not None:
        return int(fwd["id"])
    # Telegram Bot API 7+: forward_origin
    origin = message.get("forward_origin") or {}
    if origin.get("type") == "channel":
        chat = origin.get("chat") or {}
        if chat.get("id") is not None:
            return int(chat["id"])
    chat = message.get("chat") or {}
    if chat.get("type") == "channel" and chat.get("id") is not None:
        return int(chat["id"])
    return None


async def handle_bot_update(settings: Settings, update: dict[str, Any]) -> None:
    channel_id = _extract_channel_id(update)
    if channel_id is not None:
        _remember_channel_id(settings, channel_id)
        # Если это только событие канала — дальше нечего отвечать пользователю
        if update.get("my_chat_member") or update.get("channel_post") or update.get("edited_channel_post"):
            return

    callback = update.get("callback_query")
    if callback:
        await _handle_callback(settings, callback)
        return

    message = update.get("message") or {}
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    if not chat_id:
        return

    # Пересланный пост канала — сохранили id выше; подтвердим
    if channel_id is not None and (message.get("forward_from_chat") or message.get("forward_origin")):
        await _bot_message(
            settings,
            chat_id,
            from_user.get("id"),
            f"Канал привязан: <code>{channel_id}</code>\nТеперь после оплаты будет реальная пригласительная.",
        )
        return

    contact = message.get("contact")
    if contact:
        await _handle_contact(settings, from_user, chat_id, contact)
        return

    text = (message.get("text") or "").strip()
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        await _handle_start(settings, from_user, chat_id, payload)
        return

    # Любой другой текст — не дублируем welcome, просто мягко напоминаем про номер
    if not text:
        return
    await _bot_message(
        settings,
        chat_id,
        from_user.get("id"),
        "Чтобы зарегистрироваться — нажми «Показать номер».",
        PHONE_KB,
    )


async def _handle_start(
    settings: Settings,
    from_user: dict,
    chat_id: int,
    payload: str,
) -> None:
    tg_id = from_user.get("id")
    username = from_user.get("username")
    name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x)
    lang = from_user.get("language_code")
    premium = bool(from_user.get("is_premium"))

    # paid_* больше не используем как deep-link; если вдруг пришло — тихо sync, без ответа
    if payload.startswith("paid_") and tg_id:
        asyncio.create_task(_sync_open_bg(settings), name=f"sync-paid-{tg_id}")
        return

    async with SessionLocal() as session:
        if tg_id:
            paid = await latest_succeeded_for_telegram(session, tg_id)
            if paid and paid.invites:
                await _bot_message(
                    settings,
                    chat_id,
                    tg_id,
                    f"Тебе уже открыт доступ. Обучение начинается {settings.course_start_date}.",
                    _invite_kb(paid.invites[0].invite_url),
                )
                return

            client = await get_or_create_client(
                session,
                name=name,
                telegram_user_id=tg_id,
                telegram_username=username,
                language_code=lang,
                is_premium=premium,
            )
            intent = None
            extra_cid = None
            if payload.startswith("t") and len(payload) >= 5:
                intent = await consume_intent(session, payload[1:])
            elif payload.startswith("c") and payload[1:].isdigit():
                extra_cid = payload[1:]
            apply_tracking_to_client(client, intent, metrika_cid=extra_cid)
            await mark_bot_started(
                session,
                metrika_client_id=client.metrika_client_id,
                session_id=intent.behavior_session_id if intent else None,
                telegram_user_id=tg_id,
            )
            await session.commit()

            from app.services.activity_log import log_bot_started

            log_bot_started(
                telegram_user_id=tg_id,
                session_id=intent.behavior_session_id if intent else None,
                cid=client.metrika_client_id,
            )

            cid = metrika_cid_for(client.metrika_client_id, tg_id)
            visit = {
                "funnel": "bot_started",
                "telegram": {
                    "lang": lang or "",
                    "premium": "1" if premium else "0",
                    "has_username": "1" if username else "0",
                },
            }
            if client.yclid:
                visit["yclid"] = client.yclid
            if client.utm_json:
                try:
                    visit["utm"] = json.loads(client.utm_json)
                except Exception:
                    pass
            if cid:
                asyncio.create_task(
                    _track_start(settings, cid, visit, client.landing_url),
                    name=f"metrika-start-{tg_id}",
                )

    await _bot_message(settings, chat_id, tg_id, WELCOME, PHONE_KB)


async def _track_start(settings: Settings, cid: str, visit: dict, referrer: str | None) -> None:
    try:
        await track_pageview(
            settings,
            cid=cid,
            path="/bot/start",
            title="Бот: старт",
            params=visit,
            referrer=referrer,
        )
        await track_goal(settings, cid=cid, goal="bot_started", params=visit, path="/bot/start")
    except Exception:
        logger.exception("metrika start")


async def _handle_contact(
    settings: Settings,
    from_user: dict,
    chat_id: int,
    contact: dict,
) -> None:
    tg_id = from_user.get("id")
    if not tg_id:
        return

    if contact.get("user_id") and int(contact["user_id"]) != int(tg_id):
        await _bot_message(
            settings,
            chat_id,
            tg_id,
            "Нужен именно твой номер. Нажми «Показать номер».",
            PHONE_KB,
        )
        return

    phone = _normalize_phone(contact.get("phone_number"))
    if not phone:
        await _bot_message(
            settings,
            chat_id,
            tg_id,
            "Не удалось прочитать номер. Нажми «Показать номер» ещё раз.",
            PHONE_KB,
        )
        return

    username = from_user.get("username")
    name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x)
    lang = from_user.get("language_code")
    premium = bool(from_user.get("is_premium"))

    async with _action_locks[(tg_id, "pay")]:
        async with SessionLocal() as session:
            train = await latest_succeeded_for_telegram(session, tg_id, "vip_train")
            if train:
                await _bot_message(
                    settings,
                    chat_id,
                    tg_id,
                    "Ты уже в персональной Трансформации. Напишу тебе по деталям.",
                    remove_keyboard=True,
                )
                return

            paid = await latest_succeeded_for_telegram(session, tg_id)
            if paid and paid.invites:
                await _bot_message(
                    settings,
                    chat_id,
                    tg_id,
                    f"Тебе уже открыт доступ. Обучение начинается {settings.course_start_date}.",
                    _invite_kb(paid.invites[0].invite_url),
                    remove_keyboard=True,
                )
                return

            await get_or_create_client(
                session,
                name=name,
                telegram_user_id=tg_id,
                telegram_username=username,
                language_code=lang,
                is_premium=premium,
                phone=phone,
            )
            await session.commit()
            diag = await latest_succeeded_for_telegram(session, tg_id, "vip_diag")
            personal_code = "vip_train" if diag else "vip_diag"

        general = await _checkout_url(
            settings,
            from_user,
            chat_id,
            phone=phone,
            product_code="program",
        )
        personal = await _checkout_url(
            settings,
            from_user,
            chat_id,
            phone=phone,
            product_code=personal_code,
        )
        if not general or not personal:
            return

        general_url, general_order, cid = general
        personal_url, _, _ = personal
        if cid:
            asyncio.create_task(_track_phone(settings, cid, general_order))
        from app.services.activity_log import log_show_phone
        from app.services.behavior import mark_behavior_stage

        log_show_phone(telegram_user_id=tg_id, order_id=general_order)
        async with SessionLocal() as session:
            await mark_behavior_stage(
                session,
                stage="show_phone",
                telegram_user_id=tg_id,
                metrika_client_id=cid,
            )

        await _bot_message(
            settings,
            chat_id,
            tg_id,
            AFTER_PHONE,
            _paths_kb(personal_url, general_url),
        )


async def _handle_callback(settings: Settings, callback: dict[str, Any]) -> None:
    from_user = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    data = (callback.get("data") or "").strip()
    cb_id = callback.get("id")

    # Сразу гасим «часики» на кнопке — не ждём оплату
    async def _ack() -> None:
        try:
            tg = await shared_tg(settings)
            await tg.answer_callback(cb_id)
        except Exception:
            logger.exception("answerCallbackQuery")

    asyncio.create_task(_ack())

    if not chat_id or not from_user.get("id"):
        return
    if data == "vip":
        await _handle_vip(settings, from_user, int(chat_id))


async def _handle_vip(settings: Settings, from_user: dict, chat_id: int) -> None:
    tg_id = from_user.get("id")
    if not tg_id:
        return
    async with _action_locks[(int(tg_id), "vip")]:
        async with SessionLocal() as session:
            train = await latest_succeeded_for_telegram(session, tg_id, "vip_train")
            if train:
                await _bot_message(
                    settings,
                    chat_id,
                    tg_id,
                    "Ты уже в персональной Трансформации. Напишу тебе по деталям.",
                )
                return
            diag = await latest_succeeded_for_telegram(session, tg_id, "vip_diag")
            result = await session.execute(select(Client).where(Client.telegram_user_id == tg_id))
            client = result.scalar_one_or_none()
            phone = client.phone if client else None

        if not phone:
            await _bot_message(
                settings,
                chat_id,
                tg_id,
                "Сначала нажми «Показать номер».",
                PHONE_KB,
            )
            return

        if diag:
            await _send_checkout(
                settings,
                from_user,
                chat_id,
                phone=phone,
                product_code="vip_train",
                intro="После диагностики — персональная Трансформация, 190 000 ₽.",
                button="Оплатить 190 000 ₽",
            )
            return

        await _send_checkout(
            settings,
            from_user,
            chat_id,
            phone=phone,
            product_code="vip_diag",
            intro=(
                "Диагностический созвон — 60 минут, 10 000 ₽.\n"
                "На нём выясняем, что тебе нужно. Затем — персональная Трансформация 190 000 ₽."
            ),
            button="Оплатить 10 000 ₽",
        )


async def _checkout_url(
    settings: Settings,
    from_user: dict,
    chat_id: int,
    *,
    phone: str,
    product_code: str,
    notify: bool = True,
) -> tuple[str, str, str | None] | None:
    tg_id = from_user.get("id")
    username = from_user.get("username")
    name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x)

    if not settings.is_yookassa_configured:
        logger.warning("ЮKassa не настроена — pay пропущен для %s / %s", tg_id, product_code)
        if notify:
            await _bot_message(
                settings,
                chat_id,
                tg_id,
                "Сейчас оплата временно недоступна. Напиши нам чуть позже.",
            )
        return None

    try:
        async with SessionLocal() as session:
            payment = await create_checkout(
                session,
                settings,
                name=name,
                telegram_user_id=tg_id,
                telegram_username=username,
                phone=phone,
                source="telegram",
                product_code=product_code,
            )
            cid = metrika_cid_for(
                payment.client.metrika_client_id if payment.client else None,
                tg_id,
            )
            pay_url = pay_url_for(settings, payment)
            order_id = payment.order_id
    except Exception:
        logger.exception("checkout из бота (%s)", product_code)
        if notify:
            await _bot_message(
                settings,
                chat_id,
                tg_id,
                "Не удалось создать оплату. Нажми «Показать номер» ещё раз.",
                PHONE_KB,
            )
        return None

    if not pay_url:
        if notify:
            await _bot_message(
                settings,
                chat_id,
                tg_id,
                "Не удалось создать оплату. Нажми «Показать номер» ещё раз.",
                PHONE_KB,
            )
        return None
    return pay_url, order_id, cid


async def _send_checkout(
    settings: Settings,
    from_user: dict,
    chat_id: int,
    *,
    phone: str,
    product_code: str,
    intro: str,
    button: str,
    track_phone: bool = False,
    remove_keyboard: bool = False,
) -> bool:
    result = await _checkout_url(
        settings,
        from_user,
        chat_id,
        phone=phone,
        product_code=product_code,
    )
    if not result:
        return False
    pay_url, order_id, cid = result
    if track_phone and cid:
        asyncio.create_task(_track_phone(settings, cid, order_id))
    await _bot_message(
        settings,
        chat_id,
        from_user.get("id"),
        intro,
        _pay_url_kb(pay_url, button),
        remove_keyboard=remove_keyboard,
    )
    return True


async def _track_phone(settings: Settings, cid: str, order_id: str) -> None:
    try:
        await track_goal(
            settings,
            cid=cid,
            goal="show_phone",
            params={"funnel": "show_phone", "order_id": order_id},
            path="/bot/phone",
        )
    except Exception:
        logger.exception("metrika show_phone")


async def _bot_message(
    settings: Settings,
    chat_id: int,
    tg_id: int | None,
    text: str,
    markup: dict | None = None,
    *,
    remove_keyboard: bool = False,
) -> None:
    key = int(chat_id)
    now = time.monotonic()
    prev = _last_text.get(key)
    # Антидубль: одинаковый текст подряд в один чат не шлём
    if prev and prev[0] == text and (now - prev[1]) < _DEDUP_SEC:
        logger.info("Пропуск дубля в chat %s", chat_id)
        return

    async with _chat_locks[key]:
        now = time.monotonic()
        prev = _last_text.get(key)
        if prev and prev[0] == text and (now - prev[1]) < _DEDUP_SEC:
            return
        tg = await shared_tg(settings)
        if remove_keyboard and markup is not None:
            dummy = await tg.send_message(chat_id, "\u2060", reply_markup=REMOVE_KB)
            await tg.send_message(chat_id, text, reply_markup=markup)
            try:
                await tg.delete_message(chat_id, dummy["message_id"])
            except Exception:
                pass
        elif remove_keyboard:
            await tg.send_message(chat_id, text, reply_markup=REMOVE_KB)
        else:
            await tg.send_message(chat_id, text, reply_markup=markup)
        _last_text[key] = (text, time.monotonic())


async def _sync_open_bg(settings: Settings) -> None:
    try:
        async with SessionLocal() as session:
            await sync_open_payments(session, settings)
    except Exception:
        logger.exception("sync_open_payments")


class PollingRunner:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="telegram-polling")
        self._sync_task = asyncio.create_task(self._sync_loop(), name="yookassa-sync")

    async def stop(self) -> None:
        self._stopped.set()
        for task in (self._task, self._sync_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await close_shared_tg()

    async def _sync_loop(self) -> None:
        """Без webhook: каждые 4с проверяем открытые платежи и шлём invite/VIP один раз."""
        while not self._stopped.is_set():
            try:
                await _sync_open_bg(self.settings)
            except Exception:
                logger.exception("yookassa sync loop")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    async def _loop(self) -> None:
        if not self.settings.telegram_bot_token:
            logger.warning("Polling не запущен: нет TELEGRAM_BOT_TOKEN")
            return
        offset: int | None = None
        logger.info("Telegram polling запущен")
        tg = await shared_tg(self.settings)
        try:
            await tg.delete_webhook()
        except Exception:
            logger.exception("deleteWebhook не удался")
        while not self._stopped.is_set():
            try:
                updates = await tg.get_updates(offset=offset, timeout=20)
                tasks = []
                for upd in updates:
                    offset = upd["update_id"] + 1
                    tasks.append(asyncio.create_task(self._safe_handle(upd)))
                if tasks:
                    await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка polling, повтор через 1с")
                await asyncio.sleep(1)
        logger.info("Telegram polling остановлен")

    async def _safe_handle(self, upd: dict[str, Any]) -> None:
        try:
            # свежие settings — если канал только что сохранили
            settings = get_settings()
            await handle_bot_update(settings, upd)
        except Exception:
            logger.exception("Ошибка обработки update %s", upd.get("update_id"))
