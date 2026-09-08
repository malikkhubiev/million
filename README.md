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

Кнопки лендинга передают в бота ClientID Метрики (склейка визита сайта и Telegram).

Содержимое `site/` живёт в корне репозитория [life_energy](https://github.com/malikkhubiev/life_energy), откуда деплоится Vercel. Выкатка правок — зеркалим папку в клон того репозитория:

```powershell
git clone https://github.com/malikkhubiev/life_energy.git $env:TEMP\life_energy_sync
robocopy site $env:TEMP\life_energy_sync /MIR /XD $env:TEMP\life_energy_sync\.git
git -C $env:TEMP\life_energy_sync add -A
git -C $env:TEMP\life_energy_sync commit -m "лендинг: что поменяли"
git -C $env:TEMP\life_energy_sync push origin main
```

Отдельный remote на `life_energy` в этом репозитории намеренно не заведён: истории у репозиториев разные, и `git push life_energy main` залил бы туда весь проект вместо лендинга.

Яндекс.Метрика `112323537`. Цели: `view_offer` (100), `click_to_telegram` (500), `bot_started` (2000), `diagnostic_started` (3000), `diagnostic_finished` (8000), `payment_started` (15000), `payment_success` (50000 ₽ + ecommerce). Подробности в `site/README.md`.

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
         ↓ диагностика: 5 вопросов → персональный разбор
         ↓ кнопка «Записаться — 50 000 ₽»
   POST ЮKassa (Idempotence-Key) + metadata.telegram_user_id
         ↓
   клиент платит на ЮKassa
         ↓ webhook payment.succeeded
   createChatInviteLink → сообщение в Telegram
```
