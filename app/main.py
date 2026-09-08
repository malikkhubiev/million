from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db import SessionLocal, get_session, init_db
from app.services.bot import PollingRunner, handle_bot_update
from app.services.payments import (
    get_payment_by_order,
    handle_yookassa_notification,
    sync_payment_from_yookassa,
)
from app.services.telegram import TelegramClient
from app.services.tracking import create_intent
from app.services.yookassa import YOOKASSA_WEBHOOK_NETWORKS

logger = logging.getLogger(__name__)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    (Path(__file__).resolve().parent.parent / "data").mkdir(exist_ok=True)
    await init_db()
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
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)):
    return {
        "ok": True,
        "env": settings.app_env,
        "yookassa": settings.is_yookassa_configured,
        "telegram": settings.is_telegram_configured,
        "telegram_mode": settings.telegram_mode,
        "metrika": settings.is_metrika_configured,
        "bot": settings.bot_link,
    }


class IntentIn(BaseModel):
    metrika_client_id: str | None = None
    yclid: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    landing_url: str | None = None
    referrer: str | None = None


@app.post("/api/intent")
async def create_tracking_intent(
    body: IntentIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    utm = {
        k: v
        for k, v in {
            "utm_source": body.utm_source,
            "utm_medium": body.utm_medium,
            "utm_campaign": body.utm_campaign,
            "utm_content": body.utm_content,
            "utm_term": body.utm_term,
        }.items()
        if v
    }
    cid = "".join(ch for ch in (body.metrika_client_id or "") if ch.isdigit()) or None
    row = await create_intent(
        session,
        metrika_client_id=cid,
        yclid=(body.yclid or "")[:64] or None,
        utm=utm,
        landing_url=body.landing_url,
        referrer=body.referrer,
        user_agent=request.headers.get("user-agent"),
    )
    start = f"t{row.token}"
    return {
        "token": row.token,
        "start": start,
        "bot_url": f"{settings.bot_link}?start={start}",
    }


@app.get("/api/orders/{order_id}")
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
    return {
        "order_id": payment.order_id,
        "status": payment.status,
        "paid": payment.status == "succeeded",
        "invite_url": invite_url,
        "bot": settings.bot_link,
    }


@app.post(_settings.yookassa_webhook_path)
async def yookassa_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    ip = _client_ip(request)
    if settings.app_env == "production" and not _ip_allowed(ip):
        logger.warning("Отклонён webhook ЮKassa с IP %s", ip)
        raise HTTPException(403, "Forbidden")

    payload = await request.json()

    async def _process() -> None:
        async with SessionLocal() as db:
            await handle_yookassa_notification(db, settings, payload)

    background.add_task(_process)
    return {"ok": True}


@app.post(_settings.telegram_webhook_path)
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


SITE = _settings.site_path
if (SITE / "img").exists():
    app.mount("/img", StaticFiles(directory=str(SITE / "img")), name="img")


@app.get("/")
async def index():
    path = SITE / "index.html"
    if not path.exists():
        raise HTTPException(404, "Сайт не найден")
    return FileResponse(path)


@app.get("/success.html")
async def success_page():
    return FileResponse(SITE / "success.html")


@app.get("/offer.html")
async def offer_page():
    path = SITE / "offer.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404)
