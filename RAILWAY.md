# Деплой на Railway (вместо Render)

Сайт остаётся на Vercel: https://life-energy-phi.vercel.app/  
API / бот / дашборд — этот репозиторий на Railway.

## 1. Создать проект

1. Зайди на https://railway.app → **New Project** → **Deploy from GitHub**.
2. Репозиторий: `malikkhubiev/million` (корень = этот код, не `server`).
3. Railway подхватит `Dockerfile` + `railway.toml`.

Или через CLI:

```bash
npm i -g @railway/cli
railway login
cd bot-payment   # или корень репо million
railway init
railway up
railway domain
```

После `railway domain` получишь URL вида `https://….up.railway.app`.

## 2. Volume для SQLite (обязательно, иначе БД сотрётся)

1. В сервисе → **Settings** → **Volumes** → **Add Volume**.
2. Mount path: `/app/data`
3. `DATABASE_URL` оставь: `sqlite+aiosqlite:///./data/app.db`

Без volume каждый редеплой = пустая база. Для серьёзной нагрузки лучше плагин **Postgres** и URL вида `postgresql+asyncpg://…` (потребуется `asyncpg` в requirements).

## 3. Переменные окружения

Бери ключи из локального `.env` и проставь в Railway **Variables**. Минимум:

| Ключ | Значение |
| --- | --- |
| `APP_BASE_URL` | `https://твой-домен.up.railway.app` |
| `APP_ENV` | `production` |
| `TELEGRAM_MODE` | `webhook` |
| `TELEGRAM_BOT_TOKEN` | токен бота трансформации |
| `TELEGRAM_CHANNEL_ID` | id канала трансформации |
| `TELEGRAM_WEBHOOK_SECRET` | случайная строка |
| `ENGLISH_TELEGRAM_BOT_TOKEN` | токен бота английского (опционально) |
| `ENGLISH_TELEGRAM_CHANNEL_ID` | id канала английского |
| `ENGLISH_TELEGRAM_WEBHOOK_SECRET` | случайная строка |
| `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` | ключи ЮKassa |
| `YOOKASSA_RECEIPT_DESCRIPTION` | `Лицензия на цифровые материалы` |
| `YOOKASSA_PAYMENT_SUBJECT` | `intellectual_activity` (РИД, тег 1212=9) |
| `METRIKA_MP_TOKEN` | токен Measurement Protocol |
| `SECRET_KEY` | случайная строка |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app.db` |

После первого деплоя webhook Telegram выставится сам для каждого бота с токеном
(`/api/telegram/webhook` и `/api/telegram/english/webhook`).

Один сервис Railway = оба бота + одна ЮKassa (расходы пополам).

## 4. Webhook ЮKassa

В кабинете ЮKassa → **Настройки уведомлений** URL:

`https://million.up.railway.app/api/yookassa/webhook`

(или актуальный `APP_BASE_URL` + `/api/yookassa/webhook`).

События: `payment.succeeded`, `payment.canceled`, при двухстадийных платежах ещё `payment.waiting_for_capture`.

Дашборд оплат: `https://твой-домен.up.railway.app/admin/payments`  
Кнопка **«Синхронизировать pending»** подтягивает статусы из API ЮKassa, если вебхук не дошёл.

## 5. Сайт (Vercel)

В `website/index.html` обнови `LIFE_API` на новый Railway URL (сейчас там ещё Render).

Либо на лендинге перед скриптом:

```html
<script>window.LIFE_API = "https://твой-домен.up.railway.app";</script>
```

## 6. Проверка

- Дашборд: `https://твой-домен.up.railway.app/admin/behavior`
- JSON: `/api/behavior/stats`
- Бот: `/start` → «Показать номер» → оплата

## 7. Выключить Render

Когда Railway стабилен:

1. Останови / удали сервис на Render.
2. Убедись, что ЮKassa и сайт больше не бьют в `*.onrender.com`.

## Стоимость

Hobby на Railway обычно дешевле «всегда включённого» Render Web Service; спишь только если выставишь sleep (на Railway по умолчанию сервис живой). Следи за usage в биллинге.
