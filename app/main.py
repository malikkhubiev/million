from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, ORJSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db import SessionLocal, get_session, init_db
from app.services.behavior import behavior_report, export_txt, upsert_behavior
from app.services.bot import PollingRunner, handle_bot_update
from app.services.metrika import metrika_cid_for, track_add_to_cart, track_goal
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
    mode = settings.effective_telegram_mode
    if mode != settings.telegram_mode:
        logger.warning(
            "TELEGRAM_MODE=%s игнорируется на проде → используем %s",
            settings.telegram_mode,
            mode,
        )
    if mode == "polling" and settings.telegram_bot_token:
        await polling_runner.start()
    elif mode == "webhook" and settings.telegram_bot_token:
        async with TelegramClient(settings) as tg:
            url = f"{settings.app_base_url.rstrip('/')}{settings.telegram_webhook_path}"
            try:
                await tg.delete_webhook()
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
        "telegram_mode": settings.effective_telegram_mode,
        "telegram_mode_env": settings.telegram_mode,
        "metrika": settings.is_metrika_configured,
        "bot": settings.bot_link,
    }


class BehaviorSectionIn(BaseModel):
    key: str
    label: str | None = None
    reached: int = 0
    time_to_ms: int | None = None
    dwell_ms: int = 0


class IntentIn(BaseModel):
    metrika_client_id: str | None = None
    session_id: str | None = None
    yclid: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    landing_url: str | None = None
    referrer: str | None = None
    page_ms: int | None = None
    sections: list[BehaviorSectionIn] = []


class BehaviorIn(BaseModel):
    session_id: str
    metrika_client_id: str | None = None
    clicked_telegram: int = 0
    bot_started: int = 0
    page_ms: int | None = None
    landing_url: str | None = None
    referrer: str | None = None
    yclid: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    sections: list[BehaviorSectionIn] = []


@app.post("/api/intent")
async def create_tracking_intent(
    body: IntentIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    from app.services.activity_log import log_intent, log_landing_ping

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
        behavior_session_id=(body.session_id or "")[:64] or None,
    )
    if body.session_id:
        visit, newly = await upsert_behavior(
            session,
            session_id=body.session_id[:64],
            metrika_client_id=cid,
            clicked_telegram=1,
            bot_started=0,
            page_ms=body.page_ms,
            landing_url=body.landing_url,
            referrer=body.referrer,
            user_agent=request.headers.get("user-agent"),
            yclid=(body.yclid or "")[:64] or None,
            utm=utm,
            sections=[s.model_dump() for s in body.sections],
        )
        reached_total = sum(1 for s in visit.sections if s.reached)
        log_landing_ping(
            session_id=body.session_id[:64],
            metrika_client_id=cid,
            utm=utm,
            page_ms=body.page_ms,
            newly_reached=newly,
            clicked_telegram=True,
            sections_reached_total=reached_total,
        )
    log_intent(session_id=body.session_id, utm=utm, token=row.token)
    start = f"t{row.token}"
    return {
        "token": row.token,
        "start": start,
        "bot_url": f"{settings.bot_link}?start={start}",
    }


@app.post("/api/behavior")
async def save_behavior(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    from app.services.activity_log import log_landing_ping
    import json as _json

    sid_raw = ""
    try:
        data = _json.loads(await request.body())
        body = BehaviorIn.model_validate(data)
    except Exception as exc:
        raise HTTPException(400, f"bad behavior payload: {exc}") from exc

    sid = (body.session_id or "").strip()[:64]
    if not sid:
        raise HTTPException(400, "session_id required")
    sid_raw = sid
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
    visit, newly = await upsert_behavior(
        session,
        session_id=sid,
        metrika_client_id=body.metrika_client_id,
        clicked_telegram=1 if body.clicked_telegram else 0,
        bot_started=1 if body.bot_started else 0,
        page_ms=body.page_ms,
        landing_url=body.landing_url,
        referrer=body.referrer,
        user_agent=request.headers.get("user-agent"),
        yclid=(body.yclid or "")[:64] or None,
        utm=utm,
        sections=[s.model_dump() for s in body.sections],
    )
    reached_total = sum(1 for s in visit.sections if s.reached)
    if newly or body.clicked_telegram:
        log_landing_ping(
            session_id=sid_raw,
            metrika_client_id=body.metrika_client_id,
            utm=utm,
            page_ms=body.page_ms,
            newly_reached=newly,
            clicked_telegram=bool(body.clicked_telegram),
            sections_reached_total=reached_total,
        )
    return {"ok": True, "session_id": visit.session_id, "newly_reached": newly}


@app.get("/api/behavior/stats")
async def behavior_stats(
    session: AsyncSession = Depends(get_session),
    date_from: str | None = None,
    date_to: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    utm_term: str | None = None,
    bot_started: str | None = None,
    clicked_telegram: str | None = None,
    reached_section: str | None = None,
    has_yclid: str | None = None,
    q: str | None = None,
    group_by: str = "utm_content",
    sort: str = "created_at",
    order: str = "desc",
    include_visits: bool = True,
    limit: int = 500,
):
    return await behavior_report(
        session,
        date_from=date_from,
        date_to=date_to,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        utm_content=utm_content,
        utm_term=utm_term,
        bot_started=bot_started,
        clicked_telegram=clicked_telegram,
        reached_section=reached_section,
        has_yclid=has_yclid,
        q=q,
        group_by=group_by,  # type: ignore[arg-type]
        sort=sort,
        order=order,
        include_visits=include_visits,
        limit=limit,
    )


@app.get("/api/behavior/export.txt")
async def behavior_export_txt(
    session: AsyncSession = Depends(get_session),
    date_from: str | None = None,
    date_to: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    utm_term: str | None = None,
    bot_started: str | None = None,
    clicked_telegram: str | None = None,
    reached_section: str | None = None,
    has_yclid: str | None = None,
    q: str | None = None,
    group_by: str = "utm_content",
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 5000,
):
    report = await behavior_report(
        session,
        date_from=date_from,
        date_to=date_to,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        utm_content=utm_content,
        utm_term=utm_term,
        bot_started=bot_started,
        clicked_telegram=clicked_telegram,
        reached_section=reached_section,
        has_yclid=has_yclid,
        q=q,
        group_by=group_by,  # type: ignore[arg-type]
        sort=sort,
        order=order,
        include_visits=True,
        limit=limit,
    )
    text = export_txt(report)
    filename = f"behavior_{_utcnow_stamp()}.txt"
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _utcnow_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@app.get("/admin/behavior")
async def behavior_admin_page():
    path = Path(__file__).resolve().parent.parent / "static" / "behavior.html"
    if not path.exists():
        raise HTTPException(404, "behavior.html not found")
    return FileResponse(path)


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


@app.get("/api/pay/{order_id}")
async def pay_click_redirect(
    order_id: str,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Клик «Оплатить» в боте → цель payment_started → ЮKassa."""
    payment = await get_payment_by_order(session, order_id.upper())
    if not payment or not payment.confirmation_url:
        raise HTTPException(404, "Ссылка на оплату не найдена")
    if payment.status == "succeeded":
        return RedirectResponse(f"{settings.app_base_url.rstrip('/')}/success.html?order={payment.order_id}")

    client = payment.client
    cid = metrika_cid_for(
        client.metrika_client_id if client else None,
        client.telegram_user_id if client else None,
    )
    from app.services.activity_log import log_payment_started
    from app.services.behavior import mark_behavior_stage

    log_payment_started(
        telegram_user_id=client.telegram_user_id if client else None,
        order_id=payment.order_id,
        product_code=payment.product_code,
        amount=payment.amount_value,
    )
    await mark_behavior_stage(
        session,
        stage="payment_started",
        telegram_user_id=client.telegram_user_id if client else None,
        metrika_client_id=client.metrika_client_id if client else None,
    )
    if cid:
        background.add_task(
            _track_pay_click,
            settings,
            cid,
            payment.order_id,
            payment.product_code or "program",
            payment.amount_value,
        )

    return RedirectResponse(payment.confirmation_url, status_code=302)


async def _track_pay_click(
    settings: Settings,
    cid: str,
    order_id: str,
    product_code: str,
    amount_value: str | None,
) -> None:
    try:
        product = settings.product(product_code or "program")
        amount = float(amount_value or product["amount_value"])
        await track_goal(
            settings,
            cid=cid,
            goal="payment_started",
            value=int(amount),
            params={
                "funnel": "payment_started",
                "order_id": order_id,
                "product_code": product_code or "program",
            },
            path="/bot/pay",
        )
        await track_add_to_cart(
            settings,
            cid=cid,
            amount=amount,
            product_id=str(product["id"]),
            product_name=str(product["title"]),
        )
    except Exception:
        logger.exception("metrika payment_started")


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
async def index(settings: Settings = Depends(get_settings)):
    """Корень API — без редиректа на лендинг."""
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "dashboard": "/admin/behavior",
        "health": "/api/health",
        "site": settings.site_link,
    }


@app.get("/success.html")
async def success_page():
    return FileResponse(SITE / "success.html")


@app.get("/offer.html")
async def offer_page():
    path = SITE / "offer.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404)
