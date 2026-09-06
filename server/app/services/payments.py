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
from app.services.metrika import metrika_cid_for, track_goal, track_purchase
from app.services.telegram import TelegramClient, TelegramError
from app.services.yookassa import YooKassaClient, YooKassaError

logger = logging.getLogger(__name__)


def new_order_id() -> str:
    return "VS-" + uuid.uuid4().hex[:10].upper()


async def get_or_create_client(
    session: AsyncSession,
    *,
    name: str = "",
    email: str | None = None,
    telegram_user_id: int | None = None,
    telegram_username: str | None = None,
    language_code: str | None = None,
    is_premium: bool | None = None,
) -> Client:
    client: Client | None = None
    if telegram_user_id:
        result = await session.execute(select(Client).where(Client.telegram_user_id == telegram_user_id))
        client = result.scalar_one_or_none()
    if not client and email:
        result = await session.execute(select(Client).where(Client.email == email.strip().lower()))
        client = result.scalar_one_or_none()

    if client:
        if name:
            client.name = name
        if email:
            client.email = email.strip().lower()
        if telegram_user_id:
            client.telegram_user_id = telegram_user_id
        if telegram_username:
            client.telegram_username = telegram_username
        if language_code:
            client.language_code = language_code
        if is_premium is not None:
            client.is_premium = 1 if is_premium else 0
        await session.flush()
        return client

    client = Client(
        name=(name or (telegram_username or "") or "Гость").strip(),
        email=email.strip().lower() if email else None,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        language_code=language_code,
        is_premium=None if is_premium is None else (1 if is_premium else 0),
    )
    session.add(client)
    await session.flush()
    return client


async def create_checkout(
    session: AsyncSession,
    settings: Settings,
    *,
    name: str = "",
    email: str | None = None,
    telegram_user_id: int | None = None,
    telegram_username: str | None = None,
    source: str = "telegram",
) -> Payment:
    client = await get_or_create_client(
        session,
        name=name,
        email=email,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
    )

    pending = await session.execute(
        select(Payment)
        .where(
            Payment.client_id == client.id,
            Payment.status.in_(("pending", "waiting_for_capture")),
        )
        .order_by(Payment.id.desc())
    )
    existing = pending.scalars().first()
    if existing and existing.confirmation_url:
        return existing

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
        source=source,
    )
    session.add(payment)
    await session.flush()

    return_url = f"{settings.return_url}?order={order_id}"
    metadata = {
        "order_id": order_id,
        "client_id": str(client.id),
        "source": source,
    }
    if telegram_user_id:
        metadata["telegram_user_id"] = str(telegram_user_id)
    if client.email:
        metadata["email"] = client.email

    async with YooKassaClient(settings) as yk:
        try:
            data = await yk.create_payment(
                amount_value=settings.price_value,
                description=description,
                return_url=return_url,
                metadata=metadata,
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


async def latest_succeeded_for_telegram(session: AsyncSession, telegram_user_id: int) -> Payment | None:
    result = await session.execute(
        select(Payment)
        .join(Client)
        .options(selectinload(Payment.invites), selectinload(Payment.client))
        .where(Client.telegram_user_id == telegram_user_id, Payment.status == "succeeded")
        .order_by(Payment.id.desc())
    )
    return result.scalars().first()


async def mark_webhook_seen(
    session: AsyncSession,
    *,
    provider: str,
    event_key: str,
    event_type: str | None,
    payload: dict,
) -> bool:
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
    result = await session.execute(
        select(InviteLink).where(InviteLink.payment_id == payment.id).order_by(InviteLink.id.desc())
    )
    existing = result.scalars().first()
    if existing:
        payment.fulfilled_at = payment.fulfilled_at or datetime.now(timezone.utc)
        await session.commit()
        await deliver_invite(session, settings, payment, existing)
        return existing

    if not settings.is_telegram_configured:
        logger.warning("Telegram не настроен — invite не создан для %s", payment.order_id)
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
    await deliver_invite(session, settings, payment, invite)
    await _track_payment_success(settings, payment)
    return invite


async def _track_payment_success(settings: Settings, payment: Payment) -> None:
    client = payment.client
    cid = metrika_cid_for(client.metrika_client_id if client else None, client.telegram_user_id if client else None)
    if not cid:
        return
    await track_goal(
        settings,
        cid=cid,
        goal="payment_success",
        params={"funnel": "payment_success", "order_id": payment.order_id},
        path="/bot/paid",
    )
    await track_purchase(settings, cid=cid, order_id=payment.order_id, amount=settings.price_rubles)


async def deliver_invite(
    session: AsyncSession,
    settings: Settings,
    payment: Payment,
    invite: InviteLink,
) -> None:
    if payment.invite_sent_at:
        return
    client = payment.client
    if not client or not client.telegram_user_id:
        logger.info("Нет telegram_user_id для автодоставки %s", payment.order_id)
        return
    text = (
        "Оплата прошла. Ты внутри.\n\n"
        f'<a href="{invite.invite_url}">Открыть закрытый канал</a>\n\n'
        "Ссылка на одного человека. Сохрани её."
    )
    try:
        async with TelegramClient(settings) as tg:
            await tg.send_message(client.telegram_user_id, text)
        payment.invite_sent_at = datetime.now(timezone.utc)
        await session.commit()
    except TelegramError:
        logger.exception("Не удалось отправить invite в Telegram для %s", payment.order_id)


async def apply_yookassa_payment_object(
    session: AsyncSession,
    settings: Settings,
    obj: dict,
) -> Payment | None:
    payment_id = obj.get("id")
    metadata = obj.get("metadata") or {}
    order_id = metadata.get("order_id")
    tg_id = metadata.get("telegram_user_id")

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

    if tg_id and payment.client and not payment.client.telegram_user_id:
        try:
            payment.client.telegram_user_id = int(tg_id)
        except ValueError:
            pass

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
        client = payment.client
        cid = metrika_cid_for(client.metrika_client_id if client else None, client.telegram_user_id if client else None)
        if cid:
            await track_goal(
                settings,
                cid=cid,
                goal="payment_canceled",
                value=0,
                params={"funnel": "payment_canceled", "order_id": payment.order_id},
                path="/bot/canceled",
            )
    else:
        await session.commit()

    await session.refresh(payment)
    return payment


async def handle_yookassa_notification(session: AsyncSession, settings: Settings, payload: dict) -> None:
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


async def sync_payment_from_yookassa(session: AsyncSession, settings: Settings, payment: Payment) -> Payment:
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


async def get_payment_by_order(session: AsyncSession, order_id: str) -> Payment | None:
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.invites), selectinload(Payment.client))
        .where(Payment.order_id == order_id.upper())
    )
    return result.scalar_one_or_none()
