from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

CLIENT_COLUMNS = {
    "language_code": "VARCHAR(16)",
    "is_premium": "INTEGER",
    "metrika_client_id": "VARCHAR(32)",
    "yclid": "VARCHAR(64)",
    "utm_json": "TEXT",
    "landing_url": "TEXT",
    "referrer": "TEXT",
    "user_agent": "TEXT",
}


def _migrate_client_columns(sync_conn) -> None:
    insp = inspect(sync_conn)
    if "clients" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("clients")}
    for name, coltype in CLIENT_COLUMNS.items():
        if name not in existing:
            sync_conn.execute(text(f"ALTER TABLE clients ADD COLUMN {name} {coltype}"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_client_columns)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
