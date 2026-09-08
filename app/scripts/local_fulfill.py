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
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--username", default="")
    args = parser.parse_args()

    settings = get_settings()
    await init_db()
    async with SessionLocal() as session:
        client = await get_or_create_client(
            session,
            name=args.username or "Тест",
            telegram_user_id=args.telegram_id,
            telegram_username=args.username or None,
        )
        payment = Payment(
            client_id=client.id,
            order_id=new_order_id(),
            idempotence_key=str(uuid.uuid4()),
            yookassa_payment_id=f"local-{uuid.uuid4().hex[:8]}",
            amount_value=settings.price_value,
            currency="RUB",
            status="succeeded",
            description="local fulfill",
            source="local",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        invite = await fulfill_payment(session, settings, payment)
        print("order:", payment.order_id)
        print("invite:", None if not invite else invite.invite_url)


if __name__ == "__main__":
    asyncio.run(main())
