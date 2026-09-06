from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Верни себе себя"
    app_env: str = "development"
    app_base_url: str = "http://127.0.0.1:8000"
    secret_key: str = "dev-secret-change-me"
    cors_origins: str = "*"

    product_price_kopecks: int = 5_000_000
    product_title: str = "Программа «Верни себе себя»"

    database_url: str = f"sqlite+aiosqlite:///{(ROOT_DIR / 'data' / 'app.db').as_posix()}"

    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_capture: bool = True
    yookassa_vat_code: int | None = None
    yookassa_tax_system_code: int | None = None
    yookassa_webhook_path: str = "/api/yookassa/webhook"

    @field_validator("yookassa_vat_code", "yookassa_tax_system_code", mode="before")
    @classmethod
    def empty_int_to_none(cls, v: object) -> object:
        if v == "" or v is None:
            return None
        return v

    telegram_bot_token: str = ""
    telegram_bot_username: str = "teacher_life_bot"
    telegram_channel_id: str = ""
    telegram_invite_expire_days: int = 30
    telegram_invite_member_limit: int = 1
    telegram_mode: str = "polling"
    telegram_webhook_secret: str = "change-me"
    telegram_webhook_path: str = "/api/telegram/webhook"

    site_dir: str = ""

    metrika_counter_id: int = 112323537
    metrika_mp_token: str = ""
    metrika_collect_url: str = "https://mc.yandex.ru/collect"

    @property
    def is_metrika_configured(self) -> bool:
        return bool(self.metrika_counter_id and self.metrika_mp_token)

    @property
    def site_path(self) -> Path:
        if self.site_dir:
            return Path(self.site_dir)
        sibling = REPO_ROOT / "site"
        if sibling.exists():
            return sibling
        return ROOT_DIR / "static"

    @property
    def price_rubles(self) -> float:
        return self.product_price_kopecks / 100

    @property
    def price_value(self) -> str:
        return f"{self.price_rubles:.2f}"

    @property
    def is_yookassa_configured(self) -> bool:
        return bool(self.yookassa_shop_id and self.yookassa_secret_key)

    @property
    def is_telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_channel_id)

    @property
    def return_url(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/success.html"

    @property
    def bot_link(self) -> str:
        return f"https://t.me/{self.telegram_bot_username}"

    @property
    def cors_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
