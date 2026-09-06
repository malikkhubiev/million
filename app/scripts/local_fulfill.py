"""
Локальный E2E без ЮKassa: создать клиента/платёж и выдать invite.

  python -m app.scripts.local_fulfill --name Малик --email you@mail.com

Нужен TELEGRAM_CHANNEL_ID в .env.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Payment
from app.services.payments import fulfill_payment, get_or_create_client, new_order_id


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Тест")
    parser.add_argument("--email", default="test@example.com")
    args = parser.parse_args()

    settings = get_settings()
    await init_db()
    async with SessionLocal() as session:
        client = await get_or_create_client(session, name=args.name, email=args.email)
        order_id = new_order_id()
        payment = Payment(
            client_id=client.id,
            order_id=order_id,
            idempotence_key=str(uuid.uuid4()),
            yookassa_payment_id=f"local-{uuid.uuid4().hex[:8]}",
            amount_value=settings.price_value,
            currency="RUB",
            status="succeeded",
            description=f"local {order_id}",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        invite = await fulfill_payment(session, settings, payment)
        print("order_id:", order_id)
        if invite:
            print("invite:", invite.invite_url)
        else:
            print("invite не создан — проверьте TELEGRAM_CHANNEL_ID и права бота")


if __name__ == "__main__":
    asyncio.run(main())
