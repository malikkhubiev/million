from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

GOAL_VALUES = {
    "view_offer": 100,
    "click_to_telegram": 500,
    "bot_started": 2000,
    "payment_started": 15000,
    "payment_success": 50000,
    "payment_canceled": 0,
}


def metrika_cid_for(client_metrika_id: str | None, telegram_user_id: int | None) -> str | None:
    if client_metrika_id and str(client_metrika_id).isdigit():
        return str(client_metrika_id)
    if telegram_user_id:
        return str(abs(int(telegram_user_id)))
    return None


async def send_collect(settings: Settings, params: dict[str, Any]) -> None:
    if not settings.is_metrika_configured:
        logger.debug("Метрика MP не настроена — пропуск")
        return
    query = {
        "tid": settings.metrika_counter_id,
        "ms": settings.metrika_mp_token,
        **{k: v for k, v in params.items() if v is not None and v != ""},
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(settings.metrika_collect_url, params=query)
            if r.status_code >= 400:
                logger.warning("Метрика collect %s: %s", r.status_code, r.text[:400])
    except Exception:
        logger.exception("Не удалось отправить хит в Метрику")


async def track_pageview(
    settings: Settings,
    *,
    cid: str,
    path: str,
    title: str,
    params: dict | None = None,
    referrer: str | None = None,
) -> None:
    import json

    body: dict[str, Any] = {
        "cid": cid,
        "t": "pageview",
        "dl": f"https://t.me/{settings.telegram_bot_username}{path}",
        "dt": title,
    }
    if referrer:
        body["dr"] = referrer
    if params:
        body["params"] = json.dumps(params, ensure_ascii=False)
    await send_collect(settings, body)


async def track_goal(
    settings: Settings,
    *,
    cid: str,
    goal: str,
    value: int | None = None,
    params: dict | None = None,
    path: str = "/bot",
) -> None:
    import json

    ev = GOAL_VALUES[goal] if value is None and goal in GOAL_VALUES else value
    body: dict[str, Any] = {
        "cid": cid,
        "t": "event",
        "ea": goal,
        "dl": f"https://t.me/{settings.telegram_bot_username}{path}",
        "cu": "RUB",
    }
    if ev is not None:
        body["ev"] = ev
    if params:
        body["params"] = json.dumps(params, ensure_ascii=False)
    await send_collect(settings, body)


async def track_purchase(
    settings: Settings,
    *,
    cid: str,
    order_id: str,
    amount: float | str = 50000,
) -> None:
    body: dict[str, Any] = {
        "cid": cid,
        "t": "event",
        "pa": "purchase",
        "ti": order_id,
        "tr": str(amount),
        "cu": "RUB",
        "pr1id": "vs-program",
        "pr1nm": settings.product_title,
        "pr1pr": str(amount),
        "pr1qt": "1",
        "dl": f"https://t.me/{settings.telegram_bot_username}/paid",
    }
    await send_collect(settings, body)


async def track_add_to_cart(settings: Settings, *, cid: str, amount: float | str = 50000) -> None:
    await send_collect(
        settings,
        {
            "cid": cid,
            "t": "event",
            "pa": "add",
            "cu": "RUB",
            "pr1id": "vs-program",
            "pr1nm": settings.product_title,
            "pr1pr": str(amount),
            "pr1qt": "1",
            "dl": f"https://t.me/{settings.telegram_bot_username}/pay",
        },
    )
