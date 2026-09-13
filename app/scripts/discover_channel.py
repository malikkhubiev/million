"""Ждёт пересланный пост из канала и пишет TELEGRAM_CHANNEL_ID в .env.

  python -m app.scripts.discover_channel
"""

from __future__ import annotations

import asyncio
import re

from app.config import ROOT_DIR, get_settings
from app.services.telegram import TelegramClient


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN в .env")

    print("Бот ждёт 90с. Перешлите ему любой пост из закрытого канала.")
    async with TelegramClient(settings) as tg:
        me = await tg.get_me()
        print("Бот:", me.get("username"))
        await tg.delete_webhook()
        offset = None
        deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < deadline:
            updates = await tg.get_updates(offset=offset, timeout=15)
            for upd in updates:
                offset = upd["update_id"] + 1
                channel_id = None
                mcm = (upd.get("my_chat_member") or {}).get("chat") or {}
                if mcm.get("type") == "channel" and mcm.get("id"):
                    channel_id = mcm["id"]
                msg = upd.get("message") or {}
                fwd = msg.get("forward_from_chat") or {}
                if fwd.get("type") == "channel" and fwd.get("id"):
                    channel_id = fwd["id"]
                origin = msg.get("forward_origin") or {}
                if origin.get("type") == "channel":
                    chat = origin.get("chat") or {}
                    if chat.get("id"):
                        channel_id = chat["id"]
                chp = (upd.get("channel_post") or {}).get("chat") or {}
                if chp.get("type") == "channel" and chp.get("id"):
                    channel_id = chp["id"]
                if channel_id is None:
                    continue
                cid = str(channel_id)
                print(">>> TELEGRAM_CHANNEL_ID =", cid)
                env_path = ROOT_DIR / ".env"
                text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
                line = f"TELEGRAM_CHANNEL_ID={cid}"
                if re.search(r"(?m)^TELEGRAM_CHANNEL_ID=.*$", text):
                    text = re.sub(r"(?m)^TELEGRAM_CHANNEL_ID=.*$", line, text)
                else:
                    text = text.rstrip() + "\n" + line + "\n"
                env_path.write_text(text, encoding="utf-8")
                print("Записано в .env")
                chat_id = (msg.get("chat") or {}).get("id")
                if chat_id:
                    await tg.send_message(chat_id, f"Канал привязан: <code>{cid}</code>")
                return
        print("Таймаут: пост из канала не пришёл.")


if __name__ == "__main__":
    asyncio.run(main())
