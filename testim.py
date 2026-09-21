"""Локальный запуск бота через long polling.

Использование:
    python testim.py

Не поднимай одновременно uvicorn с TELEGRAM_MODE=polling —
иначе два процесса будут конкурировать за getUpdates.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db import init_db
from app.logging_utils import install_secret_redaction
from app.services.bot import PollingRunner


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    install_secret_redaction()
    (ROOT / "data").mkdir(exist_ok=True)

    settings = get_settings()
    if not settings.telegram_bot_token:
        logging.error("TELEGRAM_BOT_TOKEN не задан в .env")
        raise SystemExit(1)
    if not settings.is_yookassa_configured:
        logging.warning("ЮKassa не настроена — кнопка оплаты не создастся")

    await init_db()
    runner = PollingRunner(settings)
    await runner.start()
    logging.info("testim.py: бот слушает updates. Ctrl+C — выход.")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.stop()
        logging.info("Остановлено.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
