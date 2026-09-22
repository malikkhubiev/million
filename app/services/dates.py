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
KEY_PRICE_RUBLES = "price_rubles"
KEY_SEATS_LEFT = "seats_left"

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
DEFAULT_PRICE_RUBLES = 50_000
DEFAULT_SEATS_LEFT = 20


@dataclass(frozen=True)
class CohortSettings:
    enrollment_end: date
    transformation_start: date
    price_rubles: int
    seats_left: int

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

    @property
    def price_display(self) -> str:
        return format_price_ru(self.price_rubles)

    @property
    def price_amount_value(self) -> str:
        return f"{self.price_rubles:.2f}"

    @property
    def seats_left_label(self) -> str:
        return format_seats_left(self.seats_left)

    @property
    def pay_button_label(self) -> str:
        return f"Оплатить {self.price_display}"

    def as_public_dict(self) -> dict[str, str | int]:
        return {
            "enrollment_end": self.enrollment_end_admin,
            "transformation_start": self.transformation_start_admin,
            "enrollment_end_iso": self.enrollment_end_iso,
            "transformation_start_iso": self.transformation_start_iso,
            "enrollment_end_display": self.enrollment_end_display,
            "transformation_start_display": self.transformation_start_display,
            "countdown_deadline": self.countdown_deadline,
            "meta_line": self.meta_line,
            "price_rubles": self.price_rubles,
            "price_display": self.price_display,
            "price_amount_value": self.price_amount_value,
            "seats_left": self.seats_left,
            "seats_left_label": self.seats_left_label,
            "pay_button_label": self.pay_button_label,
        }


# Обратная совместимость для импортов
CohortDates = CohortSettings


def format_admin(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def format_ru(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]}"


def format_price_ru(rubles: int) -> str:
    s = f"{int(rubles):,}".replace(",", " ")
    return f"{s} ₽"


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not (12 <= n100 <= 14):
        return few
    return many


def format_seats_left(n: int) -> str:
    n = max(0, int(n))
    if n <= 0:
        return "Мест нет"
    word = _plural_ru(n, "место", "места", "мест")
    return f"Осталось {n} {word}"


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


def parse_price_rubles(raw: object, *, field: str = "Цена") -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field}: укажи целое число рублей, например 50000")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(f"{field}: только целые рубли, без копеек")
        value = int(raw)
    else:
        text = str(raw or "").strip().replace("\u00a0", " ")
        text = re.sub(r"[^\d]", "", text)
        if not text:
            raise ValueError(f"{field}: укажи целое число рублей, например 50000")
        value = int(text)
    if value < 1:
        raise ValueError(f"{field}: должна быть больше 0")
    if value > 10_000_000:
        raise ValueError(f"{field}: слишком большая сумма")
    return value


def parse_seats(raw: object, *, field: str = "Места") -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field}: укажи целое число, например 20")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(f"{field}: только целое число")
        value = int(raw)
    else:
        text = str(raw or "").strip().replace("\u00a0", " ")
        text = re.sub(r"[^\d]", "", text)
        if text == "":
            raise ValueError(f"{field}: укажи целое число, например 20")
        value = int(text)
    if value < 0:
        raise ValueError(f"{field}: не может быть отрицательным")
    if value > 10_000:
        raise ValueError(f"{field}: слишком большое число")
    return value


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


def _parse_int_or_none(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def defaults_from_settings(settings: Settings | None = None) -> CohortSettings:
    settings = settings or get_settings()
    enrollment = _parse_iso_or_none(getattr(settings, "enrollment_deadline_iso", None))
    transform = _parse_iso_or_none(getattr(settings, "course_start_date_iso", None))
    price = getattr(settings, "product_price_kopecks", DEFAULT_PRICE_RUBLES * 100) // 100
    if enrollment is None:
        enrollment = DEFAULT_ENROLLMENT_END
    if transform is None:
        transform = DEFAULT_TRANSFORM_START
    if price < 1:
        price = DEFAULT_PRICE_RUBLES
    return CohortSettings(
        enrollment_end=enrollment,
        transformation_start=transform,
        price_rubles=price,
        seats_left=DEFAULT_SEATS_LEFT,
    )


async def _get_value(session: AsyncSession, key: str) -> str | None:
    row = await session.get(AppSetting, key)
    return row.value if row else None


async def _set_value(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


async def ensure_defaults(session: AsyncSession, settings: Settings | None = None) -> CohortSettings:
    """Создаёт записи в БД, если их ещё нет."""
    defaults = defaults_from_settings(settings)
    changed = False
    seeds = {
        KEY_ENROLLMENT_END: defaults.enrollment_end_iso,
        KEY_TRANSFORMATION_START: defaults.transformation_start_iso,
        KEY_PRICE_RUBLES: str(defaults.price_rubles),
        KEY_SEATS_LEFT: str(defaults.seats_left),
    }
    for key, value in seeds.items():
        if await _get_value(session, key) is None:
            await _set_value(session, key, value)
            changed = True
    if changed:
        await session.commit()
    return await get_cohort_settings(session, settings)


async def get_cohort_settings(
    session: AsyncSession,
    settings: Settings | None = None,
) -> CohortSettings:
    defaults = defaults_from_settings(settings)
    enrollment = _parse_iso_or_none(await _get_value(session, KEY_ENROLLMENT_END)) or defaults.enrollment_end
    transform = _parse_iso_or_none(await _get_value(session, KEY_TRANSFORMATION_START)) or defaults.transformation_start
    price = _parse_int_or_none(await _get_value(session, KEY_PRICE_RUBLES))
    seats = _parse_int_or_none(await _get_value(session, KEY_SEATS_LEFT))
    if price is None or price < 1:
        price = defaults.price_rubles
    if seats is None or seats < 0:
        seats = defaults.seats_left
    return CohortSettings(
        enrollment_end=enrollment,
        transformation_start=transform,
        price_rubles=price,
        seats_left=seats,
    )


# Алиасы под старые имена
get_cohort_dates = get_cohort_settings


async def set_cohort_settings(
    session: AsyncSession,
    *,
    enrollment_end: str,
    transformation_start: str,
    price_rubles: object,
    seats_left: object,
) -> CohortSettings:
    end = parse_admin_date(enrollment_end, field="Дата окончания набора")
    start = parse_admin_date(transformation_start, field="Дата начала трансформации")
    if start < end:
        raise ValueError("Дата начала трансформации не может быть раньше окончания набора")
    price = parse_price_rubles(price_rubles)
    seats = parse_seats(seats_left)
    await _set_value(session, KEY_ENROLLMENT_END, end.isoformat())
    await _set_value(session, KEY_TRANSFORMATION_START, start.isoformat())
    await _set_value(session, KEY_PRICE_RUBLES, str(price))
    await _set_value(session, KEY_SEATS_LEFT, str(seats))
    await session.commit()
    return CohortSettings(
        enrollment_end=end,
        transformation_start=start,
        price_rubles=price,
        seats_left=seats,
    )


async def set_cohort_dates(
    session: AsyncSession,
    *,
    enrollment_end: str,
    transformation_start: str,
) -> CohortSettings:
    """Сохранить только даты (цена и места без изменений)."""
    current = await get_cohort_settings(session)
    return await set_cohort_settings(
        session,
        enrollment_end=enrollment_end,
        transformation_start=transformation_start,
        price_rubles=current.price_rubles,
        seats_left=current.seats_left,
    )


async def consume_seat(session: AsyncSession) -> int:
    """Уменьшить число мест на 1 (не ниже 0). Возвращает новое значение."""
    current = await get_cohort_settings(session)
    left = max(0, current.seats_left - 1)
    await _set_value(session, KEY_SEATS_LEFT, str(left))
    await session.flush()
    return left
