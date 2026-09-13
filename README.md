# Бот «Верни себе себя»

FastAPI, ЮKassa, Telegram-бот, БД. Сайт — отдельно: [life_energy](https://github.com/malikkhubiev/life_energy).

Клиент на сайте нажимает одну кнопку → Telegram (`?start=site`). Бот просит номер телефона, создаёт оплату в ЮKassa и после `payment.succeeded` сам присылает пригласительную в закрытый канал.

После номера также показывается VIP: персональное обучение за 200 000 ₽ (сначала диагностика 10 000 ₽ за час созвона, затем 190 000 ₽ за 2 недели). Для отладки оплат используйте тестовый магазин ЮKassa (`test_…` ключ).

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Локально: `TELEGRAM_MODE=polling`.  
Прод: `TELEGRAM_MODE=webhook`.

Узнать id канала: `python -m app.scripts.discover_channel`  
Проверить invite: `python -m app.scripts.test_invite`

## Render

Репозиторий [million](https://github.com/malikkhubiev/million). Root Directory — корень репозитория (не `server`).

Python: **3.12** (файл `.python-version` и `PYTHON_VERSION=3.12.10`). Не оставляйте дефолт Render — это 3.14, и `pydantic-core` / `orjson` тогда собираются из исходников и падают.

Build: `pip install -r requirements.txt`  
Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Без `--reload` (это только для локальной разработки). Хост — `0.0.0.0`, не `$HOST`: Render задаёт только `$PORT`.

Переменные: `APP_BASE_URL`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_MODE=webhook`, `TELEGRAM_WEBHOOK_SECRET`. Обязательно `METRIKA_MP_TOKEN` (Measurement Protocol в настройках счётчика).

Webhook ЮKassa: `https://<сервис>.onrender.com/api/yookassa/webhook`

SQLite на free Render сбрасывается при редеплое — для истории подключите Postgres.

## Архитектура оплаты

```
сайт → t.me/bot?start=site
         ↓
   «Показать номер» (request_contact)
         ↓ phone → metadata ЮKassa
   «Генерируем кнопку Оплаты...»
         ↓
   кнопка «Оплатить 50 000 ₽» + «VIP Персональное обучение»
         ↓ webhook payment.succeeded
   программа → createChatInviteLink
   VIP диагностика / обучение → сообщение в Telegram (без инвайта)
```

Даты набора и старта задаются переменными `ENROLLMENT_DEADLINE` и `COURSE_START_DATE`.

Яндекс.Метрика `112323537`. Цели Директа: `view_offer` (50), `bot_started` (150), `show_phone` (1000), `payment_started` (15000), `payment_success` (50000). Доскролл заголовков на сайте: `headline_1`…`headline_12`.
