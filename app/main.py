from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import SessionLocal, get_session, init_db
from app.services.bot import PollingRunner, handle_bot_update
from app.services.payments import (
    create_checkout,
    get_payment_by_order,
    handle_yookassa_notification,
    sync_payment_from_yookassa,
)
from app.services.telegram import TelegramClient
from app.services.yookassa import YOOKASSA_WEBHOOK_NETWORKS, YooKassaError

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
polling_runner = PollingRunner()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _ip_allowed(ip: str) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in YOOKASSA_WEBHOOK_NETWORKS:
        try:
            if "/" in net:
                if addr in ipaddress.ip_network(net, strict=False):
                    return True
            elif addr == ipaddress.ip_address(net):
                return True
        except ValueError:
            continue
    return False


class CheckoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    agree: bool = True


class CheckoutOut(BaseModel):
    order_id: str
    confirmation_url: str
    amount: str
    currency: str


class OrderOut(BaseModel):
    order_id: str
    status: str
    amount: str
    currency: str
    paid: bool
    invite_url: str | None = None
    email: str | None = None
    name: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    (ROOT / "data").mkdir(exist_ok=True)
    await init_db()
    settings = get_settings()
    if settings.telegram_mode == "polling" and settings.telegram_bot_token:
        await polling_runner.start()
    elif settings.telegram_mode == "webhook" and settings.telegram_bot_token:
        async with TelegramClient(settings) as tg:
            url = f"{settings.app_base_url.rstrip('/')}{settings.telegram_webhook_path}"
            try:
                await tg.set_webhook(url, settings.telegram_webhook_secret)
                logger.info("Telegram webhook: %s", url)
            except Exception:
                logger.exception("Не удалось установить Telegram webhook")
    yield
    await polling_runner.stop()


app = FastAPI(title="Верни себе себя", default_response_class=ORJSONResponse, lifespan=lifespan)


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)):
    return {
        "ok": True,
        "env": settings.app_env,
        "yookassa": settings.is_yookassa_configured,
        "telegram": settings.is_telegram_configured,
        "telegram_mode": settings.telegram_mode,
    }


@app.post("/api/checkout", response_model=CheckoutOut)
async def checkout(
    body: CheckoutIn,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    if not body.agree:
        raise HTTPException(400, "Нужно согласие с офертой")
    if not settings.is_yookassa_configured:
        raise HTTPException(
            503,
            "Оплата временно недоступна: не заданы ключи ЮKassa (YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY)",
        )
    try:
        payment = await create_checkout(session, settings, name=body.name, email=str(body.email))
    except YooKassaError as exc:
        logger.exception("Ошибка создания платежа")
        raise HTTPException(502, str(exc)) from exc
    if not payment.confirmation_url:
        raise HTTPException(502, "ЮKassa не вернула confirmation_url")
    return CheckoutOut(
        order_id=payment.order_id,
        confirmation_url=payment.confirmation_url,
        amount=payment.amount_value,
        currency=payment.currency,
    )


@app.get("/api/orders/{order_id}", response_model=OrderOut)
async def order_status(
    order_id: str,
    sync: bool = True,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    payment = await get_payment_by_order(session, order_id.upper())
    if not payment:
        raise HTTPException(404, "Заказ не найден")
    if sync and payment.status not in {"succeeded", "canceled"} and payment.yookassa_payment_id:
        try:
            payment = await sync_payment_from_yookassa(session, settings, payment)
        except Exception:
            logger.exception("Синхронизация с ЮKassa не удалась для %s", order_id)

    invite_url = payment.invites[0].invite_url if payment.invites else None
    return OrderOut(
        order_id=payment.order_id,
        status=payment.status,
        amount=payment.amount_value,
        currency=payment.currency,
        paid=payment.status == "succeeded",
        invite_url=invite_url,
        email=payment.client.email if payment.client else None,
        name=payment.client.name if payment.client else None,
    )


@app.post(get_settings().yookassa_webhook_path)
async def yookassa_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    # На проде за reverse-proxy Render IP может отличаться — в development пропускаем проверку
    ip = _client_ip(request)
    if settings.app_env == "production" and not _ip_allowed(ip):
        logger.warning("Отклонён webhook ЮKassa с IP %s", ip)
        raise HTTPException(403, "Forbidden")

    payload = await request.json()

    async def _process() -> None:
        async with SessionLocal() as db:
            await handle_yookassa_notification(db, settings, payload)

    # Быстрый 200: invite + БД в фоне (async httpx), без задержки ответа ЮKassa
    background.add_task(_process)
    return {"ok": True}


@app.post(get_settings().telegram_webhook_path)
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(403, "Bad secret")
    update = await request.json()
    background.add_task(handle_bot_update, settings, update)
    return {"ok": True}


# Статика лендинга
app.mount("/img", StaticFiles(directory=str(ROOT / "img")), name="img")


@app.get("/")
async def index():
    return FileResponse(ROOT / "index.html")


@app.get("/success.html")
async def success_page():
    return FileResponse(ROOT / "success.html")


@app.get("/offer.html")
async def offer_page():
    path = ROOT / "offer.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404)
