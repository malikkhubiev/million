# Бот «Верни себе себя» (+ бот английского)

Один FastAPI на Railway, два Telegram-бота, одна ЮKassa. Сайт — отдельно: [life_energy](https://github.com/malikkhubiev/life_energy).

- **life** — трансформация «Верни себе себя» (`TELEGRAM_*`)
- **english** — курс английского (`ENGLISH_TELEGRAM_*`), сайт не обязателен: продукт объясняется в боте

В чеке 54-ФЗ для обоих: наименование «Лицензия на цифровые материалы», `payment_subject=intellectual_activity` (РИД).

Клиент в боте нажимает «Показать номер» → оплата ЮKassa → после `payment.succeeded` invite в канал этого бота.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
# заполни .env (единственный файл секретов)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Локально: `TELEGRAM_MODE=polling` (оба бота с токеном крутятся вместе).  
Прод: `TELEGRAM_MODE=webhook`.

Дефолтные тексты ботов: `texts.py` (`LIFE`, `ENGLISH`, `DEFAULTS`) → сид в БД → правка в `/admin/texts`.

Узнать id канала: `python -m app.scripts.discover_channel`  
Проверить invite: `python -m app.scripts.test_invite`

## Railway (прод)

Пошагово: **[RAILWAY.md](./RAILWAY.md)**.

Кратко:

- Build: `Dockerfile` (Python 3.12.10)
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Volume: `/app/data` (иначе SQLite сотрётся при редеплое)
- `APP_BASE_URL=https://….up.railway.app`, `TELEGRAM_MODE=webhook`
- Webhook ЮKassa: `https://….up.railway.app/api/yookassa/webhook`
- После деплоя обнови `LIFE_API` на лендинге (Vercel)

Секреты и токены — только в `.env` локально / Variables на Railway (отдельных `*.env.example` нет).

## Render (устарело)

Раньше сервис жил на `*.onrender.com`. Конфиг `render.yaml` оставлен для истории — новый прод только на Railway.

## Архитектура оплаты

```
сайт → t.me/bot?start=site
         ↓
   «Показать номер» (request_contact)
         ↓ phone → metadata ЮKassa
   кнопка «Оплатить 65 000 ₽»
         ↓ webhook payment.succeeded
   createChatInviteLink → ссылка в канал
```

Даты, цена и оставшиеся места хранятся в БД (`app_settings`) и правятся в админке `/admin/dates`. Публичный API: `GET /api/dates` (сайт подтягивает тексты, таймер, цену и места). При `payment.succeeded` число мест уменьшается на 1. Seed при первом запуске: `ENROLLMENT_DEADLINE_ISO` / `COURSE_START_DATE_ISO` / `PRODUCT_PRICE_KOPECKS`.

Яндекс.Метрика `112323537`. Цели Директа: `view_offer` (50), `tg_click` (30), `bot_started` (150), `show_phone` (1000), `payment_started` (15000), `payment_success` (65000). Доскролл секций: `headline_*` / `section_*`. Дашборд: `https://<railway>/admin/behavior` · JSON `/api/behavior/stats`. Сайт: https://life-energy-phi.vercel.app/ · UTM — в `website/README.md`. Деплой API: [RAILWAY.md](./RAILWAY.md).
