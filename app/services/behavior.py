from __future__ import annotations

import statistics
from datetime import date, datetime, time, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import BehaviorVisit


SECTION_ORDER = [
    "top",
    "one",
    "feel",
    "recognize",
    "manifesto",
    "method",
    "author",
    "results",
    "inside",
    "not_for",
    "purchase",
]

SECTION_LABELS = {
    "top": "Верни себе себя",
    "one": "Тебе не нужно становиться другой",
    "feel": "Тобой управляет состояние",
    "recognize": "Ты узнаешь себя?",
    "manifesto": "Когда ты управляешь состоянием",
    "method": "Управление состоянием",
    "author": "Малик Хубиев",
    "results": "До и после",
    "inside": "Голос. Практика. Опыт",
    "not_for": "Кому не подходит",
    "purchase": "Прикоснись к себе настоящей",
}

GroupBy = Literal[
    "none",
    "utm_content",
    "utm_campaign",
    "utm_source",
    "utm_medium",
    "utm_term",
    "bot_started",
    "clicked_telegram",
    "day",
]


def _clean_cid(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_day(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return _as_utc(dt)
        d = date.fromisoformat(raw[:10])
        return datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc)
    except ValueError:
        return None


async def upsert_behavior(
    session: AsyncSession,
    *,
    session_id: str,
    metrika_client_id: str | None,
    clicked_telegram: int,
    bot_started: int,
    page_ms: int | None,
    landing_url: str | None,
    referrer: str | None,
    user_agent: str | None,
    yclid: str | None,
    utm: dict[str, str],
    sections: list[dict[str, Any]],
) -> tuple[BehaviorVisit, list[str]]:
    """Возвращает (visit, ключи секций, до которых дошли впервые в этом апдейте)."""
    result = await session.execute(
        select(BehaviorVisit)
        .where(BehaviorVisit.session_id == session_id)
        .options(selectinload(BehaviorVisit.sections))
    )
    visit = result.scalar_one_or_none()
    if visit is None:
        visit = BehaviorVisit(session_id=session_id)
        session.add(visit)

    cid = _clean_cid(metrika_client_id)
    if cid:
        visit.metrika_client_id = cid
    if yclid:
        visit.yclid = yclid[:64]
    visit.utm_source = (utm.get("utm_source") or visit.utm_source or "")[:64] or None
    visit.utm_medium = (utm.get("utm_medium") or visit.utm_medium or "")[:64] or None
    visit.utm_campaign = (utm.get("utm_campaign") or visit.utm_campaign or "")[:128] or None
    visit.utm_content = (utm.get("utm_content") or visit.utm_content or "")[:128] or None
    visit.utm_term = (utm.get("utm_term") or visit.utm_term or "")[:256] or None
    if landing_url:
        visit.landing_url = landing_url
    if referrer:
        visit.referrer = referrer
    if user_agent:
        visit.user_agent = user_agent[:512]
    if page_ms is not None:
        visit.page_ms = max(visit.page_ms or 0, int(page_ms))
    if clicked_telegram:
        visit.clicked_telegram = 1
    if bot_started:
        visit.bot_started = 1
    visit.updated_at = _utcnow()

    by_key = {row.key: row for row in visit.sections}
    newly_reached: list[str] = []
    for item in sections:
        key = str(item.get("key") or "").strip()[:64]
        if not key:
            continue
        row = by_key.get(key)
        if row is None:
            from app.models import BehaviorSection

            row = BehaviorSection(visit=visit, key=key)
            session.add(row)
            by_key[key] = row
        label = str(item.get("label") or "")[:200]
        if label:
            row.label = label
        reached = 1 if int(item.get("reached") or 0) else 0
        was_reached = bool(row.reached)
        if reached:
            row.reached = 1
            if not was_reached:
                newly_reached.append(key)
        tt = item.get("time_to_ms")
        if tt is not None and row.time_to_ms is None:
            row.time_to_ms = max(0, int(tt))
        dwell = item.get("dwell_ms")
        if dwell is not None:
            row.dwell_ms = max(row.dwell_ms or 0, max(0, int(dwell)))

    # Стабильный порядок воронки
    order_idx = {k: i for i, k in enumerate(SECTION_ORDER)}
    newly_reached.sort(key=lambda k: order_idx.get(k, 999))

    await session.commit()
    await session.refresh(visit)
    return visit, newly_reached


async def mark_bot_started(
    session: AsyncSession,
    *,
    metrika_client_id: str | None = None,
    session_id: str | None = None,
    telegram_user_id: int | None = None,
) -> int:
    visits: list[BehaviorVisit] = []
    cid = _clean_cid(metrika_client_id)
    if session_id:
        result = await session.execute(select(BehaviorVisit).where(BehaviorVisit.session_id == session_id))
        row = result.scalar_one_or_none()
        if row:
            visits.append(row)
    if cid:
        result = await session.execute(
            select(BehaviorVisit)
            .where(BehaviorVisit.metrika_client_id == cid)
            .order_by(BehaviorVisit.updated_at.desc())
            .limit(5)
        )
        visits.extend(result.scalars().all())

    seen: set[int] = set()
    updated = 0
    for visit in visits:
        if visit.id in seen:
            continue
        seen.add(visit.id)
        visit.bot_started = 1
        visit.clicked_telegram = 1
        if telegram_user_id:
            visit.telegram_user_id = telegram_user_id
        visit.updated_at = _utcnow()
        updated += 1
    if updated:
        await session.commit()
    return updated


def _metric(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "avg": None, "median": None, "min": None, "max": None, "sum": None}
    total = sum(values)
    return {
        "n": len(values),
        "avg": round(total / len(values), 2),
        "median": round(float(statistics.median(values)), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "sum": round(total, 2),
    }


def _fmt_metric(m: dict[str, Any], unit: str = "с") -> str:
    if not m or not m.get("n"):
        return "—"
    return (
        f"n={m['n']} avg={m['avg']}{unit} median={m['median']}{unit} "
        f"min={m['min']}{unit} max={m['max']}{unit}"
    )


def dump_visit(visit: BehaviorVisit) -> dict[str, Any]:
    created = _as_utc(visit.created_at)
    updated = _as_utc(visit.updated_at)
    sections = []
    for key in SECTION_ORDER:
        row = next((s for s in visit.sections if s.key == key), None)
        sections.append(
            {
                "key": key,
                "label": (row.label if row and row.label else SECTION_LABELS.get(key, key)),
                "reached": bool(row and row.reached),
                "time_to_ms": row.time_to_ms if row else None,
                "time_to_sec": round(row.time_to_ms / 1000, 2) if row and row.time_to_ms is not None else None,
                "dwell_ms": row.dwell_ms if row else 0,
                "dwell_sec": round((row.dwell_ms or 0) / 1000, 2) if row else 0,
            }
        )
    for row in visit.sections:
        if row.key in SECTION_ORDER:
            continue
        sections.append(
            {
                "key": row.key,
                "label": row.label or row.key,
                "reached": bool(row.reached),
                "time_to_ms": row.time_to_ms,
                "time_to_sec": round(row.time_to_ms / 1000, 2) if row.time_to_ms is not None else None,
                "dwell_ms": row.dwell_ms or 0,
                "dwell_sec": round((row.dwell_ms or 0) / 1000, 2),
            }
        )
    return {
        "id": visit.id,
        "session_id": visit.session_id,
        "created_at": created.isoformat() if created else None,
        "updated_at": updated.isoformat() if updated else None,
        "metrika_client_id": visit.metrika_client_id,
        "telegram_user_id": visit.telegram_user_id,
        "yclid": visit.yclid,
        "utm_source": visit.utm_source,
        "utm_medium": visit.utm_medium,
        "utm_campaign": visit.utm_campaign,
        "utm_content": visit.utm_content,
        "utm_term": visit.utm_term,
        "clicked_telegram": bool(visit.clicked_telegram),
        "bot_started": bool(visit.bot_started),
        "page_ms": visit.page_ms,
        "page_sec": round((visit.page_ms or 0) / 1000, 2) if visit.page_ms is not None else None,
        "landing_url": visit.landing_url,
        "referrer": visit.referrer,
        "sections": sections,
    }


def _group_stats(visits: list[BehaviorVisit]) -> dict[str, Any]:
    total = len(visits)
    out_sections: list[dict[str, Any]] = []
    for key in SECTION_ORDER:
        reached_flags: list[int] = []
        time_to: list[float] = []
        dwell: list[float] = []
        label = SECTION_LABELS.get(key, key)
        for visit in visits:
            row = next((s for s in visit.sections if s.key == key), None)
            if row and row.label:
                label = row.label
            if row and row.reached:
                reached_flags.append(1)
                if row.time_to_ms is not None:
                    time_to.append(row.time_to_ms / 1000)
                dwell.append((row.dwell_ms or 0) / 1000)
            else:
                reached_flags.append(0)
        out_sections.append(
            {
                "key": key,
                "label": label,
                "reached_count": sum(reached_flags),
                "reached_rate": round(sum(reached_flags) / total, 4) if total else 0,
                "time_to_sec": _metric(time_to),
                "dwell_sec": _metric(dwell),
            }
        )
    return {
        "visits": total,
        "clicked_telegram": sum(1 for v in visits if v.clicked_telegram),
        "bot_started": sum(1 for v in visits if v.bot_started),
        "click_rate": round(sum(1 for v in visits if v.clicked_telegram) / total, 4) if total else 0,
        "bot_rate": round(sum(1 for v in visits if v.bot_started) / total, 4) if total else 0,
        "page_sec": _metric([(v.page_ms or 0) / 1000 for v in visits if v.page_ms is not None]),
        "sections": out_sections,
    }


def _visit_group_key(visit: BehaviorVisit, group_by: GroupBy) -> str:
    if group_by == "utm_content":
        return visit.utm_content or "(без utm_content)"
    if group_by == "utm_campaign":
        return visit.utm_campaign or "(без utm_campaign)"
    if group_by == "utm_source":
        return visit.utm_source or "(без utm_source)"
    if group_by == "utm_medium":
        return visit.utm_medium or "(без utm_medium)"
    if group_by == "utm_term":
        return visit.utm_term or "(без utm_term)"
    if group_by == "bot_started":
        return "bot_started=1" if visit.bot_started else "bot_started=0"
    if group_by == "clicked_telegram":
        return "clicked_telegram=1" if visit.clicked_telegram else "clicked_telegram=0"
    if group_by == "day":
        created = _as_utc(visit.created_at)
        return created.date().isoformat() if created else "(без даты)"
    return "all"


def filter_visits(
    visits: list[BehaviorVisit],
    *,
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
) -> list[BehaviorVisit]:
    start = _parse_day(date_from, end=False)
    end = _parse_day(date_to, end=True)
    out: list[BehaviorVisit] = []
    query = (q or "").strip().lower()

    for visit in visits:
        created = _as_utc(visit.created_at)
        if start and (not created or created < start):
            continue
        if end and (not created or created > end):
            continue
        if utm_source and (visit.utm_source or "") != utm_source:
            continue
        if utm_medium and (visit.utm_medium or "") != utm_medium:
            continue
        if utm_campaign and (visit.utm_campaign or "") != utm_campaign:
            continue
        if utm_content and (visit.utm_content or "") != utm_content:
            continue
        if utm_term and (visit.utm_term or "") != utm_term:
            continue
        if bot_started in {"0", "1"} and int(bool(visit.bot_started)) != int(bot_started):
            continue
        if clicked_telegram in {"0", "1"} and int(bool(visit.clicked_telegram)) != int(clicked_telegram):
            continue
        if has_yclid == "1" and not visit.yclid:
            continue
        if has_yclid == "0" and visit.yclid:
            continue
        if reached_section:
            row = next((s for s in visit.sections if s.key == reached_section and s.reached), None)
            if not row:
                continue
        if query:
            hay = " ".join(
                str(x or "")
                for x in (
                    visit.session_id,
                    visit.metrika_client_id,
                    visit.telegram_user_id,
                    visit.yclid,
                    visit.utm_source,
                    visit.utm_medium,
                    visit.utm_campaign,
                    visit.utm_content,
                    visit.utm_term,
                    visit.landing_url,
                    visit.referrer,
                )
            ).lower()
            if query not in hay:
                continue
        out.append(visit)
    return out


def sort_visits(
    visits: list[BehaviorVisit],
    *,
    sort: str = "created_at",
    order: str = "desc",
) -> list[BehaviorVisit]:
    reverse = order != "asc"

    def key_fn(v: BehaviorVisit):
        if sort == "updated_at":
            return _as_utc(v.updated_at) or datetime.min.replace(tzinfo=timezone.utc)
        if sort == "page_ms":
            return v.page_ms or 0
        if sort == "bot_started":
            return int(bool(v.bot_started))
        if sort == "clicked_telegram":
            return int(bool(v.clicked_telegram))
        if sort == "utm_content":
            return (v.utm_content or "").lower()
        if sort == "utm_campaign":
            return (v.utm_campaign or "").lower()
        if sort == "utm_term":
            return (v.utm_term or "").lower()
        if sort == "session_id":
            return v.session_id
        return _as_utc(v.created_at) or datetime.min.replace(tzinfo=timezone.utc)

    return sorted(visits, key=key_fn, reverse=reverse)


def _facet_values(visits: list[BehaviorVisit]) -> dict[str, list[str]]:
    def uniq(getter) -> list[str]:
        vals = sorted({getter(v) for v in visits if getter(v)})
        return vals

    return {
        "utm_source": uniq(lambda v: v.utm_source),
        "utm_medium": uniq(lambda v: v.utm_medium),
        "utm_campaign": uniq(lambda v: v.utm_campaign),
        "utm_content": uniq(lambda v: v.utm_content),
        "utm_term": uniq(lambda v: v.utm_term),
        "sections": [{"key": k, "label": SECTION_LABELS.get(k, k)} for k in SECTION_ORDER],
    }


async def load_visits(session: AsyncSession) -> list[BehaviorVisit]:
    result = await session.execute(select(BehaviorVisit).options(selectinload(BehaviorVisit.sections)))
    return list(result.scalars().unique().all())


async def behavior_report(
    session: AsyncSession,
    *,
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
    group_by: GroupBy = "utm_content",
    sort: str = "created_at",
    order: str = "desc",
    include_visits: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    all_visits = await load_visits(session)
    filtered = filter_visits(
        all_visits,
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
    )
    sorted_visits = sort_visits(filtered, sort=sort, order=order)

    groups_map: dict[str, list[BehaviorVisit]] = {}
    if group_by and group_by != "none":
        for visit in sorted_visits:
            groups_map.setdefault(_visit_group_key(visit, group_by), []).append(visit)
    groups = [
        {"group": name, "group_by": group_by, **_group_stats(items)}
        for name, items in sorted(groups_map.items(), key=lambda x: (-len(x[1]), x[0]))
    ]

    visit_rows = [dump_visit(v) for v in sorted_visits[: max(0, min(limit, 5000))]] if include_visits else []

    return {
        "generated_at": _utcnow().isoformat(),
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "utm_source": utm_source,
            "utm_medium": utm_medium,
            "utm_campaign": utm_campaign,
            "utm_content": utm_content,
            "utm_term": utm_term,
            "bot_started": bot_started,
            "clicked_telegram": clicked_telegram,
            "reached_section": reached_section,
            "has_yclid": has_yclid,
            "q": q,
            "group_by": group_by,
            "sort": sort,
            "order": order,
            "limit": limit,
        },
        "facets": _facet_values(all_visits),
        "totals": _group_stats(sorted_visits),
        "by_telegram": {
            "bot_started": _group_stats([v for v in sorted_visits if v.bot_started]),
            "not_bot_started": _group_stats([v for v in sorted_visits if not v.bot_started]),
            "clicked_telegram": _group_stats([v for v in sorted_visits if v.clicked_telegram]),
            "not_clicked_telegram": _group_stats([v for v in sorted_visits if not v.clicked_telegram]),
        },
        "groups": groups,
        "visits_returned": len(visit_rows),
        "visits_matched": len(sorted_visits),
        "visits_total_db": len(all_visits),
        "visits": visit_rows,
        "note": "avg/median/min/max/sum по time_to и dwell — только среди дошедших до секции; даты в UTC ISO",
    }


def export_txt(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== ПОВЕДЕНИЕ НА ЛЕНДИНГЕ ===")
    lines.append(f"Сгенерировано: {report.get('generated_at')}")
    lines.append(f"Визитов в БД: {report.get('visits_total_db')}")
    lines.append(f"После фильтров: {report.get('visits_matched')}")
    lines.append(f"В выгрузке визитов: {report.get('visits_returned')}")
    lines.append("")
    lines.append("--- Фильтры ---")
    for k, v in (report.get("filters") or {}).items():
        lines.append(f"{k}: {v if v not in (None, '') else '—'}")
    lines.append("")

    totals = report.get("totals") or {}
    lines.append("--- ИТОГО ---")
    lines.append(
        f"visits={totals.get('visits')} clicked_telegram={totals.get('clicked_telegram')} "
        f"({(totals.get('click_rate') or 0)*100:.1f}%) bot_started={totals.get('bot_started')} "
        f"({(totals.get('bot_rate') or 0)*100:.1f}%)"
    )
    lines.append(f"page_sec: {_fmt_metric(totals.get('page_sec') or {})}")
    lines.append("")
    lines.append("Секции:")
    for s in totals.get("sections") or []:
        lines.append(
            f"  [{s['key']}] {s['label']} | дошли {s['reached_count']} "
            f"({(s.get('reached_rate') or 0)*100:.1f}%)"
        )
        lines.append(f"    time_to: {_fmt_metric(s.get('time_to_sec') or {})}")
        lines.append(f"    dwell:   {_fmt_metric(s.get('dwell_sec') or {})}")
    lines.append("")

    lines.append("--- ПО TELEGRAM ---")
    for name, block in (report.get("by_telegram") or {}).items():
        lines.append(
            f"{name}: visits={block.get('visits')} bot={block.get('bot_started')} "
            f"click={block.get('clicked_telegram')} page={_fmt_metric(block.get('page_sec') or {})}"
        )
        for s in block.get("sections") or []:
            if not s.get("reached_count"):
                continue
            lines.append(
                f"  {s['key']}: reach={s['reached_count']} "
                f"tt={_fmt_metric(s.get('time_to_sec') or {})} "
                f"dw={_fmt_metric(s.get('dwell_sec') or {})}"
            )
    lines.append("")

    lines.append("--- ГРУППЫ ---")
    for g in report.get("groups") or []:
        lines.append(
            f"[{g.get('group_by')}={g.get('group')}] visits={g.get('visits')} "
            f"click={g.get('clicked_telegram')} bot={g.get('bot_started')} "
            f"page={_fmt_metric(g.get('page_sec') or {})}"
        )
        for s in g.get("sections") or []:
            lines.append(
                f"  {s['key']}: reach={s['reached_count']}/{(s.get('reached_rate') or 0)*100:.1f}% "
                f"tt={_fmt_metric(s.get('time_to_sec') or {})} "
                f"dw={_fmt_metric(s.get('dwell_sec') or {})}"
            )
        lines.append("")

    lines.append("--- ВИЗИТЫ (ПОЛНЫЙ ДАМП) ---")
    for v in report.get("visits") or []:
        lines.append("-" * 60)
        lines.append(f"session_id: {v.get('session_id')}")
        lines.append(f"created_at: {v.get('created_at')}")
        lines.append(f"updated_at: {v.get('updated_at')}")
        lines.append(f"metrika_client_id: {v.get('metrika_client_id')}")
        lines.append(f"telegram_user_id: {v.get('telegram_user_id')}")
        lines.append(f"yclid: {v.get('yclid')}")
        lines.append(
            f"utm: source={v.get('utm_source')} medium={v.get('utm_medium')} "
            f"campaign={v.get('utm_campaign')} content={v.get('utm_content')} term={v.get('utm_term')}"
        )
        lines.append(
            f"clicked_telegram={v.get('clicked_telegram')} bot_started={v.get('bot_started')} "
            f"page_sec={v.get('page_sec')}"
        )
        lines.append(f"landing_url: {v.get('landing_url')}")
        lines.append(f"referrer: {v.get('referrer')}")
        for s in v.get("sections") or []:
            lines.append(
                f"  section {s.get('key')}: reached={s.get('reached')} "
                f"time_to_sec={s.get('time_to_sec')} dwell_sec={s.get('dwell_sec')} | {s.get('label')}"
            )
    lines.append("")
    lines.append("=== КОНЕЦ ===")
    return "\n".join(lines) + "\n"
