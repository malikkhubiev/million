from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, TrackingSession


async def create_intent(
    session: AsyncSession,
    *,
    metrika_client_id: str | None,
    yclid: str | None,
    utm: dict,
    landing_url: str | None,
    referrer: str | None,
    user_agent: str | None,
) -> TrackingSession:
    token = secrets.token_hex(4)
    row = TrackingSession(
        token=token,
        metrika_client_id=metrika_client_id or None,
        yclid=yclid or None,
        utm_json=json.dumps(utm, ensure_ascii=False) if utm else None,
        landing_url=landing_url,
        referrer=referrer,
        user_agent=user_agent,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def consume_intent(session: AsyncSession, token: str) -> TrackingSession | None:
    result = await session.execute(select(TrackingSession).where(TrackingSession.token == token))
    row = result.scalar_one_or_none()
    if not row:
        return None
    if not row.consumed_at:
        row.consumed_at = datetime.now(timezone.utc)
    return row


def apply_tracking_to_client(client: Client, intent: TrackingSession | None, *, metrika_cid: str | None = None) -> None:
    cid = metrika_cid or (intent.metrika_client_id if intent else None)
    if cid:
        client.metrika_client_id = cid
    if intent:
        if intent.yclid:
            client.yclid = intent.yclid
        if intent.utm_json:
            client.utm_json = intent.utm_json
        if intent.landing_url:
            client.landing_url = intent.landing_url
        if intent.referrer:
            client.referrer = intent.referrer
        if intent.user_agent:
            client.user_agent = intent.user_agent
        intent.telegram_user_id = client.telegram_user_id
