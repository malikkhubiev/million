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

PAYMENT_COLUMNS = {
    "source": "VARCHAR(32) DEFAULT 'telegram'",
    "invite_sent_at": "DATETIME",
    "paid_at": "DATETIME",
    "canceled_at": "DATETIME",
    "fulfilled_at": "DATETIME",
    "raw_create_response": "TEXT",
    "raw_last_event": "TEXT",
}


def _add_missing_columns(sync_conn, table: str, columns: dict[str, str]) -> None:
    insp = inspect(sync_conn)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    for name, coltype in columns.items():
        if name not in existing:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))


def _migrate_clients_email_nullable(sync_conn) -> None:
    """Старые БД держали email NOT NULL — бот создаёт клиента без почты."""
    insp = inspect(sync_conn)
    if "clients" not in insp.get_table_names():
        return
    rows = sync_conn.execute(text("PRAGMA table_info(clients)")).fetchall()
    email_notnull = any(row[1] == "email" and int(row[3]) == 1 for row in rows)
    if not email_notnull:
        return

    existing_names = {r[1] for r in rows}
    select_cols = [
        "id",
        "name",
        "email",
        "telegram_user_id",
        "telegram_username",
        "language_code" if "language_code" in existing_names else "NULL AS language_code",
        "is_premium" if "is_premium" in existing_names else "NULL AS is_premium",
        "metrika_client_id" if "metrika_client_id" in existing_names else "NULL AS metrika_client_id",
        "yclid" if "yclid" in existing_names else "NULL AS yclid",
        "utm_json" if "utm_json" in existing_names else "NULL AS utm_json",
        "landing_url" if "landing_url" in existing_names else "NULL AS landing_url",
        "referrer" if "referrer" in existing_names else "NULL AS referrer",
        "user_agent" if "user_agent" in existing_names else "NULL AS user_agent",
        "phone",
        "notes",
        "created_at",
        "updated_at",
    ]
    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    sync_conn.execute(
        text(
            """
            CREATE TABLE clients_new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                email VARCHAR(320),
                telegram_user_id INTEGER,
                telegram_username VARCHAR(120),
                language_code VARCHAR(16),
                is_premium INTEGER,
                metrika_client_id VARCHAR(32),
                yclid VARCHAR(64),
                utm_json TEXT,
                landing_url TEXT,
                referrer TEXT,
                user_agent TEXT,
                phone VARCHAR(40),
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
    )
    sync_conn.execute(
        text(
            f"""
            INSERT INTO clients_new (
                id, name, email, telegram_user_id, telegram_username,
                language_code, is_premium, metrika_client_id, yclid, utm_json,
                landing_url, referrer, user_agent, phone, notes, created_at, updated_at
            )
            SELECT {", ".join(select_cols)} FROM clients
            """
        )
    )
    sync_conn.execute(text("DROP TABLE clients"))
    sync_conn.execute(text("ALTER TABLE clients_new RENAME TO clients"))
    sync_conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_clients_telegram_user_id ON clients (telegram_user_id)"))
    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clients_email ON clients (email)"))
    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clients_metrika_client_id ON clients (metrika_client_id)"))
    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clients_yclid ON clients (yclid)"))
    sync_conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_schema(sync_conn) -> None:
    _add_missing_columns(sync_conn, "clients", CLIENT_COLUMNS)
    _add_missing_columns(sync_conn, "payments", PAYMENT_COLUMNS)
    _migrate_clients_email_nullable(sync_conn)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
