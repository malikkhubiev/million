"""API, с которым ходит лендинг: health, dates, intent, behavior."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import BehaviorVisit, TrackingSession


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["yookassa"] is True
    assert "bot" in data


@pytest.mark.asyncio
async def test_api_index(client):
    r = await client.get("/api")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["public_dates"] == "/api/dates"
    assert "/api/yookassa/webhook" in data["yookassa_webhook"]


@pytest.mark.asyncio
async def test_dates_get_and_put(client, session):
    r = await client.get("/api/dates")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["price_rubles"] == 50_000
    assert body["seats_left"] == 20
    assert "countdown_deadline" in body
    assert "pay_button_label" in body

    r2 = await client.put(
        "/api/dates",
        json={
            "enrollment_end": "15.11.2026",
            "transformation_start": "01.12.2026",
            "price_rubles": 45000,
            "seats_left": 7,
        },
    )
    assert r2.status_code == 200
    updated = r2.json()
    assert updated["price_rubles"] == 45_000
    assert updated["seats_left"] == 7
    assert updated["enrollment_end"] == "15.11.2026"
    assert "Осталось 7" in updated["seats_left_label"]

    r3 = await client.get("/api/dates")
    assert r3.json()["seats_left"] == 7
    assert r3.json()["price_rubles"] == 45_000


@pytest.mark.asyncio
async def test_dates_put_rejects_bad_order(client):
    r = await client.put(
        "/api/dates",
        json={
            "enrollment_end": "01.12.2026",
            "transformation_start": "15.11.2026",
            "price_rubles": 50000,
            "seats_left": 10,
        },
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_intent_from_website(client, session):
    """Клик «в Telegram» на сайте → /api/intent → токен для ?start=t…"""
    r = await client.post(
        "/api/intent",
        json={
            "metrika_client_id": "1234567890123456789",
            "session_id": "web-sess-abc",
            "yclid": "yclid-1",
            "utm_source": "yandex",
            "utm_medium": "cpc",
            "utm_campaign": "test_camp",
            "utm_content": "ad1",
            "landing_url": "https://life-energy-phi.vercel.app/",
            "referrer": "https://yandex.ru/",
            "page_ms": 12000,
            "sections": [
                {"key": "headline", "label": "Hero", "reached": 1, "time_to_ms": 100, "dwell_ms": 500}
            ],
        },
        headers={"user-agent": "pytest-agent"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["token"]
    assert data["start"] == f"t{data['token']}"
    assert data["bot_url"].endswith(f"?start=t{data['token']}")

    row = (
        await session.execute(select(TrackingSession).where(TrackingSession.token == data["token"]))
    ).scalar_one()
    assert row.metrika_client_id == "1234567890123456789"
    assert row.yclid == "yclid-1"
    assert row.behavior_session_id == "web-sess-abc"

    visit = (
        await session.execute(
            select(BehaviorVisit)
            .where(BehaviorVisit.session_id == "web-sess-abc")
            .options(selectinload(BehaviorVisit.sections))
        )
    ).scalar_one()
    assert visit.clicked_telegram == 1
    assert visit.utm_source == "yandex"
    assert visit.utm_content == "ad1"


@pytest.mark.asyncio
async def test_behavior_ping(client, session):
    r = await client.post(
        "/api/behavior",
        json={
            "session_id": "behavior-1",
            "metrika_client_id": "999",
            "clicked_telegram": 0,
            "bot_started": 0,
            "page_ms": 5000,
            "utm_source": "direct",
            "sections": [
                {"key": "offer", "reached": 1, "dwell_ms": 2000},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["newly_reached"]

    visit = (
        await session.execute(
            select(BehaviorVisit)
            .where(BehaviorVisit.session_id == "behavior-1")
            .options(selectinload(BehaviorVisit.sections))
        )
    ).scalar_one()
    assert visit.page_ms == 5000
    assert len(visit.sections) >= 1


@pytest.mark.asyncio
async def test_behavior_requires_session_id(client):
    r = await client.post("/api/behavior", json={"page_ms": 1})
    assert r.status_code == 400
