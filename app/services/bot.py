from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import Client
from app.services.metrika import metrika_cid_for, track_add_to_cart, track_goal, track_pageview
from app.services.payments import (
    create_checkout,
    get_or_create_client,
    get_payment_by_order,
    latest_succeeded_for_telegram,
    sync_payment_from_yookassa,
)
from app.services.telegram import TelegramClient
from app.services.tracking import apply_tracking_to_client, consume_intent
from app.services.yookassa import YooKassaError

logger = logging.getLogger(__name__)

WELCOME = (
    "Привет.\n\n"
    "Прежде чем я расскажу тебе о программе, давай за 2 минуты поймём, "
    "что именно сейчас забирает твою энергию.\n\n"
    "Пять вопросов. Отвечай тем, что ближе, — а не тем, что кажется правильным."
)

START_KB = {
    "inline_keyboard": [
        [{"text": "Начать", "callback_data": "d|0|"}],
        [{"text": "Я уже решила — к оплате", "callback_data": "pay"}],
    ]
}

QUESTIONS: list[dict] = [
    {
        "text": "Что сейчас сильнее всего похоже на твоё состояние?",
        "options": [
            "Я постоянно устаю",
            "Я живу на автопилоте",
            "Мне сложно контролировать свои привычки",
            "Я много думаю, но мало делаю",
            "Мне не хватает удовольствия от жизни",
            "Вроде всё хорошо, но внутри чего-то не хватает",
        ],
    },
    {
        "text": "Когда день чаще всего рассыпается?",
        "options": [
            "С утра — тяжело встать и включиться",
            "Днём — не могу собраться на важное",
            "Вечером — залипаю и не могу остановиться",
            "В выходные — вроде отдыхаю, но не восстанавливаюсь",
        ],
    },
    {
        "text": "Что происходит, когда ты решаешь что-то изменить?",
        "options": [
            "Начинаю мощно, хватает на несколько дней",
            "Откладываю до подходящего момента",
            "Делаю, но через постоянное насилие над собой",
            "Уже почти не решаю — устала обещать себе",
        ],
    },
    {
        "text": "Что чаще всего управляет твоими действиями?",
        "options": [
            "Настроение",
            "Тревога и бесконечное «надо»",
            "Усталость",
            "Чужие ожидания и чужие задачи",
        ],
    },
    {
        "text": "Чего тебе сейчас не хватает больше всего?",
        "options": [
            "Энергии",
            "Спокойствия",
            "Способности сосредоточиться",
            "Ощущения, что я живу свою жизнь",
        ],
    },
]

STATE_READ = {
    "1": "Усталость, которая не проходит после выходных, — это почти никогда не про количество сна. Это про то, сколько энергии уходит на фоне: на контроль, на тревогу, на переключения между делами.",
    "2": "Автопилот включается не потому, что ты «расслабилась». Он включается, когда на осознанный выбор просто не остаётся ресурса — и жизнь начинает идти по накатанной колее.",
    "3": "Привычка — это не слабость характера. Это готовый ответ психики на конкретное состояние: усталость, скуку, напряжение. Меняешь состояние — исчезает и потребность в этом ответе.",
    "4": "Разрыв между «понимаю» и «делаю» — это не про лень. Понимание живёт в голове, а действие рождается из состояния тела. Пока состояние прежнее, знание не превращается в поступок.",
    "5": "Когда удовольствие уходит из обычного дня, дело не в событиях. Дело в том, что чувствительность притупляется — и ты перестаёшь замечать то, что раньше давало вкус жизни.",
    "6": "«Вроде всё хорошо, но чего-то не хватает» — самое честное описание жизни, в которой ты давно не выбирала. Всё правильно, но не твоё.",
}

TIMING_READ = {
    "1": "Утро — момент, когда состояние решает за тебя весь день. Если оно начинается с борьбы, дальше ты уже догоняешь.",
    "2": "Днём тебя разбирает на части внимание: десять переключений — и к важному ты подходишь уже пустой.",
    "3": "Вечернее залипание — это не отдых. Так психика гасит перегрев, накопленный за день, самым доступным способом.",
    "4": "Выходные не восстанавливают, когда отдых происходит в том же состоянии, в котором ты работала. Меняется расписание — но не ты.",
}

ATTEMPT_READ = {
    "1": "Ты стартуешь на подъёме — то есть на состоянии. Оно проходит через несколько дней, и вместе с ним уходит вся система.",
    "2": "«Подходящий момент» — это на самом деле ожидание нужного состояния. Оно само не приходит, поэтому ожидание растягивается на годы.",
    "3": "Через насилие над собой можно продержаться, но за это платит энергия. Поэтому каждый следующий заход даётся тяжелее предыдущего.",
    "4": "Ты перестала обещать себе не от безразличия, а чтобы не проходить снова через разочарование в себе. Это защита, а не лень.",
}

WANT_READ = {
    "1": "энергии",
    "2": "спокойствия",
    "3": "способности сосредоточиться",
    "4": "ощущения, что ты живёшь свою жизнь",
}


def _invite_kb(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть канал", "url": url}]]}


def _pay_url_kb(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "Оплатить 50 000 ₽", "url": url}]]}


def _question_kb(index: int, answers: str) -> dict:
    rows = [
        [{"text": option, "callback_data": f"d|{index + 1}|{answers}{i}"}]
        for i, option in enumerate(QUESTIONS[index]["options"], start=1)
    ]
    return {"inline_keyboard": rows}


def _question_text(index: int) -> str:
    return f"<b>Вопрос {index + 1} из {len(QUESTIONS)}</b>\n\n{QUESTIONS[index]['text']}"


def _offer_kb(settings: Settings) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Начать трансформацию", "callback_data": "pay"}],
        ]
    }


def _diagnosis_text(settings: Settings, answers: str) -> str:
    picked = (answers + "111111")[: len(QUESTIONS)]
    want = WANT_READ.get(picked[4], "энергии")
    return "\n\n".join(
        [
            "<b>Смотри, что получилось.</b>",
            STATE_READ.get(picked[0], STATE_READ["2"]),
            TIMING_READ.get(picked[1], TIMING_READ["1"]),
            ATTEMPT_READ.get(picked[2], ATTEMPT_READ["1"]),
            "<b>Похоже, твоя проблема не в отсутствии силы воли.</b>",
            "Ты слишком долго пыталась изменить поведение, не меняя состояние, "
            "из которого это поведение возникает.",
            f"Поэтому нельзя просто «добавить» себе решением или списком дел то, чего тебе не хватает: {want}. "
            "Сначала меняется состояние — и уже из него само меняется то, что ты делаешь.",
            "Я создал программу «Верни себе себя» именно для этого. "
            "Не заставлять себя стать другой, а научиться управлять состоянием, из которого ты живёшь.",
            "Две недели, аудио и практики на каждый день. "
            f"Набор закрывается {settings.enrollment_deadline}, старт {settings.course_start_date}. "
            "50 000 ₽, доступ в закрытый канал открывается сразу после оплаты.",
        ]
    )


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
    lang = from_user.get("language_code")
    premium = bool(from_user.get("is_premium"))

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
                    f"Оплата прошла. Ты внутри. Обучение начинается {settings.course_start_date}.",
                    _invite_kb(payment.invites[0].invite_url),
                )
                return

        if tg_id:
            paid = await latest_succeeded_for_telegram(session, tg_id)
            if paid and paid.invites:
                await _send(
                    settings,
                    chat_id,
                    f"Тебе уже открыт доступ. Обучение начинается {settings.course_start_date}.",
                    _invite_kb(paid.invites[0].invite_url),
                )
                return

        client = None
        if tg_id:
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
            await session.commit()
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
                await track_pageview(
                    settings,
                    cid=cid,
                    path="/bot/start",
                    title="Бот: старт",
                    params=visit,
                    referrer=client.landing_url,
                )
                await track_goal(settings, cid=cid, goal="bot_started", params=visit, path="/bot/start")

    await _send(settings, chat_id, WELCOME, START_KB)


async def _handle_diagnostic(
    settings: Settings,
    from_user: dict,
    chat_id: int,
    message_id: int | None,
    data: str,
) -> None:
    parts = data.split("|")
    try:
        index = int(parts[1])
    except (IndexError, ValueError):
        return
    answers = "".join(ch for ch in (parts[2] if len(parts) > 2 else "") if ch.isdigit())
    tg_id = from_user.get("id")

    if index >= len(QUESTIONS):
        await _edit_or_send(
            settings,
            chat_id,
            message_id,
            _diagnosis_text(settings, answers),
            _offer_kb(settings),
        )
        await _track(settings, tg_id, "diagnostic_finished", {"answers": answers}, "/bot/diagnostic/result")
        return

    await _edit_or_send(settings, chat_id, message_id, _question_text(index), _question_kb(index, answers))
    if index == 0:
        await _track(settings, tg_id, "diagnostic_started", {"funnel": "diagnostic"}, "/bot/diagnostic")


async def _handle_callback(settings: Settings, cb: dict[str, Any]) -> None:
    data = cb.get("data") or ""
    from_user = cb.get("from") or {}
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id") or from_user.get("id")
    message_id = message.get("message_id")
    cb_id = cb.get("id")

    async with TelegramClient(settings) as tg:
        if cb_id:
            try:
                await tg.answer_callback(cb_id)
            except Exception:
                pass

    if not chat_id:
        return

    if data.startswith("d|"):
        await _handle_diagnostic(settings, from_user, chat_id, message_id, data)
        return

    if data != "pay":
        return

    tg_id = from_user.get("id")
    username = from_user.get("username")
    name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x)
    lang = from_user.get("language_code")
    premium = bool(from_user.get("is_premium"))

    async with SessionLocal() as session:
        paid = await latest_succeeded_for_telegram(session, tg_id) if tg_id else None
        if paid and paid.invites:
            await _send(
                settings,
                chat_id,
                f"Тебе уже открыт доступ. Обучение начинается {settings.course_start_date}.",
                _invite_kb(paid.invites[0].invite_url),
            )
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
        client = payment.client
        if client:
            if lang:
                client.language_code = lang
            if premium is not None:
                client.is_premium = 1 if premium else 0
            await session.commit()
        cid = metrika_cid_for(client.metrika_client_id if client else None, tg_id)
        if cid:
            await track_goal(
                settings,
                cid=cid,
                goal="payment_started",
                params={"funnel": "payment_started", "order_id": payment.order_id},
                path="/bot/pay",
            )
            await track_add_to_cart(settings, cid=cid, amount=settings.price_rubles)

    if not payment.confirmation_url:
        await _send(settings, chat_id, "Не удалось создать платёж. Попробуй ещё раз.")
        return

    await _send(
        settings,
        chat_id,
        "Один шаг. Оплата на стороне ЮKassa — карта на бота не вводится.\n"
        f"После оплаты ссылка на закрытый канал придёт сюда сама. Обучение стартует {settings.course_start_date}.",
        _pay_url_kb(payment.confirmation_url),
    )


async def _send(settings: Settings, chat_id: int, text: str, markup: dict | None = None) -> None:
    async with TelegramClient(settings) as tg:
        await tg.send_message(chat_id, text, reply_markup=markup)


async def _edit_or_send(
    settings: Settings,
    chat_id: int,
    message_id: int | None,
    text: str,
    markup: dict | None = None,
) -> None:
    async with TelegramClient(settings) as tg:
        if message_id:
            try:
                await tg.edit_message(chat_id, message_id, text, reply_markup=markup)
                return
            except Exception:
                logger.info("Не удалось отредактировать сообщение %s, отправляю новое", message_id)
        await tg.send_message(chat_id, text, reply_markup=markup)


async def _track(
    settings: Settings,
    telegram_user_id: int | None,
    goal: str,
    params: dict,
    path: str,
) -> None:
    if not telegram_user_id or not settings.is_metrika_configured:
        return
    async with SessionLocal() as session:
        result = await session.execute(select(Client).where(Client.telegram_user_id == telegram_user_id))
        client = result.scalar_one_or_none()
    cid = metrika_cid_for(client.metrika_client_id if client else None, telegram_user_id)
    if cid:
        await track_goal(settings, cid=cid, goal=goal, params=params, path=path)


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
