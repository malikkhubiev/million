"""Создать тестовую пригласительную: python -m app.scripts.test_invite"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.services.telegram import TelegramClient


async def main() -> None:
    settings = get_settings()
    if not settings.is_telegram_configured:
        raise SystemExit("Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID в .env")

    async with TelegramClient(settings) as tg:
        me = await tg.get_me()
        print("Бот OK:", me.get("username"))
        link = await tg.create_invite_link(name="TEST-LOCAL", member_limit=1, expire_days=1)
        print("Invite:", link.get("invite_link"))
        print("Raw:", link)


if __name__ == "__main__":
    asyncio.run(main())
