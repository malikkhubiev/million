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
from app.services.telegram import TelegramError
from app.services.yookassa import YooKassaClient, YooKassaError

logger = logging.getLogger(__name__)


def new_order_id() -> str:
    return "VS-" + uuid.uuid4().hex[:10].upper()


def pay_url_for(settings: Settings, payment: Payment) -> str | None:
    if not payment.confirmation_url:
        return None
    if settings.app_env == "production":
        return f"{settings.app_base_url.rstrip('/')}/api/pay/{payment.order_id}"
    return payment.confirmation_url


async def get_or_create_client(
    session: AsyncSession,
    *,
    name: str = "",
    email: str | None = None,
    telegram_user_id: int | None = None,
    telegram_username: str | None = None,
    language_code: str | None = None,
    is_premium: bool | None = None,
    phone: str | None = None,
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
        if phone:
            client.phone = phone
        await session.flush()
        return client

    client = Client(
        name=(name or (telegram_username or "") or "Гость").strip(),
        email=email.strip().lower() if email else None,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        language_code=language_code,
        is_premium=None if is_premium is None else (1 if is_premium else 0),
        phone=phone,
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
    phone: str | None = None,
    source: str = "telegram",
    product_code: str = "program",
) -> Payment:
    product = settings.product(product_code)
    client = await get_or_create_client(
        session,
        name=name,
        email=email,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        phone=phone,
    )

    pending = await session.execute(
        select(Payment)
        .options(selectinload(Payment.client), selectinload(Payment.invites))
        .where(
            Payment.client_id == client.id,
            Payment.product_code == product_code,
            Payment.status.in_(("pending", "waiting_for_capture")),
        )
        .order_by(Payment.id.desc())
    )
    existing = pending.scalars().first()
    if existing and existing.confirmation_url:
        return existing

    order_id = new_order_id()
    idempotence_key = str(uuid.uuid4())
    description = f"{product['title']} · {order_id}"
    payment = Payment(
        client_id=client.id,
        order_id=order_id,
        idempotence_key=idempotence_key,
        amount_value=str(product["amount_value"]),
        currency="RUB",
        status="pending",
        description=description,
        source=source,
        product_code=product_code,
    )
    session.add(payment)
    await session.flush()

    # Без ?start= — просто возвращаемся в чат с ботом; доставка после succeeded.
    return_url = settings.bot_link
    metadata = {
        "order_id": order_id,
        "client_id": str(client.id),
        "source": source,
        "product_code": product_code,
    }
    if telegram_user_id:
        metadata["telegram_user_id"] = str(telegram_user_id)
    if client.email:
        metadata["email"] = client.email
    if client.phone:
        metadata["phone"] = client.phone

    async with YooKassaClient(settings) as yk:
        try:
            data = await yk.create_payment(
                amount_value=str(product["amount_value"]),
                description=description,
                return_url=return_url,
                metadata=metadata,
                customer_email=client.email,
                customer_phone=client.phone,
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
    return await _payment_loaded(session, payment.id)


async def _payment_loaded(session: AsyncSession, payment_id: int) -> Payment:
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.client), selectinload(Payment.invites))
        .where(Payment.id == payment_id)
    )
    return result.scalar_one()


async def latest_succeeded_for_telegram(
    session: AsyncSession,
    telegram_user_id: int,
    product_code: str | None = "program",
) -> Payment | None:
    q = (
        select(Payment)
        .join(Client)
        .options(selectinload(Payment.invites), selectinload(Payment.client))
        .where(Client.telegram_user_id == telegram_user_id, Payment.status == "succeeded")
    )
    if product_code:
        q = q.where(Payment.product_code == product_code)
    result = await session.execute(q.order_by(Payment.id.desc()))
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
    code = payment.product_code or "program"
    if code != "program":
        already = bool(payment.fulfilled_at)
        payment.fulfilled_at = payment.fulfilled_at or datetime.now(timezone.utc)
        await session.commit()
        await deliver_vip(session, settings, payment)
        if not already:
            await _track_payment_success(settings, payment)
        return None

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

    from app.services.telegram import shared_tg

    tg = await shared_tg(settings)
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
    from app.services.activity_log import log_payment_success
    from app.services.behavior import mark_behavior_stage
    from app.db import SessionLocal

    log_payment_success(
        telegram_user_id=client.telegram_user_id if client else None,
        order_id=payment.order_id,
        amount=payment.amount_value,
    )
    try:
        async with SessionLocal() as session:
            await mark_behavior_stage(
                session,
                stage="payment_success",
                telegram_user_id=client.telegram_user_id if client else None,
                metrika_client_id=client.metrika_client_id if client else None,
                event_at=payment.paid_at,
            )
    except Exception:
        logger.exception("behavior mark payment_success")
    cid = metrika_cid_for(client.metrika_client_id if client else None, client.telegram_user_id if client else None)
    if not cid:
        return
    product = settings.product(payment.product_code or "program")
    amount = float(payment.amount_value or product["amount_value"])
    await track_goal(
        settings,
        cid=cid,
        goal="payment_success",
        value=int(amount),
        params={
            "funnel": "payment_success",
            "order_id": payment.order_id,
            "product_code": payment.product_code or "program",
        },
        path="/bot/paid",
    )
    await track_purchase(
        settings,
        cid=cid,
        order_id=payment.order_id,
        amount=amount,
        product_id=str(product["id"]),
        product_name=str(product["title"]),
    )


async def deliver_vip(session: AsyncSession, settings: Settings, payment: Payment) -> None:
    if payment.invite_sent_at:
        return
    client = payment.client
    if not client or not client.telegram_user_id:
        logger.info("Нет telegram_user_id для VIP-доставки %s", payment.order_id)
        return
    code = payment.product_code or "vip_diag"
    markup = None
    if code == "vip_train":
        text = (
            "Оплата персональной Трансформации прошла.\n\n"
            "Напишу тебе, как начинаем эти 14 дней."
        )
    else:
        text = (
            "Диагностический созвон оплачен.\n\n"
            "В течение 24 часов я свяжусь с тобой лично и назначу время.\n"
            "На диагностике разберём твоё текущее состояние, что мешает жить так, "
            "как ты хочешь, и к каким изменениям тебе нужно прийти.\n\n"
            "Если личный формат тебе подойдёт и ты будешь готова продолжить — "
            "следующий шаг 190 000 ₽. Условия оказания услуги и возврата — в оферте."
        )
        if client.phone and client.telegram_user_id:
            try:
                follow = await create_checkout(
                    session,
                    settings,
                    name=client.name or "",
                    telegram_user_id=client.telegram_user_id,
                    telegram_username=client.telegram_username,
                    phone=client.phone,
                    source="telegram",
                    product_code="vip_train",
                )
                url = pay_url_for(settings, follow)
                if url:
                    markup = {
                        "inline_keyboard": [
                            [{"text": "Персональная Трансформация · 190 000 ₽", "url": url}]
                        ]
                    }
            except Exception:
                logger.exception("Не удалось создать оплату 190 000 ₽ после диагностики %s", payment.order_id)
    try:
        from app.services.telegram import shared_tg

        tg = await shared_tg(settings)
        await tg.send_message(client.telegram_user_id, text, reply_markup=markup)
        payment.invite_sent_at = datetime.now(timezone.utc)
        await session.commit()
    except TelegramError:
        logger.exception("Не удалось отправить VIP-сообщение для %s", payment.order_id)


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
        from app.services.telegram import shared_tg

        tg = await shared_tg(settings)
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


async def sync_open_payments(session: AsyncSession, settings: Settings, *, limit: int = 20) -> int:
    """Подтягивает статусы незакрытых платежей из ЮKassa (локально без webhook)."""
    if not settings.is_yookassa_configured:
        return 0
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.client), selectinload(Payment.invites))
        .where(
            Payment.status.in_(("pending", "waiting_for_capture")),
            Payment.yookassa_payment_id.is_not(None),
        )
        .order_by(Payment.id.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    done = 0
    for payment in rows:
        try:
            await sync_payment_from_yookassa(session, settings, payment)
            done += 1
        except Exception:
            logger.exception("sync_open payment %s", payment.order_id)
    return done
