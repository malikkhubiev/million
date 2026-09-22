"""БД: клиенты, платежи, webhook_events, app_settings."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import AppSetting, Client, Payment, WebhookEvent
from app.services.dates import KEY_SEATS_LEFT, consume_seat, get_cohort_settings
from app.services.payments import get_or_create_client, mark_webhook_seen


@pytest.mark.asyncio
async def test_get_or_create_client_upsert(session):
    a = await get_or_create_client(
        session,
        name="Алина",
        telegram_user_id=100,
        telegram_username="alina",
    )
    await session.commit()
    b = await get_or_create_client(
        session,
        name="Алина 2",
        telegram_user_id=100,
        phone="+7111",
    )
    await session.commit()
    assert a.id == b.id
    assert b.phone == "+7111"
    assert b.name == "Алина 2"

    rows = (await session.execute(select(Client).where(Client.telegram_user_id == 100))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_consume_seat(session, settings):
    before = (await get_cohort_settings(session, settings)).seats_left
    left = await consume_seat(session)
    await session.commit()
    assert left == before - 1
    row = await session.get(AppSetting, KEY_SEATS_LEFT)
    assert row is not None
    assert int(row.value) == left


@pytest.mark.asyncio
async def test_mark_webhook_seen(session):
    first = await mark_webhook_seen(
        session,
        provider="yookassa",
        event_key="payment.succeeded:p1:succeeded",
        event_type="payment.succeeded",
        payload={"ok": True},
    )
    await session.commit()
    assert first is False

    second = await mark_webhook_seen(
        session,
        provider="yookassa",
        event_key="payment.succeeded:p1:succeeded",
        event_type="payment.succeeded",
        payload={"ok": True},
    )
    assert second is True

    n = len((await session.execute(select(WebhookEvent))).scalars().all())
    assert n == 1


@pytest.mark.asyncio
async def test_payment_unique_order_id(session):
    c = Client(name="Женя", telegram_user_id=200)
    session.add(c)
    await session.flush()
    session.add(
        Payment(
            client_id=c.id,
            order_id="VS-UNIQUE0001",
            idempotence_key="k1",
            amount_value="1.00",
            currency="RUB",
            status="pending",
            description="a",
            product_code="program",
        )
    )
    await session.commit()

    session.add(
        Payment(
            client_id=c.id,
            order_id="VS-UNIQUE0001",
            idempotence_key="k2",
            amount_value="1.00",
            currency="RUB",
            status="pending",
            description="b",
            product_code="program",
        )
    )
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()
