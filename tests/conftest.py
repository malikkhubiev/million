"""Общие фикстуры: изолированная SQLite, тестовые настройки, ASGI-клиент."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# До импорта app — чтобы .env не тащил прод-токены в lifespan/polling.
os.environ["APP_ENV"] = "development"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHANNEL_ID"] = ""
os.environ["TELEGRAM_MODE"] = "webhook"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-tg-secret"
os.environ["YOOKASSA_SHOP_ID"] = "test_shop"
os.environ["YOOKASSA_SECRET_KEY"] = "test_secret"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["PRODUCT_PRICE_KOPECKS"] = "6500000"
os.environ["METRIKA_MP_TOKEN"] = ""
os.environ["CORS_ORIGINS"] = "*"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)

    from app.config import get_settings

    get_settings.cache_clear()

    from app import db as db_mod
    from app.models import Base
    from app.services.bot_copy import ensure_bot_copy_defaults
    from app.services.dates import ensure_defaults

    engine = create_async_engine(url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    db_mod.engine = engine
    db_mod.SessionLocal = SessionLocal
    db_mod.settings = get_settings()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(db_mod._migrate_schema)

    settings = get_settings()
    async with SessionLocal() as session:
        await ensure_defaults(session, settings)
        await ensure_bot_copy_defaults(session)

    yield engine, SessionLocal, settings

    await engine.dispose()
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session(db_engine) -> AsyncIterator[AsyncSession]:
    _, SessionLocal, _ = db_engine
    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент к FastAPI без lifespan (БД уже поднята в db_engine)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def settings(db_engine):
    return db_engine[2]
