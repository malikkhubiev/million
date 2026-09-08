# Backend «Верни себе себя»

FastAPI + ЮKassa + Telegram. Сайт лежит отдельно в `../site`.

## Запуск локально

```bash
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# заполнить TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, ключи ЮKassa
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`TELEGRAM_MODE=polling` — локально. На Render — `webhook`.

## Render

1. New Web Service → репозиторий `million`, Root Directory: `server`.
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Env: см. `.env.example`. Обязательно:
   - `APP_BASE_URL=https://<сервис>.onrender.com`
   - `TELEGRAM_MODE=webhook`
   - `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY`
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHANNEL_ID`
   - `TELEGRAM_WEBHOOK_SECRET`
   - `METRIKA_MP_TOKEN` (Measurement Protocol в настройках счётчика)

Цели Метрики (JS-событие): `view_offer`, `click_to_telegram`, `bot_started`, `diagnostic_started`, `diagnostic_finished`, `payment_started`, `payment_success`. Ценность 100 / 500 / 2000 / 3000 / 8000 / 15000 / 50000 ₽. Ecommerce purchase на успехе.

Webhook ЮKassa: `https://<сервис>.onrender.com/api/yookassa/webhook`

SQLite на free Render сбрасывается при редеплое — для истории подключите Postgres.

## Цикл оплаты

Реклама → сайт (там вся программа обучения) → кнопка открывает бота → диагностика из 5 вопросов → персональный разбор и оффер → «Записаться — 50 000 ₽» → ЮKassa → webhook `payment.succeeded` → invite в канал + сообщение в Telegram автоматически.

Даты набора и старта задаются переменными `ENROLLMENT_DEADLINE` и `COURSE_START_DATE` — бот подставляет их в тексты.

## Диагностика в боте

Вопросы и тексты разбора лежат в `app/services/bot.py`: `QUESTIONS`, `STATE_READ`, `TIMING_READ`, `ATTEMPT_READ`, `WANT_READ`. Состояние не хранится в БД — ответы едут в `callback_data` (`d|<номер вопроса>|<ответы>`), поэтому перезапуск сервиса не рвёт диалог.
