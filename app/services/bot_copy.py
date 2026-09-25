from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

_texts_path = Path(__file__).resolve().parents[2] / "texts.py"
_spec = spec_from_file_location("bot_payment_texts", _texts_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Не найден {_texts_path}")
_texts_mod = module_from_spec(_spec)
_spec.loader.exec_module(_texts_mod)
DEFAULTS: dict[str, dict[str, str]] = _texts_mod.DEFAULTS

# Ключи в app_settings: bot_{life|english}_{field}
BOT_KEYS = ("life", "english")

FIELD_WELCOME = "welcome"
FIELD_AFTER_PHONE = "after_phone"
FIELD_ALREADY_ACCESS = "already_access"
FIELD_INVITE_BEFORE = "invite_before"
FIELD_INVITE_LINK_TEXT = "invite_link_text"
FIELD_INVITE_AFTER = "invite_after"
FIELD_INVITE_BUTTON = "invite_button"

COPY_FIELDS = (
    FIELD_WELCOME,
    FIELD_AFTER_PHONE,
    FIELD_ALREADY_ACCESS,
    FIELD_INVITE_BEFORE,
    FIELD_INVITE_LINK_TEXT,
    FIELD_INVITE_AFTER,
    FIELD_INVITE_BUTTON,
)


@dataclass
class BotCopy:
    welcome: str
    after_phone: str
    already_access: str
    invite_before: str
    invite_link_text: str
    invite_after: str
    invite_button: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> BotCopy:
        return cls(**{f: data[f] for f in COPY_FIELDS})


def _setting_key(bot: str, field: str) -> str:
    return f"bot_{bot}_{field}"


def _defaults_for(bot: str) -> BotCopy:
    key = (bot or "life").strip().lower()
    if key not in DEFAULTS:
        raise ValueError(f"Неизвестный бот: {bot}")
    return BotCopy.from_dict(DEFAULTS[key])


async def _get_value(session: AsyncSession, key: str) -> str | None:
    row = await session.get(AppSetting, key)
    return row.value if row else None


async def _set_value(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


async def ensure_bot_copy_defaults(session: AsyncSession) -> None:
    changed = False
    for bot, defaults in DEFAULTS.items():
        for field, value in defaults.items():
            key = _setting_key(bot, field)
            if await _get_value(session, key) is None:
                await _set_value(session, key, value)
                changed = True
    if changed:
        await session.commit()


async def get_bot_copy(session: AsyncSession, bot: str) -> BotCopy:
    defaults = _defaults_for(bot)
    raw = defaults.as_dict()
    for field in COPY_FIELDS:
        stored = await _get_value(session, _setting_key(bot, field))
        if stored is not None and stored.strip() != "":
            raw[field] = stored
    return BotCopy(**raw)


async def get_all_bot_copy(session: AsyncSession) -> dict[str, dict[str, str]]:
    await ensure_bot_copy_defaults(session)
    return {
        "life": (await get_bot_copy(session, "life")).as_dict(),
        "english": (await get_bot_copy(session, "english")).as_dict(),
    }


async def set_bot_copy(
    session: AsyncSession,
    bot: str,
    *,
    welcome: str,
    after_phone: str,
    already_access: str,
    invite_before: str,
    invite_link_text: str,
    invite_after: str,
    invite_button: str,
) -> BotCopy:
    _defaults_for(bot)
    values = {
        FIELD_WELCOME: (welcome or "").strip(),
        FIELD_AFTER_PHONE: (after_phone or "").strip(),
        FIELD_ALREADY_ACCESS: (already_access or "").strip(),
        FIELD_INVITE_BEFORE: (invite_before or "").strip(),
        FIELD_INVITE_LINK_TEXT: (invite_link_text or "").strip(),
        FIELD_INVITE_AFTER: (invite_after or "").strip(),
        FIELD_INVITE_BUTTON: (invite_button or "").strip() or "Открыть портал",
    }
    for field, value in values.items():
        if field != FIELD_INVITE_BUTTON and not value:
            raise ValueError(f"Поле «{field}» не может быть пустым")
        await _set_value(session, _setting_key(bot, field), value)
    await session.commit()
    return await get_bot_copy(session, bot)


def format_already_access(template: str, *, date: str | None = None) -> str:
    text = template or ""
    if "{date}" in text:
        return text.replace("{date}", date or "скоро")
    return text


def format_invite_message(copy: BotCopy, invite_url: str) -> str:
    return (
        f"{copy.invite_before}\n\n"
        f'<a href="{invite_url}">{copy.invite_link_text}</a>\n\n'
        f"{copy.invite_after}"
    )
