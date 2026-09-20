"""Краткие русские логи действий пользователя для Render Logs."""

from __future__ import annotations

import logging
from typing import Any

from app.services.behavior import SECTION_LABELS

logger = logging.getLogger("app.activity")

HEADLINE_BY_KEY = {
    "top": "1",
    "one": "2",
    "feel": "3",
    "recognize": "4",
    "manifesto": "5",
    "method": "6",
    "author": "7",
    "results": "11",
    "inside": "9",
    "not_for": "12",
    "purchase": "10",
}


def _sid(session_id: str | None) -> str:
    if not session_id:
        return "—"
    s = str(session_id)
    return s if len(s) <= 14 else s[:10] + "…"


def _utm_short(utm: dict[str, str] | None) -> str:
    if not utm:
        return ""
    parts = []
    if utm.get("utm_campaign"):
        parts.append(f"кампания={utm['utm_campaign']}")
    if utm.get("utm_content"):
        parts.append(f"объявление={utm['utm_content']}")
    if utm.get("utm_term"):
        parts.append(f"ключ={utm['utm_term']}")
    return ", ".join(parts)


def log_landing_ping(
    *,
    session_id: str,
    metrika_client_id: str | None,
    utm: dict[str, str] | None,
    page_ms: int | None,
    newly_reached: list[str],
    clicked_telegram: bool,
    sections_reached_total: int,
) -> None:
    who = f"сессия {_sid(session_id)}"
    if metrika_client_id:
        who += f", cid={metrika_client_id[-8:]}"
    utm_s = _utm_short(utm)
    base = f"Пользователь ({who}"
    if utm_s:
        base += f", {utm_s}"
    base += ")"

    if newly_reached:
        for key in newly_reached:
            label = SECTION_LABELS.get(key, key)
            hl = HEADLINE_BY_KEY.get(key)
            hl_s = f", headline_{hl}" if hl else ""
            logger.info("%s дошёл до секции «%s»%s", base, label, hl_s)
    elif clicked_telegram:
        logger.info("%s нажал «в Telegram» на сайте", base)
    else:
        sec = f", секций {sections_reached_total}" if sections_reached_total else ""
        page = f", на сайте {max(0, (page_ms or 0) // 1000)} с" if page_ms is not None else ""
        logger.info("%s обновил поведение%s%s", base, sec, page)


def log_intent(*, session_id: str | None, utm: dict[str, str] | None, token: str) -> None:
    utm_s = _utm_short(utm)
    extra = f", {utm_s}" if utm_s else ""
    logger.info(
        "Пользователь (сессия %s%s) создал переход в бота (токен %s)",
        _sid(session_id),
        extra,
        token[:8] + "…",
    )


def log_bot_started(*, telegram_user_id: int | None, session_id: str | None, cid: str | None) -> None:
    logger.info(
        "Telegram id=%s: нажал /start (сессия %s, cid=%s)",
        telegram_user_id or "—",
        _sid(session_id),
        (cid[-8:] if cid else "—"),
    )


def log_show_phone(*, telegram_user_id: int | None, order_id: str | None) -> None:
    logger.info(
        "Telegram id=%s: показал номер → заказ %s",
        telegram_user_id or "—",
        order_id or "—",
    )


def log_payment_started(
    *,
    telegram_user_id: int | None,
    order_id: str | None,
    product_code: str | None,
    amount: Any = None,
) -> None:
    price = f", {amount} ₽" if amount is not None else ""
    logger.info(
        "Telegram id=%s: начал оплату %s (%s%s)",
        telegram_user_id or "—",
        order_id or "—",
        product_code or "—",
        price,
    )


def log_payment_success(*, telegram_user_id: int | None, order_id: str | None, amount: Any = None) -> None:
    price = f", {amount} ₽" if amount is not None else ""
    logger.info(
        "Telegram id=%s: оплата успешна %s%s",
        telegram_user_id or "—",
        order_id or "—",
        price,
    )
