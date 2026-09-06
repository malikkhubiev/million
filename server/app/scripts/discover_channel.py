"""python -m app.scripts.discover_channel"""

from __future__ import annotations

import asyncio
import json

from app.config import get_settings
from app.services.telegram import TelegramClient


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN в server/.env")

    async with TelegramClient(settings) as tg:
        me = await tg.get_me()
        print("Бот:", me.get("username"), me.get("id"))
        print("Жду 60с… Перешлите боту пост из канала.")
        offset = None
        for _ in range(3):
            updates = await tg.get_updates(offset=offset, timeout=20)
            for upd in updates:
                offset = upd["update_id"] + 1
                print(json.dumps(upd, ensure_ascii=False, indent=2)[:4000])
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                if chat.get("type") in {"channel", "supergroup", "group"}:
                    print(">>> TELEGRAM_CHANNEL_ID =", chat.get("id"))
                fwd = msg.get("forward_from_chat")
                if isinstance(fwd, dict) and fwd.get("id"):
                    print(">>> Из пересланного: TELEGRAM_CHANNEL_ID =", fwd.get("id"))
                ch = (upd.get("my_chat_member") or {}).get("chat") or {}
                if ch.get("id"):
                    print(">>> my_chat_member chat id =", ch.get("id"))


if __name__ == "__main__":
    asyncio.run(main())
