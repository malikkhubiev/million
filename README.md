# Верни себе себя

Лендинг программы + приём оплаты через **ЮKassa** + выдача **пригласительной ссылки** в закрытый Telegram-канал. После оплаты карта на сайте не вводится — только редирект на ЮKassa.

Репозиторий: https://github.com/malikkhubiev/million

---

## Что внутри

| Часть | Назначение |
| --- | --- |
| `index.html` | Лендинг |
| `success.html` | Страница после оплаты: статус + invite-ссылка |
| `offer.html` | Черновик оферты |
| `app/` | FastAPI: checkout, вебхуки, БД, Telegram |
| `img/` | Фото: блок «чувствовать» (`woman-*`) и отдельный набор для манифеста (`manifesto-*`) |
| `data/app.db` | SQLite (создаётся при старте) |

---

## Архитектура

```
Браузер → POST /api/checkout → ЮKassa (redirect)
                ↓
         запись Client + Payment (status=pending, Idempotence-Key)
                ↓
Пользователь платит на стороне ЮKassa
                ↓
ЮKassa webhook → POST /api/yookassa/webhook (быстрый 200)
                ↓ фон (BackgroundTasks + httpx)
         Payment=succeeded → createChatInviteLink → InviteLink в БД
                ↓
return_url → /success.html?order=VS-… → GET /api/orders/{id}?sync=true
                ↓
         показ invite_url
```

**Telegram-бот** (`TELEGRAM_MODE`):

- `polling` — локально: long polling, webhook снимается при старте.
- `webhook` — Render: при старте вызывается `setWebhook` на `{APP_BASE_URL}/api/telegram/webhook`.

Бот отвечает на `/start` и присылает ссылку по номеру заказа (`VS-…`) или email.

---

## База данных

SQLite (async SQLAlchemy + aiosqlite). Таблицы:

- **clients** — имя, email, telegram_user_id/username, даты
- **payments** — order_id, yookassa_payment_id, idempotence_key, amount, status, paid_at, fulfilled_at, raw JSON
- **invite_links** — url, member_limit, expire_at, связь с payment/client
- **webhook_events** — идемпотентность входящих событий (`provider` + `event_key`)

Все ключевые даты хранятся для контроля и аудита.

---

## ЮKassa

Документация: [API](https://yookassa.ru/developers/api), [быстрый старт](https://yookassa.ru/developers/payment-acceptance/getting-started/quick-start), [уведомления](https://yookassa.ru/developers/using-api/webhooks).

Решения в коде:

1. Только **confirmation.redirect** — без виджета привязки карты и без сбора PAN на сайте.
2. Заголовок **Idempotence-Key** (UUID) на создание платежа; ключ сохраняется в БД.
3. `capture: true` — одностадийная оплата.
4. `metadata.order_id` — связь webhook ↔ заказ.
5. Опциональный **receipt** (54-ФЗ): если в кабинете включена фискализация — заполните `YOOKASSA_VAT_CODE` / `YOOKASSA_TAX_SYSTEM_CODE`; иначе поставьте оба пустыми в `.env` и уберите из настроек (см. `app/config.py` / `yookassa.py`).
6. Webhook обрабатывается **асинхронно** (фон + httpx), ответ ЮKassa — сразу `200`.
7. На `production` проверяется IP из белого списка ЮKassa; дубликаты режутся через `webhook_events`.
8. Страница успеха дополнительно **синхронизирует** статус через `GET /payments/{id}` на случай задержки webhook.

В личном кабинете ЮKassa укажите URL уведомлений:

`https://ВАШ-СЕРВИС.onrender.com/api/yookassa/webhook`

События: `payment.succeeded`, `payment.canceled` (и при необходимости `payment.waiting_for_capture`).

Тестовые карты демо-магазина — в документации ЮKassa (например `5555 5555 5555 4444`).

---

## Telegram

1. Бот уже админ канала с правом приглашений.
2. В `.env` нужны `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHANNEL_ID` (число вида `-100…`).
3. Узнать id канала:

```bash
python -m app.scripts.discover_channel
```

Перешлите боту пост из канала или используйте @userinfobot.

4. Проверка пригласительной:

```bash
python -m app.scripts.test_invite
```

Ссылки создаются с `member_limit=1` и сроком `TELEGRAM_INVITE_EXPIRE_DAYS` (по умолчанию 30).

**Безопасность:** токен бота нельзя коммитить. Если токен светился в чате/тикете — перевыпустите его у @BotFather.

---

## Локальный запуск

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# заполните ключи
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000  

Проверка здоровья: http://127.0.0.1:8000/api/health  

Локально: `TELEGRAM_MODE=polling`, `APP_ENV=development`.

Для приёма webhook ЮKassa с ноутбука нужен туннель (ngrok / cloudflared) на `/api/yookassa/webhook`, либо полагайтесь на `?sync=true` на success-странице после возврата с оплаты.

---

## Render.com

1. New → Web Service → этот репозиторий (или Blueprint `render.yaml`).
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Env:

| Переменная | Пример |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_BASE_URL` | `https://million-xxxx.onrender.com` |
| `TELEGRAM_MODE` | `webhook` |
| `YOOKASSA_SHOP_ID` | из кабинета |
| `YOOKASSA_SECRET_KEY` | из кабинета |
| `TELEGRAM_BOT_TOKEN` | от BotFather |
| `TELEGRAM_CHANNEL_ID` | `-100…` |
| `TELEGRAM_WEBHOOK_SECRET` | случайная строка |
| `SECRET_KEY` | случайная строка |

SQLite на free-tier Render **эфемерна** (файловая система сбрасывается при редеплое). Для продакшена с историей платежей подключите Postgres и смените `DATABASE_URL` на `postgresql+asyncpg://…` (потребуется драйвер `asyncpg` в `requirements.txt`).

После деплоя:

1. В ЮKassa — URL webhook.
2. Перезапустите сервис (чтобы `setWebhook` Telegram отработал с верным `APP_BASE_URL`).

---

## API кратко

- `POST /api/checkout` `{name, email, agree}` → `{order_id, confirmation_url}`
- `GET /api/orders/{order_id}?sync=true` → статус + `invite_url`
- `POST /api/yookassa/webhook` — уведомления ЮKassa
- `POST /api/telegram/webhook` — апдейты бота (прод)
- `GET /api/health` — конфигурация без секретов

---

## Git: коммиты и теги

- Сообщения коммитов — **кратко на русском** (что/зачем):  
  `оплата: редирект ЮKassa и выдача invite`  
  `лендинг: анимация слов и отдельные фото манифеста`
- Теги релизов: `v1.0.0`, `v1.1.0` — после стабильного деплоя.
- В git **не** попадают `.env`, `data/*.db`, секреты.

```bash
git tag -a v1.0.0 -m "Первый рабочий релиз: ЮKassa + Telegram"
git push origin v1.0.0
```

---

## Структура решений (кратко)

1. **Один платёжный провайдер** — ЮKassa, без CloudPayments и без форм карты.
2. **Доступ = invite в канал**, не раздача файлов по email.
3. **Идемпотентность** на исходящих (ЮKassa) и входящих (webhook_events + повторный fulfill не плодит ссылки).
4. **Async везде** — FastAPI + httpx + aiosqlite, фон для webhook.
5. **Два режима бота** — polling для отладки, webhook для Render.
6. **Фото не пересекаются** между блоками «чувствовать» и манифестом.

---

## Чеклист перед продакшеном

- [ ] Ключи ЮKassa боевого магазина
- [ ] Webhook ЮKassa на прод-URL
- [ ] `TELEGRAM_CHANNEL_ID` проверен через `test_invite`
- [ ] `APP_BASE_URL` = публичный HTTPS
- [ ] `TELEGRAM_MODE=webhook`
- [ ] Оферта с реквизитами
- [ ] Токен бота не закоммичен; при утечке — revoke
- [ ] Постоянная БД (Postgres), если нужна история после редеплоев
