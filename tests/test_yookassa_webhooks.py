"""ЮKassa: создание платежа (mock API), вебхуки, идемпотентность, места."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Client, Payment, WebhookEvent
from app.services.dates import get_cohort_settings
from app.services.payments import create_checkout, handle_yookassa_notification


def _yk_payment_payload(
    *,
    payment_id: str,
    order_id: str,
    status: str = "succeeded",
    telegram_user_id: int = 42,
    amount: str = "65000.00",
) -> dict:
    return {
        "event": f"payment.{status}" if status != "waiting_for_capture" else "payment.waiting_for_capture",
        "object": {
            "id": payment_id,
            "status": status,
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {
                "order_id": order_id,
                "telegram_user_id": str(telegram_user_id),
                "product_code": "program",
                "source": "telegram",
            },
        },
    }


@pytest.mark.asyncio
async def test_create_checkout_calls_yookassa(session, settings):
    fake = {
        "id": "yk-pay-1",
        "status": "pending",
        "confirmation": {"confirmation_url": "https://yookassa.ru/checkout/test"},
    }

    with patch("app.services.payments.YooKassaClient") as YK:
        inst = YK.return_value
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=None)
        inst.create_payment = AsyncMock(return_value=fake)

        payment = await create_checkout(
            session,
            settings,
            name="Тест",
            telegram_user_id=1001,
            telegram_username="tester",
            phone="+79001234567",
        )

    assert payment.order_id.startswith("VS-")
    assert payment.yookassa_payment_id == "yk-pay-1"
    assert payment.status == "pending"
    assert payment.confirmation_url == "https://yookassa.ru/checkout/test"
    assert payment.amount_value == "65000.00"
    inst.create_payment.assert_awaited_once()
    call_kw = inst.create_payment.await_args.kwargs
    assert call_kw["metadata"]["telegram_user_id"] == "1001"
    assert call_kw["metadata"]["product_code"] == "program"
    assert call_kw["customer_phone"] == "+79001234567"


@pytest.mark.asyncio
async def test_create_checkout_english_product(session, settings, monkeypatch):
    monkeypatch.setattr(settings, "english_product_price_kopecks", 1_500_000)
    monkeypatch.setattr(settings, "yookassa_vat_code", 1)
    monkeypatch.setattr(settings, "yookassa_receipt_description", "Лицензия на цифровые материалы")
    monkeypatch.setattr(settings, "yookassa_payment_subject", "intellectual_activity")

    captured: dict = {}

    async def _create_payment(**kwargs):
        captured.update(kwargs)
        return {
            "id": "yk-en-1",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.ru/checkout/en"},
        }

    with patch("app.services.payments.YooKassaClient") as YK:
        inst = YK.return_value
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=None)
        inst.create_payment = AsyncMock(side_effect=_create_payment)

        payment = await create_checkout(
            session,
            settings,
            name="English",
            telegram_user_id=2002,
            phone="+79007654321",
            product_code="english",
        )

    assert payment.order_id.startswith("EN-")
    assert payment.product_code == "english"
    assert payment.amount_value == "15000.00"
    assert captured["metadata"]["product_code"] == "english"
    assert captured["metadata"]["bot_key"] == "english"

    # Чек собирается внутри YooKassaClient — проверим напрямую
    from app.services.yookassa import YooKassaClient

    with patch.object(YooKassaClient, "_request", new_callable=AsyncMock) as req:
        req.return_value = {"id": "x", "status": "pending", "confirmation": {}}
        async with YooKassaClient(settings) as yk:
            await yk.create_payment(
                amount_value="15000.00",
                description="Курс · EN-1",
                return_url="https://t.me/bot",
                metadata={"order_id": "EN-1"},
                customer_phone="79001234567",
            )
        body = req.await_args.kwargs["json"]
        assert body["receipt"]["items"][0]["description"] == "Лицензия на цифровые материалы"
        assert body["receipt"]["items"][0]["payment_subject"] == "intellectual_activity"



@pytest.mark.asyncio
async def test_yookassa_webhook_succeeds_and_consumes_seat(client, session, settings, db_engine):
    _, SessionLocal, _ = db_engine

    client_row = Client(name="Аня", telegram_user_id=42, phone="+79001112233")
    session.add(client_row)
    await session.flush()
    payment = Payment(
        client_id=client_row.id,
        order_id="VS-TESTORDER1",
        idempotence_key="idem-1",
        yookassa_payment_id="yk-abc",
        amount_value="65000.00",
        currency="RUB",
        status="pending",
        description="test",
        source="telegram",
        product_code="program",
        confirmation_url="https://yookassa.ru/x",
    )
    session.add(payment)
    await session.commit()

    before = await get_cohort_settings(session, settings)
    seats_before = before.seats_left

    payload = _yk_payment_payload(
        payment_id="yk-abc",
        order_id="VS-TESTORDER1",
        status="succeeded",
        telegram_user_id=42,
    )

    # BackgroundTasks в ASGITransport выполняются после ответа.
    r = await client.post("/api/yookassa/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Фоновая задача может использовать свой SessionLocal — подождём и перечитаем.
    async with SessionLocal() as s:
        # На случай если background ещё не успел — обработаем явно (идемпотентно).
        await handle_yookassa_notification(s, settings, payload)
        row = (
            await s.execute(
                select(Payment)
                .options(selectinload(Payment.client))
                .where(Payment.order_id == "VS-TESTORDER1")
            )
        ).scalar_one()
        assert row.status == "succeeded"
        assert row.paid_at is not None

        seats = (await get_cohort_settings(s, settings)).seats_left
        assert seats == seats_before - 1

        events = (
            await s.execute(select(WebhookEvent).where(WebhookEvent.provider == "yookassa"))
        ).scalars().all()
        assert len(events) >= 1
        assert events[0].processed == 1


@pytest.mark.asyncio
async def test_yookassa_webhook_idempotent(session, settings):
    client_row = Client(name="Боря", telegram_user_id=77)
    session.add(client_row)
    await session.flush()
    session.add(
        Payment(
            client_id=client_row.id,
            order_id="VS-DUP0000001",
            idempotence_key="idem-dup",
            yookassa_payment_id="yk-dup",
            amount_value="65000.00",
            currency="RUB",
            status="pending",
            description="dup",
            product_code="program",
        )
    )
    await session.commit()

    payload = _yk_payment_payload(payment_id="yk-dup", order_id="VS-DUP0000001", telegram_user_id=77)
    await handle_yookassa_notification(session, settings, payload)
    seats_mid = (await get_cohort_settings(session, settings)).seats_left

    await handle_yookassa_notification(session, settings, payload)
    seats_after = (await get_cohort_settings(session, settings)).seats_left
    assert seats_after == seats_mid

    events = (
        await session.execute(select(WebhookEvent).where(WebhookEvent.provider == "yookassa"))
    ).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_yookassa_webhook_canceled(session, settings):
    client_row = Client(name="Вика", telegram_user_id=88)
    session.add(client_row)
    await session.flush()
    session.add(
        Payment(
            client_id=client_row.id,
            order_id="VS-CANCEL0001",
            idempotence_key="idem-cancel",
            yookassa_payment_id="yk-cancel",
            amount_value="65000.00",
            currency="RUB",
            status="pending",
            description="cancel",
            product_code="program",
        )
    )
    await session.commit()

    payload = _yk_payment_payload(
        payment_id="yk-cancel",
        order_id="VS-CANCEL0001",
        status="canceled",
        telegram_user_id=88,
    )
    payload["event"] = "payment.canceled"

    await handle_yookassa_notification(session, settings, payload)
    row = (
        await session.execute(select(Payment).where(Payment.order_id == "VS-CANCEL0001"))
    ).scalar_one()
    assert row.status == "canceled"
    assert row.canceled_at is not None


@pytest.mark.asyncio
async def test_webhook_forbidden_in_production(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    r = await client.post(
        "/api/yookassa/webhook",
        json={"event": "payment.succeeded", "object": {"id": "x"}},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    assert r.status_code == 403
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_order_status_endpoint(client, session):
    c = Client(name="Глеб", telegram_user_id=55)
    session.add(c)
    await session.flush()
    session.add(
        Payment(
            client_id=c.id,
            order_id="VS-STATUS0001",
            idempotence_key="idem-st",
            yookassa_payment_id="yk-st",
            amount_value="65000.00",
            currency="RUB",
            status="pending",
            description="st",
            product_code="program",
            confirmation_url="https://yookassa.ru/st",
        )
    )
    await session.commit()

    with patch("app.main.sync_payment_from_yookassa", new_callable=AsyncMock) as sync:
        sync.side_effect = lambda *a, **k: a[2] if len(a) > 2 else None
        # sync returns payment — patch to no-op by returning existing
        async def _noop(session, settings, payment):
            return payment

        sync.side_effect = _noop
        r = await client.get("/api/orders/VS-STATUS0001")
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "VS-STATUS0001"
    assert data["paid"] is False
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_pay_redirect(client, session):
    c = Client(name="Даша", telegram_user_id=66)
    session.add(c)
    await session.flush()
    session.add(
        Payment(
            client_id=c.id,
            order_id="VS-PAY0000001",
            idempotence_key="idem-pay",
            amount_value="65000.00",
            currency="RUB",
            status="pending",
            description="pay",
            product_code="program",
            confirmation_url="https://yookassa.ru/confirm/xyz",
        )
    )
    await session.commit()

    r = await client.get("/api/pay/VS-PAY0000001", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://yookassa.ru/confirm/xyz"


@pytest.mark.asyncio
async def test_clients_and_payments_list(client, session):
    c = Client(name="Ева", telegram_user_id=99, phone="+7999")
    session.add(c)
    await session.flush()
    session.add(
        Payment(
            client_id=c.id,
            order_id="VS-LIST000001",
            idempotence_key="idem-list",
            amount_value="65000.00",
            currency="RUB",
            status="pending",
            description="list",
            product_code="program",
        )
    )
    await session.commit()

    r = await client.get("/api/clients")
    assert r.status_code == 200
    assert r.json()["count"] >= 1
    assert any(x["telegram_user_id"] == 99 for x in r.json()["clients"])

    r2 = await client.get("/api/payments")
    assert r2.status_code == 200
    assert r2.json()["count"] >= 1
    assert r2.json()["webhook_hint"] == "/api/yookassa/webhook"
