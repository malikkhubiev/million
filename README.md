# Бот «Верни себе себя»

FastAPI, ЮKassa, Telegram-бот, БД. Сайт — отдельно: [life_energy](https://github.com/malikkhubiev/life_energy).

Клиент на сайте нажимает одну кнопку → Telegram. Оплата внутри бота. После `payment.succeeded` бот сам присылает пригласительную в закрытый канал. Номер заказа и email клиенту вводить не нужно.

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

Build: `pip install -r requirements.txt`  
Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Переменные: `APP_BASE_URL`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_MODE=webhook`, `TELEGRAM_WEBHOOK_SECRET`. Обязательно `METRIKA_MP_TOKEN` (Measurement Protocol в настройках счётчика).

Webhook ЮKassa: `https://<сервис>.onrender.com/api/yookassa/webhook`

SQLite на free Render сбрасывается при редеплое — для истории подключите Postgres.

## Архитектура оплаты

```
сайт → t.me/bot?start=site
         ↓
   бот знает telegram_user_id
         ↓ диагностика: 5 вопросов → персональный разбор
         ↓ кнопка «Начать трансформацию»
   POST ЮKassa (Idempotence-Key) + metadata.telegram_user_id
         ↓
   клиент платит на ЮKassa
         ↓ webhook payment.succeeded
   createChatInviteLink → сообщение в Telegram
```

Даты набора и старта задаются переменными `ENROLLMENT_DEADLINE` и `COURSE_START_DATE`.

Вопросы и тексты разбора лежат в `app/services/bot.py`. Состояние не хранится в БД — ответы едут в `callback_data`, поэтому перезапуск сервиса не рвёт диалог.

Яндекс.Метрика `112323537`. Цели: `view_offer`, `click_to_telegram`, `bot_started`, `diagnostic_started`, `diagnostic_finished`, `payment_started`, `payment_success`.
