# Верни себе себя

Два независимых куска:

| Папка | Что | Куда |
| --- | --- | --- |
| `site/` | Статический лендинг | [life_energy](https://github.com/malikkhubiev/life_energy) (Vercel) |
| `server/` | FastAPI, ЮKassa, Telegram-бот, БД | этот репозиторий → [Render](https://render.com) |

Клиент на сайте нажимает одну кнопку → Telegram. Оплата внутри бота. После `payment.succeeded` бот сам присылает пригласительную в закрытый канал. Номер заказа и email клиенту вводить не нужно.

## Сайт (`site/`)

```bash
cd site
# открыть index.html или любой static host
```

Кнопка «Вернуть себе себя» передаёт в бота ClientID Метрики (склейка визита сайта и Telegram).

Яндекс.Метрика `112323537`. Цели: `view_offer` (100), `click_to_telegram` (500), `bot_started` (2000), `payment_started` (15000), `payment_success` (50000 ₽ + ecommerce). Подробности в `site/README.md`.

## Бот (`server/`)

```bash
cd server
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

Root Directory: `server`  
Build: `pip install -r requirements.txt`  
Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Переменные: `APP_BASE_URL`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_MODE=webhook`, `TELEGRAM_WEBHOOK_SECRET`.

Webhook ЮKassa: `https://<сервис>.onrender.com/api/yookassa/webhook`

## Git

Коммиты кратко на русском. Теги: `v1.1.0`. `.env` не коммитить.

## Архитектура оплаты

```
сайт → t.me/bot?start=site
         ↓
   бот знает telegram_user_id
         ↓ кнопка «Вернуть себе себя»
   POST ЮKassa (Idempotence-Key) + metadata.telegram_user_id
         ↓
   клиент платит на ЮKassa
         ↓ webhook payment.succeeded
   createChatInviteLink → сообщение в Telegram
```
