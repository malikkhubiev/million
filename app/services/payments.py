from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import Client, InviteLink, Payment, WebhookEvent
from app.services.telegram import TelegramClient, TelegramError
from app.services.yookassa import YooKassaClient, YooKassaError

logger = logging.getLogger(__name__)


def new_order_id() -> str:
    return "VS-" + uuid.uuid4().hex[:10].upper()


async def get_or_create_client(session: AsyncSession, *, name: str, email: str) -> Client:
    email_norm = email.strip().lower()
    result = await session.execute(select(Client).where(Client.email == email_norm))
    client = result.scalar_one_or_none()
    if client:
        if name and client.name != name:
            client.name = name.strip()
        return client
    client = Client(name=name.strip(), email=email_norm)
    session.add(client)
    await session.flush()
    return client


async def create_checkout(
    session: AsyncSession,
    settings: Settings,
    *,
    name: str,
    email: str,
) -> Payment:
    client = await get_or_create_client(session, name=name, email=email)
    order_id = new_order_id()
    idempotence_key = str(uuid.uuid4())
    description = f"{settings.product_title} · {order_id}"

    payment = Payment(
        client_id=client.id,
        order_id=order_id,
        idempotence_key=idempotence_key,
        amount_value=settings.price_value,
        currency="RUB",
        status="pending",
        description=description,
    )
    session.add(payment)
    await session.flush()

    return_url = f"{settings.return_url}?order={order_id}"

    async with YooKassaClient(settings) as yk:
        try:
            data = await yk.create_payment(
                amount_value=settings.price_value,
                description=description,
                return_url=return_url,
                metadata={"order_id": order_id, "client_id": str(client.id), "email": client.email},
                customer_email=client.email,
                idempotence_key=idempotence_key,
            )
        except YooKassaError:
            payment.status = "failed"
            await session.commit()
            raise

    payment.yookassa_payment_id = data.get("id")
    payment.status = data.get("status", "pending")
    payment.confirmation_url = (data.get("confirmation") or {}).get("confirmation_url")
    payment.raw_create_response = json.dumps(data, ensure_ascii=False)
    await session.commit()
    await session.refresh(payment)
    return payment


async def mark_webhook_seen(
    session: AsyncSession,
    *,
    provider: str,
    event_key: str,
    event_type: str | None,
    payload: dict,
) -> bool:
    """Возвращает True, если событие уже обрабатывали."""
    existing = await session.execute(
        select(WebhookEvent).where(
            WebhookEvent.provider == provider,
            WebhookEvent.event_key == event_key,
        )
    )
    if existing.scalar_one_or_none():
        return True
    session.add(
        WebhookEvent(
            provider=provider,
            event_key=event_key,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False),
            processed=0,
        )
    )
    await session.flush()
    return False


async def fulfill_payment(session: AsyncSession, settings: Settings, payment: Payment) -> InviteLink | None:
    """После succeeded — создать одноразовую пригласительную (идемпотентно)."""
    if payment.fulfilled_at and payment.invites:
        return payment.invites[0]

    result = await session.execute(
        select(InviteLink).where(InviteLink.payment_id == payment.id).order_by(InviteLink.id.desc())
    )
    existing = result.scalars().first()
    if existing:
        payment.fulfilled_at = payment.fulfilled_at or datetime.now(timezone.utc)
        await session.commit()
        return existing

    if not settings.is_telegram_configured:
        logger.warning("Telegram не настроен — пригласительная не создана для %s", payment.order_id)
        return None

    async with TelegramClient(settings) as tg:
        try:
            link_data = await tg.create_invite_link(
                name=payment.order_id[:32],
                member_limit=settings.telegram_invite_member_limit,
                expire_days=settings.telegram_invite_expire_days,
            )
        except TelegramError:
            logger.exception("Не удалось создать invite для %s", payment.order_id)
            raise

    expire_raw = link_data.get("_expire_at")
    expire_at = datetime.fromisoformat(expire_raw) if expire_raw else None
    invite = InviteLink(
        payment_id=payment.id,
        client_id=payment.client_id,
        invite_url=link_data["invite_link"],
        name=link_data.get("name") or payment.order_id[:32],
        member_limit=link_data.get("member_limit") or settings.telegram_invite_member_limit,
        expire_at=expire_at,
    )
    session.add(invite)
    payment.fulfilled_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(invite)
    return invite


async def apply_yookassa_payment_object(
    session: AsyncSession,
    settings: Settings,
    obj: dict,
) -> Payment | None:
    payment_id = obj.get("id")
    metadata = obj.get("metadata") or {}
    order_id = metadata.get("order_id")

    q = select(Payment).options(selectinload(Payment.invites), selectinload(Payment.client))
    if payment_id:
        q = q.where(Payment.yookassa_payment_id == payment_id)
    elif order_id:
        q = q.where(Payment.order_id == order_id)
    else:
        return None

    result = await session.execute(q)
    payment = result.scalar_one_or_none()
    if not payment and order_id:
        result = await session.execute(
            select(Payment)
            .options(selectinload(Payment.invites), selectinload(Payment.client))
            .where(Payment.order_id == order_id)
        )
        payment = result.scalar_one_or_none()
    if not payment:
        logger.warning("Платёж не найден для объекта ЮKassa: %s", payment_id)
        return None

    payment.raw_last_event = json.dumps(obj, ensure_ascii=False)
    status = obj.get("status") or payment.status
    payment.status = status
    if not payment.yookassa_payment_id and payment_id:
        payment.yookassa_payment_id = payment_id

    if status == "succeeded":
        payment.paid_at = payment.paid_at or datetime.now(timezone.utc)
        await session.commit()
        await fulfill_payment(session, settings, payment)
    elif status == "canceled":
        payment.canceled_at = payment.canceled_at or datetime.now(timezone.utc)
        await session.commit()
    else:
        await session.commit()

    await session.refresh(payment)
    return payment


async def handle_yookassa_notification(
    session: AsyncSession,
    settings: Settings,
    payload: dict,
) -> None:
    event = payload.get("event") or "unknown"
    obj = payload.get("object") or {}
    payment_id = obj.get("id") or "none"
    event_key = f"{event}:{payment_id}:{obj.get('status', '')}"

    already = await mark_webhook_seen(
        session,
        provider="yookassa",
        event_key=event_key,
        event_type=event,
        payload=payload,
    )
    if already:
        logger.info("Дубликат вебхука ЮKassa пропущен: %s", event_key)
        return

    if event in {"payment.succeeded", "payment.waiting_for_capture", "payment.canceled"}:
        await apply_yookassa_payment_object(session, settings, obj)

    result = await session.execute(
        select(WebhookEvent).where(
            WebhookEvent.provider == "yookassa",
            WebhookEvent.event_key == event_key,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.processed = 1
        row.processed_at = datetime.now(timezone.utc)
        await session.commit()


async def sync_payment_from_yookassa(
    session: AsyncSession,
    settings: Settings,
    payment: Payment,
) -> Payment:
    if not payment.yookassa_payment_id or not settings.is_yookassa_configured:
        return payment
    async with YooKassaClient(settings) as yk:
        data = await yk.get_payment(payment.yookassa_payment_id)
    await apply_yookassa_payment_object(session, settings, data)
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.invites), selectinload(Payment.client))
        .where(Payment.id == payment.id)
    )
    return result.scalar_one()


async def get_payment_by_order(
    session: AsyncSession,
    order_id: str,
) -> Payment | None:
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.invites), selectinload(Payment.client))
        .where(Payment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def find_paid_invite_for_email(session: AsyncSession, email: str) -> InviteLink | None:
    email_norm = email.strip().lower()
    result = await session.execute(
        select(InviteLink)
        .join(Payment)
        .join(Client)
        .where(Client.email == email_norm, Payment.status == "succeeded")
        .order_by(InviteLink.id.desc())
    )
    return result.scalars().first()


async def find_paid_invite_for_order(session: AsyncSession, order_id: str) -> InviteLink | None:
    result = await session.execute(
        select(InviteLink)
        .join(Payment)
        .where(Payment.order_id == order_id.upper(), Payment.status == "succeeded")
        .order_by(InviteLink.id.desc())
    )
    return result.scalars().first()
