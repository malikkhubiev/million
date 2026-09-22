from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import AppSetting

MOSCOW = ZoneInfo("Europe/Moscow")

KEY_ENROLLMENT_END = "enrollment_end_date"
KEY_TRANSFORMATION_START = "transformation_start_date"

# Формат ввода в админке: 20.08.2026
ADMIN_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")

MONTHS_RU = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

DEFAULT_ENROLLMENT_END = date(2026, 10, 10)
DEFAULT_TRANSFORM_START = date(2026, 10, 20)


@dataclass(frozen=True)
class CohortDates:
    enrollment_end: date
    transformation_start: date

    @property
    def enrollment_end_admin(self) -> str:
        return format_admin(self.enrollment_end)

    @property
    def transformation_start_admin(self) -> str:
        return format_admin(self.transformation_start)

    @property
    def enrollment_end_display(self) -> str:
        return format_ru(self.enrollment_end)

    @property
    def transformation_start_display(self) -> str:
        return format_ru(self.transformation_start)

    @property
    def enrollment_end_iso(self) -> str:
        return self.enrollment_end.isoformat()

    @property
    def transformation_start_iso(self) -> str:
        return self.transformation_start.isoformat()

    @property
    def countdown_deadline(self) -> str:
        """Конец дня набора по Москве — для таймера на сайте."""
        end = datetime(
            self.enrollment_end.year,
            self.enrollment_end.month,
            self.enrollment_end.day,
            23,
            59,
            59,
            tzinfo=MOSCOW,
        )
        return end.isoformat()

    @property
    def meta_line(self) -> str:
        return (
            f"Набор до {self.enrollment_end_display} · "
            f"старт {self.transformation_start_display}"
        )

    def as_public_dict(self) -> dict[str, str]:
        return {
            "enrollment_end": self.enrollment_end_admin,
            "transformation_start": self.transformation_start_admin,
            "enrollment_end_iso": self.enrollment_end_iso,
            "transformation_start_iso": self.transformation_start_iso,
            "enrollment_end_display": self.enrollment_end_display,
            "transformation_start_display": self.transformation_start_display,
            "countdown_deadline": self.countdown_deadline,
            "meta_line": self.meta_line,
        }


def format_admin(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def format_ru(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]}"


def parse_admin_date(raw: str, *, field: str) -> date:
    text = (raw or "").strip()
    m = ADMIN_DATE_RE.match(text)
    if not m:
        raise ValueError(f"{field}: укажи дату в формате ДД.ММ.ГГГГ, например 20.08.2026")
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"{field}: некорректная дата {text}") from exc


def _parse_iso_or_none(raw: str | None) -> date | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parse_admin_date(text, field="date")
    except ValueError:
        return None


def defaults_from_settings(settings: Settings | None = None) -> CohortDates:
    settings = settings or get_settings()
    enrollment = _parse_iso_or_none(getattr(settings, "enrollment_deadline_iso", None))
    transform = _parse_iso_or_none(getattr(settings, "course_start_date_iso", None))
    if enrollment is None:
        enrollment = DEFAULT_ENROLLMENT_END
    if transform is None:
        transform = DEFAULT_TRANSFORM_START
    return CohortDates(enrollment_end=enrollment, transformation_start=transform)


async def _get_value(session: AsyncSession, key: str) -> str | None:
    row = await session.get(AppSetting, key)
    return row.value if row else None


async def _set_value(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


async def ensure_defaults(session: AsyncSession, settings: Settings | None = None) -> CohortDates:
    """Создаёт записи в БД, если их ещё нет."""
    defaults = defaults_from_settings(settings)
    changed = False
    if await _get_value(session, KEY_ENROLLMENT_END) is None:
        await _set_value(session, KEY_ENROLLMENT_END, defaults.enrollment_end_iso)
        changed = True
    if await _get_value(session, KEY_TRANSFORMATION_START) is None:
        await _set_value(session, KEY_TRANSFORMATION_START, defaults.transformation_start_iso)
        changed = True
    if changed:
        await session.commit()
    return await get_cohort_dates(session, settings)


async def get_cohort_dates(
    session: AsyncSession,
    settings: Settings | None = None,
) -> CohortDates:
    defaults = defaults_from_settings(settings)
    enrollment = _parse_iso_or_none(await _get_value(session, KEY_ENROLLMENT_END)) or defaults.enrollment_end
    transform = _parse_iso_or_none(await _get_value(session, KEY_TRANSFORMATION_START)) or defaults.transformation_start
    return CohortDates(enrollment_end=enrollment, transformation_start=transform)


async def set_cohort_dates(
    session: AsyncSession,
    *,
    enrollment_end: str,
    transformation_start: str,
) -> CohortDates:
    end = parse_admin_date(enrollment_end, field="Дата окончания набора")
    start = parse_admin_date(transformation_start, field="Дата начала трансформации")
    if start < end:
        raise ValueError("Дата начала трансформации не может быть раньше окончания набора")
    await _set_value(session, KEY_ENROLLMENT_END, end.isoformat())
    await _set_value(session, KEY_TRANSFORMATION_START, start.isoformat())
    await session.commit()
    return CohortDates(enrollment_end=end, transformation_start=start)